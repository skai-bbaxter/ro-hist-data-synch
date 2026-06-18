import html
import json
import logging
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import msal
import requests
from dotenv import load_dotenv


load_dotenv()

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


GRAPH_API_DEFAULT = "https://graph.microsoft.com/v1.0"
SKAINET_HC_URL = "https://healthcheck.prod.microservice.skaivision.net/skaibox/summary"
SKAINET_PC_BASE = "https://productcatalog.prod.microservice.skaivision.net/catalog/config/orgs"
SKAIBOX_ID_LABEL = "Skaibox ID:"
ACTIVATION_EMAIL_FOLDERS = ("Inbox", "FieldOps")
NY = ZoneInfo("America/New_York")


def _required_env(name):
    value = os.getenv(name)
    if value is None or not value.strip():
        raise ValueError(f"{name} not found in .env file")
    return value.strip().strip('"')


def _env_value(name, default=None):
    value = os.getenv(name, default)
    if value is None:
        return None
    return value.split("#", 1)[0].strip().strip('"')


def get_outlook_scopes():
    raw_scopes = _required_env("OUTLOOK_SCOPES")

    try:
        scopes = json.loads(raw_scopes)
    except json.JSONDecodeError:
        scopes = [scope.strip() for scope in raw_scopes.replace(",", " ").split()]

    if isinstance(scopes, str):
        scopes = [scopes.strip()]

    if not isinstance(scopes, list):
        raise ValueError("OUTLOOK_SCOPES must be a JSON array or a list of scope names")

    scopes = [str(scope).strip() for scope in scopes if str(scope).strip()]
    if not scopes:
        raise ValueError("OUTLOOK_SCOPES must contain at least one scope")

    return scopes


def get_access_token(application_id, scopes):
    """Get access token using device code flow."""
    authority = "https://login.microsoftonline.com/common"

    client = msal.PublicClientApplication(
        client_id=application_id,
        authority=authority,
    )

    refresh_token = None
    if os.path.exists("refresh_token.txt"):
        with open("refresh_token.txt", "r", encoding="utf-8") as f:
            refresh_token = f.read().strip()

    if refresh_token:
        token_response = client.acquire_token_by_refresh_token(refresh_token, scopes=scopes)
    else:
        try:
            flow = client.initiate_device_flow(scopes=scopes)
            if "user_code" not in flow:
                raise ValueError("Failed to create device flow")

            logger.info(flow["message"])
            token_response = client.acquire_token_by_device_flow(flow)
        except Exception as e:
            logger.warning("Error during authentication: %s", e)
            raise

    if "access_token" in token_response:
        if "refresh_token" in token_response:
            with open("refresh_token.txt", "w", encoding="utf-8") as f:
                f.write(token_response["refresh_token"])

        return token_response["access_token"]

    error_message = token_response.get("error_description", str(token_response))
    raise Exception(f"Failed to acquire access token: {error_message}")


def graph_get(access_token, url, params=None, extra_headers=None):
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/json",
    }
    if extra_headers:
        headers.update(extra_headers)
    response = requests.get(
        url,
        headers=headers,
        params=params,
        timeout=30,
    )
    response.raise_for_status()
    return response.json()


def _odata_string(value):
    return "'" + value.replace("'", "''") + "'"


def graph_search_messages(access_token, url, search_query, select, top=25):
    payload = graph_get(
        access_token,
        url,
        params={
            "$search": f'"{search_query}"',
            "$select": select,
            "$top": str(top),
        },
        extra_headers={"ConsistencyLevel": "eventual"},
    )
    return payload.get("value", [])


def _message_from_address(message):
    from_obj = message.get("from") or {}
    return (from_obj.get("emailAddress") or {}).get("address") or ""


def list_activation_candidates_in_folder(
    access_token, graph_api, user_email, folder_id, sender, subject, page_size=50
):
    messages_url = f"{graph_api}/users/{user_email}/mailFolders/{folder_id}/messages"
    filter_expr = (
        f"from/emailAddress/address eq {_odata_string(sender)} "
        f"and subject eq {_odata_string(subject)}"
    )
    payload = graph_get(
        access_token,
        messages_url,
        params={
            "$filter": filter_expr,
            "$select": "id,subject,receivedDateTime,from,parentFolderId",
            "$orderby": "receivedDateTime desc",
            "$top": str(page_size),
        },
        extra_headers={"ConsistencyLevel": "eventual"},
    )
    return payload.get("value", [])


def _search_activation_candidates(
    access_token,
    graph_api,
    user_email,
    sender,
    subject,
    dms_subscription_id,
    search_folders,
):
    messages_url = f"{graph_api}/users/{user_email}/messages"
    search_query = f"body:{dms_subscription_id}"
    try:
        return graph_search_messages(
            access_token,
            messages_url,
            search_query,
            select="id,subject,receivedDateTime,from,parentFolderId",
        )
    except requests.HTTPError as exc:
        if exc.response is None or exc.response.status_code != 400:
            raise
        logger.warning("Indexed mailbox search failed; using filtered folder lookup")

    candidates = []
    for folder in search_folders:
        candidates.extend(
            list_activation_candidates_in_folder(
                access_token, graph_api, user_email, folder["id"], sender, subject
            )
        )
    return candidates


