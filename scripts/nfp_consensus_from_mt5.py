#!/usr/bin/env python3
"""Read the point-in-time US NFP consensus forecast from the MT5 calendar PIT log.

Brief: docs/specs/NEXT-WORKFLOW-2026-11.md (CR-1 Part C / MT-2).
Audit: docs/reports/adversarial-audit-nfp-capture-2026-11-19.md (defect B1).

Reads the MT-2 point-in-time calendar log (rsynced from MT5 to
/opt/shared/calendar_pit/<broker>/*.jsonl) and prints the last pre-release forecast
for one release date. Every ambiguity is a hard failure (exit 1 with a precise
message); there is no name matching, no stale fallback, and no silent skip.

Expected MT-2 line schema (one JSON object per newline-terminated line; extra keys
are ignored):

    event_id              MT5 calendar event id (int or non-empty str)
    time_server           scheduled release, epoch seconds in TRADE-SERVER time
    snapshot_server_time  when the value was observed, epoch seconds in TRADE-SERVER time
    forecast_value        JSON integer, value x 10^6; LONG_MIN (-2^63) = no forecast

Trade-server time is converted to UTC with the required --server-utc-offset. A wrong
offset makes the scheduled time miss the 08:30 America/New_York release instant, so
it fails as "no records" instead of shifting the pre-release cutoff.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

RELEASE_TIME_ET = (8, 30)
EASTERN = ZoneInfo("America/New_York")
DEFAULT_PIT_DIR = Path("/opt/shared/calendar_pit")
REQUIRED_KEYS = ("event_id", "time_server", "snapshot_server_time", "forecast_value")
VALUE_SCALE = 1_000_000
LONG_MIN = -(2**63)
LONG_MAX = 2**63 - 1
# Plausible epoch-seconds window (2000-01-01 .. 2100-01-01); rejects ms epochs.
EPOCH_MIN = 946_684_800
EPOCH_MAX = 4_102_444_800
OFFSET_PATTERN = re.compile(r"^([+-])(\d{2}):(\d{2})$")


class MT5CalendarError(RuntimeError):
    """Raised when point-in-time consensus cannot be determined safely."""


@dataclass(frozen=True)
class PITCalendarRecord:
    broker: str
    event_id: str
    release_time_utc: datetime
    change_time_utc: datetime
    forecast_raw: int | None  # None only for the LONG_MIN "no forecast" sentinel
    source: str

    @property
    def forecast(self) -> float | None:
        return None if self.forecast_raw is None else self.forecast_raw / VALUE_SCALE


@dataclass(frozen=True)
class PITConsensus:
    forecast: float
    sources: tuple[PITCalendarRecord, ...]  # latest pre-release record per broker


def parse_utc_offset(value: str) -> timedelta:
    """Parse a fixed trade-server offset such as '+02:00'."""
    match = OFFSET_PATTERN.match(value.strip())
    if match is None:
        raise MT5CalendarError(f"server UTC offset must look like +HH:MM, got {value!r}")
    sign, hours, minutes = match.groups()
    offset = timedelta(hours=int(hours), minutes=int(minutes))
    if offset > timedelta(hours=14) or int(minutes) >= 60:
        raise MT5CalendarError(f"server UTC offset out of range: {value!r}")
    return -offset if sign == "-" else offset


def release_timestamp_utc(release_date: date) -> datetime:
    """Return the 08:30 America/New_York release instant in UTC."""
    hour, minute = RELEASE_TIME_ET
    local = datetime(release_date.year, release_date.month, release_date.day, hour, minute)
    return local.replace(tzinfo=EASTERN).astimezone(UTC)


def _is_json_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _server_epoch_to_utc(value: object, key: str, offset: timedelta, source: str) -> datetime:
    if not _is_json_int(value):
        raise MT5CalendarError(f"{source}: {key} must be integer epoch seconds, got {value!r}")
    if not EPOCH_MIN <= value <= EPOCH_MAX:
        raise MT5CalendarError(f"{source}: {key}={value} is outside the epoch-seconds range")
    server_wall_clock = datetime.fromtimestamp(value, tz=UTC)
    return server_wall_clock - offset


def _parse_forecast_raw(value: object, source: str) -> int | None:
    if not _is_json_int(value):
        raise MT5CalendarError(
            f"{source}: forecast_value must be a JSON integer scaled x10^6, got {value!r}"
        )
    if not LONG_MIN <= value <= LONG_MAX:
        raise MT5CalendarError(f"{source}: forecast_value={value} overflows a 64-bit long")
    return None if value == LONG_MIN else value


def parse_pit_line(
    line: str,
    *,
    source: str,
    broker: str,
    event_id: str,
    server_offset: timedelta,
) -> PITCalendarRecord | None:
    """Parse one JSONL line. Returns None only for a blank line or another event."""
    stripped = line.strip()
    if not stripped:
        return None
    try:
        data = json.loads(stripped)
    except json.JSONDecodeError as exc:
        raise MT5CalendarError(f"{source}: corrupt JSONL record: {exc}") from exc
    if not isinstance(data, dict):
        raise MT5CalendarError(f"{source}: expected a JSON object, got {type(data).__name__}")
    missing = [key for key in REQUIRED_KEYS if key not in data]
    if missing:
        raise MT5CalendarError(f"{source}: schema mismatch, missing keys {missing}")

    raw_event = data["event_id"]
    if not (_is_json_int(raw_event) or (isinstance(raw_event, str) and raw_event.strip())):
        raise MT5CalendarError(f"{source}: event_id must be an int or non-empty str")
    if str(raw_event).strip() != event_id:
        return None

    return PITCalendarRecord(
        broker=broker,
        event_id=event_id,
        release_time_utc=_server_epoch_to_utc(
            data["time_server"], "time_server", server_offset, source
        ),
        change_time_utc=_server_epoch_to_utc(
            data["snapshot_server_time"], "snapshot_server_time", server_offset, source
        ),
        forecast_raw=_parse_forecast_raw(data["forecast_value"], source),
        source=source,
    )


def _pit_files(pit_dir: Path, pit_file: Path | None) -> list[Path]:
    if pit_file is not None:
        if not pit_file.is_file():
            raise MT5CalendarError(f"specified PIT log file does not exist: {pit_file}")
        return [pit_file]
    files = sorted(pit_dir.glob("**/*.jsonl"))
    if not files:
        raise MT5CalendarError(f"no PIT JSONL files under {pit_dir}")
    return files


def read_pit_records(
    pit_dir: Path,
    *,
    event_id: str,
    server_offset: timedelta,
    pit_file: Path | None = None,
) -> list[PITCalendarRecord]:
    """Parse every line of every PIT file; any defect anywhere raises."""
    records: list[PITCalendarRecord] = []
    for path in _pit_files(pit_dir, pit_file):
        broker = path.parent.name
        text = path.read_text(encoding="utf-8")
        lines = text.splitlines(keepends=True)
        if lines and lines[-1].strip() and not lines[-1].endswith("\n"):
            raise MT5CalendarError(
                f"{path}:{len(lines)}: torn final line (no trailing newline; partial rsync?)"
            )
        for line_no, line in enumerate(lines, start=1):
            record = parse_pit_line(
                line,
                source=f"{path}:{line_no}",
                broker=broker,
                event_id=event_id,
                server_offset=server_offset,
            )
            if record is not None:
                records.append(record)
    return records


def _describe(record: PITCalendarRecord) -> str:
    value = "ABSENT(LONG_MIN)" if record.forecast is None else f"{record.forecast}"
    return f"{record.broker}={value} at {record.change_time_utc.isoformat()} ({record.source})"


def _latest_prerelease(records: list[PITCalendarRecord], release_ts: datetime) -> PITCalendarRecord:
    broker = records[0].broker
    prerelease = [r for r in records if r.change_time_utc < release_ts]
    if not prerelease:
        raise MT5CalendarError(
            f"broker {broker}: no pre-release records (release at {release_ts.isoformat()}; "
            "all changes occurred at or after release)"
        )
    latest_ts = max(r.change_time_utc for r in prerelease)
    latest = [r for r in prerelease if r.change_time_utc == latest_ts]
    if len({r.forecast_raw for r in latest}) > 1:
        raise MT5CalendarError(
            f"broker {broker}: conflicting records at the same latest pre-release timestamp: "
            + "; ".join(_describe(r) for r in latest)
        )
    chosen = latest[0]
    if chosen.forecast_raw is None:
        raise MT5CalendarError(
            f"broker {broker}: latest pre-release change has no forecast (LONG_MIN) at "
            f"{latest_ts.isoformat()} ({chosen.source}); refusing to fall back to an earlier value"
        )
    return chosen


def get_prerelease_consensus(records: list[PITCalendarRecord], release_date: date) -> PITConsensus:
    release_ts = release_timestamp_utc(release_date)
    matching = [r for r in records if r.release_time_utc == release_ts]
    if not matching:
        raise MT5CalendarError(
            f"no MT5 calendar records scheduled at {release_ts.isoformat()} for the given "
            "event id (check --event-id and --server-utc-offset)"
        )
    by_broker: dict[str, list[PITCalendarRecord]] = defaultdict(list)
    for record in matching:
        by_broker[record.broker].append(record)
    latest = tuple(_latest_prerelease(by_broker[b], release_ts) for b in sorted(by_broker))
    if len({r.forecast_raw for r in latest}) > 1:
        raise MT5CalendarError(
            "broker disagreement on pre-release forecast: "
            + "; ".join(_describe(r) for r in latest)
        )
    forecast = latest[0].forecast
    if forecast is None:
        raise MT5CalendarError("internal: absent forecast passed latest-record validation")
    return PITConsensus(forecast=forecast, sources=latest)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--release-date", required=True, help="ISO release date (2026-12-04)")
    parser.add_argument(
        "--event-id",
        required=True,
        help="MT5 calendar event_id of the US Nonfarm Payrolls HEADLINE (exact match)",
    )
    parser.add_argument(
        "--server-utc-offset",
        required=True,
        help="Trade-server offset from UTC at the release instant, e.g. +02:00",
    )
    parser.add_argument(
        "--pit-dir",
        type=Path,
        default=DEFAULT_PIT_DIR,
        help="Directory containing <broker>/*.jsonl PIT files",
    )
    parser.add_argument("--pit-file", type=Path, help="Read only this JSONL file")
    parser.add_argument("--json", action="store_true", help="Output result as JSON")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        rel_date = date.fromisoformat(args.release_date.strip())
    except ValueError as exc:
        print(f"error: release date must be ISO YYYY-MM-DD: {exc}", file=sys.stderr)
        return 1

    event_id = args.event_id.strip()
    try:
        if not event_id:
            raise MT5CalendarError("--event-id must be non-empty")
        records = read_pit_records(
            args.pit_dir,
            event_id=event_id,
            server_offset=parse_utc_offset(args.server_utc_offset),
            pit_file=args.pit_file,
        )
        consensus = get_prerelease_consensus(records, rel_date)
    except MT5CalendarError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    if args.json:
        payload = {
            "release_date": rel_date.isoformat(),
            "release_ts_utc": release_timestamp_utc(rel_date).isoformat(),
            "event_id": event_id,
            "forecast": consensus.forecast,
            "sources": [
                {
                    "broker": r.broker,
                    "forecast_raw": r.forecast_raw,
                    "change_time_utc": r.change_time_utc.isoformat(),
                    "source": r.source,
                }
                for r in consensus.sources
            ],
        }
        print(json.dumps(payload, indent=2))
    else:
        print(f"{consensus.forecast}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
