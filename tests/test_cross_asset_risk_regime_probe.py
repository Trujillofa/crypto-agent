from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from scripts.probe_cross_asset_risk_regime import (
    EXPECTED_SIGN_H1,
    EXPECTED_SIGN_H2,
    FROZEN_PROXIES,
    TradFiBar,
    audit_tradfi_data,
    crypto_entry_after_tradfi_close,
    evaluate_h1_symbol,
    latest_tradfi_bar_before,
    load_tradfi_bars,
)
from scripts.probe_macro_event_drift import HourlyBar

TRADFI_DIR = Path("data/tradfi")


def _tradfi_bar(
    *,
    proxy: str = "equity_risk",
    close_ts: datetime,
    close: float,
    gap: bool = False,
    granularity: str = "1d",
) -> TradFiBar:
    return TradFiBar(
        proxy=proxy,
        source_ticker=FROZEN_PROXIES[proxy]["ticker"],
        granularity=granularity,
        bar_open_utc=close_ts - timedelta(days=1),
        close_ts_utc=close_ts,
        close=close,
        is_weekend_gap_after=gap,
    )


def test_frozen_proxy_set_matches_spec() -> None:
    assert set(FROZEN_PROXIES) == {"equity_risk", "dxy", "us10y", "vix"}
    assert FROZEN_PROXIES["equity_risk"]["ticker"] == "QQQ"
    assert FROZEN_PROXIES["dxy"]["ticker"] == "DX-Y.NYB"
    assert FROZEN_PROXIES["us10y"]["ticker"] == "^TNX"
    assert FROZEN_PROXIES["vix"]["ticker"] == "^VIX"


def test_ex_ante_signs_frozen() -> None:
    assert EXPECTED_SIGN_H1["equity_risk"] == +1
    assert EXPECTED_SIGN_H1["dxy"] == -1
    assert EXPECTED_SIGN_H1["us10y"] == -1
    assert EXPECTED_SIGN_H1["vix"] == -1
    assert EXPECTED_SIGN_H2["risk_on"] == +1
    assert EXPECTED_SIGN_H2["dxy_strong"] == -1
    assert EXPECTED_SIGN_H2["us10y_rising"] == -1
    assert EXPECTED_SIGN_H2["vix_high"] == -1


def test_committed_tradfi_data_passes_audit() -> None:
    audit = audit_tradfi_data(TRADFI_DIR)
    assert not audit.blocked
    assert audit.equity_risk_available
    equity = next(item for item in audit.proxies if item.proxy == "equity_risk")
    assert equity.daily_rows >= 400
    assert equity.daily_start == "2024-01-02"
    assert equity.weekend_gaps_daily > 50


def test_point_in_time_crypto_entry_after_tradfi_close() -> None:
    close_ts = datetime(2024, 3, 15, 21, 0, 0, tzinfo=UTC)
    bars = [
        HourlyBar(datetime(2024, 3, 15, 20, 0, 0, tzinfo=UTC), 100.0),
        HourlyBar(datetime(2024, 3, 15, 21, 0, 0, tzinfo=UTC), 101.0),
        HourlyBar(datetime(2024, 3, 15, 22, 0, 0, tzinfo=UTC), 102.0),
    ]
    assert crypto_entry_after_tradfi_close(bars, close_ts) == 2


def test_latest_tradfi_bar_excludes_not_yet_printed() -> None:
    bars = (
        _tradfi_bar(close_ts=datetime(2024, 1, 2, 21, 0, 0, tzinfo=UTC), close=100.0),
        _tradfi_bar(close_ts=datetime(2024, 1, 3, 21, 0, 0, tzinfo=UTC), close=101.0),
    )
    query_ts = datetime(2024, 1, 3, 20, 0, 0, tzinfo=UTC)
    latest = latest_tradfi_bar_before(bars, query_ts)
    assert latest is not None
    assert latest.close_ts_utc == datetime(2024, 1, 2, 21, 0, 0, tzinfo=UTC)


