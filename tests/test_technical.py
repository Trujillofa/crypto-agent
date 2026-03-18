from __future__ import annotations

from src.features.technical import compute_indicators


def test_compute_indicators_returns_values() -> None:
    data = {
        "open": [
            100,
            101,
            102,
            103,
            104,
            105,
            106,
            107,
            108,
            109,
            110,
            111,
            112,
            113,
            114,
            115,
            116,
            117,
            118,
            119,
            120,
            121,
            122,
            123,
            124,
            125,
            126,
            127,
            128,
            129,
            130,
        ],
        "high": [
            101,
            102,
            103,
            104,
            105,
            106,
            107,
            108,
            109,
            110,
            111,
            112,
            113,
            114,
            115,
            116,
            117,
            118,
            119,
            120,
            121,
            122,
            123,
            124,
            125,
            126,
            127,
            128,
            129,
            130,
            131,
        ],
        "low": [
            99,
            100,
            101,
            102,
            103,
            104,
            105,
            106,
            107,
            108,
            109,
            110,
            111,
            112,
            113,
            114,
            115,
            116,
            117,
            118,
            119,
            120,
            121,
            122,
            123,
            124,
            125,
            126,
            127,
            128,
            129,
        ],
        "close": [
            100,
            101,
            102,
            103,
            104,
            105,
            106,
            107,
            108,
            109,
            110,
            111,
            112,
            113,
            114,
            115,
            116,
            117,
            118,
            119,
            120,
            121,
            122,
            123,
            124,
            125,
            126,
            127,
            128,
            129,
            130,
        ],
        "volume": [1000] * 31,
    }

    indicators = compute_indicators(data)

    assert indicators.rsi_14 >= 0
    assert indicators.macd_hist == indicators.macd - indicators.macd_signal


def test_compute_indicators_populates_regime_features_for_long_series() -> None:
    data = {
        "open": [100 + i * 0.25 for i in range(240)],
        "high": [101 + i * 0.25 for i in range(240)],
        "low": [99 + i * 0.25 for i in range(240)],
        "close": [100 + i * 0.25 for i in range(240)],
        "volume": [1000 + (i % 12) * 25 for i in range(240)],
    }

    indicators = compute_indicators(data)

    assert indicators.ema_slope_50 is not None
    assert indicators.volatility_percentile is not None
    assert indicators.atr_percentile is not None
    assert indicators.volume_regime is not None
    assert indicators.price_vs_weekly is not None
    assert indicators.price_vs_monthly is not None
    assert indicators.rsi_slope is not None
    assert indicators.trend_consistency is not None
