"""Tests for order-flow microstructure probe (no network)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from scripts.probe_orderflow_microstructure import (
    BLOCKED_ON_DATA,
    HAS_PULSE,
    NO_PULSE,
    NO_PULSE_FOR_STACK,
    WEAK_EDGE,
    AggTrade,
    DataAudit,
    DecileStats,
    HorizonResult,
    ProbeConfig,
    RegimeWindow,
    SymbolProbeResult,
    assign_decile,
    build_second_bars,
    check_monotonic,
    compute_signal_observations,
    decide_verdict,
    fit_decile_boundaries,
    forward_vwap_return_bps,
    probe_symbol,
    sanity_check_sign_convention,
    select_regime_window,
    sign_trade_qty,
)

T0_MS = int(datetime(2026, 5, 1, 12, 0, tzinfo=UTC).timestamp() * 1000)


def _trade(
    *,
    offset_sec: int,
    price: float,
    qty: float = 1.0,
    is_buyer_maker: bool = False,
    agg_id: int | None = None,
) -> AggTrade:
    return AggTrade(
        agg_id=agg_id if agg_id is not None else offset_sec,
        price=price,
        qty=qty,
        timestamp_ms=T0_MS + offset_sec * 1000,
        is_buyer_maker=is_buyer_maker,
    )


def _synthetic_trend_trades(seconds: int = 600) -> list[AggTrade]:
    trades: list[AggTrade] = []
    price = 100.0
    for sec in range(seconds):
        for tick in range(4):
            is_sell = tick % 2 == 0
            buy_pressure = 1.0 if sec % 20 < 10 else -1.0
            if buy_pressure > 0:
                is_sell = False
            price += 0.01 if buy_pressure > 0 else -0.01
            trades.append(
                _trade(
                    offset_sec=sec,
                    price=price,
                    qty=0.5 + (sec % 5) * 0.1,
                    is_buyer_maker=is_sell,
                    agg_id=sec * 10 + tick,
                )
            )
    return trades


def _config(**overrides) -> ProbeConfig:
    defaults = {
        "symbols": ("BTCUSDT",),
        "start": None,
        "end": None,
        "window_days": 21,
        "ofi_window_sec": 30,
        "signal_stride_sec": 5,
        "taker_fee_bps": 10.0,
        "bootstrap_resamples": 200,
        "train_fraction": 0.70,
        "min_symbols_h1": 2,
        "min_forward_samples": 20,
        "min_trades_per_symbol": 100,
        "min_coverage_fraction": 0.5,
        "cache_dir": None,
        "refresh_cache": False,
        "analysis_only": False,
        "seed": 42,
    }
    defaults.update(overrides)
    if defaults["cache_dir"] is None:
        defaults["cache_dir"] = __import__("pathlib").Path("data/microstructure")
    return ProbeConfig(**defaults)


def test_sign_trade_qty_convention():
    buy = _trade(offset_sec=0, price=100.0, is_buyer_maker=False)
    sell = _trade(offset_sec=1, price=100.0, is_buyer_maker=True)
    assert sign_trade_qty(buy) == pytest.approx(1.0)
    assert sign_trade_qty(sell) == pytest.approx(-1.0)


def test_sanity_check_flags_inverted_sign():
    trades: list[AggTrade] = []
    price = 100.0
    for sec in range(200):
        price += 0.05
        trades.append(_trade(offset_sec=sec, price=price, is_buyer_maker=False))
        trades.append(_trade(offset_sec=sec, price=price, is_buyer_maker=True))
    corr_ok, inverted_ok = sanity_check_sign_convention(trades)
    assert corr_ok > 0
    assert inverted_ok is False

    flipped = [
        AggTrade(
            agg_id=t.agg_id,
            price=t.price,
            qty=t.qty,
            timestamp_ms=t.timestamp_ms,
            is_buyer_maker=not t.is_buyer_maker,
        )
        for t in trades
    ]
    corr_bad, inverted_bad = sanity_check_sign_convention(flipped)
    assert corr_bad < 0
    assert inverted_bad is True


def test_build_second_bars_and_forward_return_no_overlap():
    trades = [
        _trade(offset_sec=0, price=100.0, qty=2.0, is_buyer_maker=False),
        _trade(offset_sec=0, price=100.1, qty=1.0, is_buyer_maker=True),
        _trade(offset_sec=5, price=101.0, qty=1.0, is_buyer_maker=False),
        _trade(offset_sec=10, price=102.0, qty=1.0, is_buyer_maker=False),
    ]
    bars = build_second_bars(trades)
    assert len(bars) >= 2
    signal_ts = bars[0].timestamp_ms
    ret = forward_vwap_return_bps(bars, signal_ts, bars[0].price_vwap, horizon_sec=5)
    assert ret is not None
    assert ret > 0


def test_decile_boundaries_and_monotonicity():
    values = [float(i) for i in range(100)]
    boundaries = fit_decile_boundaries(values)
    assert len(boundaries) == 9
    assert assign_decile(-1.0, boundaries) == 0
    assert assign_decile(50.0, boundaries) >= 4
    monotonic, violations = check_monotonic([1.0, 2.0, 2.5, 3.0, 3.5, 4.0, 4.5, 5.0, 5.5, 6.0])
    assert monotonic is True
    assert violations == 0
    bad, bad_v = check_monotonic([1.0, 2.0, 1.5, 3.0])
    assert bad is False
    assert bad_v == 1


def test_select_regime_window_finds_mixed_vol():
    days = []
    for idx in range(30):
        day = datetime(2026, 4, 1, tzinfo=UTC) + timedelta(days=idx)
        vol = 50.0 if idx % 7 == 0 else (500.0 if idx % 5 == 0 else 150.0)
        days.append((day, vol))
    window = select_regime_window(days, window_days=14)
    assert window is not None
    assert window.elevated_vol_days
    assert window.quiet_vol_days


def _horizon(
    horizon_sec: int,
    *,
    spread: float,
    h1: bool,
    h3: bool = False,
) -> HorizonResult:
    deciles = tuple(
        DecileStats(decile=i, count=10, mean_forward_return_bps=float(i)) for i in range(10)
    )
    return HorizonResult(
        horizon_sec=horizon_sec,
        deciles=deciles,
        top_minus_bottom_bps=spread,
        monotonic=True,
        monotonic_violations=0,
        bootstrap_p_value=0.01,
        bootstrap_p_adj=0.02,
        shuffled_spread_bps=0.0,
        beats_shuffled=True,
        concentration_ok=True,
        max_day_concentration=0.1,
        cost_bps=5.0,
        net_edge_bps=spread - 5.0,
        cost_survives=spread > 5.0,
        train_top_minus_bottom_bps=spread,
        forward_top_minus_bottom_bps=spread,
        forward_samples=100,
        significant=h1,
        h1_pass=h1,
        h3_pass=h3,
    )


def _symbol_result(
    symbol: str,
    *,
    sub10s: bool = False,
    tradeable: bool = False,
) -> SymbolProbeResult:
    horizons: list[HorizonResult] = []
    if sub10s:
        horizons.append(_horizon(5, spread=8.0, h1=True, h3=False))
    if tradeable:
        horizons.append(_horizon(60, spread=12.0, h1=True, h3=True))
        horizons.append(_horizon(300, spread=6.0, h1=False, h3=False))
    return SymbolProbeResult(
        symbol=symbol,
        forward_samples=100,
        horizons=tuple(horizons),
        tradeable_h1=tradeable,
        sub10s_h1_only=sub10s and not tradeable,
        any_h1=sub10s or tradeable,
    )


def _open_audit(blocked: bool = False) -> DataAudit:
    rw = RegimeWindow(
        start=datetime(2026, 5, 1, tzinfo=UTC),
        end=datetime(2026, 5, 22, tzinfo=UTC),
        anchor_symbol="BTCUSDT",
        elevated_vol_days=("2026-05-03",),
        quiet_vol_days=("2026-05-10",),
        daily_vol_bps={"2026-05-03": 500.0},
        selection_note="test",
    )
    return DataAudit(
        regime_window=rw,
        symbols=(),
        blocked=blocked,
        blocked_reason="blocked" if blocked else None,
    )


def test_decide_verdict_has_pulse():
    results = (
        _symbol_result("BTCUSDT", tradeable=True),
        _symbol_result("ETHUSDT", tradeable=True),
    )
    _, verdict, _ = decide_verdict(_open_audit(), results, _config())
    assert verdict == HAS_PULSE


def test_decide_verdict_no_pulse_for_stack():
    results = (
        _symbol_result("BTCUSDT", sub10s=True),
        _symbol_result("ETHUSDT", sub10s=True),
    )
    _, verdict, reasons = decide_verdict(_open_audit(), results, _config())
    assert verdict == NO_PULSE_FOR_STACK
    assert "sub-10s" in reasons[0]


def test_decide_verdict_weak_edge():
    results = (
        _symbol_result("BTCUSDT", tradeable=True),
        SymbolProbeResult(
            symbol="ETHUSDT",
            forward_samples=100,
            horizons=(_horizon(60, spread=2.0, h1=True, h3=False),),
            tradeable_h1=True,
            sub10s_h1_only=False,
            any_h1=True,
        ),
    )
    _, verdict, _ = decide_verdict(_open_audit(), results, _config(min_symbols_h1=2))
    assert verdict == WEAK_EDGE


def test_decide_verdict_no_pulse():
    results = (
        SymbolProbeResult(
            symbol="BTCUSDT",
            forward_samples=100,
            horizons=(_horizon(60, spread=1.0, h1=False),),
            tradeable_h1=False,
            sub10s_h1_only=False,
            any_h1=False,
        ),
    )
    _, verdict, _ = decide_verdict(_open_audit(), results, _config(min_symbols_h1=1))
    assert verdict == NO_PULSE


def test_decide_verdict_blocked_on_data():
    _, verdict, _ = decide_verdict(_open_audit(blocked=True), (), _config())
    assert verdict == BLOCKED_ON_DATA


def test_probe_symbol_runs_on_synthetic_series():
    trades = _synthetic_trend_trades(1500)
    result = probe_symbol("BTCUSDT", trades, _config(min_forward_samples=15))
    assert result.forward_samples > 0
    assert len(result.horizons) >= 5
    obs = compute_signal_observations(
        build_second_bars(trades),
        ofi_window_sec=30,
        signal_stride_sec=5,
    )
    assert len(obs) > 20
