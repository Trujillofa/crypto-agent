from __future__ import annotations

import argparse
from pathlib import Path

from scripts.run_autoresearch import (
    GATE_PROFILES,
    RESULTS_FIELDNAMES,
    RunArtifacts,
    _append_results_row,
    _build_autopilot_command,
    _deep_merge,
    _extract_output_path,
    _generate_run_id,
    _read_best_score,
    _resolve_gates,
    compute_score,
    decide_status,
)


def test_deep_merge_overrides_nested_values() -> None:
    base = {
        "strategy": {
            "aggregator": {"buy_threshold": 0.8, "sell_threshold": -0.6},
            "global_trend_filter_enabled": True,
        }
    }
    overlay = {
        "strategy": {
            "aggregator": {"buy_threshold": 1.0},
            "global_trend_filter_enabled": False,
        }
    }

    merged = _deep_merge(base, overlay)

    assert merged["strategy"]["aggregator"]["buy_threshold"] == 1.0
    assert merged["strategy"]["aggregator"]["sell_threshold"] == -0.6
    assert merged["strategy"]["global_trend_filter_enabled"] is False


def test_extract_output_path_reads_autopilot_stdout() -> None:
    stdout = "Report: research/archive/report.md\nJSON: research/archive/result.json\n"

    path = _extract_output_path(stdout, "JSON")

    assert path == Path("research/archive/result.json")


def test_compute_score_rewards_passing_candidates() -> None:
    summary = {
        "passes_gates": True,
        "wfo_total_return_pct": 12.5,
        "wfo_mean_sharpe": 1.2,
        "max_drawdown_pct": 8.0,
    }
    gates = {}

    score = compute_score(summary, gates)

    assert score == 101254.0


def test_compute_score_penalizes_failed_gates() -> None:
    summary = {
        "passes_gates": False,
        "max_drawdown_pct": 18.0,
        "bootstrap_p_loss_pct": 40.0,
        "profit_concentration_pct": 70.0,
        "total_trades": 12,
        "wfo_total_trades": 12,
        "wfo_mean_sharpe": 0.1,
        "wfo_total_return_pct": -3.0,
    }
    gates = {
        "max_drawdown_pct": 10.0,
        "max_bootstrap_p_loss_pct": 25.0,
        "max_profit_concentration_pct": 50.0,
        "min_trades": 0,
        "min_wfo_trades": 20,
        "min_wfo_sharpe": 0.5,
        "min_oos_return_pct": 0.0,
    }

    score = compute_score(summary, gates)

    assert score == -146.0


def test_decide_status_marks_only_improvements_as_keep() -> None:
    assert decide_status("completed", 10.0, None) == "keep"
    assert decide_status("completed", 11.0, 10.0) == "keep"
    assert decide_status("completed", 10.0, 10.0) == "discard"
    assert decide_status("timeout", None, 10.0) == "timeout"
    assert decide_status("crash", None, 10.0) == "crash"


def test_results_log_header_is_not_duplicated(tmp_path: Path) -> None:
    results_path = tmp_path / "results.tsv"
    first_row = dict.fromkeys(RESULTS_FIELDNAMES, "")
    second_row = dict.fromkeys(RESULTS_FIELDNAMES, "")
    first_row["run_id"] = "run-1"
    second_row["run_id"] = "run-2"
    first_row["score"] = "10.0"
    second_row["score"] = "9.0"
    first_row["status"] = "keep"
    second_row["status"] = "discard"

    _append_results_row(results_path, first_row)
    _append_results_row(results_path, second_row)

    lines = results_path.read_text(encoding="utf-8").splitlines()
    assert lines[0].startswith("timestamp\trun_id\tcommit")
    assert len(lines) == 3


def test_read_best_score_skips_crashes(tmp_path: Path) -> None:
    results_path = tmp_path / "results.tsv"
    rows = [
        {"score": "10.0", "status": "keep"},
        {"score": "50.0", "status": "crash"},
        {"score": "11.5", "status": "discard"},
    ]
    with results_path.open("w", encoding="utf-8") as handle:
        handle.write("score\tstatus\n")
        for row in rows:
            handle.write(f"{row['score']}\t{row['status']}\n")

    best = _read_best_score(results_path)

    assert best == 11.5


def test_generate_run_id_is_unique_and_high_resolution() -> None:
    first = _generate_run_id()
    second = _generate_run_id()

    assert first != second
    assert len(first.split("-")) >= 4


def test_sparse_trend_gate_profile_resolves_expected_values() -> None:
    args = argparse.Namespace(
        gate_profile="sparse_trend_3_2",
        min_trades=None,
        min_wfo_trades=None,
        min_wfo_sharpe=None,
        max_drawdown_pct=None,
        max_bootstrap_p_loss_pct=None,
        min_oos_return_pct=None,
        max_profit_concentration_pct=None,
    )

    resolved = _resolve_gates(args)

    assert resolved == GATE_PROFILES["sparse_trend_3_2"]


def test_explicit_gate_flags_override_profile_defaults() -> None:
    args = argparse.Namespace(
        gate_profile="sparse_trend_3_2",
        min_trades=1,
        min_wfo_trades=6,
        min_wfo_sharpe=0.4,
        max_drawdown_pct=8.0,
        max_bootstrap_p_loss_pct=20.0,
        min_oos_return_pct=2.0,
        max_profit_concentration_pct=55.0,
    )

    resolved = _resolve_gates(args)

    assert resolved == {
        "min_trades": 1,
        "min_wfo_trades": 6,
        "min_wfo_sharpe": 0.4,
        "max_drawdown_pct": 8.0,
        "max_bootstrap_p_loss_pct": 20.0,
        "min_oos_return_pct": 2.0,
        "max_profit_concentration_pct": 55.0,
    }


def test_build_autopilot_command_forwards_replay_sentiment_flags(tmp_path: Path) -> None:
    args = argparse.Namespace(
        gate_profile="standard",
        train_months=3,
        test_months=2,
        bootstrap=500,
        seed=42,
        initial_capital=10000.0,
        min_trades=None,
        min_wfo_trades=None,
        min_wfo_sharpe=None,
        max_drawdown_pct=None,
        max_bootstrap_p_loss_pct=None,
        min_oos_return_pct=None,
        max_profit_concentration_pct=None,
        symbol="BTCUSDT",
        timeframe="1h",
        start=None,
        end=None,
        disable_trend_filter=False,
        replay_sentiment_log="data/event_log_sentiment-macro-bot.jsonl",
        replay_sentiment_max_age_hours=24.0,
    )
    artifacts = RunArtifacts(
        output_dir=tmp_path,
        archive_dir=tmp_path / "archive",
        resolved_dir=tmp_path / "resolved",
        run_log_path=tmp_path / "run.log",
        last_result_path=tmp_path / "last_result.json",
        results_path=tmp_path / "results.tsv",
        autopilot_prefix=tmp_path / "archive" / "experiment-autopilot-run",
        resolved_config_path=tmp_path / "resolved" / "settings-run.yaml",
    )

    command = _build_autopilot_command(args, artifacts)

    assert "--replay-sentiment-log" in command
    replay_index = command.index("--replay-sentiment-log")
    assert command[replay_index + 1] == "data/event_log_sentiment-macro-bot.jsonl"
    assert "--replay-sentiment-max-age-hours" in command
    max_age_index = command.index("--replay-sentiment-max-age-hours")
    assert command[max_age_index + 1] == "24.0"
