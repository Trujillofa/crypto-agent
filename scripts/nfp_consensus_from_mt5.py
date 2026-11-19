#!/usr/bin/env python3
"""Read point-in-time NFP consensus forecast from MT5 calendar PIT log.

Brief: docs/specs/NEXT-WORKFLOW-2026-11.md (CR-1 Part C / MT-2).
Takes the MT-2 point-in-time calendar log (rsynced from MT5 to e.g.
/opt/shared/calendar_pit/<broker>/*.jsonl) and extracts the pre-release
forecast for a given release date. Fails closed (exits non-zero) if the
last pre-release change is missing, unparseable, or logged after the release.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

RELEASE_TIME_ET = (8, 30)
EASTERN = ZoneInfo("America/New_York")
DEFAULT_PIT_DIR = Path("/opt/shared/calendar_pit")
NFP_NAME_PATTERN = re.compile(r"(non-?farm\s+payrolls?|nfp|headline_nfp)", re.IGNORECASE)


class MT5CalendarError(RuntimeError):
    """Raised when point-in-time consensus cannot be determined safely."""


@dataclass(frozen=True)
class PITCalendarRecord:
    event_id: str
    event_name: str
    release_time_utc: datetime
    change_time_utc: datetime
    forecast: float
    source_file: str


def release_timestamp_utc(release_date: date) -> datetime:
    """Return the 08:30 America/New_York release instant in UTC."""
    hour, minute = RELEASE_TIME_ET
    local = datetime(release_date.year, release_date.month, release_date.day, hour, minute)
    return local.replace(tzinfo=EASTERN).astimezone(UTC)


def _parse_iso_or_epoch(value: object) -> datetime:
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, tz=UTC)
    if isinstance(value, str):
        normalized = value.strip()
        if normalized.endswith("Z"):
            normalized = normalized[:-1] + "+00:00"
        dt = datetime.fromisoformat(normalized)
        return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)
    raise ValueError(f"cannot parse timestamp from {value!r}")


def _is_nfp_event(payload: dict[str, object]) -> bool:
    name = str(payload.get("event_name") or payload.get("name") or "")
    code = str(payload.get("event_code") or payload.get("event_id") or "")
    country = str(payload.get("country") or payload.get("currency") or "").upper()
    if country and country not in {"US", "USD"}:
        return False
    return bool(NFP_NAME_PATTERN.search(name) or NFP_NAME_PATTERN.search(code))


def parse_pit_record(line: str, source_file: str = "") -> PITCalendarRecord | None:
    stripped = line.strip()
    if not stripped or stripped.startswith("#"):
        return None
    try:
        data = json.loads(stripped)
    except json.JSONDecodeError as exc:
        raise MT5CalendarError(f"corrupt JSONL record in {source_file}: {exc}") from exc

    if not isinstance(data, dict):
        return None
    if not _is_nfp_event(data):
        return None

    raw_rel = data.get("time") or data.get("release_time") or data.get("date")
    raw_change = (
        data.get("change_time")
        or data.get("logged_at")
        or data.get("timestamp")
        or data.get("time_update")
    )
    raw_fc = data.get("forecast") or data.get("forecast_value")

    if raw_rel is None or raw_change is None or raw_fc is None:
        return None

    try:
        rel_ts = _parse_iso_or_epoch(raw_rel)
        change_ts = _parse_iso_or_epoch(raw_change)
        forecast_val = float(raw_fc)
    except (ValueError, TypeError):
        return None

    return PITCalendarRecord(
        event_id=str(data.get("event_id", "")),
        event_name=str(data.get("event_name", "US Nonfarm Payrolls")),
        release_time_utc=rel_ts,
        change_time_utc=change_ts,
        forecast=forecast_val,
        source_file=source_file,
    )


def read_pit_records(pit_dir: Path, pit_file: Path | None = None) -> list[PITCalendarRecord]:
    records: list[PITCalendarRecord] = []
    files: list[Path] = []
    if pit_file is not None:
        if not pit_file.exists():
            raise MT5CalendarError(f"specified PIT log file does not exist: {pit_file}")
        files = [pit_file]
    elif pit_dir.exists():
        files = sorted(pit_dir.glob("**/*.jsonl"))

    for path in files:
        with path.open(encoding="utf-8") as handle:
            for line_no, line in enumerate(handle, start=1):
                rec = parse_pit_record(line, source_file=f"{path}:{line_no}")
                if rec is not None:
                    records.append(rec)
    return records


def get_prerelease_consensus(
    records: list[PITCalendarRecord],
    release_date: date,
) -> PITCalendarRecord:
    release_ts = release_timestamp_utc(release_date)
    matching = [
        r
        for r in records
        if r.release_time_utc.date() == release_date
        or abs((r.release_time_utc - release_ts).total_seconds()) < 12 * 3600
    ]
    if not matching:
        raise MT5CalendarError(
            f"no MT5 calendar records found for NFP release {release_date.isoformat()}"
        )

    prerelease = [r for r in matching if r.change_time_utc < release_ts]
    if not prerelease:
        raise MT5CalendarError(
            f"no pre-release records found for {release_date.isoformat()} "
            f"(release at {release_ts.isoformat()}; all changes occurred at or after release)"
        )

    # Sort by change time ascending, select latest pre-release record
    prerelease.sort(key=lambda r: r.change_time_utc)
    latest = prerelease[-1]
    return latest


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--release-date",
        required=True,
        help="ISO release date (e.g. 2026-12-04)",
    )
    parser.add_argument(
        "--pit-dir",
        type=Path,
        default=DEFAULT_PIT_DIR,
        help="Directory containing calendar PIT JSONL files",
    )
    parser.add_argument(
        "--pit-file",
        type=Path,
        help="Optional specific JSONL file to read",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output result as JSON",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        rel_date = date.fromisoformat(args.release_date.strip())
    except ValueError as exc:
        print(f"error: release date must be ISO YYYY-MM-DD: {exc}", file=sys.stderr)
        return 1

    try:
        records = read_pit_records(args.pit_dir, args.pit_file)
        latest = get_prerelease_consensus(records, rel_date)
    except MT5CalendarError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    if args.json:
        payload = {
            "release_date": rel_date.isoformat(),
            "release_ts_utc": release_timestamp_utc(rel_date).isoformat(),
            "forecast": latest.forecast,
            "change_time_utc": latest.change_time_utc.isoformat(),
            "source_file": latest.source_file,
            "event_name": latest.event_name,
        }
        print(json.dumps(payload, indent=2))
    else:
        print(f"{latest.forecast}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
