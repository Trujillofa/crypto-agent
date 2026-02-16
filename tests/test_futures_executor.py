"""Tests for FuturesTradingExecutor."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.execution.futures_executor import FuturesTradingExecutor, FuturesTradingConfig
from src.execution.futures_client import FuturesOrderInfo, FuturesPositionInfo
from src.risk.manager import RiskManager
from src.execution.metrics import ExecutionMetrics
from src.strategy.signals import Signal, SignalType


class TestFuturesTradingExecutor:
    """Test suite for FuturesTradingExecutor."""

    @pytest.fixture
    def config(self):
        """Create test configuration."""
        return FuturesTradingConfig(
            api_key="test_key",
            api_secret="test_secret",
            test_mode=True,
            enabled=True,
            symbols=["BTCUSDT", "ETHUSDT"],
            default_leverage=5,
            max_leverage=10,
            order_size_usdt=100.0,
        )

    @pytest.fixture
    def executor(self, config):
        """Create executor with mocked dependencies."""
        risk_manager = MagicMock(spec=RiskManager)
        risk_manager.is_trading_allowed.return_value = (True, "OK")
        risk_manager.check_max_leverage.return_value = (True, "OK")
        risk_manager.check_margin_usage.return_value = (True, "OK")
        risk_manager.check_liquidation_buffer.return_value = (True, "OK")

        metrics = MagicMock(spec=ExecutionMetrics)

        return FuturesTradingExecutor(
            config=config,
            risk_manager=risk_manager,
            metrics=metrics,
        )

    def test_executor_initialization(self, executor):
        """Test executor initializes correctly."""
        assert executor._config.enabled is True
        assert executor._config.default_leverage == 5
        assert executor._config.symbols == ["BTCUSDT", "ETHUSDT"]
        assert executor._positions == {}

    @pytest.mark.asyncio
    async def test_place_futures_order_disabled(self, executor):
        """Test that disabled executor rejects orders."""
        executor._config = FuturesTradingConfig(
            api_key="test_key",
            api_secret="test_secret",
            enabled=False,  # Disabled
        )

        with pytest.raises(RuntimeError) as exc_info:
            await executor.place_futures_order("BTCUSDT", "BUY", 0.01)

        assert "disabled" in str(exc_info.value).lower()

    @pytest.mark.asyncio
    async def test_place_futures_order_risk_check_fails(self, executor):
        """Test that failed risk check blocks order."""
        executor._risk_manager.is_trading_allowed.return_value = (
            False,
            "Kill switch active",
        )

        # Mock client
        executor._client = MagicMock()

        with pytest.raises(RuntimeError) as exc_info:
            await executor.place_futures_order("BTCUSDT", "BUY", 0.01)

        assert "blocked" in str(exc_info.value).lower()

    @pytest.mark.asyncio
    async def test_on_signal_buy_opens_long(self, executor):
        """Test BUY signal opens LONG position."""
        # Mock client with no existing position
        mock_client = MagicMock()
        mock_client.get_position_risk = AsyncMock(return_value=[])
        mock_client.get_account_info = AsyncMock(
            return_value=MagicMock(
                total_margin_balance=10000.0,
                available_balance=5000.0,
            )
        )
        mock_client.place_order = AsyncMock(
            return_value=FuturesOrderInfo(
                order_id="12345",
                symbol="BTCUSDT",
                side="BUY",
                position_side="LONG",
                order_type="MARKET",
                quantity=0.01,
                price=50000.0,
                status="FILLED",
                executed_quantity=0.01,
                create_time=1234567890,
                reduce_only=False,
            )
        )
        executor._client = mock_client
        executor._notifier = AsyncMock()

        signal = Signal(
            type=SignalType.BUY,
            symbol="BTCUSDT",
            price=50000.0,
            confidence=0.85,
            reason="EMA crossover",
            indicators={},
            trading_mode="futures",
        )

        await executor.on_signal(signal)

        # Verify order was placed
        mock_client.place_order.assert_called_once()
        call_kwargs = mock_client.place_order.call_args.kwargs
        assert call_kwargs["symbol"] == "BTCUSDT"
        assert call_kwargs["side"] == "BUY"
        assert call_kwargs["position_side"] == "LONG"
        assert call_kwargs["reduce_only"] is False

    @pytest.mark.asyncio
    async def test_on_signal_sell_closes_long(self, executor):
        """Test SELL signal closes LONG position with reduceOnly."""
        # Mock client with existing LONG position
        mock_client = MagicMock()
        mock_client.get_position_risk = AsyncMock(
            return_value=[
                FuturesPositionInfo(
                    symbol="BTCUSDT",
                    position_side="LONG",
                    position_amt=0.01,
                    entry_price=49000.0,
                    mark_price=50000.0,
                    liquidation_price=45000.0,
                    leverage=5,
                    isolated_margin=100.0,
                    unrealized_pnl=100.0,
                    notional_value=500.0,
                )
            ]
        )
        mock_client.get_account_info = AsyncMock(
            return_value=MagicMock(
                total_margin_balance=10000.0,
                available_balance=5000.0,
            )
        )
        mock_client.place_order = AsyncMock(
            return_value=FuturesOrderInfo(
                order_id="12346",
                symbol="BTCUSDT",
                side="SELL",
                position_side="LONG",
                order_type="MARKET",
                quantity=0.01,
                price=50000.0,
                status="FILLED",
                executed_quantity=0.01,
                create_time=1234567890,
                reduce_only=True,  # Important: close position
            )
        )
        executor._client = mock_client
        executor._notifier = AsyncMock()

        signal = Signal(
            type=SignalType.SELL,
            symbol="BTCUSDT",
            price=50000.0,
            confidence=0.80,
            reason="EMA divergence",
            indicators={},
            trading_mode="futures",
        )

        await executor.on_signal(signal)

        # Verify order was placed with reduce_only=True
        mock_client.place_order.assert_called_once()
        call_kwargs = mock_client.place_order.call_args.kwargs
        assert call_kwargs["symbol"] == "BTCUSDT"
        assert call_kwargs["side"] == "SELL"
        assert call_kwargs["position_side"] == "LONG"
        assert call_kwargs["reduce_only"] is True  # Key: reduces position
        assert call_kwargs["quantity"] == 0.01  # Full position size

    @pytest.mark.asyncio
    async def test_on_signal_buy_ignores_when_long_exists(self, executor):
        mock_client = MagicMock()
        mock_client.get_position_risk = AsyncMock(
            return_value=[
                FuturesPositionInfo(
                    symbol="BTCUSDT",
                    position_side="LONG",
                    position_amt=0.01,
                    entry_price=49000.0,
                    mark_price=50000.0,
                    liquidation_price=45000.0,
                    leverage=5,
                    isolated_margin=100.0,
                    unrealized_pnl=100.0,
                    notional_value=500.0,
                )
            ]
        )
        executor._client = mock_client
        executor._notifier = AsyncMock()

        signal = Signal(
            type=SignalType.BUY,
            symbol="BTCUSDT",
            price=50000.0,
            confidence=0.85,
            reason="EMA crossover",
            indicators={},
            trading_mode="futures",
        )

        await executor.on_signal(signal)

        mock_client.place_order.assert_not_called()

    @pytest.mark.asyncio
    async def test_on_signal_ignores_non_futures_mode(self, executor):
        """Test that signals with non-futures trading_mode are ignored."""
        executor._client = MagicMock()
        executor._notifier = AsyncMock()

        signal = Signal(
            type=SignalType.BUY,
            symbol="BTCUSDT",
            price=50000.0,
            confidence=0.85,
            reason="EMA crossover",
            indicators={},
            trading_mode="spot",  # Not futures!
        )

        await executor.on_signal(signal)

        # Verify no order was placed
        executor._client.place_order.assert_not_called()

    @pytest.mark.asyncio
    async def test_on_signal_sell_no_position(self, executor):
        """Test SELL signal with no position is ignored."""
        # Mock client with no position
        mock_client = MagicMock()
        mock_client.get_position_risk = AsyncMock(return_value=[])
        executor._client = mock_client
        executor._notifier = AsyncMock()

        signal = Signal(
            type=SignalType.SELL,
            symbol="BTCUSDT",
            price=50000.0,
            confidence=0.80,
            reason="EMA divergence",
            indicators={},
            trading_mode="futures",
        )

        await executor.on_signal(signal)

        # Verify no order was placed
        mock_client.place_order.assert_not_called()

    @pytest.mark.asyncio
    async def test_liquidation_buffer_check(self, executor):
        """Test that liquidation buffer check is performed."""
        # Mock client with position near liquidation
        mock_client = MagicMock()
        mock_client.get_position_risk = AsyncMock(
            return_value=[
                FuturesPositionInfo(
                    symbol="BTCUSDT",
                    position_side="BOTH",
                    position_amt=0.1,
                    entry_price=50000.0,
                    mark_price=45500.0,  # Close to liq
                    liquidation_price=45000.0,
                    leverage=10,
                    isolated_margin=500.0,
                    unrealized_pnl=-450.0,
                    notional_value=4550.0,
                )
            ]
        )
        mock_client.get_account_info = AsyncMock(
            return_value=MagicMock(
                total_margin_balance=10000.0,
                available_balance=5000.0,
            )
        )
        executor._client = mock_client

        executor._risk_manager.check_liquidation_buffer.return_value = (
            False,  # Within buffer!
            "LONG position within 5.0% of liquidation",
        )

        # Should trigger alert
        executor._notifier = AsyncMock()

        await executor._monitor_and_update()

        # Verify liquidation check was called (once per symbol with position)
        assert executor._risk_manager.check_liquidation_buffer.call_count == 2
        first_call = executor._risk_manager.check_liquidation_buffer.call_args_list[0]
        assert first_call.kwargs["position_side"] == "LONG"

    @pytest.mark.asyncio
    async def test_leverage_check_blocks_excessive_leverage(self, executor):
        """Test that excessive leverage is blocked."""
        mock_client = MagicMock()
        mock_client.get_account_info = AsyncMock(
            return_value=MagicMock(
                total_margin_balance=10000.0,
                available_balance=5000.0,
            )
        )
        executor._client = mock_client
        executor._risk_manager.check_max_leverage.return_value = (
            False,
            "Leverage 50x exceeds hard safety cap of 20x",
        )

        with pytest.raises(RuntimeError) as exc_info:
            await executor.place_futures_order("BTCUSDT", "BUY", 0.01)

        assert "Leverage" in str(exc_info.value)

    def test_calculate_quantity_defaults(self, executor):
        """Test default quantity calculations."""
        btc_qty = executor._calculate_quantity("BTCUSDT")
        eth_qty = executor._calculate_quantity("ETHUSDT")
        unknown_qty = executor._calculate_quantity("UNKNOWN")

        assert btc_qty == 0.01
        assert eth_qty == 0.1
        assert unknown_qty == 0.01  # Default
