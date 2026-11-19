"""Verified NFP release schedule: the single source of release dates.

The Employment Situation release is usually the first Friday of the month, but not
always: the January 2027 print is January 8 (the first Friday is the New Year
holiday) and the September-2027-data print is October 8. A first-Friday heuristic
already misdated both, so every caller reads the checked-in schedule and refuses
dates that are not on it.

Keep ``data/macro_events/nfp_release_schedule.csv`` current with
https://www.bls.gov/schedule/news_release/empsit.htm. ``next_release`` fails closed
once the schedule horizon passes, which is the signal to extend the file.

Stdlib only: the production drift sentinel runs this module under the system
interpreter.
"""

from __future__ import annotations

import csv
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

RELEASE_TIME_ET = time(8, 30)
EASTERN = ZoneInfo("America/New_York")
CAPTURE_WINDOW = timedelta(hours=24)
DEFAULT_SCHEDULE_CSV = Path("data/macro_events/nfp_release_schedule.csv")


class ScheduleError(RuntimeError):
    """A release date is not on the verified schedule, or the schedule is unusable."""


def load_schedule(path: Path | None = None) -> list[date]:
    """Read the verified schedule, sorted, failing loudly on any bad row."""
    schedule_path = Path(path) if path is not None else DEFAULT_SCHEDULE_CSV
    if not schedule_path.exists():
        raise ScheduleError(
            f"verified NFP schedule not found at {schedule_path}. Keep it current with "
            "https://www.bls.gov/schedule/news_release/empsit.htm"
        )
    dates: list[date] = []
    with schedule_path.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None or "release_date_et" not in reader.fieldnames:
            raise ScheduleError(f"schedule {schedule_path} must have a release_date_et column")
        for row in reader:
            raw = (row.get("release_date_et") or "").strip()
            try:
                dates.append(date.fromisoformat(raw))
            except ValueError as exc:
                raise ScheduleError(f"schedule {schedule_path} has a bad date {raw!r}") from exc
    if not dates:
        raise ScheduleError(f"schedule {schedule_path} has no rows")
    return sorted(set(dates))


def release_ts_utc(release_date: date) -> datetime:
    """The 08:30 America/New_York release instant in UTC (DST-aware)."""
    return datetime.combine(release_date, RELEASE_TIME_ET, tzinfo=EASTERN).astimezone(UTC)


def is_scheduled(release_date: date, path: Path | None = None) -> bool:
    return release_date in load_schedule(path)


def next_release(now: datetime, path: Path | None = None) -> date:
    """First scheduled release strictly after ``now``; fails when the horizon passes."""
    for candidate in load_schedule(path):
        if release_ts_utc(candidate) > now:
            return candidate
    raise ScheduleError(
        f"the verified NFP schedule has no release after {now.isoformat()}; extend "
        "data/macro_events/nfp_release_schedule.csv from "
        "https://www.bls.gov/schedule/news_release/empsit.htm"
    )


def previous_release(release_date: date, path: Path | None = None) -> date:
    """The scheduled release strictly before ``release_date``.

    Dates before the file's first row fall back to the first Friday of the prior
    month — reachable only for historical rescues, never for a live capture.
    """
    earlier = [value for value in load_schedule(path) if value < release_date]
    if earlier:
        return earlier[-1]
    year = release_date.year - 1 if release_date.month == 1 else release_date.year
    month = 12 if release_date.month == 1 else release_date.month - 1
    first = date(year, month, 1)
    return first + timedelta(days=(4 - first.weekday()) % 7)
