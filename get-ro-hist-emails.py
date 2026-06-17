import json
import logging
import os
import subprocess
import sys

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


def _odata_string(value):
    return "'" + value.replace("'", "''") + "'"


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


def graph_get(access_token, url, params=None):
    response = requests.get(
        url,
        headers={
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/json",
        },
        params=params,
        timeout=30,
    )
    response.raise_for_status()
    return response.json()


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


def main():
    graph_api = _env_value("OUTLOOK_GRAPH_API", GRAPH_API_DEFAULT) or GRAPH_API_DEFAULT
    application_id = _required_env("OUTLOOK_CLIENT_ID")
    folder_name = _required_env("OUTLOOK_FOLDER_NAME")
    subject_prefix = _required_env("OUTLOOK_EMAIL_SUBJECT_PREFIX")
    user_email = _required_env("OUTLOOK_USER_EMAIL")
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
        return_code = call_hubspot_company_script(company_name)
        if return_code == 0:
            processed_count += 1
        else:
            logger.warning(
                "get-hubspot-company-info.py exited with status %s for company: %s",
                return_code,
                company_name,
            )

    logger.info(
        "Finished. Matched %s unread message(s); HubSpot script succeeded for %s.",
        matched_count,
        processed_count,
    )


if __name__ == "__main__":
    main()
