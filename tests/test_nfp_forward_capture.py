"""Tests for the NFP forward-gate capture tool.

No network access: every test exercises pure date/CSV/verification logic, the
verified release schedule, and the capture-refusal guards.
"""

from __future__ import annotations

import csv
import json
from datetime import UTC, date, datetime
from pathlib import Path

import httpx
import pytest

from scripts.nfp_forward_capture import (
    CSV_COLUMNS,
    EVENT_TYPE,
    MISSED_EVENT_TYPE,
    Z_DIVISOR,
    CaptureError,
    append_row,
    build_missed_row,
    build_parser,
    build_row,
    consensus_source_for,
    extract_consensus_from_page,
    latest_release_date,
    main,
    next_scheduled_release,
    page_mentions_date,
    previous_scheduled_release,
    read_pending,
    release_timestamp_utc,
    resolve_snapshot_timestamp,
    snapshot_timestamp,
    snapshot_ts_from_served_url,
    validate_metric_value,
    verify_snapshot_is_point_in_time,
)
from src.utils.nfp_schedule import (
    ScheduleError,
    load_schedule,
    next_release,
    release_ts_utc,
)

GOOD_SNAPSHOT_HTML = (
    "<div>Latest Release Oct 02, 2026 Actual 29.00K Forecast 89.00K "
    "Previous 133.00K</div><div>Nov 06, 2026 (Oct) 13:30 65.00K 29.00K</div>"
)


def snap_response(url: str, text: str) -> httpx.Response:
    """A fetched response whose final (served) URL is ``url``."""
    return httpx.Response(200, text=text, request=httpx.Request("GET", url))


def write_pending_capture(path: Path, release_date: str, *, verified: bool = True) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "release_date_et": release_date,
                "release_ts_utc": "2026-12-04T13:30:00Z",
                "snapshot_url": "https://web.archive.org/web/20261203070000/x",
                "snapshot_ts_utc": "2026-12-03T07:00:00Z",
                "captured_at_utc": "2026-12-03T07:00:00Z",
                "verified": verified,
            }
        ),
        encoding="utf-8",
    )


def test_release_timestamp_is_dst_aware():
    """08:30 ET is 12:30 UTC in summer and 13:30 UTC in winter."""
    assert release_timestamp_utc(date(2026, 8, 7)) == datetime(2026, 8, 7, 12, 30, tzinfo=UTC)
    assert release_timestamp_utc(date(2026, 12, 4)) == datetime(2026, 12, 4, 13, 30, tzinfo=UTC)


def test_release_timestamp_matches_committed_oos_rows():
    """Recompute two rows from the committed OOS table."""
    assert release_timestamp_utc(date(2021, 1, 8)) == datetime(2021, 1, 8, 13, 30, tzinfo=UTC)
    assert release_timestamp_utc(date(2021, 4, 2)) == datetime(2021, 4, 2, 12, 30, tzinfo=UTC)


def test_schedule_file_pins_committed_and_verified_bls_dates():
    """The checked-in schedule carries the committed prints and the official BLS dates.

    2027-01-08 (not the Jan 1 holiday) and 2027-10-08 are exactly the dates a
    first-Friday heuristic gets wrong.
    """
    schedule = load_schedule()
    for pinned in ("2026-08-07", "2026-11-06", "2026-12-04", "2027-01-08", "2027-10-08"):
        assert date.fromisoformat(pinned) in schedule, pinned
    assert date(2027, 1, 1) not in schedule


def test_next_release_crosses_year_boundary_on_the_verified_schedule():
    # BLS: the first Friday of January 2027 is the New Year holiday; the print is Jan 8.
    assert next_release(datetime(2026, 12, 5, 0, 0, tzinfo=UTC)) == date(2027, 1, 8)
    assert next_release(datetime(2026, 12, 31, 23, 0, tzinfo=UTC)) == date(2027, 1, 8)


def test_next_release_rolls_after_the_print():
    before = datetime(2026, 8, 6, 12, 0, tzinfo=UTC)
    after = datetime(2026, 8, 7, 13, 0, tzinfo=UTC)
    assert next_scheduled_release(before) == date(2026, 8, 7)
    assert next_scheduled_release(after) == date(2026, 9, 4)


def test_next_release_fails_closed_past_the_horizon(tmp_path: Path):
    schedule = tmp_path / "schedule.csv"
    schedule.write_text("release_date_et\n2026-08-07\n", encoding="utf-8")
    with pytest.raises(ScheduleError, match="extend"):
        next_release(datetime(2026, 8, 8, tzinfo=UTC), schedule)


def test_load_schedule_rejects_missing_and_malformed(tmp_path: Path):
    with pytest.raises(ScheduleError, match="not found"):
        load_schedule(tmp_path / "absent.csv")
    bad = tmp_path / "bad.csv"
    bad.write_text("release_date_et\nnot-a-date\n", encoding="utf-8")
    with pytest.raises(ScheduleError, match="bad date"):
        load_schedule(bad)


