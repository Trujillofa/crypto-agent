"""Tests for staged order queue."""

from src.execution.staged import (
    OrderStage,
    StagedOrder,
    StagedOrderQueue,
    get_staged_order_queue,
)
from src.strategy.signals import SignalType


class TestStagedOrder:
    """Tests for StagedOrder dataclass."""

    def test_create_staged_order(self):
        order = StagedOrder(
            id="test_001",
            symbol="BTCUSDT",
            side=SignalType.BUY,
            quantity=0.01,
        )
        assert order.id == "test_001"
        assert order.symbol == "BTCUSDT"
        assert order.side == SignalType.BUY
        assert order.quantity == 0.01
        assert order.stage == OrderStage.STAGED
        assert order.hash == ""

    def test_to_dict(self):
        order = StagedOrder(
            id="test_001",
            symbol="BTCUSDT",
            side=SignalType.BUY,
            quantity=0.01,
            signal_confidence=0.75,
            signal_reason="Test signal",
        )
        data = order.to_dict()
        assert data["id"] == "test_001"
        assert data["symbol"] == "BTCUSDT"
        assert data["side"] == "BUY"
        assert data["quantity"] == 0.01
        assert data["stage"] == "staged"
        assert data["signal_confidence"] == 0.75
        assert data["filled_at"] is None


class TestStagedOrderQueue:
    """Tests for StagedOrderQueue."""

    def test_stage_order(self):
        queue = StagedOrderQueue()
        order = queue.stage(
            symbol="BTCUSDT",
            side=SignalType.BUY,
            quantity=0.01,
            signal_confidence=0.8,
            signal_reason="Test",
        )
        assert order.symbol == "BTCUSDT"
        assert order.stage == OrderStage.STAGED

    def test_commit_order(self):
        queue = StagedOrderQueue()
        order = queue.stage(
            symbol="BTCUSDT",
            side=SignalType.BUY,
            quantity=0.01,
        )
        order_id = order.id

        commit_hash = queue.commit(order_id)
        assert commit_hash is not None
        assert len(commit_hash) == 8

        committed_order = queue._orders[order_id]
        assert committed_order.stage == OrderStage.COMMITTED
        assert committed_order.hash == commit_hash
        assert committed_order.committed_at is not None

    def test_commit_nonexistent_order(self):
        queue = StagedOrderQueue()
        result = queue.commit("nonexistent")
        assert result is None

    def test_commit_already_committed(self):
        queue = StagedOrderQueue()
        order = queue.stage(symbol="BTCUSDT", side=SignalType.BUY, quantity=0.01)
        queue.commit(order.id)

        result = queue.commit(order.id)
        assert result is None

    def test_get_pending(self):
        queue = StagedOrderQueue()
        queue.stage(symbol="BTCUSDT", side=SignalType.BUY, quantity=0.01)
        queue.stage(symbol="ETHUSDT", side=SignalType.BUY, quantity=0.1)

        pending = queue.get_pending()
        assert len(pending) == 2

    def test_get_ready(self):
        queue = StagedOrderQueue()
        o1 = queue.stage(symbol="BTCUSDT", side=SignalType.BUY, quantity=0.01)
        queue.stage(symbol="ETHUSDT", side=SignalType.BUY, quantity=0.1)

        queue.commit(o1.id)

        ready = queue.get_ready()
        assert len(ready) == 1
        assert ready[0].symbol == "BTCUSDT"

    def test_commit_all_pending(self):
        queue = StagedOrderQueue()
        queue.stage(symbol="BTCUSDT", side=SignalType.BUY, quantity=0.01)
        queue.stage(symbol="ETHUSDT", side=SignalType.BUY, quantity=0.1)

        hashes = queue.commit_all_pending()
        assert len(hashes) == 2
        assert all(len(h) == 8 for h in hashes)

    def test_update_stage(self):
        queue = StagedOrderQueue()
        order = queue.stage(symbol="BTCUSDT", side=SignalType.BUY, quantity=0.01)

        success = queue.update_stage(order.id, OrderStage.PUSHED, exchange_order_id="12345")
        assert success is True
        assert queue._orders[order.id].stage == OrderStage.PUSHED
        assert queue._orders[order.id].exchange_order_id == "12345"

    def test_get_by_hash(self):
        queue = StagedOrderQueue()
        order = queue.stage(symbol="BTCUSDT", side=SignalType.BUY, quantity=0.01)
        commit_hash = queue.commit(order.id)

        found = queue.get_by_hash(commit_hash)
        assert found is not None
        assert found.id == order.id

    def test_get_history(self):
        queue = StagedOrderQueue()
        for i in range(5):
            queue.stage(symbol=f"SYM{i}USDT", side=SignalType.BUY, quantity=0.01)

        history = queue.get_history(limit=3)
        assert len(history) == 3

    def test_get_stats(self):
        queue = StagedOrderQueue()
        o1 = queue.stage(symbol="BTCUSDT", side=SignalType.BUY, quantity=0.01)
        queue.stage(symbol="ETHUSDT", side=SignalType.BUY, quantity=0.1)

        queue.commit(o1.id)

        stats = queue.get_stats()
        assert stats["staged"] == 1
        assert stats["committed"] == 1

    def test_stage_trims_oldest_when_history_limit_exceeded(self):
        queue = StagedOrderQueue(max_history=2)
        first = queue.stage(symbol="BTCUSDT", side=SignalType.BUY, quantity=0.01)
        queue.stage(symbol="ETHUSDT", side=SignalType.BUY, quantity=0.1)
        third = queue.stage(symbol="SOLUSDT", side=SignalType.BUY, quantity=0.2)

        assert first.id not in queue._orders
        assert third.id in queue._orders
        assert len(queue._orders) == 2

    def test_update_stage_filled_sets_filled_at_without_overwriting_pushed_at(self):
        queue = StagedOrderQueue()
        order = queue.stage(symbol="BTCUSDT", side=SignalType.BUY, quantity=0.01)
        queue.update_stage(order.id, OrderStage.PUSHED, exchange_order_id="12345")
        pushed_at = queue._orders[order.id].pushed_at

        success = queue.update_stage(order.id, OrderStage.FILLED)

        assert success is True
        updated = queue._orders[order.id]
        assert updated.stage == OrderStage.FILLED
        assert updated.filled_at is not None
        assert updated.pushed_at == pushed_at

    def test_update_stage_filled_without_pushed_logs_warning(self):
        queue = StagedOrderQueue()
        order = queue.stage(symbol="BTCUSDT", side=SignalType.BUY, quantity=0.01)

        success = queue.update_stage(order.id, OrderStage.FILLED)

        assert success is True
        updated = queue._orders[order.id]
        assert updated.pushed_at is None
        assert updated.filled_at is not None


class TestGlobalQueue:
    """Tests for global queue singleton."""

    def test_get_staged_order_queue(self):
        queue1 = get_staged_order_queue()
        queue2 = get_staged_order_queue()
        assert queue1 is queue2
