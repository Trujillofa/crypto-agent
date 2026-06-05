from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pytest

from scripts.autoresearch_loop import (
    _parse_families,
    _run_command,
    build_run_command,
    generate_candidate,
)
from scripts.run_autoresearch import (
    GATE_PROFILES,
    RESULTS_FIELDNAMES,
    RunArtifacts,
    _append_results_row,
    _build_autopilot_command,
    _deep_merge,
    _eligible_for_bootstrap_1000,
    _extract_output_path,
    _generate_run_id,
    _normalize_subprocess_output,
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


def test_normalize_subprocess_output_decodes_timeout_bytes() -> None:
    assert _normalize_subprocess_output(b"partial stdout\n") == "partial stdout\n"
    assert _normalize_subprocess_output("stderr\n") == "stderr\n"
    assert _normalize_subprocess_output(None) == ""


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


def test_probe_1h_gate_profile_resolves_expected_values() -> None:
    args = argparse.Namespace(
        gate_profile="probe_1h",
        min_trades=None,
        min_wfo_trades=None,
        min_wfo_sharpe=None,
        max_drawdown_pct=None,
        max_bootstrap_p_loss_pct=None,
        min_oos_return_pct=None,
        max_profit_concentration_pct=None,
    )

    resolved = _resolve_gates(args)

    assert resolved == {
        "min_trades": 0,
        "min_wfo_trades": 15,
        "min_wfo_sharpe": 0.5,
        "max_drawdown_pct": 10.0,
        "max_bootstrap_p_loss_pct": 25.0,
        "min_oos_return_pct": 0.0,
        "max_profit_concentration_pct": 50.0,
    }


def test_promotion_candidate_gate_profile_is_stricter_than_standard() -> None:
    promo = GATE_PROFILES["promotion_candidate"]
    standard = GATE_PROFILES["standard"]

    assert promo["min_oos_return_pct"] > standard["min_oos_return_pct"]
    assert promo["max_drawdown_pct"] < standard["max_drawdown_pct"]
    assert promo["max_bootstrap_p_loss_pct"] < standard["max_bootstrap_p_loss_pct"]
    assert promo["max_profit_concentration_pct"] < standard["max_profit_concentration_pct"]


def test_eligible_for_bootstrap_1000_requires_promotion_candidate_at_b100() -> None:
    strong = {
        "symbol": "BNBUSDT",
        "timeframe": "1h",
        "start": "2024-01-01T00:00:00+00:00",
        "end": "2026-01-01T00:00:00+00:00",
        "total_trades": 30,
        "win_rate": 55.0,
        "total_return_pct": 12.0,
        "max_drawdown_pct": 6.0,
        "sharpe_ratio": 0.8,
        "wfo_windows": 7,
        "wfo_total_trades": 24,
        "wfo_mean_sharpe": 0.6,
        "wfo_total_return_pct": 3.5,
        "bootstrap_p_loss_pct": 12.0,
        "profit_concentration_pct": 30.0,
        "passes_gates": True,
        "failure_reasons": [],
    }
    weak = dict(strong)
    weak["wfo_total_return_pct"] = 0.5
    weak["bootstrap_p_loss_pct"] = 30.0

    ok, failures = _eligible_for_bootstrap_1000(strong, bootstrap=100)
    assert ok is True
    assert failures == []

    ok_weak, weak_failures = _eligible_for_bootstrap_1000(weak, bootstrap=100)
    assert ok_weak is False
    assert weak_failures

    ok_b1000, b1000_failures = _eligible_for_bootstrap_1000(strong, bootstrap=1000)
    assert ok_b1000 is False
    assert b1000_failures == ["bootstrap_gt_100"]


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


def test_autoresearch_loop_candidate_generation_is_reproducible() -> None:
    first = generate_candidate(1, seed=123)
    second = generate_candidate(1, seed=123)

    assert first == second
    assert first.overlay
    assert first.description


def test_autoresearch_loop_candidate_ranges_stay_bounded() -> None:
    candidates = [generate_candidate(index, seed=42) for index in range(1, 30)]

    for candidate in candidates:
        if candidate.family == "aggregator_thresholds":
            aggregator = candidate.overlay["strategy"]["aggregator"]
            assert 0.65 <= aggregator["buy_threshold"] <= 1.10
            assert 0.55 <= aggregator["buy_threshold_uptrend"] <= 1.10
            assert -0.90 <= aggregator["sell_threshold"] <= -0.50
        elif candidate.family == "risk_atr_exits":
            execution = candidate.overlay["trading_execution"]
            assert 1.4 <= execution["sl_atr_multiplier"] <= 2.8
            assert 2.2 <= execution["tp_atr_multiplier"] <= 5.0
            assert 1.0 <= execution["trailing_activate_atr"] <= 2.4
            assert 0.6 <= execution["trailing_offset_atr"] <= 1.5
        elif candidate.family == "sentiment_filters":
            config = candidate.overlay["strategy"]["strategies"][0]["config"]
            assert 25.0 <= config["sentiment_gate_threshold"] <= 55.0
            assert 10.0 <= config["sentiment_panic_threshold"] < config["sentiment_gate_threshold"]
            assert config["sentiment_boost_threshold"] > config["sentiment_gate_threshold"]
        elif candidate.family == "indicator_thresholds":
            strategies = candidate.overlay["strategy"]["strategies"]
            bollinger = strategies[0]["config"]
            macd = strategies[1]["config"]
            assert 0.002 <= bollinger["band_distance_threshold"] <= 0.012
            assert bollinger["rsi_oversold"] in {30, 35, 40}
            assert bollinger["rsi_overbought"] in {60, 65, 70}
            assert 0.0 <= macd["min_histogram_threshold"] <= 0.0005
            assert 0.002 <= macd["atr_min_pct"] <= 0.010
        elif candidate.family == "combined_focus":
            strategy = candidate.overlay["strategy"]
            aggregator = strategy["aggregator"]
            strategies = strategy["strategies"]
            assert len(strategies) == 5
            assert [entry["name"] for entry in strategies] == [
                "rsi_reversal",
                "macd_histogram",
                "bollinger_bounce",
                "cci_breakout",
                "vwap_reversion",
            ]
            assert 1.04 <= aggregator["buy_threshold"] <= 1.16
            assert 0.98 <= aggregator["buy_threshold_uptrend"] <= 1.16
            assert -0.92 <= aggregator["sell_threshold"] <= -0.72
        elif candidate.family == "near_pass_expansion":
            strategy = candidate.overlay["strategy"]
            aggregator = strategy["aggregator"]
            assert len(strategy["strategies"]) == 5
            assert 1.00 <= aggregator["buy_threshold"] <= 1.10
            assert 0.94 <= aggregator["buy_threshold_uptrend"] <= 1.10
            assert -0.90 <= aggregator["sell_threshold"] <= -0.74
        elif candidate.family == "standard_gate_bridge":
            strategy = candidate.overlay["strategy"]
            aggregator = strategy["aggregator"]
            assert len(strategy["strategies"]) == 5
            assert 1.04 <= aggregator["buy_threshold"] <= 1.09
            assert 1.01 <= aggregator["buy_threshold_uptrend"] <= 1.09
            assert -0.78 <= aggregator["sell_threshold"] <= -0.71
        elif candidate.family == "near_miss_trade_lift":
            strategy = candidate.overlay["strategy"]
            aggregator = strategy["aggregator"]
            assert len(strategy["strategies"]) == 5
            assert 1.05 <= aggregator["buy_threshold"] <= 1.10
            assert 1.03 <= aggregator["buy_threshold_uptrend"] <= 1.10
            assert -0.80 <= aggregator["sell_threshold"] <= -0.74
        elif candidate.family == "trend_pullback_overlay":
            strategy = candidate.overlay["strategy"]
            aggregator = strategy["aggregator"]
            assert len(strategy["strategies"]) == 6
            assert strategy["strategies"][-1]["name"] == "trend_pullback"
            assert 1.10 <= aggregator["buy_threshold"] <= 1.35
            assert 0.95 <= aggregator["buy_threshold_uptrend"] <= 1.15
            assert -0.86 <= aggregator["sell_threshold"] <= -0.74
        elif candidate.family in {
            "breakout_retest_overlay",
            "volatility_squeeze_overlay",
            "funding_extreme_overlay",
            "regime_gated_pullback_overlay",
            "breakout_retest_bridge",
            "regime_gated_pullback_bridge",
        }:
            strategy = candidate.overlay["strategy"]
            assert len(strategy["strategies"]) == 6
            assert 0.80 <= strategy["aggregator"]["buy_threshold"] <= 1.40
        elif candidate.family in {
            "trend_pullback_standalone",
            "breakout_retest_standalone",
            "volatility_squeeze_standalone",
            "mtf_breakout_standalone",
            "range_reversion_bounded",
            "funding_primary_standalone",
            "funding_normalization_standalone",
        }:
            strategy = candidate.overlay["strategy"]
            assert len(strategy["strategies"]) == 1
            assert 0.40 <= strategy["aggregator"]["buy_threshold"] <= 0.75
        else:
            raise AssertionError(f"unexpected candidate family: {candidate.family}")


def test_autoresearch_loop_parses_family_filter() -> None:
    families = _parse_families("aggregator_thresholds, indicator_thresholds")

    assert families == ("aggregator_thresholds", "indicator_thresholds")


def test_autoresearch_loop_rejects_unknown_family() -> None:
    with pytest.raises(ValueError, match="Unsupported candidate families"):
        _parse_families("aggregator_thresholds,nope")


def test_autoresearch_loop_focused_aggregator_candidates_stay_near_best_region() -> None:
    candidates = [
        generate_candidate(
            index,
            seed=42,
            families=("aggregator_thresholds",),
            aggregator_focus=True,
        )
        for index in range(1, 30)
    ]

    for candidate in candidates:
        aggregator = candidate.overlay["strategy"]["aggregator"]
        per_symbol = candidate.overlay["strategy"]["per_symbol_aggregator_config"]["SOLUSDT"]
        assert 0.98 <= aggregator["buy_threshold"] <= 1.14
        assert 0.90 <= aggregator["buy_threshold_uptrend"] <= 1.14
        assert -0.92 <= aggregator["sell_threshold"] <= -0.72
        assert per_symbol["min_agreement"] == 1


def test_autoresearch_loop_combined_focus_keeps_full_strategy_stack() -> None:
    candidate = generate_candidate(1, seed=42, families=("combined_focus",))

    assert candidate.family == "combined_focus"
    assert set(candidate.overlay) == {"trading_execution", "strategy"}
    strategy = candidate.overlay["strategy"]
    assert [entry["name"] for entry in strategy["strategies"]] == [
        "rsi_reversal",
        "macd_histogram",
        "bollinger_bounce",
        "cci_breakout",
        "vwap_reversion",
    ]
    assert strategy["per_symbol_aggregator_config"]["SOLUSDT"]["min_agreement"] == 1
    assert "sl_atr_multiplier" in candidate.overlay["trading_execution"]


def test_autoresearch_loop_near_pass_expansion_targets_more_trades_region() -> None:
    candidate = generate_candidate(1, seed=42, families=("near_pass_expansion",))

    assert candidate.family == "near_pass_expansion"
    strategy = candidate.overlay["strategy"]
    execution = candidate.overlay["trading_execution"]
    bollinger = {entry["name"]: entry["config"] for entry in strategy["strategies"]}[
        "bollinger_bounce"
    ]
    macd = {entry["name"]: entry["config"] for entry in strategy["strategies"]}["macd_histogram"]

    assert strategy["per_symbol_aggregator_config"]["SOLUSDT"]["min_agreement"] == 1
    assert strategy["per_symbol_aggregator_config"]["SOLUSDT"]["sell_min_agreement"] == 2
    assert 0.008 <= bollinger["band_distance_threshold"] <= 0.014
    assert 0.0035 <= macd["atr_min_pct"] <= 0.007
    assert 1.45 <= execution["sl_atr_multiplier"] <= 1.85
    assert 2.6 <= execution["tp_atr_multiplier"] <= 3.4


def test_autoresearch_loop_standard_gate_bridge_targets_near_miss_region() -> None:
    candidate = generate_candidate(1, seed=42, families=("standard_gate_bridge",))

    assert candidate.family == "standard_gate_bridge"
    strategy = candidate.overlay["strategy"]
    execution = candidate.overlay["trading_execution"]
    strategies_by_name = {entry["name"]: entry["config"] for entry in strategy["strategies"]}
    aggregator = strategy["aggregator"]
    per_symbol = strategy["per_symbol_aggregator_config"]["SOLUSDT"]

    assert 1.04 <= aggregator["buy_threshold"] <= 1.09
    assert 1.01 <= aggregator["buy_threshold_uptrend"] <= aggregator["buy_threshold"]
    assert -0.78 <= aggregator["sell_threshold"] <= -0.71
    assert per_symbol["min_agreement"] == 1
    assert 0.0045 <= strategies_by_name["bollinger_bounce"]["band_distance_threshold"] <= 0.0065
    assert 0.0047 <= strategies_by_name["macd_histogram"]["atr_min_pct"] <= 0.0089
    assert 0.0108 <= strategies_by_name["cci_breakout"]["atr_min_pct"] <= 0.0124
    assert 2.05 <= execution["sl_atr_multiplier"] <= 2.35
    assert 3.20 <= execution["tp_atr_multiplier"] <= 4.75


def test_autoresearch_loop_near_miss_trade_lift_stays_close_to_best_candidate() -> None:
    candidate = generate_candidate(1, seed=42, families=("near_miss_trade_lift",))

    assert candidate.family == "near_miss_trade_lift"
    strategy = candidate.overlay["strategy"]
    execution = candidate.overlay["trading_execution"]
    strategies_by_name = {entry["name"]: entry["config"] for entry in strategy["strategies"]}
    aggregator = strategy["aggregator"]
    per_symbol = strategy["per_symbol_aggregator_config"]["SOLUSDT"]

    assert 1.05 <= aggregator["buy_threshold"] <= 1.10
    assert 1.03 <= aggregator["buy_threshold_uptrend"] <= aggregator["buy_threshold"]
    assert -0.80 <= aggregator["sell_threshold"] <= -0.74
    assert per_symbol["min_agreement"] == 1
    assert 0.0045 <= strategies_by_name["bollinger_bounce"]["band_distance_threshold"] <= 0.0058
    assert 0.0075 <= strategies_by_name["macd_histogram"]["atr_min_pct"] <= 0.0092
    assert 0.0105 <= strategies_by_name["cci_breakout"]["atr_min_pct"] <= 0.0118
    assert 1.85 <= strategies_by_name["vwap_reversion"]["vwap_atr_multiplier"] <= 2.12
    assert 2.15 <= execution["sl_atr_multiplier"] <= 2.35
    assert 3.70 <= execution["tp_atr_multiplier"] <= 4.20


def test_autoresearch_loop_trend_pullback_overlay_adds_complementary_signal() -> None:
    candidate = generate_candidate(1, seed=42, families=("trend_pullback_overlay",))

    assert candidate.family == "trend_pullback_overlay"
    strategy = candidate.overlay["strategy"]
    execution = candidate.overlay["trading_execution"]
    strategy_names = [entry["name"] for entry in strategy["strategies"]]
    pullback_config = strategy["strategies"][-1]["config"]
    aggregator = strategy["aggregator"]
    per_symbol = strategy["per_symbol_aggregator_config"]["SOLUSDT"]

    assert strategy_names == [
        "rsi_reversal",
        "macd_histogram",
        "bollinger_bounce",
        "cci_breakout",
        "vwap_reversion",
        "trend_pullback",
    ]
    assert 1.10 <= aggregator["buy_threshold"] <= 1.35
    assert 0.95 <= aggregator["buy_threshold_uptrend"] <= aggregator["buy_threshold"]
    assert 0.0 <= aggregator["min_confidence"] <= 0.45
    assert per_symbol["min_agreement"] == 1
    assert pullback_config["rsi_reclaim_level"] in {48, 50, 52}
    assert 0.006 <= pullback_config["min_trend_strength_pct"] <= 0.012
    assert 0.015 <= pullback_config["max_pullback_distance_pct"] <= 0.030
    assert 0.015 <= pullback_config["vwap_pullback_distance_pct"] <= 0.035
    assert 2.10 <= execution["sl_atr_multiplier"] <= 2.45
    assert 3.60 <= execution["tp_atr_multiplier"] <= 4.40


def test_autoresearch_loop_breakout_retest_overlay_adds_specialist() -> None:
    candidate = generate_candidate(
        3,
        seed=42,
        symbol="ETHUSDT",
        families=("breakout_retest_overlay",),
    )

    assert candidate.family == "breakout_retest_overlay"
    strategy = candidate.overlay["strategy"]
    names = [entry["name"] for entry in strategy["strategies"]]
    assert names[-1] == "breakout_retest"
    assert "ETHUSDT" in strategy["per_symbol_aggregator_config"]
    assert not strategy["aggregator"].get("btc_regime_filter_enabled")


def test_autoresearch_loop_volatility_squeeze_overlay_adds_specialist() -> None:
    candidate = generate_candidate(
        4,
        seed=42,
        symbol="SOLUSDT",
        families=("volatility_squeeze_overlay",),
    )

    assert candidate.family == "volatility_squeeze_overlay"
    names = [entry["name"] for entry in candidate.overlay["strategy"]["strategies"]]
    assert names[-1] == "volatility_squeeze"
    squeeze = candidate.overlay["strategy"]["strategies"][-1]["config"]
    assert 0.15 <= squeeze["squeeze_percentile"] <= 0.25


def test_autoresearch_loop_funding_extreme_overlay_adds_specialist() -> None:
    candidate = generate_candidate(
        5,
        seed=42,
        symbol="BNBUSDT",
        families=("funding_extreme_overlay",),
    )

    assert candidate.family == "funding_extreme_overlay"
    names = [entry["name"] for entry in candidate.overlay["strategy"]["strategies"]]
    assert names[-1] == "funding_rate"
    assert "BNBUSDT" in candidate.overlay["strategy"]["per_symbol_aggregator_config"]


def test_autoresearch_loop_regime_gated_pullback_enables_btc_filter() -> None:
    candidate = generate_candidate(
        6,
        seed=42,
        symbol="AVAXUSDT",
        families=("regime_gated_pullback_overlay",),
    )

    assert candidate.family == "regime_gated_pullback_overlay"
    strategy = candidate.overlay["strategy"]
    assert strategy["aggregator"]["btc_regime_filter_enabled"] is True
    assert strategy["strategies"][-1]["name"] == "trend_pullback"
    pullback = strategy["strategies"][-1]["config"]
    assert pullback["min_atr_pct"] >= 0.008


def test_parse_families_accepts_wave2_overlay_names() -> None:
    families = _parse_families(
        "breakout_retest_overlay,volatility_squeeze_overlay,funding_extreme_overlay,"
        "regime_gated_pullback_overlay"
    )
    assert families == (
        "breakout_retest_overlay",
        "volatility_squeeze_overlay",
        "funding_extreme_overlay",
        "regime_gated_pullback_overlay",
    )


def test_autoresearch_loop_trend_pullback_standalone_single_strategy() -> None:
    candidate = generate_candidate(
        7,
        seed=42,
        symbol="SOLUSDT",
        families=("trend_pullback_standalone",),
    )

    assert candidate.family == "trend_pullback_standalone"
    strategies = candidate.overlay["strategy"]["strategies"]
    assert len(strategies) == 1
    assert strategies[0]["name"] == "trend_pullback"
    assert "SOLUSDT" in candidate.overlay["strategy"]["per_symbol_aggregator_config"]


def test_autoresearch_loop_mtf_breakout_standalone_sets_1h_timeframe() -> None:
    candidate = generate_candidate(
        8,
        seed=42,
        symbol="SOLUSDT",
        families=("mtf_breakout_standalone",),
    )

    assert candidate.family == "mtf_breakout_standalone"
    assert candidate.overlay["trading"]["timeframe"] == "1h"
    assert candidate.overlay["strategy"]["strategies"][0]["name"] == "mtf_breakout"


def test_autoresearch_loop_range_reversion_bounded_has_time_stop() -> None:
    candidate = generate_candidate(
        9,
        seed=42,
        symbol="ETHUSDT",
        families=("range_reversion_bounded",),
    )

    assert candidate.family == "range_reversion_bounded"
    assert candidate.overlay["strategy"]["strategies"][0]["name"] == "bollinger_bounce"
    assert candidate.overlay["trading_execution"]["exit_rules"]["time_stop_minutes"] >= 2880


def test_autoresearch_loop_volatility_squeeze_bounded_hold_time_stop_pairing() -> None:
    for run_index in range(1, 25):
        candidate = generate_candidate(
            run_index,
            seed=42,
            symbol="BTCUSDT",
            families=("volatility_squeeze_bounded",),
        )
        assert candidate.family == "volatility_squeeze_bounded"
        strat = candidate.overlay["strategy"]["strategies"][0]
        assert strat["name"] == "volatility_squeeze"
        max_hold_bars = int(strat["config"]["max_hold_bars"])
        time_stop_minutes = candidate.overlay["trading_execution"]["exit_rules"][
            "time_stop_minutes"
        ]
        assert max_hold_bars in (10, 12, 14)
        assert time_stop_minutes == max_hold_bars * 60
        assert candidate.overlay["trading_execution"]["exit_rules"][
            "backtest_use_executor_exit_model"
        ]


def test_autoresearch_loop_funding_normalization_standalone_single_strategy() -> None:
    candidate = generate_candidate(
        11,
        seed=42,
        symbol="SOLUSDT",
        families=("funding_normalization_standalone",),
    )

    assert candidate.family == "funding_normalization_standalone"
    strat = candidate.overlay["strategy"]["strategies"][0]
    assert strat["name"] == "funding_normalization"
    assert strat["config"]["long_only"] is True
    assert 0.00012 <= strat["config"]["entry_threshold"] <= 0.00022


def test_autoresearch_loop_funding_primary_standalone_single_strategy() -> None:
    candidate = generate_candidate(
        10,
        seed=42,
        symbol="BTCUSDT",
        families=("funding_primary_standalone",),
    )

    assert candidate.family == "funding_primary_standalone"
    assert candidate.overlay["strategy"]["strategies"][0]["name"] == "funding_rate"


def test_autoresearch_loop_builds_existing_runner_command() -> None:
    args = argparse.Namespace(
        config="config/settings.autoresearch.yaml",
        output_dir="research",
        symbol="SOLUSDT",
        timeframe="4h",
        start="2024-01-01",
        end="2026-01-01",
        train_months=3,
        test_months=2,
        bootstrap=100,
        seed=7,
        gate_profile="sparse_trend_3_2",
        timeout_seconds=600,
    )

    command = build_run_command(
        args,
        overlay_path=Path("research/candidates/0001.yaml"),
        description="candidate",
    )

    assert command[:2] == [sys.executable, "scripts/run_autoresearch.py"]
    assert "--overlay" in command
    assert command[command.index("--overlay") + 1] == "research/candidates/0001.yaml"
    assert command[command.index("--description") + 1] == "candidate"
    assert command[command.index("--gate-profile") + 1] == "sparse_trend_3_2"


def test_autoresearch_loop_captures_child_output(tmp_path: Path) -> None:
    log_path = tmp_path / "loop.log"

    code = _run_command(
        [sys.executable, "-c", "import sys; print('out'); print('err', file=sys.stderr)"],
        log_path=log_path,
    )

    assert code == 0
    log = log_path.read_text(encoding="utf-8")
    assert "[stdout]\nout" in log
    assert "[stderr]\nerr" in log
