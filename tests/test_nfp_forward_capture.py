"""Tests for the NFP forward-gate capture tool.

No network access: every test exercises pure date/CSV/verification logic.
"""

from __future__ import annotations

import csv
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
    build_row,
    first_friday,
    next_scheduled_release,
    page_mentions_date,
    previous_scheduled_release,
    release_timestamp_utc,
    resolve_snapshot_timestamp,
    snapshot_timestamp,
    verify_snapshot_is_point_in_time,
)


def test_release_timestamp_is_dst_aware():
    """08:30 ET is 12:30 UTC in summer and 13:30 UTC in winter."""
    assert release_timestamp_utc(date(2026, 8, 7)) == datetime(2026, 8, 7, 12, 30, tzinfo=UTC)
    assert release_timestamp_utc(date(2026, 12, 4)) == datetime(2026, 12, 4, 13, 30, tzinfo=UTC)


def test_release_timestamp_matches_committed_oos_rows():
    """Recompute two rows from the committed OOS table."""
    assert release_timestamp_utc(date(2021, 1, 8)) == datetime(2021, 1, 8, 13, 30, tzinfo=UTC)
    assert release_timestamp_utc(date(2021, 4, 2)) == datetime(2021, 4, 2, 12, 30, tzinfo=UTC)


def test_first_friday_and_previous_release():
    assert first_friday(2026, 8) == date(2026, 8, 7)
    assert first_friday(2026, 5) == date(2026, 5, 1)
    assert previous_scheduled_release(date(2026, 8, 7)) == date(2026, 7, 3)
    assert previous_scheduled_release(date(2026, 1, 2)) == date(2025, 12, 5)


def test_next_scheduled_release_rolls_after_the_print():
    before = datetime(2026, 8, 6, 12, 0, tzinfo=UTC)
    after = datetime(2026, 8, 7, 13, 0, tzinfo=UTC)
    assert next_scheduled_release(before) == date(2026, 8, 7)
    assert next_scheduled_release(after) == date(2026, 9, 4)


def test_next_scheduled_release_crosses_year_boundary():
    assert next_scheduled_release(datetime(2026, 12, 5, 0, 0, tzinfo=UTC)) == date(2027, 1, 1)


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
    assert set(row) == set(CSV_COLUMNS)


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
