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

``pre --rescue --snapshot-url <url>`` recovers a historical snapshot after its
release: the 24h window is enforced against the Wayback timestamp, and the page's
``Latest Release`` label must still be the previous print.

Rows are append-only. A committed row is never edited by this script.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import re
import sys
import time
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from urllib.parse import urlparse

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.utils.nfp_schedule import (  # noqa: E402
    CAPTURE_WINDOW,
    DEFAULT_SCHEDULE_CSV,
    ScheduleError,
    is_scheduled,
)
from src.utils.nfp_schedule import (
    next_release as next_scheduled_release,
)
from src.utils.nfp_schedule import (
    previous_release as previous_scheduled_release,
)
from src.utils.nfp_schedule import (
    release_ts_utc as release_timestamp_utc,
)

CONSENSUS_URL = "https://www.investing.com/economic-calendar/nonfarm-payrolls-227"
WAYBACK_SAVE_URL = f"https://web.archive.org/save/{CONSENSUS_URL}"
WAYBACK_PREFIX = "https://web.archive.org/"
ARCHIVE_PH_SUBMIT_URL = "https://archive.ph/submit/"
ARCHIVE_PH_PREFIXES = (
    "https://archive.ph/",
    "http://archive.ph/",
    "https://archive.today/",
    "http://archive.today/",
    "https://archive.is/",
    "http://archive.is/",
)
SNAPSHOT_TS_RE = re.compile(r"/web/(\d{14})")
HTML_TAG_RE = re.compile(r"<[^>]+>")
LATEST_RELEASE_RE = re.compile(r"Latest Release\s+([A-Za-z]{3,9}\.?\s+\d{1,2},\s+\d{4})")
LATEST_RELEASE_FORMATS = ("%b %d, %Y", "%B %d, %Y", "%b. %d, %Y", "%B. %d, %Y")

EVENT_TYPE = "NFP"
MISSED_EVENT_TYPE = "NFP_MISSED_CAPTURE"
METRIC = "headline_nfp_change_k"
ACTUAL_SOURCE = "bls.gov"
MISSED_SENTINEL = "MISSED_CAPTURE"

# Frozen by the gate for reporting continuity only. The entry condition is
# surprise > 0, which is equivalent to z > 0 for any positive divisor.
Z_DIVISOR = 220.28

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


def latest_release_date(html: str) -> date | None:
    """Read the page's ``Latest Release`` date when the layout exposes it.

    Investing.com shows the upcoming release as a scheduled row with a forecast
    before the print, so merely mentioning the upcoming date is not evidence of
    a post-release capture. The ``Latest Release`` label names the last print
    whose data are actually published; that is the reliable pre/post signal.
    """
    text = " ".join(_strip_html(html).split())
    match = LATEST_RELEASE_RE.search(text)
    if match is None:
        return None
    raw = match.group(1)
    for fmt in LATEST_RELEASE_FORMATS:
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
    return None


def verify_snapshot_is_point_in_time(
    html: str, release_date: date, schedule_path: Path | None = None
) -> tuple[bool, str]:
    """Check the archived page predates the upcoming print.

    A pre-release calendar page lists the upcoming release as a scheduled row with
    its forecast, so the upcoming date being present is not by itself a problem.
    When the page exposes ``Latest Release``, require it to be the previous print:
    that means the upcoming actual has not been published. If the label is absent,
    fall back to the older containment check (previous print present, upcoming
    print absent).
    """
    previous = previous_scheduled_release(release_date, schedule_path)
    latest = latest_release_date(html)
    if latest is not None:
        if latest >= release_date:
            return False, (
                f"snapshot Latest Release is {latest.isoformat()}, not before the upcoming "
                f"release {release_date.isoformat()} — it is not a point-in-time capture"
            )
        if latest != previous:
            return False, (
                f"snapshot Latest Release is {latest.isoformat()}, expected the previous "
                f"release {previous.isoformat()}"
            )
        return True, (
            f"snapshot Latest Release is {previous.isoformat()}, before {release_date.isoformat()}"
        )

    if page_mentions_date(html, release_date):
        return False, (
            f"snapshot already mentions the upcoming release date {release_date.isoformat()} "
            "— it is not a point-in-time capture"
        )
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


