from __future__ import annotations

from dataclasses import dataclass

import pytest

from src.execution.staged_orders import OrderStage, StagedOrder, StagedOrderManager

pytestmark = pytest.mark.asyncio


@dataclass(frozen=True)
class CompletedOrderResult:
    order_id: str
    symbol: str
    side: str
    order_type: str
    quantity: float
    price: float | None
    status: str
    executed_quantity: float
    create_time: int
    filled_qty: float
    executed_price: float | None = None


class TestStagedOrder:
    async def test_create_staged_order(self) -> None:
        order = StagedOrder(
            order_id="test_001",
            symbol="BTCUSDT",
            side="BUY",
            quantity=0.01,
        )

        assert order.order_id == "test_001"
        assert order.symbol == "BTCUSDT"
        assert order.side == "BUY"
        assert order.quantity == 0.01
        assert order.stage == OrderStage.STAGED
        assert order.metadata == {}


class TestStagedOrderManager:
    async def test_stage_order(self) -> None:
        manager = StagedOrderManager()

        order = await manager.stage(
            symbol="BTCUSDT",
            side="BUY",
            quantity=0.01,
            metadata={"strategy": "test"},
        )

        assert order.symbol == "BTCUSDT"
        assert order.side == "BUY"
        assert order.quantity == 0.01
        assert order.stage == OrderStage.STAGED
        assert order.metadata == {"strategy": "test"}
        assert manager.get_staged(order.order_id) is order

    async def test_commit_order(self) -> None:
        manager = StagedOrderManager()
        order = await manager.stage(symbol="BTCUSDT", side="BUY", quantity=0.01)

        committed = await manager.commit(order.order_id)

        assert committed is order
        assert committed.stage == OrderStage.COMMITTED
        assert committed.committed_at is not None

    async def test_commit_nonexistent_order_raises_value_error(self) -> None:
        manager = StagedOrderManager()

        with pytest.raises(ValueError, match="not found"):
            await manager.commit("missing")

    async def test_commit_already_committed_order_raises_value_error(self) -> None:
        manager = StagedOrderManager()
        order = await manager.stage(symbol="BTCUSDT", side="BUY", quantity=0.01)
        await manager.commit(order.order_id)

        with pytest.raises(ValueError, match="not in STAGED state"):
            await manager.commit(order.order_id)

    async def test_get_all_staged_returns_only_staged_orders(self) -> None:
        manager = StagedOrderManager()
        staged_order = await manager.stage(symbol="BTCUSDT", side="BUY", quantity=0.01)
        committed_order = await manager.stage(symbol="ETHUSDT", side="BUY", quantity=0.1)
        await manager.commit(committed_order.order_id)

        staged = manager.get_all_staged()

        assert staged == [staged_order]

    async def test_get_all_committed_returns_only_committed_orders(self) -> None:
        manager = StagedOrderManager()
        order = await manager.stage(symbol="BTCUSDT", side="BUY", quantity=0.01)
        await manager.stage(symbol="ETHUSDT", side="BUY", quantity=0.1)
        await manager.commit(order.order_id)

        committed = manager.get_all_committed()

        assert len(committed) == 1
        assert committed[0].symbol == "BTCUSDT"
        assert committed[0].stage == OrderStage.COMMITTED

    async def test_mark_executing_updates_stage_and_timestamp(self) -> None:
        manager = StagedOrderManager()
        order = await manager.stage(symbol="BTCUSDT", side="BUY", quantity=0.01)
        await manager.commit(order.order_id)

        executing = await manager.mark_executing(order.order_id)

        assert executing.stage == OrderStage.EXECUTING
        assert executing.executed_at is not None

    async def test_mark_completed_sets_result_and_filled_stage(self) -> None:
        manager = StagedOrderManager()
        order = await manager.stage(symbol="BTCUSDT", side="BUY", quantity=0.01)
        await manager.commit(order.order_id)
        await manager.mark_executing(order.order_id)
        result = CompletedOrderResult(
            order_id="exchange-123",
            symbol="BTCUSDT",
            side="BUY",
            order_type="MARKET",
            quantity=0.01,
            price=None,
            status="FILLED",
            executed_quantity=0.01,
            create_time=1234567890,
            filled_qty=0.01,
            executed_price=65000.0,
        )

        completed = await manager.mark_completed(order.order_id, result)

        assert completed.stage == OrderStage.FILLED
        assert completed.result == result

    async def test_mark_rejected_sets_reason_and_rejected_stage(self) -> None:
        manager = StagedOrderManager()
        order = await manager.stage(symbol="BTCUSDT", side="BUY", quantity=0.01)

        rejected = await manager.mark_rejected(order.order_id, "Risk block")

        assert rejected.stage == OrderStage.REJECTED
        assert rejected.reject_reason == "Risk block"

    async def test_cancel_marks_order_canceled(self) -> None:
        manager = StagedOrderManager()
        order = await manager.stage(symbol="BTCUSDT", side="BUY", quantity=0.01)

        canceled = await manager.cancel(order.order_id)

        assert canceled.stage == OrderStage.CANCELED

    async def test_cancel_executing_order_raises_value_error(self) -> None:
        manager = StagedOrderManager()
        order = await manager.stage(symbol="BTCUSDT", side="BUY", quantity=0.01)
        await manager.commit(order.order_id)
        await manager.mark_executing(order.order_id)

        with pytest.raises(ValueError, match="Cannot cancel order"):
            await manager.cancel(order.order_id)

    async def test_cancel_filled_order_raises_value_error(self) -> None:
        manager = StagedOrderManager()
        order = await manager.stage(symbol="BTCUSDT", side="BUY", quantity=0.01)
        await manager.commit(order.order_id)
        await manager.mark_executing(order.order_id)
        result = CompletedOrderResult(
            order_id="exchange-123",
            symbol="BTCUSDT",
            side="BUY",
            order_type="MARKET",
            quantity=0.01,
            price=None,
            status="FILLED",
            executed_quantity=0.01,
            create_time=1234567890,
            filled_qty=0.01,
        )
        await manager.mark_completed(order.order_id, result)

        with pytest.raises(ValueError, match="Cannot cancel order"):
            await manager.cancel(order.order_id)

    async def test_auto_commit_moves_new_orders_to_committed(self) -> None:
        manager = StagedOrderManager(auto_commit=True)

        order = await manager.stage(symbol="BTCUSDT", side="BUY", quantity=0.01)

        assert order.stage == OrderStage.COMMITTED
        assert order.committed_at is not None
        assert manager.get_all_staged() == []
        assert manager.get_all_committed() == [order]

    async def test_get_stats_counts_orders_by_stage(self) -> None:
        manager = StagedOrderManager()
        committed = await manager.stage(symbol="BTCUSDT", side="BUY", quantity=0.01)
        staged = await manager.stage(symbol="ETHUSDT", side="BUY", quantity=0.1)
        rejected = await manager.stage(symbol="SOLUSDT", side="SELL", quantity=0.2)
        canceled = await manager.stage(symbol="XRPUSDT", side="SELL", quantity=10)

        await manager.commit(committed.order_id)
        await manager.mark_rejected(rejected.order_id, "Risk check failed")
        await manager.cancel(canceled.order_id)

        stats = manager.get_stats()

        assert stats[OrderStage.STAGED.name] == 1
        assert stats[OrderStage.COMMITTED.name] == 1
        assert stats[OrderStage.REJECTED.name] == 1
        assert stats[OrderStage.CANCELED.name] == 1
        assert stats[OrderStage.EXECUTING.name] == 0
        assert stats[OrderStage.FILLED.name] == 0
        assert staged.stage == OrderStage.STAGED
