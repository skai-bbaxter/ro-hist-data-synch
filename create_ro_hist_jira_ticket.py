#!/usr/bin/env python3
"""
Create a Jira ticket for an RO History (DMS bulkload) data-load request.

Matches the structure of ro-hist-jira-ticket-example.json: SKAI project,
Sub-task under the Historical RO Fetch parent, AdHoc Reporting component.

Authentication: set JIRA_API_TOKEN in a .env file next to this script (or cwd).
  Use the full Authorization value, e.g. JIRA_API_TOKEN=Basic <base64...>
  or only the base64 part after Basic (either works).
"""

# Example usage:
# python create_ro_hist_jira_ticket.py --summary "DMS Bulkload - RO history for Seminole Toyota" --description "Pull window 01-27-2023 - 01-26-2026"

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Any

import requests
from dotenv import load_dotenv


def _load_jira_env() -> None:
    """Load .env from script directory, then cwd (cwd overrides duplicate keys)."""
    script_dir = Path(__file__).resolve().parent
    load_dotenv(script_dir / ".env")
    load_dotenv(Path.cwd() / ".env", override=True)


def _jira_auth_headers() -> dict[str, str]:
    raw = os.environ.get("JIRA_API_TOKEN", "").strip()
    if not raw:
        return {}
    # Allow JIRA_API_TOKEN=Basic <base64> or bare base64
    auth = raw if raw.lower().startswith("basic ") else f"Basic {raw}"
    return {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "Authorization": auth,
    }


def plain_text_to_adf(text: str) -> dict[str, Any]:
    """
    Convert plain text to Atlassian Document Format (ADF) used by Jira Cloud API v3.
    - Blank lines (\\n\\n) start a new paragraph.
    - Single newlines within a block become hard line breaks.
    """
    text = text.strip()
    if not text:
        return {
            "type": "doc",
            "version": 1,
            "content": [{"type": "paragraph", "content": []}],
        }

    paragraphs: list[dict[str, Any]] = []
    for block in text.split("\n\n"):
        block = block.strip()
        if not block:
            paragraphs.append({"type": "paragraph"})
            continue
        line_parts: list[dict[str, Any]] = []
        lines = block.split("\n")
        for i, line in enumerate(lines):
            if i > 0:
                line_parts.append({"type": "hardBreak"})
            line_parts.append({"type": "text", "text": line})
        paragraphs.append({"type": "paragraph", "content": line_parts})

    return {"type": "doc", "version": 1, "content": paragraphs}


def build_issue_payload(
    summary: str,
    description_plain: str,
    *,
    project_key: str,
    parent_key: str,
    issue_type_name: str,
    component_name: str,
    priority_name: str,
) -> dict[str, Any]:
    return {
        "fields": {
            "project": {"key": project_key},
            "summary": summary,
            "description": plain_text_to_adf(description_plain),
            "issuetype": {"name": issue_type_name},
            "parent": {"key": parent_key},
            "components": [{"name": component_name}],
            "priority": {"name": priority_name},
            "assignee": {"accountId": "61c35e8d7aa7ac00708078d3"},
        }
    }


def create_issue(
    base_url: str, payload: dict[str, Any], headers: dict[str, str]
) -> dict[str, Any]:
    url = f"{base_url.rstrip('/')}/rest/api/3/issue"
    response = requests.post(url, json=payload, headers=headers, timeout=60)
    if not response.ok:
        try:
            detail = response.json()
        except Exception:
            detail = response.text
        raise requests.HTTPError(
            f"{response.status_code} {response.reason}: {detail}",
            response=response,
        )
    return response.json()


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Create a Jira RO History data-load ticket (Sub-task under parent epic/story)."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s --summary "DMS Bulkload - RO history for Example Motors" \\
    --description "Re-launch\\n01/15/2026\\n\\nPull window\\n01/01/2024 - 06/30/2025"

  %(prog)s --summary "..." --description-file ./load-details.txt

Environment (.env or shell):
  JIRA_API_TOKEN   Required for API calls (Basic <base64> or full value after =)
  JIRA_BASE_URL    Optional (default: https://skaivision.atlassian.net)
        """,
    )
    parser.add_argument(
        "--summary",
        required=True,
        help="Issue summary (title), e.g. 'DMS Bulkload - RO history for Dealer Name'",
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--description",
        help="Issue description (plain text; use \\n\\n for paragraph breaks)",
    )
    group.add_argument(
        "--description-file",
        metavar="PATH",
        help="Read description from a UTF-8 text file",
    )
    parser.add_argument(
        "--parent",
        default=os.environ.get("JIRA_RO_HIST_PARENT", "SKAI-6378"),
        help=(
            "Parent issue key (Historical RO Fetch epic/story). "
            "Default: SKAI-6378 or JIRA_RO_HIST_PARENT env."
        ),
    )
    parser.add_argument(
        "--project",
        default=os.environ.get("JIRA_PROJECT_KEY", "SKAI"),
        help="Project key (default: SKAI)",
    )
    parser.add_argument(
        "--issue-type",
        default="Sub-task",
        help="Issue type name (default: Sub-task, per RO History ticket example)",
    )
    parser.add_argument(
        "--component",
        default="Application: AdHoc Reporting",
        help='Component name (default: Application: AdHoc Reporting)',
    )
    parser.add_argument(
        "--priority",
        default="Medium",
        help="Priority name (default: Medium)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print JSON payload only; do not call the API",
    )
    args = parser.parse_args()

    _load_jira_env()

    base_url = os.environ.get(
        "JIRA_BASE_URL", "https://skaivision.atlassian.net"
    ).strip()

    if args.description_file:
        path = os.path.expanduser(args.description_file)
        with open(path, encoding="utf-8") as f:
            description = f.read()
    else:
        description = args.description or ""

    payload = build_issue_payload(
        args.summary.strip(),
        description,
        project_key=args.project,
        parent_key=args.parent.strip(),
        issue_type_name=args.issue_type,
        component_name=args.component,
        priority_name=args.priority,
    )

    if args.dry_run:
        import json

        print(json.dumps(payload, indent=2))
        return

    headers = _jira_auth_headers()
    if not headers.get("Authorization"):
        print(
            "Error: JIRA_API_TOKEN is not set. Add it to .env in this folder, e.g.\n"
            '  JIRA_API_TOKEN=Basic <your-base64-credentials>\n'
            "(same value as the Authorization header for Jira REST API.)",
            file=sys.stderr,
        )
        sys.exit(1)

    try:
        result = create_issue(base_url, payload, headers)
    except requests.HTTPError as e:
        print(f"Jira API error: {e}", file=sys.stderr)
        sys.exit(1)
    except requests.RequestException as e:
        print(f"Request failed: {e}", file=sys.stderr)
        sys.exit(1)

    key = result.get("key", "")
    issue_id = result.get("id", "")
    self_link = result.get("self", "")
    browse = f"{base_url.rstrip('/')}/browse/{key}" if key else ""

    print(f"Created issue: {key} (id={issue_id})")
    if browse:
        print(browse)
    if self_link:
        print(self_link)


if __name__ == "__main__":
    main()
