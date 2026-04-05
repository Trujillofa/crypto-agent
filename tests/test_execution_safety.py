"""Tests for guard pipeline and staged order integration in executors.

All tests verify that the safety layer is additive and transparent when disabled.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.execution.executor import TradingExecutor, TradingConfig
from src.execution.staged_orders import OrderStage, StagedOrderManager
from src.risk.guards import (
    Guard,
    GuardContext,
    GuardPipeline,
    GuardResult,
)


class AlwaysBlockGuard(Guard):
    """A guard that always blocks orders."""

    @property
    def name(self) -> str:
        return "AlwaysBlock"

    def check(self, context: GuardContext) -> GuardResult:
        return GuardResult.block(reason="Test block", guard_name="AlwaysBlock")


class AlwaysPassGuard(Guard):
    """A guard that always passes orders."""

    @property
    def name(self) -> str:
        return "AlwaysPass"

    def check(self, context: GuardContext) -> GuardResult:
        return GuardResult.allow(reason="Test pass", guard_name="AlwaysPass")


def _make_trading_config() -> TradingConfig:
    return TradingConfig(
        api_key="test-key",
        api_secret="test-secret",
        test_mode=True,
        enabled=True,
    )


def _make_mock_risk_manager() -> MagicMock:
    rm = MagicMock()
    rm.is_trading_allowed.return_value = (True, "")
    rm.check_position_limit.return_value = (True, "")
    rm._agent_id = "test-agent"
    rm._config.position_limits.max_position_pct = 0.10
    return rm


def _make_mock_account_info() -> MagicMock:
    info = MagicMock()
    info.available_balance = 10000.0
    info.total_balance = 10000.0
    return info


class TestGuardPipelineOptional:
    """Guards are optional — when None, executor behavior is unchanged."""

    @pytest.mark.asyncio
    async def test_executor_works_with_no_guard_pipeline(self) -> None:
        config = _make_trading_config()
        risk_manager = _make_mock_risk_manager()

        executor = TradingExecutor(
            config=config,
            risk_manager=risk_manager,
            metrics=MagicMock(),
            guard_pipeline=None,
            staged_manager=None,
        )

        assert executor._guard_pipeline is None
        assert executor._staged_manager is None


class TestGuardPipelineBlocking:
    """A blocking guard prevents order placement."""

    @pytest.mark.asyncio
    async def test_blocking_guard_rejects_market_order(self) -> None:
        config = _make_trading_config()
        risk_manager = _make_mock_risk_manager()

        pipeline = GuardPipeline(guards=[AlwaysBlockGuard()])
        executor = TradingExecutor(
            config=config,
            risk_manager=risk_manager,
            metrics=MagicMock(),
            guard_pipeline=pipeline,
            staged_manager=None,
        )
        executor._client = AsyncMock()
        executor._client.get_account_info = AsyncMock(return_value=_make_mock_account_info())

        with pytest.raises(RuntimeError, match="Guard blocked"):
            await executor.place_market_order("BTCUSDT", "BUY", 0.001)

    @pytest.mark.asyncio
    async def test_blocking_guard_rejects_limit_order(self) -> None:
        config = _make_trading_config()
        risk_manager = _make_mock_risk_manager()

        pipeline = GuardPipeline(guards=[AlwaysBlockGuard()])
        executor = TradingExecutor(
            config=config,
            risk_manager=risk_manager,
            metrics=MagicMock(),
            guard_pipeline=pipeline,
            staged_manager=None,
        )
        executor._client = AsyncMock()
        executor._client.get_account_info = AsyncMock(return_value=_make_mock_account_info())

        with pytest.raises(RuntimeError, match="Guard blocked"):
            await executor.place_limit_order("BTCUSDT", "BUY", 50000.0, 0.001)

    @pytest.mark.asyncio
    async def test_second_guard_blocks_short_circuits(self) -> None:
        config = _make_trading_config()
        risk_manager = _make_mock_risk_manager()

        first = AlwaysPassGuard()
        second = AlwaysBlockGuard()
        pipeline = GuardPipeline(guards=[first, second])
        executor = TradingExecutor(
            config=config,
            risk_manager=risk_manager,
            metrics=MagicMock(),
            guard_pipeline=pipeline,
            staged_manager=None,
        )
        executor._client = AsyncMock()
        executor._client.get_account_info = AsyncMock(return_value=_make_mock_account_info())

        with pytest.raises(RuntimeError, match="Guard blocked"):
            await executor.place_market_order("BTCUSDT", "BUY", 0.001)


class TestStagedOrderManager:
    """Staged order lifecycle works correctly when wired in."""

    @pytest.mark.asyncio
    async def test_staged_manager_auto_commit_flow(self) -> None:
        sm = StagedOrderManager(auto_commit=True)
        order = await sm.stage(symbol="BTCUSDT", side="BUY", quantity=0.01)
        assert order.stage == OrderStage.COMMITTED
        stats = sm.get_stats()
        assert stats["STAGED"] == 0
        assert stats["COMMITTED"] == 1

    @pytest.mark.asyncio
    async def test_staged_manager_manual_commit_flow(self) -> None:
        sm = StagedOrderManager(auto_commit=False)
        order = await sm.stage(symbol="BTCUSDT", side="BUY", quantity=0.01)
        assert order.stage == OrderStage.STAGED
        stats = sm.get_stats()
        assert stats["STAGED"] == 1

        await sm.commit(order.order_id)
        stats = sm.get_stats()
        assert stats["COMMITTED"] == 1
        assert stats["STAGED"] == 0

    @pytest.mark.asyncio
    async def test_staged_manager_reject_order(self) -> None:
        sm = StagedOrderManager(auto_commit=True)
        order = await sm.stage(symbol="BTCUSDT", side="BUY", quantity=0.01)
        await sm.mark_rejected(order.order_id, "Guard blocked")
        assert order.stage == OrderStage.REJECTED
        assert order.reject_reason == "Guard blocked"
        stats = sm.get_stats()
        assert stats["REJECTED"] == 1


class TestGuardPipelineApi:
    """GuardPipeline API from risk/guards.py works as documented."""

    def test_empty_pipeline_passes(self) -> None:
        pipeline = GuardPipeline()
        ctx = GuardContext(symbol="BTCUSDT", side="BUY", quantity=0.01, portfolio_value=10000.0)
        result = pipeline.check(ctx)
        assert result.passed
        assert not result.blocked

    def test_single_passing_guard(self) -> None:
        pipeline = GuardPipeline(guards=[AlwaysPassGuard()])
        ctx = GuardContext(symbol="BTCUSDT", side="BUY", quantity=0.01, portfolio_value=10000.0)
        result = pipeline.check(ctx)
        assert result.passed

    def test_single_blocking_guard(self) -> None:
        pipeline = GuardPipeline(guards=[AlwaysBlockGuard()])
        ctx = GuardContext(symbol="BTCUSDT", side="BUY", quantity=0.01, portfolio_value=10000.0)
        result = pipeline.check(ctx)
        assert result.blocked
        assert "AlwaysBlock" in result.guard_name

    def test_chain_add(self) -> None:
        pipeline = GuardPipeline().add(AlwaysPassGuard()).add(AlwaysPassGuard())
        assert len(pipeline.get_guard_names()) == 2

    def test_chain_short_circuits_on_block(self) -> None:
        third = MagicMock()
        third.check.return_value = GuardResult.allow()
        pipeline = GuardPipeline(guards=[AlwaysBlockGuard(), AlwaysPassGuard(), third])
        ctx = GuardContext(symbol="BTCUSDT", side="BUY", quantity=0.01, portfolio_value=10000.0)
        pipeline.check(ctx)
        third.check.assert_not_called()
