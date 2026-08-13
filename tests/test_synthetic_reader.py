from __future__ import annotations

from datetime import UTC, datetime

from src.backtest.models import BacktestConfig
from src.backtest.synthetic import RegimeParams, generate_regime_path
from src.backtest.synthetic_eval import evaluate_synthetic_pass_rate, score_path
from src.backtest.synthetic_reader import (
    SyntheticIndicatorReader,
    aggregate_ohlcv,
    candles_to_indicator_rows,
)
from src.strategy.simple_ma import SimpleMACrossoverStrategy


def _regime_params() -> RegimeParams:
    return RegimeParams(
        mu_calm=0.0,
        sigma_calm=0.008,
        mu_stress=-0.001,
        sigma_stress=0.025,
        p_calm_to_stress=0.08,
        p_stress_to_calm=0.2,
        p_start_stress=0.15,
    )


def _wide_window() -> tuple[datetime, datetime]:
    return datetime(1990, 1, 1, tzinfo=UTC), datetime(2100, 1, 1, tzinfo=UTC)


async def test_mtf_join_has_no_lookahead() -> None:
    warmup = 200
    candles, _states = generate_regime_path(
        _regime_params(),
        n_bars=warmup + 48,
        start_price=100.0,
        seed=1,
    )
    reader = SyntheticIndicatorReader(candles, warmup_bars=warmup)
    regime_4h = aggregate_ohlcv(candles, "4h")
    expected_by_open = {row["time"]: row for row in candles_to_indicator_rows(regime_4h)}

    joined = await reader.fetch_multi_timeframe(
        symbol="SYNTH",
        entry_timeframe="1h",
        regime_timeframe="4h",
        start_time=reader.eval_start,
        end_time=reader.eval_end,
    )

    for row in joined:
        entry_time = row["time"]
        assert isinstance(entry_time, datetime)
        completed = [bar for bar in regime_4h if bar.close_time <= entry_time]
        for key in row:
            if key.endswith("_4h"):
                assert completed, f"{key} present at {entry_time} with no completed 4h bar"
                assert completed[-1].open_time < entry_time
                assert completed[-1].close_time <= entry_time

        if not completed:
            continue

        latest = completed[-1]
        expected = expected_by_open.get(latest.open_time)
        next_idx = regime_4h.index(latest) + 1
        next_expected = (
            expected_by_open.get(regime_4h[next_idx].open_time)
            if next_idx < len(regime_4h)
            else None
        )

        for field in ("ema_slope_50", "rsi_14"):
            joined_key = f"{field}_4h"
            if joined_key not in row or row[joined_key] is None:
                continue
            assert expected is not None
            assert row[joined_key] == expected[field]
            if next_expected is not None and next_expected[field] != expected[field]:
                assert row[joined_key] != next_expected[field]


async def test_warmup_rows_not_exposed_and_first_row_formed() -> None:
    warmup = 200
    candles, _states = generate_regime_path(
        _regime_params(),
        n_bars=250,
        start_price=100.0,
        seed=1,
    )
    reader = SyntheticIndicatorReader(candles, warmup_bars=warmup)
    start, end = _wide_window()
    rows = await reader.fetch_range("SYNTH", "1h", start, end)

    assert len(rows) == 50
    assert rows[0]["time"] == candles[200].open_time
    assert all(row["time"] >= candles[200].open_time for row in rows)
    assert rows[0]["ema_200"] is not None
    assert rows[0]["sma_200"] is not None
    assert rows[0]["trend_consistency"] is not None


async def test_reader_seed_determinism() -> None:
    params = _regime_params()
    candles_a, _ = generate_regime_path(params, n_bars=250, start_price=100.0, seed=1)
    candles_b, _ = generate_regime_path(params, n_bars=250, start_price=100.0, seed=1)
    candles_c, _ = generate_regime_path(params, n_bars=250, start_price=100.0, seed=2)
    start, end = _wide_window()

    rows_a = await SyntheticIndicatorReader(candles_a, warmup_bars=200).fetch_range(
        "SYNTH", "1h", start, end
    )
    rows_b = await SyntheticIndicatorReader(candles_b, warmup_bars=200).fetch_range(
        "SYNTH", "1h", start, end
    )
    rows_c = await SyntheticIndicatorReader(candles_c, warmup_bars=200).fetch_range(
        "SYNTH", "1h", start, end
    )

    assert [row["close_price"] for row in rows_a] == [row["close_price"] for row in rows_b]
    assert [row["ema_50"] for row in rows_a] == [row["ema_50"] for row in rows_b]
    assert [row["close_price"] for row in rows_a] != [row["close_price"] for row in rows_c]
    assert [row["ema_50"] for row in rows_a] != [row["ema_50"] for row in rows_c]


async def test_engine_yields_pass_rate() -> None:
    config = BacktestConfig(
        symbol="SYNTH",
        timeframe="1h",
        start_date="2020-01-01T00:00:00+00:00",
        end_date="2020-02-01T00:00:00+00:00",
        strategy_classes=[SimpleMACrossoverStrategy],
        strategy_configs=[None],
        apply_global_trend_filter=False,
        initial_capital=10000,
    )
    result = await evaluate_synthetic_pass_rate(
        config,
        n_regime_paths=2,
        include_stress=True,
        warmup_bars=200,
        eval_bars=80,
        seed=7,
    )
    assert result.status in {"scored", "inconclusive"}
    assert 0.0 <= result.pass_rate_pct <= 100.0


def test_path_passes_empty() -> None:
    assert score_path([], kind="regime") is None
    assert score_path([], kind="stress") is None


def test_path_passes_known_sequence() -> None:
    assert score_path([1.0, 1.0, 1.0], kind="regime") is True
    assert score_path([-20.0], kind="stress", max_drawdown_pct=10.0) is False


async def test_fetch_funding_settlements_empty_by_default() -> None:
    candles, _states = generate_regime_path(
        _regime_params(),
        n_bars=250,
        start_price=100.0,
        seed=1,
    )
    reader = SyntheticIndicatorReader(candles, warmup_bars=200)
    start, end = _wide_window()
    assert await reader.fetch_funding_settlements("SYNTH", start, end) == []
