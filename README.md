# RO History Data Sync Utilities

This repo contains a small set of Python utilities for RO History / DMS bulkload work. The scripts help look up HubSpot company details, verify repair-order counts over a date range, and create Jira tickets for historical RO data-load requests.

## Contents

- `verify-ro-history.py`: Queries the production adhoc reporting API for repair-order counts by day, fills in missing days as zeroes, and reports possible load gaps.
- `get-hubspot-company-info.py`: Looks up a HubSpot company by ID or exact name and prints company properties, associated deal owner, and matching onboarding contacts.
- `create_ro_hist_jira_ticket.py`: Creates a Jira sub-task for an RO History / DMS bulkload request.
- `todo-ro-history-loads-needed.txt`: Working list of dealers that still need RO history load follow-up.
- `example-output-bluebonnet-ford.txt` and `*.out`: Example or captured output from previous verification runs.

## Setup

Use Python 3.11+ if available.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Create a local `.env` file in this directory for API credentials and runtime options. Do not commit real credentials.

```bash
HUBSPOT_PROD_TOKEN=<hubspot-private-app-token>
JIRA_API_TOKEN=Basic <base64-jira-credentials>
JIRA_BASE_URL=https://skaivision.atlassian.net
JIRA_RO_HIST_PARENT=SKAI-6378
JIRA_PROJECT_KEY=SKAI
RO_OVERLAP_BUFFER_DAYS=7
ZERO_DAY_SPAN=1
EXCLUDED_DAYS=["2026-01-01"]
```

Only `HUBSPOT_PROD_TOKEN` is needed for HubSpot lookups. `JIRA_API_TOKEN` is needed for creating Jira tickets. `RO_OVERLAP_BUFFER_DAYS`, `ZERO_DAY_SPAN`, and `EXCLUDED_DAYS` are used by `verify-ro-history.py`.

## Typical Workflow

1. Use `get-hubspot-company-info.py` to confirm the HubSpot company, organization ID, owner, and contacts.
2. Use `verify-ro-history.py` to inspect RO counts for the requested history window and identify any zero-count gaps.
3. Use `create_ro_hist_jira_ticket.py` to create the RO History Jira sub-task, using the verified dealer and date-window details.

## Program Reference

### `get-hubspot-company-info.py`

Retrieves all HubSpot company properties for a company, then checks related data that is useful when preparing an RO History request:

- Resolves a company by numeric HubSpot company ID or by exact company name.
- Prints the full company record with all company properties.
- Finds deals associated with the company and prints the first deal owner.
- Searches deal and company contacts for an email matching `hubspot+*@skaivision.net`.

Required environment:

- `HUBSPOT_PROD_TOKEN`

Examples:

```bash
python get-hubspot-company-info.py 12345678901
python get-hubspot-company-info.py "Stokes Toyota Hilton Head"
```

Notes:

- Company-name lookup is an exact HubSpot `name` match.
- Output is intentionally verbose and may include sensitive HubSpot data.

### `verify-ro-history.py`

Verifies repair-order history counts by day over an inclusive date range. The script queries the production adhoc reporting API in 90-day windows, merges the results, displays each UTC calendar day that overlaps the requested New York calendar range, and treats days missing from the API response as implicit zeroes.

Use it to answer questions like:

- Did the RO history load populate counts across the requested window?
- Are the only zero-count days Sundays?
- What is the most recent zero-count gap, and what end date should be used after applying the overlap buffer?
- What are the basic count statistics for the displayed days?

Required arguments:

- `--start-date MM-DD-YYYY`
- `--end-date MM-DD-YYYY`
- One of `--organization-id` or `--hs-company-name`

Relevant environment:

- `HUBSPOT_PROD_TOKEN`: Required only when using `--hs-company-name`.
- `RO_OVERLAP_BUFFER_DAYS`: Required when a qualifying zero-count day is found and the script needs to print the buffered overlap end date.
- `ZERO_DAY_SPAN`: Optional; number of consecutive non-Sunday zero days required before reporting a zero-count gap. Defaults to `1`.
- `EXCLUDED_DAYS`: Optional Python list literal of ISO dates to ignore, for example `["2026-01-01", "2026-01-20"]`.

Examples:

```bash
python verify-ro-history.py \
  --start-date 01-01-2024 \
  --end-date 06-30-2025 \
  --organization-id 00000000-0000-0000-0000-000000000000
```

```bash
python verify-ro-history.py \
  --start-date 01-01-2024 \
  --end-date 06-30-2025 \
  --hs-company-name "Bluebonnet Ford" \
  --stats
```

The output starts with a table like:

```text
Date                       Count
2026-04-11                    54
2026-04-10                   242
```

Depending on the data, the script then prints one of:

- Every zero-count day in the range is a Sunday.
- The most recent qualifying zero-count day and the overlap-buffered end date.
- No date in the range had an implicit zero count.

### `create_ro_hist_jira_ticket.py`

Creates a Jira issue for an RO History / DMS bulkload request. By default, it creates a `Sub-task` in project `SKAI`, under parent `SKAI-6378`, with component `Application: AdHoc Reporting`, priority `Medium`, and a fixed assignee.

Required environment:

- `JIRA_API_TOKEN`: Jira REST API `Authorization` value. Both `Basic <base64...>` and bare base64 are accepted.

Optional environment:

- `JIRA_BASE_URL`: Defaults to `https://skaivision.atlassian.net`.
- `JIRA_RO_HIST_PARENT`: Defaults to `SKAI-6378`.
- `JIRA_PROJECT_KEY`: Defaults to `SKAI`.

Examples:

```bash
python create_ro_hist_jira_ticket.py \
  --summary "DMS Bulkload - RO history for Example Motors" \
  --description "Re-launch\n01/15/2026\n\nPull window\n01/01/2024 - 06/30/2025"
```

```bash
python create_ro_hist_jira_ticket.py \
  --summary "DMS Bulkload - RO history for Example Motors" \
  --description-file ./load-details.txt
```

Preview the Jira payload without creating an issue:

```bash
python create_ro_hist_jira_ticket.py \
  --summary "DMS Bulkload - RO history for Example Motors" \
  --description "Pull window\n01/01/2024 - 06/30/2025" \
  --dry-run
```

Override defaults when needed:

```bash
python create_ro_hist_jira_ticket.py \
  --summary "DMS Bulkload - RO history for Example Motors" \
  --description-file ./load-details.txt \
  --parent SKAI-6378 \
  --project SKAI \
  --priority Medium
```

On success, the script prints the created Jira issue key, browser URL, and REST API self link.

## Troubleshooting

- `ModuleNotFoundError: No module named 'requests'`: Activate the virtual environment and run `pip install -r requirements.txt`.
- `HUBSPOT_PROD_TOKEN not found in .env file`: Add the HubSpot token to `.env` or export it in the shell before running HubSpot-dependent commands.
- `JIRA_API_TOKEN is not set`: Add the Jira token to `.env` before creating Jira tickets.
- `No company found with name ...`: Confirm the HubSpot company name is an exact match, or use the numeric HubSpot company ID.
- `RO_OVERLAP_BUFFER_DAYS not set or empty`: Set it in `.env` before running `verify-ro-history.py` on ranges that may contain zero-count gaps.