def test_previous_release_uses_schedule_then_friday_fallback():
    # On the schedule: the previous print before Dec 4 is Nov 6.
    assert previous_scheduled_release(date(2026, 12, 4)) == date(2026, 11, 6)
    # Before the schedule's first row: first-Friday fallback for historical rescues.
    assert previous_scheduled_release(date(2026, 8, 7)) == date(2026, 7, 3)
    assert previous_scheduled_release(date(2026, 1, 2)) == date(2025, 12, 5)


def test_page_mentions_date_handles_html_and_formats():
    assert page_mentions_date("<td>Jul 03, 2026</td>", date(2026, 7, 3))
    assert page_mentions_date("<span>July 3, 2026</span>", date(2026, 7, 3))
    assert not page_mentions_date("<td>Jun 05, 2026</td>", date(2026, 7, 3))


def test_verify_rejects_snapshot_containing_the_upcoming_print():
    html = "<div>Aug 07, 2026</div><div>Jul 03, 2026</div>"
    verified, detail = verify_snapshot_is_point_in_time(html, date(2026, 8, 7))
    assert verified is False
    assert "not a point-in-time capture" in detail


def test_verify_rejects_page_without_the_previous_print():
    verified, detail = verify_snapshot_is_point_in_time("<div>nothing here</div>", date(2026, 8, 7))
    assert verified is False
    assert "previous release" in detail


def test_verify_accepts_point_in_time_snapshot():
    html = "<table><tr><td>Jul 03, 2026</td><td>147K</td></tr></table>"
    verified, _ = verify_snapshot_is_point_in_time(html, date(2026, 8, 7))
    assert verified is True


def test_latest_release_date_reads_investing_label():
    html = (
        "<div>Latest Release Oct 02, 2026 Actual 29.00K Forecast 89.00K "
        "Previous 133.00K</div><div>Nov 06, 2026 (Oct) 13:30 65.00K 29.00K</div>"
    )
    assert latest_release_date(html) == date(2026, 10, 2)


def test_verify_accepts_upcoming_row_when_latest_release_is_previous():
    html = (
        "<div>Latest Release Oct 02, 2026 Actual 29.00K Forecast 89.00K "
        "Previous 133.00K</div><div>Nov 06, 2026 (Oct) 13:30 65.00K 29.00K</div>"
    )
    verified, detail = verify_snapshot_is_point_in_time(html, date(2026, 11, 6))
    assert verified is True
    assert "2026-10-02" in detail


def test_verify_rejects_page_whose_latest_release_is_upcoming():
    html = "<div>Latest Release Nov 06, 2026 Actual 165.00K Forecast 65.00K</div>"
    verified, detail = verify_snapshot_is_point_in_time(html, date(2026, 11, 6))
    assert verified is False
    assert "Latest Release" in detail


def test_verify_rejects_stale_latest_release():
    html = "<div>Latest Release Sep 04, 2026 Actual 162.00K</div>"
    verified, detail = verify_snapshot_is_point_in_time(html, date(2026, 11, 6))
    assert verified is False
    assert "expected the previous release" in detail


def test_snapshot_timestamp_parsed_from_wayback_url():
    url = "https://web.archive.org/web/20260806131415/https://www.investing.com/x"
    assert snapshot_timestamp(url) == datetime(2026, 8, 6, 13, 14, 15, tzinfo=UTC)


def test_snapshot_timestamp_rejects_non_wayback_url():
    with pytest.raises(CaptureError):
        snapshot_timestamp("https://www.investing.com/economic-calendar/nonfarm-payrolls-227")


def test_resolve_snapshot_timestamp_uses_wayback_ts_when_present():
    url = "https://web.archive.org/web/20260903124500/https://www.investing.com/x"
    captured = datetime(2026, 9, 3, 12, 50, tzinfo=UTC)
    assert resolve_snapshot_timestamp(url, captured_at=captured) == datetime(
        2026, 9, 3, 12, 45, tzinfo=UTC
    )


def test_resolve_snapshot_timestamp_falls_back_to_capture_time_for_manual_mirrors(
    capsys: pytest.CaptureFixture[str],
):
    url = "https://archive.ph/abcd1234"
    captured = datetime(2026, 9, 3, 13, 0, tzinfo=UTC)
    assert resolve_snapshot_timestamp(url, captured_at=captured) == captured
    assert "not a Wayback" in capsys.readouterr().out


def test_snapshot_ts_from_served_url_reads_archive_timestamped_form():
    url = "https://archive.ph/20261203120000/https://www.investing.com/x"
    assert snapshot_ts_from_served_url(url) == datetime(2026, 12, 3, 12, 0, tzinfo=UTC)


