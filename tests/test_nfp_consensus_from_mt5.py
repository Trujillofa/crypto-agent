"""Tests for scripts/nfp_consensus_from_mt5.py (fail-closed MT-2 PIT reader)."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

from scripts.nfp_consensus_from_mt5 import (
    LONG_MIN,
    MT5CalendarError,
    get_prerelease_consensus,
    main,
    parse_pit_line,
    parse_utc_offset,
    read_pit_records,
)

HEADLINE = "65"
PRIVATE = "55"
OFFSET = timedelta(hours=2)
RELEASE = date(2026, 12, 4)  # 08:30 ET = 13:30 UTC = 15:30 server (+02:00)


def server_epoch(utc: datetime) -> int:
    """Epoch seconds of the trade-server wall clock for a UTC instant."""
    return int((utc + OFFSET).timestamp())


def utc(day: int, hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 12, day, hour, minute, tzinfo=UTC)


RELEASE_UTC = utc(4, 13, 30)


def line(
    *,
    changed: datetime,
    forecast: object,
    event_id: object = HEADLINE,
    scheduled: datetime = RELEASE_UTC,
) -> str:
    return json.dumps(
        {
            "event_id": event_id,
            "time_server": server_epoch(scheduled),
            "snapshot_server_time": server_epoch(changed),
            "forecast_value": forecast,
        }
    )


def write_pit(path: Path, lines: list[str], *, trailing_newline: bool = True) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + ("\n" if trailing_newline else ""), encoding="utf-8")
    return path


def consensus_from(tmp_path: Path, lines: list[str]) -> float:
    write_pit(tmp_path / "brokerA" / "calendar.jsonl", lines)
    records = read_pit_records(tmp_path, event_id=HEADLINE, server_offset=OFFSET)
    return get_prerelease_consensus(records, RELEASE).forecast


def parse(raw: str, event_id: str = HEADLINE):
    return parse_pit_line(
        raw, source="t.jsonl:1", broker="brokerA", event_id=event_id, server_offset=OFFSET
    )


def test_parse_converts_server_time_and_scales_value():
    record = parse(line(changed=utc(4, 12), forecast=140_000_000))
    assert record is not None
    assert record.forecast == 140.0
    assert record.release_time_utc == RELEASE_UTC
    assert record.change_time_utc == utc(4, 12)


def test_get_prerelease_consensus_picks_latest_before_release(tmp_path: Path):
    lines = [
        line(changed=utc(3, 12), forecast=60_000_000),
        line(changed=utc(4, 10), forecast=65_000_000),
        line(changed=utc(4, 13, 35), forecast=70_000_000),
    ]
    assert consensus_from(tmp_path, lines) == 65.0


def test_get_prerelease_consensus_fails_closed_when_only_postrelease_available(tmp_path: Path):
    with pytest.raises(MT5CalendarError, match="no pre-release records"):
        consensus_from(tmp_path, [line(changed=utc(4, 13, 31), forecast=65_000_000)])


# (1) event identity: strict id match, no name matching or name default


def test_private_payrolls_event_never_selected(tmp_path: Path):
    lines = [
        line(changed=utc(4, 9), forecast=50_000_000),
        line(changed=utc(4, 12), forecast=45_000_000, event_id=PRIVATE),
    ]
    assert consensus_from(tmp_path, lines) == 50.0


def test_unknown_event_is_ignored_not_relabelled():
    raw = json.dumps(
        {
            "event_id": 999,
            "event_name": "Nonfarm Payrolls",
            "time_server": server_epoch(RELEASE_UTC),
            "snapshot_server_time": server_epoch(utc(4, 9)),
            "forecast_value": 1,
        }
    )
    assert parse(raw) is None


def test_cli_requires_event_id():
    with pytest.raises(SystemExit):
        main(["--release-date", "2026-12-04", "--server-utc-offset", "+02:00"])


# (2) trade-server time -> UTC


def test_freshest_change_within_server_offset_of_release_is_kept(tmp_path: Path):
    # 12:00 UTC is 14:00 server; reading server time as UTC would drop it as post-release.
    lines = [
        line(changed=utc(3, 9), forecast=60_000_000),
        line(changed=utc(4, 12), forecast=70_000_000),
    ]
    assert consensus_from(tmp_path, lines) == 70.0


def test_wrong_server_offset_fails_instead_of_shifting_cutoff(tmp_path: Path):
    write_pit(tmp_path / "b" / "c.jsonl", [line(changed=utc(4, 12), forecast=1)])
    records = read_pit_records(tmp_path, event_id=HEADLINE, server_offset=timedelta(0))
    with pytest.raises(MT5CalendarError, match="no MT5 calendar records scheduled"):
        get_prerelease_consensus(records, RELEASE)


@pytest.mark.parametrize("bad", ["2", "+2:00", "UTC+2", "+25:00", "+02:60"])
def test_parse_utc_offset_rejects_malformed(bad: str):
    with pytest.raises(MT5CalendarError, match="offset"):
        parse_utc_offset(bad)


def test_millisecond_epoch_rejected():
    raw = json.dumps(
        {
            "event_id": HEADLINE,
            "time_server": server_epoch(RELEASE_UTC) * 1000,
            "snapshot_server_time": server_epoch(utc(4, 9)),
            "forecast_value": 1,
        }
    )
    with pytest.raises(MT5CalendarError, match="epoch-seconds range"):
        parse(raw)


# (3)/(4)/(5) scaling, missing markers, forecast absent vs zero


def test_long_min_latest_change_is_hard_failure_not_stale_fallback(tmp_path: Path):
    lines = [
        line(changed=utc(1, 9), forecast=60_000_000),
        line(changed=utc(4, 9), forecast=LONG_MIN),
    ]
    with pytest.raises(MT5CalendarError, match="LONG_MIN.*refusing to fall back"):
        consensus_from(tmp_path, lines)


def test_earlier_long_min_then_valid_forecast_is_fine(tmp_path: Path):
    lines = [
        line(changed=utc(1, 9), forecast=LONG_MIN),
        line(changed=utc(4, 9), forecast=65_000_000),
    ]
    assert consensus_from(tmp_path, lines) == 65.0


@pytest.mark.parametrize("bad", [None, "n/a", "65", 65.0, True])
def test_non_integer_latest_forecast_is_hard_failure(tmp_path: Path, bad: object):
    lines = [
        line(changed=utc(1, 9), forecast=60_000_000),
        line(changed=utc(4, 9), forecast=bad),
    ]
    with pytest.raises(MT5CalendarError, match="forecast_value must be a JSON integer"):
        consensus_from(tmp_path, lines)


def test_nan_forecast_is_hard_failure(tmp_path: Path):
    nan_line = line(changed=utc(4, 9), forecast=float("nan"))
    assert '"forecast_value": NaN' in nan_line
    with pytest.raises(MT5CalendarError, match="forecast_value must be a JSON integer"):
        consensus_from(tmp_path, [nan_line])


def test_forecast_overflowing_long_is_hard_failure():
    with pytest.raises(MT5CalendarError, match="overflows"):
        parse(line(changed=utc(4, 9), forecast=2**63))


def test_zero_forecast_is_a_legitimate_consensus(tmp_path: Path):
    lines = [
        line(changed=utc(1, 9), forecast=60_000_000),
        line(changed=utc(4, 9), forecast=0),
    ]
    assert consensus_from(tmp_path, lines) == 0.0


def test_same_timestamp_conflict_within_broker_fails(tmp_path: Path):
    lines = [
        line(changed=utc(4, 9), forecast=60_000_000),
        line(changed=utc(4, 9), forecast=65_000_000),
    ]
    with pytest.raises(MT5CalendarError, match="conflicting records"):
        consensus_from(tmp_path, lines)


# (6) broker disagreement


def test_broker_disagreement_fails_with_both_values(tmp_path: Path):
    write_pit(tmp_path / "brokerA" / "c.jsonl", [line(changed=utc(4, 9), forecast=65_000_000)])
    write_pit(tmp_path / "brokerB" / "c.jsonl", [line(changed=utc(4, 11), forecast=70_000_000)])
    records = read_pit_records(tmp_path, event_id=HEADLINE, server_offset=OFFSET)
    with pytest.raises(MT5CalendarError, match=r"disagreement.*brokerA=65\.0.*brokerB=70\.0"):
        get_prerelease_consensus(records, RELEASE)


def test_brokers_agreeing_return_consensus(tmp_path: Path):
    write_pit(tmp_path / "brokerA" / "c.jsonl", [line(changed=utc(4, 9), forecast=65_000_000)])
    write_pit(tmp_path / "brokerB" / "c.jsonl", [line(changed=utc(4, 11), forecast=65_000_000)])
    records = read_pit_records(tmp_path, event_id=HEADLINE, server_offset=OFFSET)
    result = get_prerelease_consensus(records, RELEASE)
    assert result.forecast == 65.0
    assert [r.broker for r in result.sources] == ["brokerA", "brokerB"]


# (7) torn / partial lines


def test_torn_final_line_without_newline_fails(tmp_path: Path):
    path = write_pit(
        tmp_path / "b" / "c.jsonl",
        [line(changed=utc(4, 9), forecast=65_000_000)],
        trailing_newline=False,
    )
    with pytest.raises(MT5CalendarError, match="torn final line"):
        read_pit_records(tmp_path, event_id=HEADLINE, server_offset=OFFSET, pit_file=path)


def test_truncated_json_line_fails(tmp_path: Path):
    full = line(changed=utc(4, 9), forecast=65_000_000)
    with pytest.raises(MT5CalendarError, match="corrupt JSONL"):
        consensus_from(tmp_path, [full, full[: len(full) // 2]])


# (8) schema mismatch fails loudly, including on other events' lines


def test_missing_required_key_fails_even_for_other_event():
    raw = json.dumps({"event_id": PRIVATE, "time": 1, "forecast": 2})
    with pytest.raises(MT5CalendarError, match="schema mismatch"):
        parse(raw)


def test_legacy_iso_timestamps_are_schema_mismatch():
    raw = json.dumps(
        {
            "event_id": HEADLINE,
            "time_server": "2026-12-04T15:30:00",
            "snapshot_server_time": "2026-12-04T11:00:00",
            "forecast_value": 65_000_000,
        }
    )
    with pytest.raises(MT5CalendarError, match="integer epoch seconds"):
        parse(raw)


def test_non_object_line_fails():
    with pytest.raises(MT5CalendarError, match="expected a JSON object"):
        parse("[1, 2, 3]")


def test_empty_pit_dir_fails(tmp_path: Path):
    with pytest.raises(MT5CalendarError, match="no PIT JSONL files"):
        read_pit_records(tmp_path, event_id=HEADLINE, server_offset=OFFSET)


# CLI


def test_cli_json_output(tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    pit = write_pit(tmp_path / "brokerA" / "c.jsonl", [line(changed=utc(4, 8), forecast=0)])
    argv = ["--release-date", "2026-12-04", "--event-id", HEADLINE, "--server-utc-offset"]
    assert main([*argv, "+02:00", "--pit-file", str(pit), "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["forecast"] == 0.0
    assert data["sources"][0]["forecast_raw"] == 0
    assert data["sources"][0]["change_time_utc"] == "2026-12-04T08:00:00+00:00"


def test_cli_exits_nonzero_with_message_on_failure(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
):
    pit = write_pit(tmp_path / "brokerA" / "c.jsonl", [line(changed=utc(4, 8), forecast=LONG_MIN)])
    argv = ["--release-date", "2026-12-04", "--event-id", HEADLINE, "--server-utc-offset"]
    assert main([*argv, "+02:00", "--pit-file", str(pit)]) == 1
    assert "LONG_MIN" in capsys.readouterr().err