def _submit_archive_ph(
    url: str,
    timeout: float = SAVE_TIMEOUT_SECONDS,
    *,
    attempts: int = SAVE_MAX_ATTEMPTS,
) -> tuple[str, str]:
    """Submit a URL to archive.ph and return (snapshot_url, page_html)."""
    headers = {
        "User-Agent": USER_AGENT,
        "Referer": "https://archive.ph/",
    }
    data = {"url": url}
    last_exc: Exception | None = None
    for attempt in range(attempts):
        try:
            with httpx.Client(follow_redirects=True, timeout=timeout, headers=headers) as client:
                resp = client.post(ARCHIVE_PH_SUBMIT_URL, data=data)
                try:
                    final_url = str(resp.url)
                except RuntimeError:
                    final_url = ""
                refresh = resp.headers.get("refresh", "")
                if "url=" in refresh:
                    target = refresh.split("url=", 1)[1].strip()
                    if target.startswith("http"):
                        final_url = target
                if (
                    any(final_url.startswith(p) for p in ARCHIVE_PH_PREFIXES)
                    and final_url.rstrip("/") != "https://archive.ph/submit"
                ):
                    body = resp.text
                    if not body.strip():
                        body = client.get(final_url).text
                    return final_url, body
                match = re.search(
                    r'href=["\'](https?://archive\.(?:ph|today|is)/[a-zA-Z0-9]+)["\']', resp.text
                )
                if match:
                    found_url = match.group(1)
                    body = client.get(found_url).text
                    return found_url, body
                raise CaptureError(
                    f"archive.ph submit did not return a snapshot URL (status {resp.status_code}, url {resp.url})"
                )
        except Exception as exc:
            last_exc = exc
            if attempt >= attempts - 1:
                break
            delay = SAVE_RETRY_DELAYS_SECONDS[min(attempt, len(SAVE_RETRY_DELAYS_SECONDS) - 1)]
            print(
                f"[pre] archive.ph submit failed (attempt {attempt + 1}/{attempts}): {exc}; "
                f"retrying in {delay:.0f}s"
            )
            time.sleep(delay)
    assert last_exc is not None
    raise last_exc


def send_telegram_alert(message: str) -> bool:
    """Send alert via Telegram if configured in environment."""
    enabled = os.getenv("TELEGRAM_ENABLED", "").strip().lower() in {"1", "true", "yes", "on"}
    bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    if not enabled or not bot_token or not chat_id:
        return False
    try:
        import urllib.parse
        import urllib.request

        payload = urllib.parse.urlencode({"chat_id": chat_id, "text": message}).encode("utf-8")
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{bot_token}/sendMessage",
            data=payload,
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return bool(data.get("ok"))
    except Exception as exc:
        print(f"[alert] Failed to send Telegram alert: {exc}", file=sys.stderr)
        return False


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


ARCHIVE_TS_RE = re.compile(r"archive\.(?:ph|today|is)/(?:newest/)?(\d{14})(?:/|$)")
FORECAST_K_RE = re.compile(r"(-?\d+(?:\.\d+)?)K")
PLAUSIBLE_MIN_K = -25000.0
PLAUSIBLE_MAX_K = 5000.0


def snapshot_ts_from_served_url(served_url: str) -> datetime:
    """Strict timestamp for a manually supplied snapshot: machine-readable or refuse.

    Wayback serves the capture nearest the requested URL, so the SERVED timestamp is
    authoritative, never the typed one. Live (non-archived) URLs are refused outright.
    """
    wayback = SNAPSHOT_TS_RE.search(served_url)
    if wayback:
        return datetime.strptime(wayback.group(1), "%Y%m%d%H%M%S").replace(tzinfo=UTC)
    archive = ARCHIVE_TS_RE.search(served_url)
    if archive:
        return datetime.strptime(archive.group(1), "%Y%m%d%H%M%S").replace(tzinfo=UTC)
    if any(served_url.startswith(prefix) for prefix in ARCHIVE_PH_PREFIXES):
        raise CaptureError(
            f"cannot read a capture timestamp from {served_url}; use the timestamped form "
            f"https://archive.ph/<YYYYMMDDHHMMSS>/{CONSENSUS_URL} so the 24h window can be verified"
        )
    raise CaptureError(
        f"served URL {served_url} is not an archived snapshot; refusing a live page as a "
        f"consensus source (use a Wayback /web/<ts>/ URL or an archive.ph capture of {CONSENSUS_URL})"
    )


