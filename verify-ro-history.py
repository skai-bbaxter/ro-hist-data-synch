#!/usr/bin/env python3
"""
Fetch repair-order counts by day and list each UTC calendar day in the requested
range with implicit zeros for days absent from the API response.
"""

from __future__ import annotations

import argparse
import ast
import json
import math
import os
import statistics
import sys
from datetime import date, datetime, time, timedelta, timezone
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

import requests
from dotenv import load_dotenv

API_URL = "https://reports.prod.microservice.skaivision.net/reports/adhoc"
HUBSPOT_API_BASE = "https://api.hubapi.com"
NY = ZoneInfo("America/New_York")


def is_utc_sunday(d: date) -> bool:
    """True if d is a Sunday on the UTC calendar (matches API date buckets)."""
    return d.weekday() == 6


def parse_mm_dd_yyyy(s: str) -> date:
    return datetime.strptime(s, "%m-%d-%Y").date()


def parse_excluded_days_from_env(raw: str | None) -> frozenset[date]:
    """Parse EXCLUDED_DAYS from env as a Python list literal of ISO date strings."""
    if raw is None or not str(raw).strip():
        return frozenset()
    try:
        value = ast.literal_eval(str(raw).strip())
    except (ValueError, SyntaxError) as e:
        print(
            f"EXCLUDED_DAYS must be a valid Python literal (list of ISO date strings): {e}",
            file=sys.stderr,
        )
        sys.exit(1)
    if not isinstance(value, list):
        print("EXCLUDED_DAYS must be a list of ISO date strings.", file=sys.stderr)
        sys.exit(1)
    out: set[date] = set()
    for item in value:
        if not isinstance(item, str):
            print("EXCLUDED_DAYS list items must be ISO date strings.", file=sys.stderr)
            sys.exit(1)
        out.add(date.fromisoformat(item))
    return frozenset(out)


def parse_zero_day_span_from_env(raw: str | None) -> int:
    """Parse ZERO_DAY_SPAN; default 1 if missing or invalid (must be >= 1)."""
    if raw is None or not str(raw).strip():
        return 1
    try:
        v = int(str(raw).strip())
    except ValueError:
        return 1
    if v < 1:
        return 1
    return v


def ny_midnight_to_epoch_ms(d: date) -> int:
    dt = datetime.combine(d, time.min, tzinfo=NY)
    return int(dt.timestamp() * 1000)


def utc_dates_covering_ny_range(d_start: date, d_end: date) -> list[date]:
    """UTC calendar days that overlap [NY midnight d_start, NY midnight d_end+1)."""
    t0 = datetime.combine(d_start, time.min, tzinfo=NY)
    t1 = datetime.combine(d_end + timedelta(days=1), time.min, tzinfo=NY)
    u0 = t0.astimezone(timezone.utc)
    last_instant = t1 - timedelta(microseconds=1)
    u_last = last_instant.astimezone(timezone.utc).date()
    out: list[date] = []
    cur = u0.date()
    while cur <= u_last:
        out.append(cur)
        cur += timedelta(days=1)
    return out


def iter_90_day_windows(d_start: date, d_end: date):
    """Yield (first_day, last_day) inclusive per window, at most 90 days each."""
    d = d_start
    while d <= d_end:
        last = min(d + timedelta(days=89), d_end)
        yield (d, last)
        d = last + timedelta(days=1)


def build_request_body(organization_id: str, gte_ms: int, lt_ms: int) -> dict:
    return {
        "dataSource": "prod_automotive_services",
        "repository": "dms_repair_orders",
        "queryLanguage": "MONGO_AGGREGATE",
        "query": [
            {
                "$match": {
                    "organizationId": organization_id,
                    "createdTimestampUTCEpochMilli": {"$gte": gte_ms, "$lt": lt_ms},
                }
            },
            {
                "$group": {
                    "_id": {
                        "organizationId": "$organizationId",
                        "dmsProvider": "$dmsProvider",
                        "date": {
                            "$dateToString": {
                                "format": "%Y-%m-%d",
                                "date": {"$toDate": "$createdTimestampUTCEpochMilli"},
                            }
                        },
                    },
                    "count": {"$sum": 1},
                }
            },
            {
                "$project": {
                    "_id": 0,
                    "organizationId": "$_id.organizationId",
                    "dmsProvider": "$_id.dmsProvider",
                    "date": "$_id.date",
                    "count": "$count",
                }
            },
            {"$sort": {"date": -1}},
        ],
        "metadata": {"allowDiskUse": True},
    }


def post_adhoc(body: dict) -> dict:
    data = json.dumps(body).encode("utf-8")
    req = Request(
        API_URL,
        data=data,
        method="POST",
        headers={
            "accept": "*/*",
            "content-type": "application/json",
            "origin": "https://internaltools.skaivision.net",
            "referer": "https://internaltools.skaivision.net/",
        },
    )
    try:
        with urlopen(req, timeout=120) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except HTTPError as e:
        body_txt = e.read().decode("utf-8", errors="replace") if e.fp else ""
        print(f"HTTP {e.code}: {body_txt}", file=sys.stderr)
        raise
    except URLError as e:
        print(f"Request failed: {e.reason}", file=sys.stderr)
        raise