def test_snapshot_ts_from_served_url_rejects_bare_archive_id():
    with pytest.raises(CaptureError, match="timestamped form"):
        snapshot_ts_from_served_url("https://archive.ph/abcd1234")


def test_snapshot_ts_from_served_url_rejects_live_page():
    with pytest.raises(CaptureError, match="not an archived snapshot"):
        snapshot_ts_from_served_url(
            "https://www.investing.com/economic-calendar/nonfarm-payrolls-227"
        )


def test_http_get_with_retries_retries_then_succeeds(monkeypatch: pytest.MonkeyPatch):
    from scripts import nfp_forward_capture as mod

    calls = {"n": 0}

    def flaky(_url: str, _timeout: float):
        calls["n"] += 1
        if calls["n"] < 3:
            raise httpx.ConnectError("Connection reset by peer")
        return httpx.Response(200, text="ok")

    sleeps: list[float] = []
    monkeypatch.setattr(mod, "_http_get", flaky)
    monkeypatch.setattr(mod.time, "sleep", sleeps.append)

    response = mod._http_get_with_retries("https://example.test", 1.0, label="Wayback")
    assert response.text == "ok"
    assert calls["n"] == 3
    assert sleeps == [2.0, 5.0]


def test_http_get_with_retries_raises_after_exhaustion(monkeypatch: pytest.MonkeyPatch):
    from scripts import nfp_forward_capture as mod

    monkeypatch.setattr(
        mod,
        "_http_get",
        lambda _url, _timeout: (_ for _ in ()).throw(httpx.ConnectError("boom")),
    )
    monkeypatch.setattr(mod.time, "sleep", lambda _delay: None)

    with pytest.raises(httpx.ConnectError, match="boom"):
        mod._http_get_with_retries("https://example.test", 1.0, attempts=2)


def test_build_row_keeps_full_float_precision():
    """The OOS loader rejects rounded z, so z must round-trip exactly."""
    row = build_row(date(2026, 8, 7), 175.0, 160.0, "https://web.archive.org/web/2026/x")
    assert row["surprise"] == repr(15.0)
    assert float(row["z"]) == 15.0 / Z_DIVISOR
    assert row["z"] == repr(15.0 / Z_DIVISOR)
    assert row["release_ts_utc"] == "2026-08-07T12:30:00Z"
    assert row["event_type"] == EVENT_TYPE
    assert row["consensus_source"] == "investing.com/Wayback"
    assert set(row) == set(CSV_COLUMNS)


def test_build_row_labels_the_actual_snapshot_source():
    wayback = build_row(date(2026, 8, 7), 175.0, 160.0, "https://web.archive.org/web/2026/x")
    archive = build_row(date(2026, 8, 7), 175.0, 160.0, "https://archive.ph/abcd1234")
    assert wayback["consensus_source"] == "investing.com/Wayback"
    assert archive["consensus_source"] == "investing.com/archive.ph"
    assert consensus_source_for("https://mirror.example/spot") == "manual/mirror.example"


def test_build_row_handles_negative_surprise():
    row = build_row(date(2026, 9, 4), 90.0, 150.0, "https://web.archive.org/web/2026/x")
    assert float(row["surprise"]) == -60.0
    assert float(row["z"]) < 0


def test_append_row_writes_header_then_appends(tmp_path: Path):
    csv_path = tmp_path / "forward.csv"
    append_row(csv_path, build_row(date(2026, 8, 7), 175.0, 160.0, "https://web.archive.org/a"))
    append_row(csv_path, build_row(date(2026, 9, 4), 90.0, 150.0, "https://web.archive.org/b"))

    with csv_path.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        assert reader.fieldnames == list(CSV_COLUMNS)
        rows = list(reader)
    assert [row["release_date_et"] for row in rows] == ["2026-08-07", "2026-09-04"]


def test_append_row_refuses_duplicate_release_date(tmp_path: Path):
    csv_path = tmp_path / "forward.csv"
    append_row(csv_path, build_row(date(2026, 8, 7), 175.0, 160.0, "https://web.archive.org/a"))
    with pytest.raises(CaptureError, match="append-only"):
        append_row(csv_path, build_row(date(2026, 8, 7), 999.0, 1.0, "https://web.archive.org/c"))


def test_missed_capture_row_is_blank_and_distinctly_typed(tmp_path: Path):
    csv_path = tmp_path / "forward.csv"
    row = build_missed_row(date(2026, 10, 2), "server down")
    append_row(csv_path, row)

    assert row["event_type"] == MISSED_EVENT_TYPE
    assert row["actual"] == ""
    assert row["consensus"] == ""
    assert row["z"] == ""
    assert row["source_snapshot_url"] == ""
    assert row["actual_source"] == "server down"
    assert row["release_ts_utc"] == "2026-10-02T12:30:00Z"


