from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from scripts.register_paper_strategies import PAPER_STRATEGIES
from src.strategy.lifecycle import LifecycleManager


@pytest.mark.asyncio
async def test_is_live_blocks_non_live_status(caplog) -> None:
    lifecycle = LifecycleManager({})
    lifecycle.conn = AsyncMock()
    lifecycle.conn.fetchrow.return_value = {"status": "validated"}

    with caplog.at_level("WARNING"):
        allowed = await lifecycle.is_live(["TrendPullbackStrategy"])

    assert allowed is False
    assert "status: validated (blocked)" in caplog.text


@pytest.mark.asyncio
async def test_is_paper_ready_allows_unknown_and_validated(caplog) -> None:
    lifecycle = LifecycleManager({})
    lifecycle.conn = AsyncMock()
    lifecycle.conn.fetchrow.side_effect = [
        None,
        {"status": "validated"},
        {"status": "paper"},
        {"status": "live"},
    ]

    with caplog.at_level("INFO"):
        allowed = await lifecycle.is_paper_ready(
            [
                "SentimentMeanReversionStrategy",
                "TrendPullbackStrategy",
                "MTFStrategyTemplate",
                "RSIReversalStrategy",
            ]
        )

    assert allowed is True
    assert "allowed in paper mode" in caplog.text
    assert "blocked" not in caplog.text


@pytest.mark.asyncio
async def test_is_paper_ready_still_blocks_rejected_status(caplog) -> None:
    lifecycle = LifecycleManager({})
    lifecycle.conn = AsyncMock()
    lifecycle.conn.fetchrow.return_value = {"status": "archived"}

    with caplog.at_level("WARNING"):
        allowed = await lifecycle.is_paper_ready(["LegacyStrategy"])

    assert allowed is False
    assert "status: archived (blocked)" in caplog.text


@pytest.mark.asyncio
async def test_set_strategy_status_upserts_requested_status() -> None:
    lifecycle = LifecycleManager({})
    lifecycle.conn = AsyncMock()

    await lifecycle.set_strategy_status(
        "SentimentMeanReversionStrategy",
        "1.0",
        "paper",
        {
            "sharpe": 0.42,
            "win_rate": 0.55,
            "max_drawdown": 8.5,
            "total_trades": 12,
            "notes": "paper validation",
        },
    )

    execute_args = lifecycle.conn.execute.await_args.args
    assert execute_args[1] == "SentimentMeanReversionStrategy"
    assert execute_args[2] == "1.0"
    assert execute_args[3] == "paper"
    assert execute_args[5] == 0.42
    assert execute_args[6] == 0.55
    assert execute_args[7] == 8.5
    assert execute_args[8] == 12


@pytest.mark.asyncio
async def test_register_paper_strategy_delegates_to_paper_status() -> None:
    lifecycle = LifecycleManager({})
    lifecycle.conn = AsyncMock()

    await lifecycle.register_paper_strategy("MTFStrategyTemplate", "1.0")

    execute_args = lifecycle.conn.execute.await_args.args
    assert execute_args[1] == "MTFStrategyTemplate"
    assert execute_args[2] == "1.0"
    assert execute_args[3] == "paper"


def test_register_paper_strategies_includes_simple_ma_candidate() -> None:
    strategy_names = {strategy["name"] for strategy in PAPER_STRATEGIES}

    assert "SentimentMeanReversionStrategy" in strategy_names
    assert "MTFStrategyTemplate" in strategy_names
    assert "SimpleMACrossoverStrategy" in strategy_names