def org_id_from_hubspot_company_name(company_name: str, token: str) -> str:
    """Resolve organization id from HubSpot company name (name EQ) and skai_org_id_long."""
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    name = company_name.strip()
    search_url = f"{HUBSPOT_API_BASE}/crm/v3/objects/companies/search"
    search_payload = {
        "filterGroups": [
            {
                "filters": [
                    {
                        "propertyName": "name",
                        "operator": "EQ",
                        "value": name,
                    }
                ]
            }
        ],
        "properties": ["name"],
        "limit": 1,
    }
    search_response = requests.post(
        search_url, headers=headers, json=search_payload, timeout=60
    )
    search_response.raise_for_status()
    search_data = search_response.json()
    results = search_data.get("results") or []
    if not results:
        print(
            f"No company found with name '{name}'",
            file=sys.stderr,
        )
        sys.exit(1)
    company_id = results[0]["id"]
    company_url = f"{HUBSPOT_API_BASE}/crm/v3/objects/companies/{company_id}"
    company_response = requests.get(
        company_url,
        headers=headers,
        params={"properties": "skai_org_id_long", "archived": "false"},
        timeout=60,
    )
    company_response.raise_for_status()
    company_data = company_response.json()
    props = company_data.get("properties") or {}
    raw = props.get("skai_org_id_long")
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        print("Hubspot error: No skai_org_id_long found", file=sys.stderr)
        sys.exit(1)
    return raw.strip() if isinstance(raw, str) else str(raw)


def merge_counts(organization_id: str, d_start: date, d_end: date) -> dict[str, int]:
    totals: dict[str, int] = {}
    for first, last in iter_90_day_windows(d_start, d_end):
        gte_ms = ny_midnight_to_epoch_ms(first)
        lt_ms = ny_midnight_to_epoch_ms(last + timedelta(days=1))
        payload = build_request_body(organization_id, gte_ms, lt_ms)
        result = post_adhoc(payload)
        if not result.get("success"):
            raise RuntimeError(
                f"API error: {result.get('message', result)}"
            )
        for row in result.get("data") or []:
            ds = row.get("date")
            if not ds:
                continue
            totals[ds] = totals.get(ds, 0) + int(row.get("count") or 0)
    return totals


def _linear_percentile(sorted_values: list[int], p: float) -> float:
    """Return the p-th percentile (0–100) using linear interpolation between ranks."""
    n = len(sorted_values)
    if n == 1:
        return float(sorted_values[0])
    pos = (p / 100.0) * (n - 1)
    lo = int(math.floor(pos))
    hi = int(math.ceil(pos))
    if lo == hi:
        return float(sorted_values[lo])
    return float(
        sorted_values[lo]
        + (sorted_values[hi] - sorted_values[lo]) * (pos - lo)
    )