def test_weekend_gap_flagged_in_committed_equity_series() -> None:
    daily = load_tradfi_bars(TRADFI_DIR / "equity_risk_1d.csv")
    gaps = [bar for bar in daily if bar.is_weekend_gap_after]
    assert len(gaps) >= 50
    assert all(bar.close_ts_utc.weekday() in (0, 1, 2, 3, 4) for bar in gaps)


def test_forward_predictive_edge_separates_from_contemporaneous_on_synthetic() -> None:
    tradfi: list[TradFiBar] = []
    base = datetime(2024, 1, 2, 21, 0, 0, tzinfo=UTC)
    price = 100.0
    for day in range(30):
        price *= 1.01 if day % 2 == 0 else 0.99
        tradfi.append(
            _tradfi_bar(
                close_ts=base + timedelta(days=day),
                close=price,
            )
        )

    crypto: list[HourlyBar] = []
    cprice = 50_000.0
    start = datetime(2024, 1, 1, 0, 0, 0, tzinfo=UTC)
    for hour in range(30 * 24 + 48):
        cprice += 1.0
        crypto.append(HourlyBar(start + timedelta(hours=hour), cprice))

    for index in range(1, len(tradfi)):
        prior_move = tradfi[index].close / tradfi[index - 1].close - 1.0
        entry = crypto_entry_after_tradfi_close(crypto, tradfi[index].close_ts_utc)
        assert entry is not None
        bump = 200.0 if prior_move > 0 else -200.0
        for offset in range(1, 7):
            idx = entry + offset
            crypto[idx] = HourlyBar(crypto[idx].time, crypto[idx].close_price + bump)

    from scripts.probe_cross_asset_risk_regime import ProbeConfig

    config = ProbeConfig(
        symbols=("BTCUSDT",),
        timeframe="1h",
        start="2024-01-01T00:00:00",
        end="2026-06-01T00:00:00",
        tradfi_dir=TRADFI_DIR,
        horizons_hours=(6,),
        fee_noise_bar_pct=0.1,
        min_symbols_pass=2,
        random_baseline_seed=42,
        sma_lookback_days=5,
        vix_high_threshold=20.0,
    )
    result = evaluate_h1_symbol(
        crypto,
        tuple(tradfi),
        config,
        proxy="equity_risk",
        symbol="BTCUSDT",
        granularity="1d",
    )
    metrics = result.horizons[0]
    assert metrics.observation_count >= 10
    assert metrics.predictive_rank_corr > metrics.contemporaneous_rank_corr
    assert metrics.excess_vs_baseline_pct > config.fee_noise_bar_pct
    assert metrics.h1_pass


def test_h1_fails_when_only_contemporaneous_correlation() -> None:
    tradfi: list[TradFiBar] = []
    base = datetime(2024, 1, 2, 21, 0, 0, tzinfo=UTC)
    for day in range(20):
        tradfi.append(_tradfi_bar(close_ts=base + timedelta(days=day), close=100.0 + day))

    crypto: list[HourlyBar] = []
    start = datetime(2024, 1, 1, 0, 0, 0, tzinfo=UTC)
    for hour in range(20 * 24):
        crypto.append(HourlyBar(start + timedelta(hours=hour), 1000.0 + hour * 0.01))

    from scripts.probe_cross_asset_risk_regime import ProbeConfig

    config = ProbeConfig(
        symbols=("BTCUSDT",),
        timeframe="1h",
        start="2024-01-01T00:00:00",
        end="2026-06-01T00:00:00",
        tradfi_dir=TRADFI_DIR,
        horizons_hours=(6,),
        fee_noise_bar_pct=0.3,
        min_symbols_pass=2,
        random_baseline_seed=42,
        sma_lookback_days=5,
        vix_high_threshold=20.0,
    )
    result = evaluate_h1_symbol(
        crypto,
        tuple(tradfi),
        config,
        proxy="equity_risk",
        symbol="BTCUSDT",
        granularity="1d",
    )
    assert not result.horizons[0].h1_pass