def ensure_scheduled(release_date: date, args: argparse.Namespace, *, command: str) -> None:
    """Refuse dates not on the verified BLS schedule; --force overrides with a warning."""
    schedule_path = Path(args.schedule)
    if is_scheduled(release_date, schedule_path):
        return
    if args.force:
        print(
            f"[warn] {command}: release date {release_date.isoformat()} is NOT on the verified "
            f"schedule {schedule_path}; proceeding because --force was passed"
        )
        return
    raise CaptureError(
        f"{command}: release date {release_date.isoformat()} is not on the verified schedule "
        f"{schedule_path}. Employment Situation releases are not always the first Friday "
        "(2027-01-08, 2027-10-08). Confirm against "
        "https://www.bls.gov/schedule/news_release/empsit.htm, extend the schedule file, "
        "or pass --force if this really is a scheduled print."
    )


def validate_metric_value(value: float, label: str, *, force: bool) -> None:
    """Headline NFP change in thousands; nan/inf never, absurd units only with --force."""
    if not math.isfinite(value):
        raise CaptureError(f"{label} must be a finite number, got {value!r}")
    if not PLAUSIBLE_MIN_K <= value <= PLAUSIBLE_MAX_K:
        if force:
            print(
                f"[post] {label}={value} is outside the plausible NFP range "
                f"[{PLAUSIBLE_MIN_K:.0f}, {PLAUSIBLE_MAX_K:.0f}]K; proceeding because --force was passed"
            )
            return
        raise CaptureError(
            f"{label}={value} is outside the plausible NFP headline range "
            f"[{PLAUSIBLE_MIN_K:.0f}, {PLAUSIBLE_MAX_K:.0f}]K; the metric is thousands of jobs "
            "(165, not 165000). Re-check the BLS headline, or pass --force only if it really is."
        )


def consensus_source_for(snapshot_url: str) -> str:
    if snapshot_url.startswith(("https://web.archive.org/", "http://web.archive.org/")):
        return "investing.com/Wayback"
    if any(snapshot_url.startswith(prefix) for prefix in ARCHIVE_PH_PREFIXES):
        return "investing.com/archive.ph"
    return f"manual/{urlparse(snapshot_url).hostname or 'unknown'}"


def extract_consensus_from_page(html: str, release_date: date) -> float | None:
    """Best-effort forecast read off a frozen snapshot page; None when unreadable."""
    text = " ".join(_strip_html(html).split())
    for rendering in _date_renderings(release_date):
        idx = text.find(rendering)
        if idx < 0:
            continue
        match = FORECAST_K_RE.search(text[idx : idx + 120])
        if match:
            return float(match.group(1).replace(",", ""))
    return None


def _cross_check_consensus(snapshot_url: str, release_date: date, args: argparse.Namespace) -> None:
    """Best-effort cross-check of the typed consensus against the frozen page."""
    try:
        response = _http_get_with_retries(
            snapshot_url,
            FETCH_TIMEOUT_SECONDS,
            label="snapshot re-fetch for cross-check",
        )
        page_consensus = extract_consensus_from_page(response.text, release_date)
    except Exception as exc:
        print(f"[post] skipping the consensus cross-check (snapshot re-fetch failed: {exc})")
        return
    if page_consensus is None:
        print("[post] skipping the consensus cross-check (no forecast found on the page)")
        return
    if abs(page_consensus - float(args.consensus)) > 1.0:
        if args.force:
            print(
                f"[post] page forecast {page_consensus}K differs from --consensus "
                f"{args.consensus}K; proceeding because --force was passed"
            )
            return
        raise CaptureError(
            f"the snapshot page shows forecast {page_consensus}K but --consensus {args.consensus}K "
            "was given; re-read the consensus off the snapshot, or pass --force if the page "
            "extraction is wrong"
        )
    print(f"[post] consensus cross-check passed: page shows {page_consensus}K")


def _fetch_wayback_snapshot() -> tuple[str, datetime, str]:
    print(f"[pre] requesting Wayback Save Page Now for {CONSENSUS_URL}")
    response = _http_get_with_retries(
        WAYBACK_SAVE_URL,
        SAVE_TIMEOUT_SECONDS,
        label="Wayback Save Page Now",
    )
    snapshot_url = _snapshot_url_from_response(response)
    snapshot_ts = resolve_snapshot_timestamp(snapshot_url, captured_at=datetime.now(UTC))
    print(f"[pre] Wayback snapshot: {snapshot_url}")
    body = response.text
    if not body.strip():
        body = _http_get_with_retries(
            snapshot_url,
            FETCH_TIMEOUT_SECONDS,
            label="snapshot fetch",
        ).text
    return snapshot_url, snapshot_ts, body