def print_displayed_count_statistics(counts: list[int]) -> None:
    """Print mean, median, mode, daily average, and percentiles for displayed counts."""
    print("\n--- RO count statistics ---")
    if not counts:
        print("No displayed days; no statistics.")
        return

    sorted_counts = sorted(counts)
    mean_val = statistics.mean(counts)
    median_val = statistics.median(counts)
    modes = statistics.multimode(counts)
    mode_val = modes[0]

    active = [c for c in counts if c > 0]
    if active:
        daily_avg = statistics.mean(active)
        daily_avg_str = f"{daily_avg:.6g}"
    else:
        daily_avg_str = "n/a (no days with activity)"

    print(f"Mean:   {mean_val:.6g}")
    print(f"Median: {median_val:.6g}")
    print(f"Mode:   {mode_val}")
    print(f"Daily average (mean of counts on days with activity): {daily_avg_str}")

    # Min/max and percentiles use only days with count > 0 so zeros in the displayed
    # range do not dominate the distribution (same population as daily average).
    print(
        "Percentiles, days with activity only (min, 10, 25, 50, 75, 90, 95, 99, max):"
    )
    if not active:
        print("  n/a (no days with activity)")
        return
    sorted_active = sorted(active)
    pct_specs = [
        ("min", None),
        ("p10", 10.0),
        ("p25", 25.0),
        ("p50", 50.0),
        ("p75", 75.0),
        ("p90", 90.0),
        ("p95", 95.0),
        ("p99", 99.0),
        ("max", None),
    ]
    for label, p in pct_specs:
        if label == "min":
            val = sorted_active[0]
        elif label == "max":
            val = sorted_active[-1]
        else:
            assert p is not None
            val = _linear_percentile(sorted_active, p)
        print(f"  {label}: {val:.6g}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Verify repair-order history by day over a date range."
    )
    parser.add_argument(
        "--start-date",
        required=True,
        help="Start date (MM-DD-YYYY), inclusive, America/New_York calendar day.",
    )
    parser.add_argument(
        "--end-date",
        required=True,
        help="End date (MM-DD-YYYY), inclusive, America/New_York calendar day.",
    )
    parser.add_argument(
        "--organization-id",
        help=(
            "Organization UUID for the report query. "
            "If set, this value is used even when --hs-company-name is also passed."
        ),
    )
    parser.add_argument(
        "--hs-company-name",
        help=(
            "HubSpot company name (exact match on the name property). "
            "Resolves skai_org_id_long when --organization-id is not provided."
        ),
    )
    parser.add_argument(
        "--stats",
        action="store_true",
        help="After all other output, print statistics for displayed RO counts.",
    )
    args = parser.parse_args()
    load_dotenv()
    excluded_days = parse_excluded_days_from_env(os.getenv("EXCLUDED_DAYS"))
    zero_day_span = parse_zero_day_span_from_env(os.getenv("ZERO_DAY_SPAN"))

    d_start = parse_mm_dd_yyyy(args.start_date)
    d_end = parse_mm_dd_yyyy(args.end_date)
    if d_end < d_start:
        print("end-date must be on or after start-date.", file=sys.stderr)
        sys.exit(1)

    if args.organization_id:
        organization_id = args.organization_id
    elif args.hs_company_name:
        hubspot_token = os.getenv("HUBSPOT_PROD_TOKEN")
        if not hubspot_token:
            print("HUBSPOT_PROD_TOKEN not found in .env file", file=sys.stderr)
            sys.exit(1)
        organization_id = org_id_from_hubspot_company_name(args.hs_company_name, hubspot_token)
    else:
        print(
            "one of --organization-id or --hs-company-name is required.",
            file=sys.stderr,
        )
        sys.exit(1)

    totals = merge_counts(organization_id, d_start, d_end)
    utc_days = utc_dates_covering_ny_range(d_start, d_end)

    rows: list[tuple[str, int]] = []
    for ud in utc_days:
        key = ud.isoformat()
        c = totals.get(key, 0)
        rows.append((key, c))

    rows.sort(key=lambda x: x[0], reverse=True)

    displayed_counts: list[int] = []
    print(f"{'Date':<24}{'Count':>8}")
    for ds, cnt in rows:
        d_row = date.fromisoformat(ds)
        if d_row in excluded_days:
            continue
        if cnt == 0 and is_utc_sunday(d_row):
            continue
        displayed_counts.append(cnt)
        print(f"{ds:<24}{cnt:>8}")

    zero_dates_in_range: list[date] = []
    for ds, cnt in rows:
        if cnt != 0:
            continue
        d_row = date.fromisoformat(ds)
        if d_start <= d_row <= d_end:
            zero_dates_in_range.append(d_row)

    every_zero_is_sunday = (
        len(zero_dates_in_range) > 0
        and all(is_utc_sunday(d) for d in zero_dates_in_range)
    )

    def qualifies_zero_count_day(d_row: date) -> bool:
        if not (d_start <= d_row <= d_end):
            return False
        if is_utc_sunday(d_row):
            return False
        if d_row in excluded_days:
            return False
        return totals.get(d_row.isoformat(), 0) == 0

    def has_zero_day_streak_ending(end: date, span: int) -> bool:
        for i in range(span):
            cur = end - timedelta(days=i)
            if not qualifies_zero_count_day(cur):
                return False
        return True

    most_recent_zero: str | None = None
    for ds, cnt in rows:
        if cnt != 0:
            continue
        d_row = date.fromisoformat(ds)
        if has_zero_day_streak_ending(d_row, zero_day_span):
            most_recent_zero = ds
            break

    if every_zero_is_sunday:
        print("\nEvery zero-count day in the range is a Sunday")
    elif most_recent_zero is not None:
        raw_buffer = os.getenv("RO_OVERLAP_BUFFER_DAYS")
        if raw_buffer is None or not str(raw_buffer).strip():
            print(
                "RO_OVERLAP_BUFFER_DAYS not set or empty in environment (.env).",
                file=sys.stderr,
            )
            sys.exit(1)
        try:
            buffer_days = int(str(raw_buffer).strip())
        except ValueError:
            print(
                "RO_OVERLAP_BUFFER_DAYS must be an integer.",
                file=sys.stderr,
            )
            sys.exit(1)
        overlap_date = date.fromisoformat(most_recent_zero) + timedelta(
            days=buffer_days
        )
        print(
            f"\nMost recent date between {d_start.isoformat()} and {d_end.isoformat()} "
            f"with implicit zero count: {most_recent_zero}"
        )
        print(f"End date with {buffer_days} day over-lap: {overlap_date.isoformat()}")
    else:
        print(
            f"\nNo date between {d_start.isoformat()} and {d_end.isoformat()} "
            "had an implicit zero count."
        )

    if args.stats:
        print_displayed_count_statistics(displayed_counts)


if __name__ == "__main__":
    main()