def find_mail_folder(access_token, graph_api, user_email, folder_name):
    folders_url = f"{graph_api}/users/{user_email}/mailFolders"
    folder_name_cf = folder_name.casefold()
    next_url = folders_url
    params = {"$select": "id,displayName"}

    while next_url:
        payload = graph_get(access_token, next_url, params=params)
        params = None

        for folder in payload.get("value", []):
            if folder.get("displayName", "").casefold() == folder_name_cf:
                return folder

        next_url = payload.get("@odata.nextLink")

    raise ValueError(f"Outlook folder not found: {folder_name!r}")


def get_unread_messages(access_token, graph_api, user_email, folder_id):
    messages_url = f"{graph_api}/users/{user_email}/mailFolders/{folder_id}/messages"
    next_url = messages_url
    params = {
        "$filter": "isRead eq false",
        "$select": "id,subject,receivedDateTime,isRead",
        "$top": "50",
    }

    while next_url:
        payload = graph_get(access_token, next_url, params=params)
        params = None

        for message in payload.get("value", []):
            yield message

        next_url = payload.get("@odata.nextLink")


def exit_with_error(message):
    logger.error(message)
    sys.exit(1)


def get_message_body(access_token, graph_api, user_email, message_id):
    url = f"{graph_api}/users/{user_email}/messages/{message_id}"
    payload = graph_get(access_token, url, params={"$select": "body"})
    body = payload.get("body") or {}
    return body.get("content") or ""


def _body_to_searchable_text(body_text):
    text = html.unescape(body_text)
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text)


def extract_skaibox_id(body_text):
    searchable = _body_to_searchable_text(body_text)
    marker_index = searchable.find(SKAIBOX_ID_LABEL)
    if marker_index == -1:
        exit_with_error("Skaibox ID not found in email body")

    remainder = searchable[marker_index + len(SKAIBOX_ID_LABEL) :].strip()
    match = re.match(r"([A-Za-z0-9_-]+)", remainder)
    if not match:
        exit_with_error("Skaibox ID not found in email body")

    return match.group(1)


def get_org_long_id_from_skaibox(skaibox_id):
    payload = {
        "query": "",
        "skaiboxIds": [skaibox_id],
        "organizationIds": [],
        "filterBy": {
            "monitoredStatus": "ANY",
            "serverStatus": "ANY",
            "vpnStatus": "ANY",
            "serverHealthStatus": ["ANY"],
            "billingStatus": "ANY",
            "serverType": ["ANY"],
            "statusCodes": ["ANY"],
            "vendors": ["ANY"],
        },
        "startIndex": 0,
        "pageSize": 1,
    }
    response = requests.post(
        SKAINET_HC_URL,
        json=payload,
        headers={"Content-Type": "application/json"},
        timeout=30,
    )
    response.raise_for_status()
    items = response.json().get("items") or []
    if not items:
        exit_with_error(f"No org found for Skaibox ID: {skaibox_id}")

    org_long_id = (items[0].get("organization") or {}).get("longId")
    if not org_long_id:
        exit_with_error(f"No org found for Skaibox ID: {skaibox_id}")

    return org_long_id


def get_dms_subscription_id(org_long_id, skaibox_id):
    response = requests.get(f"{SKAINET_PC_BASE}/{org_long_id}", timeout=30)
    response.raise_for_status()
    products = (response.json().get("productConfig") or {}).get("products") or []
    for product in products:
        if product.get("productType") == "SKAI_BOX":
            subscription_id = (product.get("dms") or {}).get("subscriptionId")
            if subscription_id:
                return subscription_id

    exit_with_error(f"No DMS Subscription ID found for Skaibox ID: {skaibox_id}")


def get_activation_search_folders(access_token, graph_api, user_email):
    folders = []
    for folder_name in ACTIVATION_EMAIL_FOLDERS:
        if folder_name.casefold() == "inbox":
            url = f"{graph_api}/users/{user_email}/mailFolders/inbox"
            folders.append(
                graph_get(access_token, url, params={"$select": "id,displayName"})
            )
        else:
            folders.append(
                find_mail_folder(access_token, graph_api, user_email, folder_name)
            )
    return folders


def find_activation_email(
    access_token, graph_api, user_email, sender, subject, dms_subscription_id
):
    matches = []
    search_folders = get_activation_search_folders(access_token, graph_api, user_email)
    allowed_folder_ids = {folder["id"] for folder in search_folders}
    candidates = _search_activation_candidates(
        access_token,
        graph_api,
        user_email,
        sender,
        subject,
        dms_subscription_id,
        search_folders,
    )

    for message in candidates:
        if message.get("parentFolderId") not in allowed_folder_ids:
            continue
        if _message_from_address(message).casefold() != sender.casefold():
            continue
        if message.get("subject") != subject:
            continue

        body_text = get_message_body(access_token, graph_api, user_email, message["id"])
        if dms_subscription_id in _body_to_searchable_text(body_text):
            matches.append(message)

    if not matches:
        exit_with_error(
            f"No activation email found for DMS Subscription ID: {dms_subscription_id}"
        )

    if len(matches) > 1:
        exit_with_error(
            f"Multiple activation emails found for DMS Subscription ID: {dms_subscription_id}"
        )

    return matches[0]


