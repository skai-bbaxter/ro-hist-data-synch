#!/usr/bin/env python3
"""
Fetch repair-order counts by day and list each UTC calendar day in the requested
range with implicit zeros for days absent from the API response.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, time, timedelta, timezone
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

API_URL = "https://reports.prod.microservice.skaivision.net/reports/adhoc"
NY = ZoneInfo("America/New_York")


def parse_mm_dd_yyyy(s: str) -> date:
    return datetime.strptime(s, "%m-%d-%Y").date()


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
        required=True,
        help="Organization UUID for the report query.",
    )
    args = parser.parse_args()

    d_start = parse_mm_dd_yyyy(args.start_date)
    d_end = parse_mm_dd_yyyy(args.end_date)
    if d_end < d_start:
        print("end-date must be on or after start-date.", file=sys.stderr)
        sys.exit(1)

    totals = merge_counts(args.organization_id, d_start, d_end)
    utc_days = utc_dates_covering_ny_range(d_start, d_end)

    rows: list[tuple[str, int]] = []
    for ud in utc_days:
        key = ud.isoformat()
        c = totals.get(key, 0)
        rows.append((key, c))

    rows.sort(key=lambda x: x[0], reverse=True)

    print(f"{'Date':<24}{'Count':>8}")
    for ds, cnt in rows:
        print(f"{ds:<24}{cnt:>8}")

    most_recent_zero: str | None = None
    for ds, cnt in rows:
        if cnt != 0:
            continue
        d_row = date.fromisoformat(ds)
        if d_start <= d_row <= d_end:
            most_recent_zero = ds
            break

    if most_recent_zero is not None:
        print(
            f"\nMost recent date between {d_start.isoformat()} and {d_end.isoformat()} "
            f"with implicit zero count: {most_recent_zero}"
        )
    else:
        print(
            f"\nNo date between {d_start.isoformat()} and {d_end.isoformat()} "
            "had an implicit zero count."
        )


if __name__ == "__main__":
    main()
