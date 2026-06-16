"""Unit tests for daily-trend breadth probe helpers (no DB/network)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from scripts.probe_daily_trend_breadth import (
    _max_symbol_pnl_share,
    _mean_pairwise_signal_correlation,
    _symbol_positions_and_returns,
    build_coverage_audit,
    decide_verdict,
    evaluate_portfolio_breadth,
)
from scripts.probe_higher_tf_trend_following import DailyBar as TrendDailyBar


def _bars(symbol_suffix: float, prices: list[float]) -> list[TrendDailyBar]:
    base = datetime(2024, 1, 1, tzinfo=UTC)
    return [
        TrendDailyBar(day=base + timedelta(days=i), close_price=price + symbol_suffix)
        for i, price in enumerate(prices)
    ]


def _trend_prices(n: int, drift: float, crash_at: int | None = None) -> list[float]:
    prices = [100.0]
    for i in range(1, n):
        if crash_at is not None and i >= crash_at:
            prices.append(prices[-1] * 0.97)
        else:
            prices.append(prices[-1] * (1.0 + drift))
    return prices


def test_build_coverage_audit_blocks_thin_universe() -> None:
    rows = [
        {
            "symbol": "BTCUSDT",
            "bars": 1000,
            "first_ts": datetime(2024, 1, 1, tzinfo=UTC),
            "last_ts": datetime(2026, 6, 1, tzinfo=UTC),
            "quote_volume": 1e12,
        },
        {
            "symbol": "ETHUSDT",
            "bars": 1000,
            "first_ts": datetime(2024, 1, 1, tzinfo=UTC),
            "last_ts": datetime(2026, 6, 1, tzinfo=UTC),
            "quote_volume": 5e11,
        },
    ]
    audit = build_coverage_audit(
        rows,
        min_history_days=700,
        min_universe_symbols=15,
        target_universe_symbols=20,
    )
    assert audit.blocked is True
    assert audit.blocked_reason is not None
    assert "only 2" in audit.blocked_reason


def test_build_coverage_audit_selects_top_liquid_universe() -> None:
    rows = []
    for index in range(18):
        symbol = f"SYM{index:02d}USDT"
        rows.append(
            {
                "symbol": symbol,
                "bars": 2000,
                "first_ts": datetime(2023, 1, 1, tzinfo=UTC),
                "last_ts": datetime(2026, 6, 1, tzinfo=UTC),
                "quote_volume": float(index + 1),
            }
        )
    audit = build_coverage_audit(
        rows,
        min_history_days=700,
        min_universe_symbols=15,
        target_universe_symbols=20,
    )
    assert audit.blocked is False
    assert len(audit.universe) == 18
    assert audit.universe[0] == "SYM17USDT"


def test_symbol_positions_are_long_only() -> None:
    prices = _trend_prices(120, 0.01, crash_at=80)
    days, positions, _net, _gross, switches, _total = _symbol_positions_and_returns(
        _bars(0.0, prices),
        sma_window=50,
        fee_pct=0.04,
    )
    assert days
    assert set(positions).issubset({0, 1})
    assert switches > 0


def test_max_symbol_pnl_share_concentration() -> None:
    share = _max_symbol_pnl_share(
        [
            ("A", 80.0),
            ("B", 10.0),
            ("C", 10.0),
        ]
    )
    assert share == 80.0

    dominant = _max_symbol_pnl_share(
        [
            ("A", 30.0),
            ("B", 25.0),
            ("C", 25.0),
            ("D", 20.0),
        ]
    )
    assert dominant == 30.0


def test_mean_pairwise_signal_correlation() -> None:
    identical = _mean_pairwise_signal_correlation(
        {
            "A": [1, 1, 1, 0, 0],
            "B": [1, 1, 1, 0, 0],
        }
    )
    assert identical > 0.99

    mixed = _mean_pairwise_signal_correlation(
        {
            "A": [1, 1, 1, 0, 0],
            "B": [1, 1, 1, 0, 0],
            "C": [0, 0, 1, 1, 1],
        }
    )
    assert -1.0 <= mixed <= 1.0
    assert mixed < identical

    low = _mean_pairwise_signal_correlation(
        {
            "A": [1, 0, 1, 0, 1],
            "B": [0, 1, 0, 1, 0],
        }
    )
    assert low < 0.0


def test_evaluate_portfolio_breadth_on_synthetic_multi_symbol_series() -> None:
    n = 160
    symbol_bars = {
        "AAAUSDT": _bars(0.0, _trend_prices(n, 0.008)),
        "BBBUSDT": _bars(1.0, _trend_prices(n, 0.006, crash_at=110)),
        "CCCUSDT": _bars(2.0, _trend_prices(n, 0.007)),
        "DDDUSDT": _bars(3.0, _trend_prices(n, 0.005)),
    }
    metrics = evaluate_portfolio_breadth(
        symbol_bars,
        sma_window=50,
        fee_pct=0.04,
        vol_lookback_days=20,
        wfo_oos_months=2,
    )
    assert metrics is not None
    assert metrics.n_symbols == 4
    assert metrics.total_state_changes > 0
    assert 0.0 <= metrics.max_symbol_pnl_share_pct <= 100.0
    assert metrics.strat_max_dd_pct >= 0.0
    assert metrics.bh_max_dd_pct >= 0.0


def test_decide_verdict_has_pulse_when_all_gates_pass() -> None:
    from scripts.probe_daily_trend_breadth import PortfolioMetrics

    metrics = PortfolioMetrics(
        n_days=500,
        n_symbols=18,
        total_state_changes=400,
        state_changes_per_oos_window=25.0,
        max_symbol_pnl_share_pct=35.0,
        mean_pairwise_signal_corr=0.4,
        strat_total_return_pct=40.0,
        bh_total_return_pct=20.0,
        strat_sharpe=1.2,
        bh_sharpe=0.8,
        strat_max_dd_pct=20.0,
        bh_max_dd_pct=35.0,
        vol_target_total_return_pct=42.0,
        vol_target_sharpe=1.25,
        vol_target_max_dd_pct=18.0,
        per_symbol_pnl_pct=(("A", 10.0), ("B", 12.0)),
        per_symbol_switches=(("A", 20), ("B", 22)),
    )
    status, verdict, reasons = decide_verdict(metrics, blocked=False)
    assert status == "OK"
    assert verdict == "HAS_PULSE"
    assert reasons == ()


def test_decide_verdict_blocked_on_ingestion() -> None:
    status, verdict, _reasons = decide_verdict(None, blocked=True)
    assert status == "BLOCKED_ON_INGESTION"
    assert verdict == "NO_PULSE"


def test_decide_verdict_no_pulse_on_high_concentration() -> None:
    from scripts.probe_daily_trend_breadth import PortfolioMetrics

    metrics = PortfolioMetrics(
        n_days=500,
        n_symbols=18,
        total_state_changes=400,
        state_changes_per_oos_window=25.0,
        max_symbol_pnl_share_pct=92.0,
        mean_pairwise_signal_corr=0.85,
        strat_total_return_pct=10.0,
        bh_total_return_pct=30.0,
        strat_sharpe=0.3,
        bh_sharpe=0.8,
        strat_max_dd_pct=40.0,
        bh_max_dd_pct=35.0,
        vol_target_total_return_pct=8.0,
        vol_target_sharpe=0.25,
        vol_target_max_dd_pct=42.0,
        per_symbol_pnl_pct=(("A", 90.0), ("B", 8.0)),
        per_symbol_switches=(("A", 20), ("B", 22)),
    )
    _status, verdict, reasons = decide_verdict(metrics, blocked=False)
    assert verdict == "NO_PULSE"
    assert any("concentration" in reason for reason in reasons)
