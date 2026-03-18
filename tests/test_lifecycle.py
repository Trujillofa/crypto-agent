from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

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
