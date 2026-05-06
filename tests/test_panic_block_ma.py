from __future__ import annotations

import pytest

from src.strategy.panic_block_ma import PanicBlockMACrossoverStrategy
from src.strategy.signals import SignalType


def _indicators(
    *,
    ema_12: float,
    ema_26: float,
    close_price: float,
    ema_50: float = 80.0,
    ema_200: float = 100.0,
    rsi_14: float = 50.0,
    atr_pct: float = 0.02,
) -> dict[str, float]:
    return {
        "ema_12": ema_12,
        "ema_26": ema_26,
        "ema_50": ema_50,
        "ema_200": ema_200,
        "close_price": close_price,
        "rsi_14": rsi_14,
        "atr_pct": atr_pct,
    }


@pytest.mark.asyncio
async def test_panic_block_ma_vetoes_buy_in_risk_off_regime():
    strategy = PanicBlockMACrossoverStrategy()

    await strategy.evaluate("SOLUSDT", _indicators(ema_12=99.0, ema_26=100.0, close_price=90.0))
    signal = await strategy.evaluate(
        "SOLUSDT",
        _indicators(ema_12=101.0, ema_26=100.0, close_price=90.0, rsi_14=30.0),
    )

    assert signal.type is SignalType.HOLD
    assert "Panic-block vetoed EMA BUY" in signal.reason
    assert signal.indicators["rsi_14"] == 30.0


@pytest.mark.asyncio
async def test_panic_block_ma_allows_buy_when_overlay_is_clear():
    strategy = PanicBlockMACrossoverStrategy()

    await strategy.evaluate("SOLUSDT", _indicators(ema_12=99.0, ema_26=100.0, close_price=110.0))
    signal = await strategy.evaluate(
        "SOLUSDT",
        _indicators(ema_12=101.0, ema_26=100.0, close_price=110.0, rsi_14=50.0),
    )

    assert signal.type is SignalType.BUY


@pytest.mark.asyncio
async def test_panic_block_ma_does_not_veto_sell_exits():
    strategy = PanicBlockMACrossoverStrategy()

    await strategy.evaluate("SOLUSDT", _indicators(ema_12=101.0, ema_26=100.0, close_price=70.0))
    signal = await strategy.evaluate(
        "SOLUSDT",
        _indicators(ema_12=99.0, ema_26=100.0, close_price=70.0, rsi_14=20.0),
    )

    assert signal.type is SignalType.SELL
