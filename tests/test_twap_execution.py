import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.execution.binance_client import OrderInfo
from src.execution.executor import TradingConfig, TradingExecutor
from src.execution.metrics import ExecutionMetrics
from src.risk.manager import RiskManager


@pytest.fixture
def trading_config():
    return TradingConfig(
        api_key="test_key",
        api_secret="test_secret",
        test_mode=True,
        enabled=True,
        symbols=["BTCUSDT"],
        order_size_usdt=100.0,
    )


@pytest.fixture
def risk_manager():
    manager = MagicMock(spec=RiskManager)
    manager.is_trading_allowed.return_value = (True, "")
    manager.check_position_limit.return_value = (True, "")
    return manager


@pytest.fixture
def metrics():
    return MagicMock(spec=ExecutionMetrics)


class TestTWAPExecution:
    @pytest.mark.asyncio
    async def test_twap_splits_order(self, trading_config, risk_manager, metrics):
        """TWAP splits a single order into multiple slices."""
        executor = TradingExecutor(trading_config, risk_manager, metrics)

        filled_order = OrderInfo(
            order_id="123",
            symbol="BTCUSDT",
            side="BUY",
            order_type="MARKET",
            quantity=20.0,
            price=None,
            status="FILLED",
            executed_quantity=20.0,
            create_time=int(time.time() * 1000),
        )

        with patch.object(
            executor, "place_market_order", new_callable=AsyncMock
        ) as mock_order:
            mock_order.return_value = filled_order

            orders = await executor.place_twap_order(
                "BTCUSDT", "BUY", total_quantity=100.0,
                num_slices=5, interval_seconds=0.01,  # Short interval for tests
            )

        assert len(orders) == 5
        assert mock_order.call_count == 5
        # Each slice should be 100/5 = 20.0
        for call in mock_order.call_args_list:
            assert call[0] == ("BTCUSDT", "BUY", 20.0)

    @pytest.mark.asyncio
    async def test_twap_aborts_on_failure(self, trading_config, risk_manager, metrics):
        """TWAP aborts remaining slices if one fails."""
        executor = TradingExecutor(trading_config, risk_manager, metrics)

        filled_order = OrderInfo(
            order_id="123",
            symbol="BTCUSDT",
            side="BUY",
            order_type="MARKET",
            quantity=50.0,
            price=None,
            status="FILLED",
            executed_quantity=50.0,
            create_time=int(time.time() * 1000),
        )

        call_count = 0

        async def side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count >= 3:
                raise RuntimeError("Trading blocked")
            return filled_order

        with patch.object(
            executor, "place_market_order", side_effect=side_effect,
        ):
            orders = await executor.place_twap_order(
                "BTCUSDT", "BUY", total_quantity=100.0,
                num_slices=5, interval_seconds=0.01,
            )

        # Should have 2 successful slices before abort
        assert len(orders) == 2

    @pytest.mark.asyncio
    async def test_twap_single_slice(self, trading_config, risk_manager, metrics):
        """TWAP with num_slices=1 is equivalent to a single market order."""
        executor = TradingExecutor(trading_config, risk_manager, metrics)

        filled_order = OrderInfo(
            order_id="123",
            symbol="BTCUSDT",
            side="BUY",
            order_type="MARKET",
            quantity=100.0,
            price=None,
            status="FILLED",
            executed_quantity=100.0,
            create_time=int(time.time() * 1000),
        )

        with patch.object(
            executor, "place_market_order", new_callable=AsyncMock
        ) as mock_order:
            mock_order.return_value = filled_order

            orders = await executor.place_twap_order(
                "BTCUSDT", "BUY", total_quantity=100.0,
                num_slices=1, interval_seconds=0.01,
            )

        assert len(orders) == 1
        mock_order.assert_called_once_with("BTCUSDT", "BUY", 100.0)
