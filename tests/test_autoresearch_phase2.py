from __future__ import annotations

from scripts.autoresearch_phase2 import Result, find_consistent_configs, parse_args


def test_parse_args_defaults_to_sol_4h() -> None:
    args = parse_args([])

    assert args.symbol == "SOLUSDT"
    assert args.timeframe == "4h"


def test_parse_args_accepts_symbol_and_timeframe_overrides() -> None:
    args = parse_args(["--symbol", "AVAXUSDT", "--timeframe", "1h"])

    assert args.symbol == "AVAXUSDT"
    assert args.timeframe == "1h"


def test_find_consistent_configs_excludes_zero_trade_configs() -> None:
    zero_trade_results = [
        Result("zero", "p1", 0, 0, 0, 0.0, 0.0, 0.0, 0.0, 0.0),
        Result("zero", "p2", 0, 0, 0, 0.0, 0.0, 0.0, 0.0, 0.0),
        Result("zero", "p3", 0, 0, 0, 0.0, 0.0, 0.0, 0.0, 0.0),
    ]
    valid_results = [
        Result("valid", "p1", 5, 3, 2, 60.0, 5.0, 5.0, 1.0, 1.2),
        Result("valid", "p2", 6, 4, 2, 66.7, 6.0, 4.0, 1.1, 1.3),
        Result("valid", "p3", 7, 4, 3, 57.1, 4.0, 6.0, 0.9, 1.1),
    ]

    consistent = find_consistent_configs(
        zero_trade_results + valid_results,
        required_periods=3,
        min_trades=3,
    )

    assert [item[0] for item in consistent] == ["valid"]