def _fetch_archive_ph_snapshot() -> tuple[str, datetime, str]:
    print("[pre] submitting to archive.ph (redundant capture leg)")
    snapshot_url, body = _submit_archive_ph(CONSENSUS_URL)
    snapshot_ts = resolve_snapshot_timestamp(snapshot_url, captured_at=datetime.now(UTC))
    print(f"[pre] archive.ph snapshot: {snapshot_url}")
    return snapshot_url, snapshot_ts, body


def _fetch_manual_snapshot(requested_url: str) -> tuple[str, datetime, str]:
    print(f"[pre] using manual --snapshot-url (skipped Wayback Save Page Now): {requested_url}")
    response = _http_get_with_retries(
        requested_url,
        FETCH_TIMEOUT_SECONDS,
        label="snapshot fetch",
    )
    served_url = str(response.url)
    body = response.text
    if "/wip/" in served_url:
        raise CaptureError(
            f"snapshot {served_url} is still being created (archive.ph /wip/); retry once it completes"
        )
    snapshot_ts = snapshot_ts_from_served_url(served_url)
    print(f"[pre] served snapshot: {served_url} (captured {snapshot_ts.isoformat()})")
    return served_url, snapshot_ts, body


def _check_snapshot_window(snapshot_ts: datetime, release_ts: datetime) -> None:
    if snapshot_ts >= release_ts:
        raise CaptureError(
            f"snapshot timestamp {snapshot_ts.isoformat()} is not before the release "
            f"{release_ts.isoformat()}"
        )
    if release_ts - snapshot_ts > CAPTURE_WINDOW:
        raise CaptureError(
            f"snapshot timestamp {snapshot_ts.isoformat()} is more than 24h before the release "
            f"{release_ts.isoformat()}"
        )


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
        "consensus_source": consensus_source_for(snapshot_url),
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
    schedule_path = Path(args.schedule)
    release_date = parse_release_date(args.release_date) if args.release_date else None
    if release_date is None:
        release_date = next_scheduled_release(now, schedule_path)
        print(
            f"[pre] no --release-date given; next scheduled release per {schedule_path} "
            f"is {release_date.isoformat()}"
        )
    ensure_scheduled(release_date, args, command="pre")

    release_ts = release_timestamp_utc(release_date)
    if args.rescue:
        if not args.release_date:
            raise CaptureError("--rescue requires --release-date")
        if not args.snapshot_url:
            raise CaptureError("--rescue requires --snapshot-url")
        print(f"[pre] rescue mode for already-passed release {release_date.isoformat()}")
    else:
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

    if args.snapshot_url:
        snapshot_url, snapshot_ts, body = _fetch_manual_snapshot(str(args.snapshot_url).strip())
        _check_snapshot_window(snapshot_ts, release_ts)
        verified, detail = verify_snapshot_is_point_in_time(body, release_date, schedule_path)
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
    else:
        chosen: tuple[str, datetime, bool, str] | None = None
        first_unverified: tuple[str, datetime, str] | None = None
        failures: list[str] = []
        for label, fetch in (
            ("Wayback Save Page Now", _fetch_wayback_snapshot),
            ("archive.ph submit", _fetch_archive_ph_snapshot),
        ):
            try:
                snapshot_url, snapshot_ts, body = fetch()
            except Exception as exc:
                failures.append(f"{label}: {exc}")
                print(f"[pre] {label} failed: {exc}")
                continue
            if "/wip/" in snapshot_url:
                failures.append(f"{label}: {snapshot_url} is still in progress (/wip/)")
                print(f"[pre] {label}: {snapshot_url} is still in progress (/wip/)")
                continue
            try:
                _check_snapshot_window(snapshot_ts, release_ts)
            except CaptureError as exc:
                failures.append(f"{label}: {exc}")
                print(f"[pre] {label}: {exc}")
                continue
            verified, detail = verify_snapshot_is_point_in_time(body, release_date, schedule_path)
            if verified:
                chosen = (snapshot_url, snapshot_ts, True, detail)
                break
            failures.append(f"{label}: {detail}")
            print(f"[pre] {label} did not verify: {detail}")
            if first_unverified is None:
                first_unverified = (snapshot_url, snapshot_ts, detail)
        if chosen is None:
            if first_unverified is None:
                raise CaptureError(
                    "both capture attempts failed: "
                    + "; ".join(failures)
                    + f". Capture manually: save {CONSENSUS_URL} via Wayback or archive.ph and "
                    "re-run `pre` with --snapshot-url."
                )
            if not args.allow_unverified:
                raise CaptureError(
                    "no capture attempt verified as point-in-time; refusing to stash. "
                    + "; ".join(failures)
                    + ". Open a snapshot URL, confirm the Latest Release label is the previous "
                    "print, then re-run with --allow-unverified (or capture manually)."
                )
            print(f"[pre] NOT VERIFIED: {first_unverified[2]}")
            print("[pre] stashing anyway because --allow-unverified was passed")
            chosen = (first_unverified[0], first_unverified[1], False, first_unverified[2])
        snapshot_url, snapshot_ts, verified, detail = chosen
        if verified:
            print(f"[pre] verified: {detail}")

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

    ensure_scheduled(release_date, args, command="post")

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

    validate_metric_value(args.actual, "--actual", force=args.force)
    validate_metric_value(args.consensus, "--consensus", force=args.force)
    _cross_check_consensus(pending.snapshot_url, release_date, args)

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
    ensure_scheduled(release_date, args, command="miss")

    now = datetime.now(UTC)
    if release_date > now.date() and not args.force:
        raise CaptureError(
            f"miss: release date {release_date.isoformat()} is in the future; a MISSED_CAPTURE "
            "row can only be committed after the print. Pass --force only to correct a "
            "scheduling mistake."
        )

    pending_path = Path(args.pending)
    pending = read_pending(pending_path)
    if (
        pending is not None
        and pending.release_date_et == release_date.isoformat()
        and pending.verified
        and not args.force
    ):
        raise CaptureError(
            f"miss: a VERIFIED pending capture for {release_date.isoformat()} exists at "
            f"{pending_path}; run `post` with the BLS actual instead. Pass --force to discard it."
        )

    row = build_missed_row(release_date, args.note)
    csv_path = Path(args.csv)
    append_row(csv_path, row)
    if pending is not None and pending.release_date_et == release_date.isoformat():
        pending_path.unlink()
        print(f"[miss] cleared pending capture {pending_path}")

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

    try:
        upcoming = next_scheduled_release(now, Path(args.schedule))
    except ScheduleError as exc:
        print(f"[status] error: {exc}", file=sys.stderr)
        return 1
    release_ts = release_timestamp_utc(upcoming)
    opens = release_ts - CAPTURE_WINDOW
    print(f"[status] next scheduled release: {upcoming.isoformat()} at {release_ts.isoformat()}")
    print(f"[status] capture window opens:  {opens.isoformat()}")
    print(f"[status] schedule: {args.schedule} (keep current with bls.gov/schedule)")

    if getattr(args, "strict", False):
        if opens <= now < release_ts:
            if pending is None or pending.release_date_et != upcoming.isoformat():
                time_to_release = release_ts - now
                hours = time_to_release.total_seconds() / 3600.0
                err_msg = (
                    f"[status] STRICT CHECK FAILED: release {upcoming.isoformat()} is in "
                    f"{hours:.1f}h ({release_ts.isoformat()}) but no pending capture exists!"
                )
                print(err_msg, file=sys.stderr)
                if getattr(args, "alert", False) or hours <= 3.0:
                    alert_text = (
                        f"CRITICAL: crypto-agent NFP consensus capture missing!\n\n"
                        f"Next release: {upcoming.isoformat()} at {release_ts.isoformat()} UTC\n"
                        f"Time remaining: {hours:.1f}h\n\n"
                        f"Save {CONSENSUS_URL} via Wayback or archive.ph and run:\n"
                        f"uv run python scripts/nfp_forward_capture.py pre --release-date {upcoming.isoformat()} --snapshot-url <URL>"
                    )
                    send_telegram_alert(alert_text)
                return 1
    return 0


