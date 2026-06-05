from __future__ import annotations

import pytest

from src.strategy.funding_normalization import FundingNormalizationStrategy
from src.strategy.signals import SignalType


@pytest.mark.asyncio
async def test_funding_normalization_buys_once_per_negative_cycle() -> None:
    strategy = FundingNormalizationStrategy(
        {"entry_threshold": 0.0005, "exit_threshold": 0.00015, "long_only": True}
    )
    symbol = "SOLUSDT"
    base = {"close_price": 100.0}

    hold_extreme = await strategy.evaluate(symbol, {**base, "funding_rate": -0.0008})
    assert hold_extreme.type == SignalType.HOLD

    buy = await strategy.evaluate(symbol, {**base, "funding_rate": 0.00005})
    assert buy.type == SignalType.BUY
    assert buy.reason == "funding_normalized_from_negative"

    hold_cooldown = await strategy.evaluate(symbol, {**base, "funding_rate": 0.00004})
    assert hold_cooldown.type == SignalType.HOLD

    await strategy.evaluate(symbol, {**base, "funding_rate": -0.0007})
    second_buy = await strategy.evaluate(symbol, {**base, "funding_rate": 0.00003})
    assert second_buy.type == SignalType.BUY


@pytest.mark.asyncio
async def test_funding_normalization_holds_without_funding() -> None:
    strategy = FundingNormalizationStrategy({})
    signal = await strategy.evaluate("SOLUSDT", {"close_price": 50.0})
    assert signal.type == SignalType.HOLD
    assert signal.reason == "no_funding_rate"