def company_name_from_subject(subject, subject_prefix):
    if not subject.startswith(subject_prefix):
        return None

    company_name = subject[len(subject_prefix) :].strip()
    if company_name:
        return company_name

    if "-" not in subject:
        return None

    company_name = subject.rsplit("-", 1)[1].strip()
    return company_name or None


def call_hubspot_company_script(company_name):
    script_path = os.path.join(os.path.dirname(__file__), "get-hubspot-company-info.py")
    logger.info("Calling get-hubspot-company-info.py for company: %s", company_name)
    return subprocess.run([sys.executable, script_path, company_name], check=False).returncode


def _activation_date_ny(received_datetime):
    if not received_datetime:
        return None

    normalized = received_datetime.replace("Z", "+00:00")
    dt = datetime.fromisoformat(normalized)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(NY).date()


def _verify_ro_history_dates(activation_date):
    try:
        start_date = activation_date.replace(year=activation_date.year - 2)
    except ValueError:
        start_date = activation_date.replace(year=activation_date.year - 2, day=28)
    end_date = datetime.now(NY).date()
    return start_date.strftime("%m-%d-%Y"), end_date.strftime("%m-%d-%Y")


def call_verify_ro_history(company_name, received_datetime):
    script_path = os.path.join(os.path.dirname(__file__), "verify-ro-history.py")
    activation_date = _activation_date_ny(received_datetime)
    if activation_date is None:
        logger.warning(
            "Skipping verify-ro-history.py for company %s: missing activation date",
            company_name,
        )
        return 1

    start_date, end_date = _verify_ro_history_dates(activation_date)
    logger.info(
        "Calling verify-ro-history.py for company: %s (%s to %s)",
        company_name,
        start_date,
        end_date,
    )
    result = subprocess.run(
        [
            sys.executable,
            script_path,
            "--hs-company-name",
            company_name,
            "--start-date",
            start_date,
            "--end-date",
            end_date,
            "--stats",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.stdout:
        sys.stdout.write(result.stdout)
        if not result.stdout.endswith("\n"):
            sys.stdout.write("\n")
    if result.stderr:
        sys.stderr.write(result.stderr)
        if not result.stderr.endswith("\n"):
            sys.stderr.write("\n")
    return result.returncode


def main():
    graph_api = _env_value("OUTLOOK_GRAPH_API", GRAPH_API_DEFAULT) or GRAPH_API_DEFAULT
    application_id = _required_env("OUTLOOK_CLIENT_ID")
    folder_name = _required_env("OUTLOOK_FOLDER_NAME")
    subject_prefix = _required_env("OUTLOOK_EMAIL_SUBJECT_PREFIX")
    user_email = _required_env("OUTLOOK_USER_EMAIL")
    activation_sender = _required_env("ACTIVATION_EMAIL_SENDER")
    activation_subject = _required_env("ACTIVATION_EMAIL_SUBJECT")
    scopes = get_outlook_scopes()

    access_token = get_access_token(
        application_id=application_id,
        scopes=scopes,
    )
    folder = find_mail_folder(access_token, graph_api, user_email, folder_name)

    processed_count = 0
    matched_count = 0

    for message in get_unread_messages(access_token, graph_api, user_email, folder["id"]):
        subject = message.get("subject") or ""
        company_name = company_name_from_subject(subject, subject_prefix)
        if not company_name:
            continue

        matched_count += 1
        body_text = get_message_body(access_token, graph_api, user_email, message["id"])
        skaibox_id = extract_skaibox_id(body_text)
        org_long_id = get_org_long_id_from_skaibox(skaibox_id)
        dms_subscription_id = get_dms_subscription_id(org_long_id, skaibox_id)

        print(f"Subject: {subject}")
        print(f"Skaibox ID: {skaibox_id}")
        print(f"DMS Subscription ID: {dms_subscription_id}")

        activation_email = find_activation_email(
            access_token,
            graph_api,
            user_email,
            activation_sender,
            activation_subject,
            dms_subscription_id,
        )
        print(
            f"DMS Activation email found. DMS Activation Date: {activation_email.get('receivedDateTime', '')}"
        )

        return_code = call_hubspot_company_script(company_name)
        if return_code == 0:
            processed_count += 1
        else:
            logger.warning(
                "get-hubspot-company-info.py exited with status %s for company: %s",
                return_code,
                company_name,
            )

        verify_return_code = call_verify_ro_history(
            company_name,
            activation_email.get("receivedDateTime", ""),
        )
        if verify_return_code != 0:
            logger.warning(
                "verify-ro-history.py exited with status %s for company: %s",
                verify_return_code,
                company_name,
            )

    logger.info(
        "Finished. Matched %s unread message(s); HubSpot script succeeded for %s.",
        matched_count,
        processed_count,
    )


if __name__ == "__main__":
    main()