def cmd_cron(args: argparse.Namespace) -> int:
    """Automated cron routine run periodically (e.g. hourly on Hetzner host).

    If within the 24h pre-release capture window:
      - Checks if a verified pending capture already exists (if so, exits 0).
      - If missing, attempts pre-capture (Wayback SPN -> archive.ph submit).
      - If within 3h and capture fails or is missing, fires a Telegram alert.
    """
    now = datetime.now(UTC)
    csv_path = Path(args.csv)
    pending_path = Path(args.pending)
    schedule_path = Path(args.schedule)

    try:
        upcoming = next_scheduled_release(now, schedule_path)
    except ScheduleError as exc:
        print(f"[cron] error: {exc}", file=sys.stderr)
        return 1
    release_ts = release_timestamp_utc(upcoming)
    opens = release_ts - CAPTURE_WINDOW

    if now < opens or now >= release_ts:
        print(
            f"[cron] outside capture window for {upcoming.isoformat()} "
            f"(window opens {opens.isoformat()}, release {release_ts.isoformat()})"
        )
        return 0

    pending = read_pending(pending_path)
    if pending is not None and pending.release_date_et == upcoming.isoformat() and pending.verified:
        print(f"[cron] verified pending capture already exists: {pending.snapshot_url}")
        return 0

    print(
        f"[cron] inside capture window for {upcoming.isoformat()} (release at {release_ts.isoformat()}); "
        "triggering automated pre-release capture"
    )
    pre_args = argparse.Namespace(
        release_date=upcoming.isoformat(),
        snapshot_url=None,
        allow_unverified=False,
        force=False,
        rescue=False,
        pending=str(pending_path),
        csv=str(csv_path),
        schedule=str(schedule_path),
    )
    try:
        res = cmd_pre(pre_args)
        if res == 0:
            print(f"[cron] successfully captured pending snapshot for {upcoming.isoformat()}")
            return 0
    except Exception as exc:
        print(f"[cron] automated capture attempt failed: {exc}", file=sys.stderr)

    hours_left = (release_ts - now).total_seconds() / 3600.0
    if hours_left <= 3.0:
        alert_text = (
            f"CRITICAL: crypto-agent automated NFP consensus capture failed!\n\n"
            f"Next release: {upcoming.isoformat()} at {release_ts.isoformat()} UTC\n"
            f"Time remaining: {hours_left:.1f}h\n\n"
            f"Please save {CONSENSUS_URL} manually via Wayback or archive.ph and stash:\n"
            f"uv run python scripts/nfp_forward_capture.py pre --release-date {upcoming.isoformat()} --snapshot-url <URL>"
        )
        send_telegram_alert(alert_text)
    return 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--csv", default=str(DEFAULT_FORWARD_CSV), help="append-only forward CSV")
    parser.add_argument(
        "--pending",
        default=os.environ.get("NFP_PENDING_CAPTURE_PATH", str(DEFAULT_PENDING_JSON)),
        help="pending capture stash (env: NFP_PENDING_CAPTURE_PATH)",
    )
    parser.add_argument(
        "--schedule",
        default=str(DEFAULT_SCHEDULE_CSV),
        help="verified NFP release schedule (BLS Employment Situation)",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    pre = subparsers.add_parser("pre", help="freeze the pre-release consensus via Wayback")
    pre.add_argument(
        "--release-date", help="ISO release date; defaults to the next scheduled release"
    )
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
    pre.add_argument(
        "--force",
        action="store_true",
        help="replace an existing pending capture and override the schedule refusal",
    )
    pre.add_argument(
        "--rescue",
        action="store_true",
        help=(
            "stash a historical snapshot for a release that already happened; "
            "requires --snapshot-url and checks the 24h window against the snapshot timestamp"
        ),
    )
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
    post.add_argument(
        "--force",
        action="store_true",
        help="override the schedule/units/cross-check refusals with a warning",
    )
    post.set_defaults(func=cmd_post)

    miss = subparsers.add_parser("miss", help="record a print with no pre-release capture")
    miss.add_argument("--release-date", required=True, help="ISO release date")
    miss.add_argument("--note", default="", help="short reason, recorded in the row")
    miss.add_argument(
        "--force",
        action="store_true",
        help="override the schedule/future-date/verified-pending refusals with a warning",
    )
    miss.set_defaults(func=cmd_miss)

    status = subparsers.add_parser("status", help="show pending capture and the next release")
    status.add_argument(
        "--strict",
        action="store_true",
        help="exit non-zero if next release is <24h away with no pending capture",
    )
    status.add_argument(
        "--alert",
        action="store_true",
        help="send Telegram alert if strict check fails or release is <3h away",
    )
    status.set_defaults(func=cmd_status)

    cron = subparsers.add_parser("cron", help="automated capture check / trigger for cron or timer")
    cron.set_defaults(func=cmd_cron)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return int(args.func(args))
    except (CaptureError, ScheduleError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except httpx.HTTPError as exc:
        print(f"error: network failure talking to Wayback: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
