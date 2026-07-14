from __future__ import annotations

import csv
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from scripts.probe_nfp_good_news_oos import (
    BLOCKED_ON_DATA,
    NO_PULSE,
    YES,
    NfpSurprise,
    Trade,
    compute_metrics,
    decide_verdict,
    main,
    run_probe,
)


def _surprise(index: int, *, z: float = 1.0) -> NfpSurprise:
    timestamp = datetime(2021, 1, 8, tzinfo=UTC) + timedelta(days=31 * index)
    return NfpSurprise(
        release_date_et=timestamp.date().isoformat(),
        release_ts=timestamp,
        actual=110.0,
        consensus=100.0,
        surprise=10.0,
        z=z,
        consensus_source="investing.com/Wayback",
        actual_source="bls.gov",
        source_snapshot_url="https://web.archive.org/web/20210101000000/https://www.investing.com/",
    )


def _trade(index: int, net_return_pct: float) -> Trade:
    timestamp = datetime(2021, 1, 8, tzinfo=UTC) + timedelta(days=index)
    return Trade(
        release_ts=timestamp.isoformat(),
        z=1.0,
        entry_ts=timestamp.isoformat(),
        exit_ts=(timestamp + timedelta(hours=24)).isoformat(),
        gross_return_pct=net_return_pct + 0.12,
        net_return_pct=net_return_pct,
    )


def test_metrics_compute_profit_factor_drawdown_and_leave_one_out() -> None:
    metrics = compute_metrics((_trade(0, 2.0), _trade(1, 1.0), _trade(2, -0.5)))

    assert metrics.trade_count == 3
    assert metrics.net_expectancy_pct == pytest.approx(2.5 / 3)
    assert metrics.profit_factor == pytest.approx(6.0)
    assert metrics.max_drawdown_pct == pytest.approx(0.5)
    assert metrics.leave_one_out_min_expectancy_pct == pytest.approx(0.25)
    assert metrics.all_leave_one_out_positive is True


def test_verdict_requires_pre_registered_gates() -> None:
    yes_metrics = compute_metrics((_trade(0, 1.0), _trade(1, 0.5), _trade(2, -0.2)))
    status, verdict, _ = decide_verdict(aligned_events=19, metrics=yes_metrics)
    assert status == "OK"
    assert verdict == YES

    outlier_metrics = compute_metrics((_trade(0, 10.0), _trade(1, -1.0), _trade(2, -1.0)))
    _, verdict, reasons = decide_verdict(aligned_events=19, metrics=outlier_metrics)
    assert verdict == NO_PULSE
    assert "result depends on at least one trade" in reasons


def test_verdict_rejects_drawdown_above_locked_limit() -> None:
    metrics = compute_metrics((_trade(0, 20.0), _trade(1, -11.0), _trade(2, 2.0)))

    _, verdict, reasons = decide_verdict(aligned_events=19, metrics=metrics)

    assert verdict == NO_PULSE
    assert "max drawdown > 10%" in reasons


def test_verdict_blocks_without_strict_majority_coverage() -> None:
    status, verdict, _ = decide_verdict(aligned_events=18, metrics=None)
    assert status == BLOCKED_ON_DATA
    assert verdict == BLOCKED_ON_DATA


def _write_surprises(path: Path, count: int) -> None:
    fields = [
        "event_type",
        "release_date_et",
        "release_ts_utc",
        "actual",
        "consensus",
        "surprise",
        "z",
        "consensus_source",
        "actual_source",
        "source_snapshot_url",
    ]
    surprises = [10.0 + item for item in range(count)]
    mean = sum(surprises) / len(surprises)
    stdev = (sum((value - mean) ** 2 for value in surprises) / len(surprises)) ** 0.5
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for item in range(count):
            timestamp = datetime(2021, 1, 8, 13, 30, tzinfo=UTC) + timedelta(days=31 * item)
            writer.writerow(
                {
                    "event_type": "NFP",
                    "release_date_et": timestamp.date().isoformat(),
                    "release_ts_utc": timestamp.isoformat(),
                    "actual": str(100.0 + surprises[item]),
                    "consensus": "100",
                    "surprise": str(surprises[item]),
                    "z": str(surprises[item] / stdev),
                    "consensus_source": "investing.com/Wayback",
                    "actual_source": "bls.gov",
                    "source_snapshot_url": "https://web.archive.org/web/20210101000000/https://www.investing.com/",
                }
            )


def _write_bars(path: Path) -> None:
    start = datetime(2021, 1, 1, tzinfo=UTC)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["time", "close_price"])
        writer.writeheader()
        for hour in range(24 * 650):
            writer.writerow(
                {
                    "time": (start + timedelta(hours=hour)).isoformat(),
                    "close_price": 100 + hour / 100,
                }
            )


def test_runner_emits_json_and_markdown_outputs(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    surprises = tmp_path / "surprises.csv"
    bars = tmp_path / "bars.csv"
    output_json = tmp_path / "report.json"
    output_markdown = tmp_path / "report.md"
    _write_surprises(surprises, 19)
    _write_bars(bars)

    exit_code = main(
        [
            "--surprises-csv",
            str(surprises),
            "--ohlcv-csv",
            str(bars),
            "--output-json",
            str(output_json),
            "--output-report",
            str(output_markdown),
            "--json",
        ]
    )

    assert exit_code in (0, 1)
    assert json.loads(output_json.read_text())["aligned_events"] == 19
    assert "NFP Good-News-Is-Good OOS Probe" in output_markdown.read_text()
    assert json.loads(capsys.readouterr().out)["surprise_rows"] == 19


def test_runner_rejects_duplicate_surprise_timestamp(tmp_path: Path) -> None:
    surprises = tmp_path / "surprises.csv"
    bars = tmp_path / "bars.csv"
    _write_surprises(surprises, 2)
    rows = list(csv.DictReader(surprises.open(encoding="utf-8")))
    rows[1]["release_ts_utc"] = rows[0]["release_ts_utc"]
    with surprises.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)
    _write_bars(bars)

    with pytest.raises(ValueError, match="duplicate release timestamp"):
        run_probe(surprises, bars)


def test_runner_rejects_nonstandardized_z_values(tmp_path: Path) -> None:
    surprises = tmp_path / "surprises.csv"
    bars = tmp_path / "bars.csv"
    _write_surprises(surprises, 2)
    rows = list(csv.DictReader(surprises.open(encoding="utf-8")))
    rows[0]["z"] = "0"
    with surprises.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)
    _write_bars(bars)

    with pytest.raises(ValueError, match="z values do not match"):
        run_probe(surprises, bars)


def test_missing_default_inputs_block_without_raising(tmp_path: Path) -> None:
    report = run_probe(tmp_path / "missing-surprises.csv", tmp_path / "missing-bars.csv")

    assert report.status == BLOCKED_ON_DATA
    assert report.verdict == BLOCKED_ON_DATA
    assert report.input_sha256[str(tmp_path / "missing-surprises.csv")] == "missing"
