"""Tests for scripts/nfp_consensus_from_mt5.py."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from scripts.nfp_consensus_from_mt5 import (
    MT5CalendarError,
    get_prerelease_consensus,
    main,
    parse_pit_record,
    read_pit_records,
)


def test_parse_pit_record_extracts_nfp_fields():
    line = json.dumps(
        {
            "event_name": "Nonfarm Payrolls",
            "country": "US",
            "time": "2026-11-06T13:30:00Z",
            "change_time": "2026-11-06T07:00:00Z",
            "forecast": 65.0,
        }
    )
    record = parse_pit_record(line, source_file="test.jsonl:1")
    assert record is not None
    assert record.forecast == 65.0
    assert record.release_time_utc == datetime(2026, 11, 6, 13, 30, tzinfo=UTC)
    assert record.change_time_utc == datetime(2026, 11, 6, 7, 0, tzinfo=UTC)


def test_parse_pit_record_ignores_non_nfp_events():
    line = json.dumps(
        {
            "event_name": "CPI m/m",
            "country": "US",
            "time": "2026-11-10T13:30:00Z",
            "change_time": "2026-11-10T08:00:00Z",
            "forecast": 0.2,
        }
    )
    assert parse_pit_record(line) is None


def test_get_prerelease_consensus_picks_latest_before_release():
    records = [
        parse_pit_record(
            json.dumps(
                {
                    "event_name": "US Nonfarm Payrolls",
                    "time": "2026-11-06T13:30:00Z",
                    "change_time": "2026-11-05T12:00:00Z",
                    "forecast": 60.0,
                }
            )
        ),
        parse_pit_record(
            json.dumps(
                {
                    "event_name": "US Nonfarm Payrolls",
                    "time": "2026-11-06T13:30:00Z",
                    "change_time": "2026-11-06T10:00:00Z",
                    "forecast": 65.0,
                }
            )
        ),
        parse_pit_record(
            json.dumps(
                {
                    "event_name": "US Nonfarm Payrolls",
                    "time": "2026-11-06T13:30:00Z",
                    "change_time": "2026-11-06T13:35:00Z",
                    "forecast": 70.0,
                }
            )
        ),
    ]
    latest = get_prerelease_consensus([r for r in records if r is not None], date(2026, 11, 6))
    assert latest.forecast == 65.0
    assert latest.change_time_utc == datetime(2026, 11, 6, 10, 0, tzinfo=UTC)


def test_get_prerelease_consensus_fails_closed_when_only_postrelease_available():
    records = [
        parse_pit_record(
            json.dumps(
                {
                    "event_name": "US Nonfarm Payrolls",
                    "time": "2026-11-06T13:30:00Z",
                    "change_time": "2026-11-06T13:31:00Z",
                    "forecast": 65.0,
                }
            )
        )
    ]
    with pytest.raises(MT5CalendarError, match="no pre-release records found"):
        get_prerelease_consensus([r for r in records if r is not None], date(2026, 11, 6))


def test_cli_execution_with_pit_file(tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    pit_file = tmp_path / "pit.jsonl"
    pit_file.write_text(
        json.dumps(
            {
                "event_name": "Nonfarm Payrolls",
                "country": "US",
                "time": "2026-12-04T13:30:00Z",
                "change_time": "2026-12-04T08:00:00Z",
                "forecast": 140.0,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    assert (
        main(
            [
                "--release-date",
                "2026-12-04",
                "--pit-file",
                str(pit_file),
                "--json",
            ]
        )
        == 0
    )
    captured = capsys.readouterr()
    data = json.loads(captured.out)
    assert data["forecast"] == 140.0
    assert data["release_date"] == "2026-12-04"


def test_read_pit_records_scans_directory(tmp_path: Path):
    broker_dir = tmp_path / "broker1"
    broker_dir.mkdir()
    (broker_dir / "calendar.jsonl").write_text(
        json.dumps(
            {
                "event_name": "Nonfarm Payrolls",
                "time": "2026-12-04T13:30:00Z",
                "change_time": "2026-12-04T08:00:00Z",
                "forecast": 140.0,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    records = read_pit_records(pit_dir=tmp_path)
    assert len(records) == 1
    assert records[0].forecast == 140.0
