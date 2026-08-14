#!/usr/bin/env python3
"""Capture point-in-time NFP consensus/actual rows for the signed forward gate.

Gate: ``docs/evidence_portfolio/NFP_FORWARD_GATE.md`` (signed 2026-07-21, in force).
Build brief: ``docs/specs/nfp-forward-capture-routine.md``.

This tool is read-only with respect to production services. It touches exactly two
paths: the append-only forward CSV and a pending-capture stash. It never trades,
never reads config, and never evaluates the gate.

Workflow per release:

1. ``pre``  — within 24h *before* the release, freeze the Investing.com consensus
   via a Wayback "Save Page Now" snapshot and stash the snapshot URL.
2. ``post`` — after the release, supply the BLS headline actual and the consensus
   read off the frozen snapshot; one row is appended and the stash is cleared.
3. ``miss`` — if no pre-release snapshot was taken, record a ``NFP_MISSED_CAPTURE``
   row. Those rows carry no verdict weight; 3 or more cap the sample.

Rows are append-only. A committed row is never edited by this script.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import time
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx

CONSENSUS_URL = "https://www.investing.com/economic-calendar/nonfarm-payrolls-227"
WAYBACK_SAVE_URL = f"https://web.archive.org/save/{CONSENSUS_URL}"
WAYBACK_PREFIX = "https://web.archive.org/"
SNAPSHOT_TS_RE = re.compile(r"/web/(\d{14})")
HTML_TAG_RE = re.compile(r"<[^>]+>")

EVENT_TYPE = "NFP"
MISSED_EVENT_TYPE = "NFP_MISSED_CAPTURE"
METRIC = "headline_nfp_change_k"
CONSENSUS_SOURCE = "investing.com/Wayback"
ACTUAL_SOURCE = "bls.gov"
MISSED_SENTINEL = "MISSED_CAPTURE"

# Frozen by the gate for reporting continuity only. The entry condition is
# surprise > 0, which is equivalent to z > 0 for any positive divisor.
Z_DIVISOR = 220.28

RELEASE_TIME_ET = (8, 30)
EASTERN = ZoneInfo("America/New_York")
CAPTURE_WINDOW = timedelta(hours=24)

CSV_COLUMNS = (
    "event_type",
    "release_date_et",
    "release_ts_utc",
    "metric",
    "actual",
    "consensus",
    "surprise",
    "z",
    "consensus_source",
    "actual_source",
    "source_snapshot_url",
)

DEFAULT_FORWARD_CSV = Path("data/macro_events/nfp_good_news_forward.csv")
DEFAULT_PENDING_JSON = Path("research/nfp_forward/pending_capture.json")

USER_AGENT = "crypto-agent-nfp-forward-capture/1.0 (research; read-only)"
SAVE_TIMEOUT_SECONDS = 180.0
FETCH_TIMEOUT_SECONDS = 60.0
SAVE_MAX_ATTEMPTS = 3
# Delays after attempt 1 and 2 (Aug-7 miss was a single ECONNRESET with no retry).
SAVE_RETRY_DELAYS_SECONDS = (2.0, 5.0)


class CaptureError(RuntimeError):
    """Raised when a capture step cannot be completed safely."""


@dataclass(frozen=True)
class PendingCapture:
    release_date_et: str
    release_ts_utc: str
    snapshot_url: str
    snapshot_ts_utc: str
    captured_at_utc: str
    verified: bool

    def to_json(self) -> dict[str, object]:
        return {
            "release_date_et": self.release_date_et,
            "release_ts_utc": self.release_ts_utc,
            "snapshot_url": self.snapshot_url,
            "snapshot_ts_utc": self.snapshot_ts_utc,
            "captured_at_utc": self.captured_at_utc,
            "verified": self.verified,
        }

    @classmethod
    def from_json(cls, payload: dict[str, object]) -> PendingCapture:
        missing = {field for field in cls.__annotations__ if field not in payload}
        if missing:
            raise CaptureError(f"pending capture file is missing fields: {sorted(missing)}")
        return cls(
            release_date_et=str(payload["release_date_et"]),
            release_ts_utc=str(payload["release_ts_utc"]),
            snapshot_url=str(payload["snapshot_url"]),
            snapshot_ts_utc=str(payload["snapshot_ts_utc"]),
            captured_at_utc=str(payload["captured_at_utc"]),
            verified=bool(payload["verified"]),
        )


def release_timestamp_utc(release_date: date) -> datetime:
    """Return the 08:30 America/New_York release instant in UTC (DST-aware)."""
    hour, minute = RELEASE_TIME_ET
    local = datetime(release_date.year, release_date.month, release_date.day, hour, minute)
    return local.replace(tzinfo=EASTERN).astimezone(UTC)


def first_friday(year: int, month: int) -> date:
    """Return the first Friday of the given month (the usual NFP release day)."""
    first = date(year, month, 1)
    return first + timedelta(days=(4 - first.weekday()) % 7)


def next_scheduled_release(now: datetime) -> date:
    """Best-guess next NFP date. Always verify against the BLS release schedule."""
    candidate = first_friday(now.year, now.month)
    if release_timestamp_utc(candidate) > now:
        return candidate
    year = now.year + 1 if now.month == 12 else now.year
    month = 1 if now.month == 12 else now.month + 1
    return first_friday(year, month)


def parse_release_date(raw: str) -> date:
    try:
        return date.fromisoformat(raw.strip())
    except ValueError as exc:
        raise CaptureError(f"release date must be ISO YYYY-MM-DD, got {raw!r}") from exc


def _strip_html(html: str) -> str:
    return HTML_TAG_RE.sub(" ", html)


def _date_renderings(value: date) -> tuple[str, ...]:
    """Textual forms Investing.com uses for a release date, for containment checks."""
    return (
        value.isoformat(),
        value.strftime("%b %d, %Y"),
        value.strftime("%B %d, %Y"),
        value.strftime("%b %-d, %Y"),
        value.strftime("%B %-d, %Y"),
        value.strftime("%d/%m/%Y"),
        value.strftime("%m/%d/%Y"),
    )


def page_mentions_date(html: str, value: date) -> bool:
    text = " ".join(_strip_html(html).split())
    return any(rendering in text for rendering in _date_renderings(value))


def previous_scheduled_release(release_date: date) -> date:
    """First Friday of the month before ``release_date``."""
    year = release_date.year - 1 if release_date.month == 1 else release_date.year
    month = 12 if release_date.month == 1 else release_date.month - 1
    return first_friday(year, month)


def verify_snapshot_is_point_in_time(html: str, release_date: date) -> tuple[bool, str]:
    """Check the archived page predates the upcoming print.

    The invariant that matters: the snapshot must NOT already contain the upcoming
    release. A page that mentions the previous release and not the upcoming one is
    a valid point-in-time consensus capture.
    """
    if page_mentions_date(html, release_date):
        return False, (
            f"snapshot already mentions the upcoming release date {release_date.isoformat()} "
            "— it is not a point-in-time capture"
        )
    previous = previous_scheduled_release(release_date)
    if not page_mentions_date(html, previous):
        return False, (
            f"snapshot does not mention the previous release {previous.isoformat()}; "
            "could not confirm this is a live NFP calendar page"
        )
    return True, f"snapshot predates {release_date.isoformat()} and shows {previous.isoformat()}"


def _http_get(url: str, timeout: float) -> httpx.Response:
    headers = {"User-Agent": USER_AGENT}
    with httpx.Client(follow_redirects=True, timeout=timeout, headers=headers) as client:
        return client.get(url)


def _http_get_with_retries(
    url: str,
    timeout: float,
    *,
    attempts: int = SAVE_MAX_ATTEMPTS,
    label: str = "request",
) -> httpx.Response:
    """GET with bounded retries for transient network failures (e.g. ECONNRESET)."""
    last_exc: httpx.HTTPError | None = None
    for attempt in range(attempts):
        try:
            return _http_get(url, timeout)
        except httpx.HTTPError as exc:
            last_exc = exc
            if attempt >= attempts - 1:
                break
            delay = SAVE_RETRY_DELAYS_SECONDS[min(attempt, len(SAVE_RETRY_DELAYS_SECONDS) - 1)]
            print(
                f"[pre] {label} failed (attempt {attempt + 1}/{attempts}): {exc}; "
                f"retrying in {delay:.0f}s"
            )
            time.sleep(delay)
    assert last_exc is not None
    raise last_exc


def _snapshot_url_from_response(response: httpx.Response) -> str:
    candidates = [str(response.url)]
    content_location = response.headers.get("content-location")
    if content_location:
        candidates.append(f"https://web.archive.org{content_location}")
    for candidate in candidates:
        if candidate.startswith(WAYBACK_PREFIX) and SNAPSHOT_TS_RE.search(candidate):
            return candidate
    raise CaptureError(
        "Wayback did not return a usable snapshot URL "
        f"(status {response.status_code}, url {response.url}). "
        "Save Page Now may be rate-limited; retry or capture manually."
    )


def snapshot_timestamp(snapshot_url: str) -> datetime:
    match = SNAPSHOT_TS_RE.search(snapshot_url)
    if match is None:
        raise CaptureError(f"cannot read a Wayback timestamp from {snapshot_url!r}")
    return datetime.strptime(match.group(1), "%Y%m%d%H%M%S").replace(tzinfo=UTC)


def resolve_snapshot_timestamp(snapshot_url: str, *, captured_at: datetime) -> datetime:
    """Wayback URLs yield the archive timestamp; other PIT URLs use capture time."""
    if SNAPSHOT_TS_RE.search(snapshot_url):
        return snapshot_timestamp(snapshot_url)
    if not snapshot_url.startswith(("http://", "https://")):
        raise CaptureError(f"snapshot URL must be http(s): {snapshot_url!r}")
    print(
        "[pre] snapshot URL is not a Wayback /web/<ts>/ URL; "
        f"using capture time {captured_at.strftime('%Y-%m-%dT%H:%M:%SZ')} as snapshot timestamp"
    )
    return captured_at.astimezone(UTC)


def read_pending(path: Path) -> PendingCapture | None:
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise CaptureError(f"pending capture file is not valid JSON: {path}") from exc
    if not isinstance(payload, dict):
        raise CaptureError(f"pending capture file must contain a JSON object: {path}")
    return PendingCapture.from_json(payload)


def write_pending(path: Path, pending: PendingCapture) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(pending.to_json(), indent=2) + "\n", encoding="utf-8")


def committed_release_dates(csv_path: Path) -> set[str]:
    if not csv_path.exists():
        return set()
    with csv_path.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            return set()
        if not set(CSV_COLUMNS).issubset(reader.fieldnames):
            raise CaptureError(f"forward CSV has unexpected columns: {csv_path}")
        return {row["release_date_et"].strip() for row in reader}


def append_row(csv_path: Path, row: dict[str, str]) -> None:
    """Append one row, creating the file with a header if it does not exist yet."""
    release_date_et = row["release_date_et"]
    if release_date_et in committed_release_dates(csv_path):
        raise CaptureError(
            f"a row for {release_date_et} is already committed in {csv_path}; "
            "rows are append-only and are never edited"
        )
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    write_header = not csv_path.exists()
    with csv_path.open("a", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_COLUMNS, lineterminator="\n")
        if write_header:
            writer.writeheader()
        writer.writerow(row)


def build_row(
    release_date: date, actual: float, consensus: float, snapshot_url: str
) -> dict[str, str]:
    surprise = actual - consensus
    z = surprise / Z_DIVISOR
    return {
        "event_type": EVENT_TYPE,
        "release_date_et": release_date.isoformat(),
        "release_ts_utc": release_timestamp_utc(release_date).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "metric": METRIC,
        "actual": repr(actual),
        "consensus": repr(consensus),
        "surprise": repr(surprise),
        "z": repr(z),
        "consensus_source": CONSENSUS_SOURCE,
        "actual_source": ACTUAL_SOURCE,
        "source_snapshot_url": snapshot_url,
    }


def build_missed_row(release_date: date, note: str) -> dict[str, str]:
    return {
        "event_type": MISSED_EVENT_TYPE,
        "release_date_et": release_date.isoformat(),
        "release_ts_utc": release_timestamp_utc(release_date).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "metric": METRIC,
        "actual": "",
        "consensus": "",
        "surprise": "",
        "z": "",
        "consensus_source": MISSED_SENTINEL,
        "actual_source": note or MISSED_SENTINEL,
        "source_snapshot_url": "",
    }


def cmd_pre(args: argparse.Namespace) -> int:
    now = datetime.now(UTC)
    release_date = parse_release_date(args.release_date) if args.release_date else None
    if release_date is None:
        release_date = next_scheduled_release(now)
        print(
            f"[warn] no --release-date given; assuming next first Friday "
            f"{release_date.isoformat()}. Verify against "
            "https://www.bls.gov/schedule/news_release/empsit.htm"
        )

    release_ts = release_timestamp_utc(release_date)
    if now >= release_ts:
        raise CaptureError(
            f"release {release_date.isoformat()} already happened at {release_ts.isoformat()}; "
            "a post-release snapshot is not a point-in-time consensus. Use `miss` instead."
        )
    if release_ts - now > CAPTURE_WINDOW:
        raise CaptureError(
            f"release {release_date.isoformat()} is more than 24h away "
            f"({release_ts.isoformat()}); the gate wants the snapshot inside the 24h window."
        )

    pending_path = Path(args.pending)
    existing = read_pending(pending_path)
    if existing is not None and not args.force:
        raise CaptureError(
            f"a pending capture for {existing.release_date_et} already exists at {pending_path}; "
            "run `post` (or `miss`) first, or pass --force to replace it"
        )

    body = ""
    if args.snapshot_url:
        snapshot_url = str(args.snapshot_url).strip()
        print("[pre] using manual --snapshot-url (skipped Wayback Save Page Now)")
        print(f"[pre] snapshot: {snapshot_url}")
        snapshot_ts = resolve_snapshot_timestamp(snapshot_url, captured_at=now)
        body = _http_get_with_retries(
            snapshot_url,
            FETCH_TIMEOUT_SECONDS,
            label="snapshot fetch",
        ).text
    else:
        print(f"[pre] requesting Wayback Save Page Now for {CONSENSUS_URL}")
        response = _http_get_with_retries(
            WAYBACK_SAVE_URL,
            SAVE_TIMEOUT_SECONDS,
            label="Wayback Save Page Now",
        )
        snapshot_url = _snapshot_url_from_response(response)
        snapshot_ts = resolve_snapshot_timestamp(snapshot_url, captured_at=now)
        print(f"[pre] snapshot: {snapshot_url}")
        body = response.text
        if not body.strip():
            body = _http_get_with_retries(
                snapshot_url,
                FETCH_TIMEOUT_SECONDS,
                label="snapshot fetch",
            ).text

    if snapshot_ts >= release_ts:
        raise CaptureError(
            f"snapshot timestamp {snapshot_ts.isoformat()} is not before the release "
            f"{release_ts.isoformat()}"
        )

    verified, detail = verify_snapshot_is_point_in_time(body, release_date)
    if verified:
        print(f"[pre] verified: {detail}")
    else:
        print(f"[pre] NOT VERIFIED: {detail}")
        if not args.allow_unverified:
            raise CaptureError(
                "refusing to stash an unverified snapshot. Open the snapshot URL, confirm the "
                "listed latest release is the previous print, then re-run with --allow-unverified."
            )
        print("[pre] stashing anyway because --allow-unverified was passed")

    pending = PendingCapture(
        release_date_et=release_date.isoformat(),
        release_ts_utc=release_ts.strftime("%Y-%m-%dT%H:%M:%SZ"),
        snapshot_url=snapshot_url,
        snapshot_ts_utc=snapshot_ts.strftime("%Y-%m-%dT%H:%M:%SZ"),
        captured_at_utc=now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        verified=verified,
    )
    write_pending(pending_path, pending)
    print(f"[pre] stashed -> {pending_path}")
    print("[pre] next: read the consensus off the snapshot, then after the release run `post`")
    return 0


def cmd_post(args: argparse.Namespace) -> int:
    pending_path = Path(args.pending)
    pending = read_pending(pending_path)
    if pending is None:
        raise CaptureError(
            f"no pending capture at {pending_path}. Without a pre-release snapshot this print "
            "must be recorded with `miss`."
        )
    release_date = parse_release_date(pending.release_date_et)
    if args.release_date and parse_release_date(args.release_date) != release_date:
        raise CaptureError(
            f"pending capture is for {pending.release_date_et}, not {args.release_date}"
        )

    now = datetime.now(UTC)
    release_ts = release_timestamp_utc(release_date)
    if now < release_ts:
        raise CaptureError(
            f"release {release_date.isoformat()} has not happened yet ({release_ts.isoformat()})"
        )
    if not pending.verified and not args.allow_unverified:
        raise CaptureError(
            "pending snapshot was stashed unverified; re-run with --allow-unverified to commit it"
        )

    row = build_row(release_date, args.actual, args.consensus, pending.snapshot_url)
    csv_path = Path(args.csv)
    append_row(csv_path, row)
    pending_path.unlink()

    print(f"[post] appended {release_date.isoformat()} -> {csv_path}")
    print(
        f"[post]   actual={row['actual']} consensus={row['consensus']} surprise={row['surprise']}"
    )
    print(f"[post]   z={row['z']}  snapshot={pending.snapshot_url}")
    direction = (
        "HOT (long entry per gate)" if args.actual > args.consensus else "not hot (no trade)"
    )
    print(f"[post]   {direction}")
    print(f"[post] cleared {pending_path}")
    return 0


def cmd_miss(args: argparse.Namespace) -> int:
    release_date = parse_release_date(args.release_date)
    row = build_missed_row(release_date, args.note)
    csv_path = Path(args.csv)
    append_row(csv_path, row)

    pending_path = Path(args.pending)
    pending = read_pending(pending_path)
    if pending is not None and pending.release_date_et == release_date.isoformat():
        pending_path.unlink()
        print(f"[miss] cleared stale pending capture {pending_path}")

    misses = sum(1 for value in _read_event_types(csv_path) if value == MISSED_EVENT_TYPE)
    print(f"[miss] recorded MISSED_CAPTURE for {release_date.isoformat()} -> {csv_path}")
    print(f"[miss] missed captures so far: {misses} (3 or more cap the sample per the gate)")
    return 0


def _read_event_types(csv_path: Path) -> list[str]:
    if not csv_path.exists():
        return []
    with csv_path.open(encoding="utf-8", newline="") as handle:
        return [row["event_type"].strip() for row in csv.DictReader(handle)]


def cmd_status(args: argparse.Namespace) -> int:
    now = datetime.now(UTC)
    csv_path = Path(args.csv)
    pending_path = Path(args.pending)

    event_types = _read_event_types(csv_path)
    captured = sum(1 for value in event_types if value == EVENT_TYPE)
    misses = sum(1 for value in event_types if value == MISSED_EVENT_TYPE)
    print(f"[status] forward CSV: {csv_path} ({captured} captured, {misses} missed)")

    pending = read_pending(pending_path)
    if pending is None:
        print("[status] no pending capture")
    else:
        flag = "verified" if pending.verified else "UNVERIFIED"
        print(f"[status] pending: {pending.release_date_et} ({flag}) {pending.snapshot_url}")

    upcoming = next_scheduled_release(now)
    release_ts = release_timestamp_utc(upcoming)
    opens = release_ts - CAPTURE_WINDOW
    print(f"[status] next expected release: {upcoming.isoformat()} at {release_ts.isoformat()}")
    print(f"[status] capture window opens:  {opens.isoformat()}")
    print("[status] verify the date against https://www.bls.gov/schedule/news_release/empsit.htm")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--csv", default=str(DEFAULT_FORWARD_CSV), help="append-only forward CSV")
    parser.add_argument(
        "--pending", default=str(DEFAULT_PENDING_JSON), help="pending capture stash"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    pre = subparsers.add_parser("pre", help="freeze the pre-release consensus via Wayback")
    pre.add_argument("--release-date", help="ISO release date; defaults to the next first Friday")
    pre.add_argument(
        "--snapshot-url",
        help=(
            "skip Save Page Now and stash this already-captured PIT URL "
            "(Wayback or archive.ph / manual mirror); still verifies the page"
        ),
    )
    pre.add_argument(
        "--allow-unverified", action="store_true", help="stash despite a failed page check"
    )
    pre.add_argument("--force", action="store_true", help="replace an existing pending capture")
    pre.set_defaults(func=cmd_pre)

    post = subparsers.add_parser("post", help="append the row once the BLS actual is out")
    post.add_argument(
        "--actual", type=float, required=True, help="BLS headline NFP change, thousands"
    )
    post.add_argument(
        "--consensus", type=float, required=True, help="consensus read off the snapshot"
    )
    post.add_argument("--release-date", help="optional cross-check against the pending capture")
    post.add_argument(
        "--allow-unverified", action="store_true", help="commit an unverified snapshot"
    )
    post.set_defaults(func=cmd_post)

    miss = subparsers.add_parser("miss", help="record a print with no pre-release capture")
    miss.add_argument("--release-date", required=True, help="ISO release date")
    miss.add_argument("--note", default="", help="short reason, recorded in the row")
    miss.set_defaults(func=cmd_miss)

    status = subparsers.add_parser("status", help="show pending capture and the next release")
    status.set_defaults(func=cmd_status)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return int(args.func(args))
    except CaptureError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except httpx.HTTPError as exc:
        print(f"error: network failure talking to Wayback: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