def test_missed_capture_still_blocks_a_later_duplicate(tmp_path: Path):
    csv_path = tmp_path / "forward.csv"
    append_row(csv_path, build_missed_row(date(2026, 10, 2), ""))
    with pytest.raises(CaptureError):
        append_row(csv_path, build_row(date(2026, 10, 2), 1.0, 0.0, "https://web.archive.org/d"))


def test_submit_archive_ph_extracts_url(monkeypatch: pytest.MonkeyPatch):
    from scripts import nfp_forward_capture as mod

    fake_html = "<html>Latest Release Oct 02, 2026 Actual 29.00K</html>"

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def post(self, url, data=None):
            return httpx.Response(
                200,
                headers={"refresh": "0;url=https://archive.ph/test1234"},
                text=fake_html,
                request=httpx.Request("POST", "https://archive.ph/submit/"),
            )

        def get(self, url):
            return httpx.Response(
                200,
                text=fake_html,
                request=httpx.Request("GET", url),
            )

    monkeypatch.setattr(mod.httpx, "Client", FakeClient)
    url, body = mod._submit_archive_ph("https://example.com")
    assert url == "https://archive.ph/test1234"
    assert "Oct 02, 2026" in body


def test_pre_rescue_stashes_historical_snapshot_and_post_appends(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    from scripts import nfp_forward_capture as mod

    html = GOOD_SNAPSHOT_HTML
    snapshot = (
        "https://web.archive.org/web/20261106130744/"
        "https://www.investing.com/economic-calendar/nonfarm-payrolls-227"
    )
    monkeypatch.setattr(
        mod, "_http_get_with_retries", lambda *_a, **_k: snap_response(snapshot, html)
    )
    pending = tmp_path / "pending.json"
    csv_path = tmp_path / "forward.csv"

    assert (
        main(
            [
                "--csv",
                str(csv_path),
                "--pending",
                str(pending),
                "pre",
                "--release-date",
                "2026-11-06",
                "--snapshot-url",
                snapshot,
                "--rescue",
            ]
        )
        == 0
    )
    stashed = read_pending(pending)
    assert stashed is not None
    assert stashed.verified is True

    assert (
        main(
            [
                "--csv",
                str(csv_path),
                "--pending",
                str(pending),
                "post",
                "--release-date",
                "2026-11-06",
                "--actual",
                "165",
                "--consensus",
                "65",
            ]
        )
        == 0
    )
    with csv_path.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert len(rows) == 1
    assert rows[0]["event_type"] == EVENT_TYPE
    assert rows[0]["actual"] == "165.0"
    assert rows[0]["consensus"] == "65.0"
    assert rows[0]["surprise"] == "100.0"
    assert float(rows[0]["z"]) == pytest.approx(100.0 / Z_DIVISOR)
    assert "20261106130744" in rows[0]["source_snapshot_url"]
    assert rows[0]["consensus_source"] == "investing.com/Wayback"


def test_rescue_requires_release_date(tmp_path: Path) -> None:
    assert (
        main(
            [
                "--pending",
                str(tmp_path / "pending.json"),
                "pre",
                "--snapshot-url",
                "https://web.archive.org/web/20261106130744/https://example.invalid/x",
                "--rescue",
            ]
        )
        == 1
    )


def test_rescue_rejects_snapshot_outside_the_24h_window(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    from scripts import nfp_forward_capture as mod

    html = "<div>Latest Release Oct 02, 2026</div>"
    snapshot = (
        "https://web.archive.org/web/20261104000000/"
        "https://www.investing.com/economic-calendar/nonfarm-payrolls-227"
    )
    monkeypatch.setattr(
        mod, "_http_get_with_retries", lambda *_a, **_k: snap_response(snapshot, html)
    )
    assert (
        main(
            [
                "--pending",
                str(tmp_path / "pending.json"),
                "pre",
                "--release-date",
                "2026-11-06",
                "--snapshot-url",
                snapshot,
                "--rescue",
            ]
        )
        == 1
    )


def test_manual_snapshot_uses_the_served_timestamp_not_the_typed_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """M1: Wayback serves the nearest capture; the served ts governs the window."""
    from scripts import nfp_forward_capture as mod

    inside_window = datetime(2026, 12, 3, 20, 0, 0, tzinfo=UTC)

    class FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return inside_window

    monkeypatch.setattr(mod, "datetime", FrozenDatetime)

    # Typed URL claims a Dec-03 timestamp, but Wayback serves the Nov-20 capture.
    typed = "https://web.archive.org/web/20261203120000/https://www.investing.com/x"
    served = "https://web.archive.org/web/20261120120000/https://www.investing.com/x"
    monkeypatch.setattr(
        mod, "_http_get_with_retries", lambda *_a, **_k: snap_response(served, GOOD_SNAPSHOT_HTML)
    )
    assert (
        main(
            [
                "--pending",
                str(tmp_path / "pending.json"),
                "pre",
                "--release-date",
                "2026-12-04",
                "--snapshot-url",
                typed,
            ]
        )
        == 1
    )
    # Nothing stashed: the pending file must not exist.
    assert not (tmp_path / "pending.json").exists()


def test_manual_snapshot_rejects_live_page(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from scripts import nfp_forward_capture as mod

    inside_window = datetime(2026, 12, 3, 20, 0, 0, tzinfo=UTC)

    class FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return inside_window

    monkeypatch.setattr(mod, "datetime", FrozenDatetime)
    live = "https://www.investing.com/economic-calendar/nonfarm-payrolls-227"
    monkeypatch.setattr(
        mod, "_http_get_with_retries", lambda *_a, **_k: snap_response(live, GOOD_SNAPSHOT_HTML)
    )
    assert (
        main(
            [
                "--pending",
                str(tmp_path / "pending.json"),
                "pre",
                "--release-date",
                "2026-12-04",
                "--snapshot-url",
                live,
            ]
        )
        == 1
    )
    assert not (tmp_path / "pending.json").exists()


def test_pre_refuses_a_date_not_on_the_schedule(tmp_path: Path):
    """H1: 2027-01-01 is a federal holiday, not a scheduled print; refuse it."""
    pending = tmp_path / "pending.json"
    assert (
        main(
            [
                "--pending",
                str(pending),
                "pre",
                "--release-date",
                "2027-01-01",
            ]
        )
        == 1
    )
    assert not pending.exists()


def test_pre_force_overrides_the_schedule_refusal(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from scripts import nfp_forward_capture as mod

    inside_window = datetime(2026, 12, 31, 20, 0, 0, tzinfo=UTC)

    class FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return inside_window

    monkeypatch.setattr(mod, "datetime", FrozenDatetime)
    snapshot = "https://web.archive.org/web/20261231150000/https://www.investing.com/x"
    html = "<div>Latest Release Dec 04, 2026 Actual 1.00K</div>"
    monkeypatch.setattr(
        mod, "_http_get_with_retries", lambda *_a, **_k: snap_response(snapshot, html)
    )
    pending = tmp_path / "pending.json"
    assert (
        main(
            [
                "--pending",
                str(pending),
                "pre",
                "--release-date",
                "2027-01-01",
                "--snapshot-url",
                snapshot,
                "--force",
            ]
        )
        == 0
    )
    stashed = read_pending(pending)
    assert stashed is not None
    assert stashed.release_date_et == "2027-01-01"


def test_pre_falls_back_to_archive_ph_when_wayback_verification_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """H3: Wayback captured the 403 challenge page; the archive.ph leg must still run."""
    from scripts import nfp_forward_capture as mod

    challenge_html = "<html>Access denied. Error 403.</html>"
    good_for_december = (
        "<div>Latest Release Nov 06, 2026 Actual 165.00K Forecast 65.00K "
        "Previous 29.00K</div><div>Dec 04, 2026 (Nov) 13:30 140.00K 165.00K</div>"
    )

    def fake_get_retries(url, timeout, **kwargs):
        return snap_response(
            "https://web.archive.org/web/20261203190000/https://www.investing.com/x",
            challenge_html,
        )

    monkeypatch.setattr(mod, "_http_get_with_retries", fake_get_retries)
    monkeypatch.setattr(
        mod,
        "_submit_archive_ph",
        lambda url, **kwargs: ("https://archive.ph/dec2026", good_for_december),
    )

    inside_window = datetime(2026, 12, 3, 20, 0, 0, tzinfo=UTC)

    class FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return inside_window

    monkeypatch.setattr(mod, "datetime", FrozenDatetime)

    pending = tmp_path / "pending.json"
    assert (
        main(
            [
                "--pending",
                str(pending),
                "pre",
                "--release-date",
                "2026-12-04",
            ]
        )
        == 0
    )
    stashed = read_pending(pending)
    assert stashed is not None
    assert stashed.snapshot_url == "https://archive.ph/dec2026"
    assert stashed.verified is True


def test_pre_falls_back_to_archive_ph_when_wayback_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    from scripts import nfp_forward_capture as mod

    html = (
        "<div>Latest Release Nov 06, 2026 Actual 165.00K Forecast 65.00K "
        "Previous 29.00K</div><div>Dec 04, 2026 (Nov) 13:30 140.00K 165.00K</div>"
    )

    def fake_get_retries(url, timeout, **kwargs):
        if "web.archive.org/save" in url:
            raise httpx.ConnectError("Connection reset by peer")
        return snap_response("https://archive.ph/dec2026", html)

    monkeypatch.setattr(mod, "_http_get_with_retries", fake_get_retries)
    monkeypatch.setattr(
        mod,
        "_submit_archive_ph",
        lambda url, **kwargs: ("https://archive.ph/dec2026", html),
    )

    # Freeze 'now' inside the 24h capture window for Dec 04, 2026 (release is 13:30 UTC)
    inside_window = datetime(2026, 12, 3, 20, 0, 0, tzinfo=UTC)

    class FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return inside_window

    monkeypatch.setattr(mod, "datetime", FrozenDatetime)

    pending = tmp_path / "pending.json"
    assert (
        main(
            [
                "--pending",
                str(pending),
                "pre",
                "--release-date",
                "2026-12-04",
            ]
        )
        == 0
    )
    stashed = read_pending(pending)
    assert stashed is not None
    assert stashed.snapshot_url == "https://archive.ph/dec2026"
    assert stashed.verified is True


def test_pre_reports_both_legs_when_both_fail_verification(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    from scripts import nfp_forward_capture as mod

    challenge_html = "<html>Access denied. Error 403.</html>"

    monkeypatch.setattr(
        mod,
        "_http_get_with_retries",
        lambda url, timeout, **kwargs: snap_response(
            "https://web.archive.org/web/20261203190000/https://www.investing.com/x",
            challenge_html,
        ),
    )
    monkeypatch.setattr(
        mod,
        "_submit_archive_ph",
        lambda url, **kwargs: ("https://archive.ph/dec2026", challenge_html),
    )

    inside_window = datetime(2026, 12, 3, 20, 0, 0, tzinfo=UTC)

    class FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return inside_window

    monkeypatch.setattr(mod, "datetime", FrozenDatetime)

    assert (
        main(
            [
                "--pending",
                str(tmp_path / "pending.json"),
                "pre",
                "--release-date",
                "2026-12-04",
            ]
        )
        == 1
    )
    err = capsys.readouterr().err
    assert "no capture attempt verified" in err
    assert "Wayback Save Page Now" in err
    assert "archive.ph submit" in err
    assert not (tmp_path / "pending.json").exists()


def test_pre_wip_snapshot_is_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from scripts import nfp_forward_capture as mod

    inside_window = datetime(2026, 12, 3, 20, 0, 0, tzinfo=UTC)

    class FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return inside_window

    monkeypatch.setattr(mod, "datetime", FrozenDatetime)
    wip = "https://archive.ph/wip/abc123"
    monkeypatch.setattr(
        mod, "_http_get_with_retries", lambda *_a, **_k: snap_response(wip, "<html>saving</html>")
    )
    assert (
        main(
            [
                "--pending",
                str(tmp_path / "pending.json"),
                "pre",
                "--release-date",
                "2026-12-04",
                "--snapshot-url",
                wip,
            ]
        )
        == 1
    )
    assert not (tmp_path / "pending.json").exists()


def test_extract_consensus_from_page_reads_the_upcoming_forecast():
    html = (
        "<div>Latest Release Oct 02, 2026 Actual 29.00K Forecast 89.00K "
        "Previous 133.00K</div><div>Nov 06, 2026 (Oct) 13:30 65.00K 29.00K</div>"
    )
    assert extract_consensus_from_page(html, date(2026, 11, 6)) == 65.0
    assert extract_consensus_from_page("<div>nothing</div>", date(2026, 11, 6)) is None


def test_validate_metric_value_bounds():
    validate_metric_value(165.0, "--actual", force=False)
    validate_metric_value(-800.0, "--actual", force=False)
    with pytest.raises(CaptureError, match="finite"):
        validate_metric_value(float("nan"), "--actual", force=True)
    with pytest.raises(CaptureError, match="finite"):
        validate_metric_value(float("inf"), "--consensus", force=True)
    with pytest.raises(CaptureError, match="plausible"):
        validate_metric_value(165000.0, "--actual", force=False)
    # Out-of-range is overridable with --force; nan/inf never is.
    validate_metric_value(165000.0, "--actual", force=True)


def test_post_refuses_implausible_and_nonfinite_numbers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    from scripts import nfp_forward_capture as mod

    pending = tmp_path / "pending.json"
    write_pending_capture(pending, "2026-11-06")
    csv_path = tmp_path / "forward.csv"
    monkeypatch.setattr(
        mod,
        "_http_get_with_retries",
        lambda *_a, **_k: (_ for _ in ()).throw(httpx.ConnectError("offline")),
    )

    for bad_actual in ("165000", "nan"):
        assert (
            main(
                [
                    "--csv",
                    str(csv_path),
                    "--pending",
                    str(pending),
                    "post",
                    "--actual",
                    bad_actual,
                    "--consensus",
                    "65",
                ]
            )
            == 1
        )
    assert not csv_path.exists()


def test_post_cross_check_refuses_a_mismatched_consensus(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    from scripts import nfp_forward_capture as mod

    pending = tmp_path / "pending.json"
    write_pending_capture(pending, "2026-11-06")
    csv_path = tmp_path / "forward.csv"
    snapshot = "https://web.archive.org/web/20261106130744/https://www.investing.com/x"
    monkeypatch.setattr(
        mod, "_http_get_with_retries", lambda *_a, **_k: snap_response(snapshot, GOOD_SNAPSHOT_HTML)
    )
    # The page shows 65.00K; a typed consensus of 90 must be refused.
    assert (
        main(
            [
                "--csv",
                str(csv_path),
                "--pending",
                str(pending),
                "post",
                "--actual",
                "165",
                "--consensus",
                "90",
            ]
        )
        == 1
    )
    assert not csv_path.exists()


def test_post_refuses_unscheduled_date(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from scripts import nfp_forward_capture as mod

    frozen = datetime(2027, 1, 2, 12, 0, 0, tzinfo=UTC)

    class FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return frozen

    monkeypatch.setattr(mod, "datetime", FrozenDatetime)
    pending = tmp_path / "pending.json"
    write_pending_capture(pending, "2027-01-01")
    csv_path = tmp_path / "forward.csv"
    monkeypatch.setattr(
        mod,
        "_http_get_with_retries",
        lambda *_a, **_k: (_ for _ in ()).throw(httpx.ConnectError("offline")),
    )
    assert (
        main(
            [
                "--csv",
                str(csv_path),
                "--pending",
                str(pending),
                "post",
                "--actual",
                "150",
                "--consensus",
                "140",
            ]
        )
        == 1
    )
    # --force overrides the schedule refusal.
    assert (
        main(
            [
                "--csv",
                str(csv_path),
                "--pending",
                str(pending),
                "post",
                "--actual",
                "150",
                "--consensus",
                "140",
                "--force",
            ]
        )
        == 0
    )
    with csv_path.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert len(rows) == 1
    assert rows[0]["release_date_et"] == "2027-01-01"


def test_miss_refuses_future_release_date(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from scripts import nfp_forward_capture as mod

    frozen = datetime(2026, 12, 5, 12, 0, 0, tzinfo=UTC)

    class FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return frozen

    monkeypatch.setattr(mod, "datetime", FrozenDatetime)
    csv_path = tmp_path / "forward.csv"
    assert (
        main(
            [
                "--csv",
                str(csv_path),
                "--pending",
                str(tmp_path / "pending.json"),
                "miss",
                "--release-date",
                "2027-01-08",
            ]
        )
        == 1
    )
    assert not csv_path.exists()


def test_miss_force_overrides_the_future_refusal(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from scripts import nfp_forward_capture as mod

    frozen = datetime(2026, 12, 5, 12, 0, 0, tzinfo=UTC)

    class FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return frozen

    monkeypatch.setattr(mod, "datetime", FrozenDatetime)
    csv_path = tmp_path / "forward.csv"
    assert (
        main(
            [
                "--csv",
                str(csv_path),
                "--pending",
                str(tmp_path / "pending.json"),
                "miss",
                "--release-date",
                "2027-01-08",
                "--force",
            ]
        )
        == 0
    )
    with csv_path.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert rows[0]["event_type"] == MISSED_EVENT_TYPE


def test_miss_refuses_to_discard_a_verified_pending_capture(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    from scripts import nfp_forward_capture as mod

    frozen = datetime(2026, 11, 19, 12, 0, 0, tzinfo=UTC)

    class FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return frozen

    monkeypatch.setattr(mod, "datetime", FrozenDatetime)
    pending = tmp_path / "pending.json"
    write_pending_capture(pending, "2026-11-06")
    csv_path = tmp_path / "forward.csv"

    assert (
        main(
            [
                "--csv",
                str(csv_path),
                "--pending",
                str(pending),
                "miss",
                "--release-date",
                "2026-11-06",
            ]
        )
        == 1
    )
    assert pending.exists()
    assert not csv_path.exists()

    # --force discards the verified pending capture and records the miss.
    assert (
        main(
            [
                "--csv",
                str(csv_path),
                "--pending",
                str(pending),
                "miss",
                "--release-date",
                "2026-11-06",
                "--force",
            ]
        )
        == 0
    )
    assert not pending.exists()
    with csv_path.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert rows[0]["event_type"] == MISSED_EVENT_TYPE


def test_status_strict_passes_outside_window(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from scripts import nfp_forward_capture as mod

    # 'now' is well before Dec 4, 2026 (e.g. Nov 19)
    outside_window = datetime(2026, 11, 19, 12, 0, 0, tzinfo=UTC)

    class FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return outside_window

    monkeypatch.setattr(mod, "datetime", FrozenDatetime)
    pending = tmp_path / "pending.json"
    csv_path = tmp_path / "forward.csv"
    assert (
        main(
            [
                "--csv",
                str(csv_path),
                "--pending",
                str(pending),
                "status",
                "--strict",
            ]
        )
        == 0
    )


def test_status_strict_fails_inside_window_without_pending(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    from scripts import nfp_forward_capture as mod

    # Release is 2026-12-04 13:30 UTC; test at 2026-12-03 16:00 UTC (inside 24h window)
    inside_window = datetime(2026, 12, 3, 16, 0, 0, tzinfo=UTC)

    class FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return inside_window

    monkeypatch.setattr(mod, "datetime", FrozenDatetime)
    pending = tmp_path / "pending.json"
    csv_path = tmp_path / "forward.csv"

    alerts = []
    monkeypatch.setattr(mod, "send_telegram_alert", lambda msg: alerts.append(msg))

    assert (
        main(
            [
                "--csv",
                str(csv_path),
                "--pending",
                str(pending),
                "status",
                "--strict",
            ]
        )
        == 1
    )
    # Outside 3h, no alert unless --alert is passed
    assert len(alerts) == 0

    assert (
        main(
            [
                "--csv",
                str(csv_path),
                "--pending",
                str(pending),
                "status",
                "--strict",
                "--alert",
            ]
        )
        == 1
    )
    assert len(alerts) == 1
    assert "NFP consensus capture missing" in alerts[0]


def test_status_strict_alerts_when_under_3h(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from scripts import nfp_forward_capture as mod

    # Release is 2026-12-04 13:30 UTC; test at 2026-12-04 11:30 UTC (2h before release)
    under_3h = datetime(2026, 12, 4, 11, 30, 0, tzinfo=UTC)

    class FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return under_3h

    monkeypatch.setattr(mod, "datetime", FrozenDatetime)
    pending = tmp_path / "pending.json"
    csv_path = tmp_path / "forward.csv"

    alerts = []
    monkeypatch.setattr(mod, "send_telegram_alert", lambda msg: alerts.append(msg))

    assert (
        main(
            [
                "--csv",
                str(csv_path),
                "--pending",
                str(pending),
                "status",
                "--strict",
            ]
        )
        == 1
    )
    assert len(alerts) == 1
    assert "Time remaining: 2.0h" in alerts[0]


def test_cron_skips_when_outside_window(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from scripts import nfp_forward_capture as mod

    outside_window = datetime(2026, 11, 19, 12, 0, 0, tzinfo=UTC)

    class FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return outside_window

    monkeypatch.setattr(mod, "datetime", FrozenDatetime)
    pending = tmp_path / "pending.json"
    csv_path = tmp_path / "forward.csv"

    assert (
        main(
            [
                "--csv",
                str(csv_path),
                "--pending",
                str(pending),
                "cron",
            ]
        )
        == 0
    )


def test_cron_captures_when_inside_window(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from scripts import nfp_forward_capture as mod

    html = (
        "<div>Latest Release Nov 06, 2026 Actual 165.00K Forecast 65.00K "
        "Previous 29.00K</div><div>Dec 04, 2026 (Nov) 13:30 140.00K 165.00K</div>"
    )
    monkeypatch.setattr(
        mod,
        "_submit_archive_ph",
        lambda url, **kwargs: ("https://archive.ph/dec2026", html),
    )

    def fake_get_retries(url, timeout, **kwargs):
        raise httpx.ConnectError("Wayback down")

    monkeypatch.setattr(mod, "_http_get_with_retries", fake_get_retries)

    inside_window = datetime(2026, 12, 3, 20, 0, 0, tzinfo=UTC)

    class FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return inside_window

    monkeypatch.setattr(mod, "datetime", FrozenDatetime)
    pending = tmp_path / "pending.json"
    csv_path = tmp_path / "forward.csv"

    assert (
        main(
            [
                "--csv",
                str(csv_path),
                "--pending",
                str(pending),
                "cron",
            ]
        )
        == 0
    )
    stashed = read_pending(pending)
    assert stashed is not None
    assert stashed.snapshot_url == "https://archive.ph/dec2026"

    # Second cron run sees existing verified capture and exits 0 immediately
    assert (
        main(
            [
                "--csv",
                str(csv_path),
                "--pending",
                str(pending),
                "cron",
            ]
        )
        == 0
    )


def test_pending_path_defaults_to_the_environment_override(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    override = tmp_path / "state" / "pending_capture.json"
    monkeypatch.setenv("NFP_PENDING_CAPTURE_PATH", str(override))
    args = build_parser().parse_args(["status"])
    assert args.pending == str(override)


def test_release_ts_utc_matches_the_gate_rows():
    assert release_ts_utc(date(2026, 12, 4)) == datetime(2026, 12, 4, 13, 30, tzinfo=UTC)
    assert release_ts_utc(date(2027, 1, 8)) == datetime(2027, 1, 8, 13, 30, tzinfo=UTC)
