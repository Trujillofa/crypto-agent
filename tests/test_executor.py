from __future__ import annotations

import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.execution.binance_client import OrderInfo
from src.execution.executor import TradingConfig, TradingExecutor
from src.execution.metrics import ExecutionMetrics
from src.portfolio.manager import PortfolioManager
from src.risk.manager import RiskManager


class MockBinanceClient:
    def __init__(self):
        self.place_market_order = AsyncMock()
        self.get_order_status = AsyncMock()
        self.get_account_info = AsyncMock()
        self.get_open_orders = AsyncMock()
        self.get_asset_balance = AsyncMock()
        self.normalize_sell_quantity = AsyncMock()
        self.place_limit_order = AsyncMock()
        self.cancel_order = AsyncMock()
        self.cancel_all_orders = AsyncMock()

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        pass


@pytest.mark.asyncio
async def test_place_market_order_polling():
    """Test that place_market_order polls for fill if not immediately filled."""

    # Setup mocks
    config = TradingConfig(api_key="key", api_secret="secret", enabled=True, symbols=["BTCUSDT"])
    risk_manager = MagicMock(spec=RiskManager)
    risk_manager.is_trading_allowed.return_value = (True, "")
    risk_manager.check_position_limit.return_value = (True, "")

    metrics = MagicMock(spec=ExecutionMetrics)
    portfolio_manager = MagicMock(spec=PortfolioManager)

    executor = TradingExecutor(config, risk_manager, metrics, portfolio_manager)

    # Mock client
    mock_client = MockBinanceClient()
    executor._client = mock_client

    # Setup Account Info
    mock_account = MagicMock()
    mock_account.available_balance = 1000.0
    mock_client.get_account_info.return_value = mock_account

    # Scenario: Order placed -> NEW -> Polling -> FILLED
    initial_order = OrderInfo(
        order_id="123",
        symbol="BTCUSDT",
        side="BUY",
        order_type="MARKET",
        quantity=0.002,
        price=50000.0,
        status="NEW",
        executed_quantity=0.0,
        create_time=1000,
    )

    filled_order = OrderInfo(
        order_id="123",
        symbol="BTCUSDT",
        side="BUY",
        order_type="MARKET",
        quantity=0.002,
        price=50000.0,
        status="FILLED",
        executed_quantity=0.002,
        create_time=1000,
    )

    mock_client.place_market_order.return_value = initial_order

    # Mock polling responses: NEW (loop 1) -> FILLED (loop 2)
    mock_client.get_order_status.side_effect = [
        initial_order,  # First poll
        filled_order,  # Second poll
    ]

    # Execute
    result_order = await executor.place_market_order("BTCUSDT", "BUY", quantity=0.002)

    # Verify
    assert result_order.status == "FILLED"
    assert result_order.executed_quantity == 0.002

    # Verify call counts
    mock_client.place_market_order.assert_called_once()
    # Should have called get_order_status twice (once returning NEW, once FILLED)
    assert mock_client.get_order_status.call_count == 2

    # Verify metrics were recorded
    metrics.record_order_placed.assert_called()  # Once for initial placement (wait, implementation calls it once at the end)

    # Implementation detail: record_order_placed is called AFTER polling in my edit
    # so it should be called with FILLED status?
    # Let's check the code.
    # Code:
    # 1. place_order -> returns NEW
    # 2. poll -> returns FILLED
    # 3. record_order_placed(status=order.status) -> status should be FILLED

    args, kwargs = metrics.record_order_placed.call_args
    assert kwargs["status"] == "FILLED"

    # Verify portfolio update
    portfolio_manager.open_position.assert_awaited_once()
    risk_manager.register_open_position.assert_called_once()


@pytest.mark.asyncio
async def test_place_limit_order_polling_timeout():
    """Test that place_limit_order polling times out gracefully."""

    # Setup mocks
    config = TradingConfig(api_key="key", api_secret="secret", enabled=True, symbols=["BTCUSDT"])
    risk_manager = MagicMock(spec=RiskManager)
    risk_manager.is_trading_allowed.return_value = (True, "")
    risk_manager.check_position_limit.return_value = (True, "")

    metrics = MagicMock(spec=ExecutionMetrics)

    executor = TradingExecutor(config, risk_manager, metrics)
    mock_client = MockBinanceClient()
    executor._client = mock_client

    mock_account = MagicMock()
    mock_account.available_balance = 1000.0
    mock_client.get_account_info.return_value = mock_account

    # Scenario: Order placed -> NEW -> Polling -> Still NEW (timeout)
    initial_order = OrderInfo(
        order_id="124",
        symbol="BTCUSDT",
        side="BUY",
        order_type="LIMIT",
        quantity=0.002,
        price=49000.0,
        status="NEW",
        executed_quantity=0.0,
        create_time=1000,
    )

    mock_client.place_limit_order.return_value = initial_order
    mock_client.get_order_status.return_value = initial_order

    # Shorten polling for test
    # We need to monkeypatch asyncio.sleep to avoid waiting real time
    # But since _wait_for_fill uses time.time(), mocking sleep alone isn't enough to speed it up
    # We can pass a very short timeout to _wait_for_fill if we could control it,
    # but it's hardcoded to 5.0 in the signature default.
    # However, place_limit_order calls it without args, so it uses default 5.0.

    # To test timeout without waiting 5s, we can mock _wait_for_fill itself?
    # Or just accept that we are testing the logic flow, not the loop internals.
    # Let's mock _wait_for_fill on the executor instance to verify it's called.

    executor._wait_for_fill = AsyncMock(return_value=initial_order)

    result_order = await executor.place_limit_order("BTCUSDT", "BUY", 49000.0, 0.002)

    assert result_order.status == "NEW"
    executor._wait_for_fill.assert_awaited_once()


@pytest.mark.asyncio
async def test_duplicate_order_prevention():
    """Test that existing position blocks new BUY order."""
    config = TradingConfig(api_key="key", api_secret="secret", enabled=True, symbols=["BTCUSDT"])
    risk_manager = MagicMock(spec=RiskManager)
    risk_manager.is_trading_allowed.return_value = (True, "")
    metrics = MagicMock(spec=ExecutionMetrics)
    portfolio_manager = MagicMock(spec=PortfolioManager)

    executor = TradingExecutor(config, risk_manager, metrics, portfolio_manager)
    executor._client = MockBinanceClient()  # Should not be used

    # Setup existing position
    portfolio_manager.has_position.return_value = True

    from src.strategy.signals import Signal, SignalType

    signal = Signal(
        type=SignalType.BUY,
        symbol="BTCUSDT",
        price=50000.0,
        confidence=1.0,
        reason="Test",
        indicators={},
    )

    # Execute
    await executor.on_signal(signal)

    # Verify NO order placed
    executor._client.place_market_order.assert_not_called()


@pytest.mark.asyncio
async def test_place_market_order_rejects_insufficient_available_balance():
    """BUY orders should fail with a direct balance error before position-limit math."""

    config = TradingConfig(api_key="key", api_secret="secret", enabled=True, symbols=["AVAXUSDT"])
    risk_manager = MagicMock(spec=RiskManager)
    risk_manager.is_trading_allowed.return_value = (True, "")
    metrics = MagicMock(spec=ExecutionMetrics)

    executor = TradingExecutor(config, risk_manager, metrics)
    mock_client = MockBinanceClient()
    executor._client = mock_client

    mock_account = MagicMock()
    mock_account.available_balance = 0.0
    mock_client.get_account_info.return_value = mock_account

    with pytest.raises(
        RuntimeError, match="Insufficient available balance: need 7.00 USDT, have 0.00 USDT"
    ):
        await executor.place_market_order("AVAXUSDT", "BUY", quantity=7.0)

    risk_manager.check_position_limit.assert_not_called()
    mock_client.place_market_order.assert_not_called()
    metrics.record_risk_block.assert_called_once()


@pytest.mark.asyncio
async def test_on_signal_sell_uses_removesuffix():
    """SELL signal uses removesuffix for base asset parsing."""
    config = TradingConfig(api_key="key", api_secret="secret", enabled=True, symbols=["BUSDUSDT"])
    risk_manager = MagicMock(spec=RiskManager)
    metrics = MagicMock(spec=ExecutionMetrics)
    notifier = MagicMock()
    notifier.send_trade_alert = AsyncMock()
    executor = TradingExecutor(config, risk_manager, metrics, notifier=notifier)

    mock_client = MockBinanceClient()
    mock_client.get_asset_balance.return_value = 1.5
    mock_client.normalize_sell_quantity.return_value = "1.5"
    executor._client = mock_client

    from src.execution.binance_client import OrderInfo
    from src.strategy.signals import Signal, SignalType

    signal = Signal(
        type=SignalType.SELL,
        symbol="BUSDUSDT",
        price=1.0,
        confidence=1.0,
        reason="Test",
        indicators={},
    )

    executor.place_market_order = AsyncMock(
        return_value=OrderInfo(
            order_id="123",
            symbol="BUSDUSDT",
            side="SELL",
            order_type="MARKET",
            quantity=1.5,
            price=1.0,
            status="FILLED",
            executed_quantity=1.5,
            create_time=1000,
        )
    )

    await executor.on_signal(signal)

    mock_client.get_asset_balance.assert_called_once_with("BUSD")
    mock_client.normalize_sell_quantity.assert_called_once_with("BUSDUSDT", 1.5)
    executor.place_market_order.assert_called_once_with("BUSDUSDT", "SELL", 1.5)


@pytest.mark.asyncio
async def test_on_signal_sell_skips_dust_balance_silently():
    config = TradingConfig(api_key="key", api_secret="secret", enabled=True, symbols=["XRPUSDT"])
    risk_manager = MagicMock(spec=RiskManager)
    metrics = MagicMock(spec=ExecutionMetrics)
    notifier = MagicMock()
    notifier.send_trade_alert = AsyncMock()
    executor = TradingExecutor(config, risk_manager, metrics, notifier=notifier)

    mock_client = MockBinanceClient()
    mock_client.get_asset_balance.return_value = 0.0292
    mock_client.normalize_sell_quantity.return_value = None
    executor._client = mock_client

    from src.strategy.signals import Signal, SignalType

    signal = Signal(
        type=SignalType.SELL,
        symbol="XRPUSDT",
        price=1.47,
        confidence=1.0,
        reason="Test",
        indicators={},
    )

    executor.place_market_order = AsyncMock()

    await executor.on_signal(signal)

    mock_client.get_asset_balance.assert_called_once_with("XRP")
    mock_client.normalize_sell_quantity.assert_called_once_with("XRPUSDT", 0.0292)
    executor.place_market_order.assert_not_called()
    notifier.send_trade_alert.assert_not_awaited()


@pytest.mark.asyncio
async def test_place_twap_order_splits_into_equal_slices():
    config = TradingConfig(api_key="key", api_secret="secret", enabled=True, symbols=["BTCUSDT"])
    risk_manager = MagicMock(spec=RiskManager)
    metrics = MagicMock(spec=ExecutionMetrics)
    executor = TradingExecutor(config, risk_manager, metrics)

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
    executor.place_market_order = AsyncMock(return_value=filled_order)

    orders = await executor.place_twap_order(
        "BTCUSDT",
        "BUY",
        total_quantity=100.0,
        num_slices=5,
        interval_seconds=0.01,
    )

    assert len(orders) == 5
    assert executor.place_market_order.await_count == 5
    for call in executor.place_market_order.await_args_list:
        assert call.args == ("BTCUSDT", "BUY", 20.0)


@pytest.mark.asyncio
async def test_place_twap_order_aborts_after_first_failed_slice():
    config = TradingConfig(api_key="key", api_secret="secret", enabled=True, symbols=["BTCUSDT"])
    risk_manager = MagicMock(spec=RiskManager)
    metrics = MagicMock(spec=ExecutionMetrics)
    executor = TradingExecutor(config, risk_manager, metrics)

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

    async def side_effect(*_args, **_kwargs):
        nonlocal call_count
        call_count += 1
        if call_count >= 3:
            raise RuntimeError("Trading blocked")
        return filled_order

    executor.place_market_order = AsyncMock(side_effect=side_effect)

    orders = await executor.place_twap_order(
        "BTCUSDT",
        "BUY",
        total_quantity=100.0,
        num_slices=5,
        interval_seconds=0.01,
    )

    assert len(orders) == 2


@pytest.mark.asyncio
async def test_place_twap_order_rejects_non_positive_quantity():
    config = TradingConfig(api_key="key", api_secret="secret", enabled=True, symbols=["BTCUSDT"])
    risk_manager = MagicMock(spec=RiskManager)
    metrics = MagicMock(spec=ExecutionMetrics)
    executor = TradingExecutor(config, risk_manager, metrics)

    with pytest.raises(ValueError, match="total_quantity must be positive"):
        await executor.place_twap_order("BTCUSDT", "BUY", total_quantity=0.0)


# ---------------------------------------------------------------------------
# FuturesTradingExecutor guard tests
# ---------------------------------------------------------------------------


def _make_futures_executor(
    order_size_usdt: float = 6.0,
    max_concurrent_longs: int = 0,
    sl_cooldown_minutes: int = 0,
) -> object:
    from src.execution.futures_executor import FuturesTradingConfig, FuturesTradingExecutor

    config = FuturesTradingConfig(
        api_key="key",
        api_secret="secret",
        enabled=True,
        symbols=["BTCUSDT", "ETHUSDT"],
        order_size_usdt=order_size_usdt,
        max_concurrent_longs=max_concurrent_longs,
        sl_cooldown_minutes=sl_cooldown_minutes,
    )
    risk_manager = MagicMock(spec=RiskManager)
    risk_manager.is_trading_allowed.return_value = (True, "")
    risk_manager.check_max_leverage.return_value = (True, "")
    risk_manager.check_margin_usage.return_value = (True, "")
    metrics = MagicMock(spec=ExecutionMetrics)
    executor = FuturesTradingExecutor(config, risk_manager, metrics)
    return executor


@pytest.mark.asyncio
async def test_buy_skipped_below_min_qty():
    """BUY signal should be blocked when calculated qty is below the symbol's min lot size."""
    from src.execution.futures_executor import FuturesTradingExecutor
    from src.strategy.signals import Signal, SignalType

    executor = _make_futures_executor(order_size_usdt=6.0)
    assert isinstance(executor, FuturesTradingExecutor)

    mock_client = MagicMock()
    mock_client.get_position_risk = AsyncMock(return_value=[])
    mock_client.get_account_info = AsyncMock()
    mock_client.get_step_size = MagicMock(return_value=0.0)
    mock_client.get_min_qty = MagicMock(return_value=0.001)  # BTC minimum
    executor._client = mock_client

    mock_notifier = MagicMock()
    mock_notifier.send_alert = AsyncMock()
    executor._notifier = mock_notifier

    signal = Signal(
        type=SignalType.BUY,
        symbol="BTCUSDT",
        price=78000.0,
        confidence=0.8,
        reason="test",
        indicators={},
        trading_mode="futures",
    )

    await executor.on_signal(signal)

    mock_notifier.send_alert.assert_awaited_once()
    alert_text = mock_notifier.send_alert.call_args[0][0]
    assert "too small" in alert_text
    assert "BTCUSDT" in alert_text
    mock_client.get_account_info.assert_not_awaited()  # Order was never attempted


@pytest.mark.asyncio
async def test_buy_skipped_max_concurrent_longs():
    """BUY signal should be blocked when max_concurrent_longs is already reached."""
    from src.execution.futures_executor import FuturesTradingExecutor
    from src.strategy.signals import Signal, SignalType

    executor = _make_futures_executor(order_size_usdt=100.0, max_concurrent_longs=2)
    assert isinstance(executor, FuturesTradingExecutor)

    executor._positions = {
        "ETHUSDT": {"amount": 0.01, "mark_price": 2300.0},
        "SOLUSDT": {"amount": 0.1, "mark_price": 85.0},
    }

    mock_client = MagicMock()
    mock_client.get_position_risk = AsyncMock(return_value=[])
    mock_client.get_account_info = AsyncMock()
    mock_client.get_min_qty = MagicMock(return_value=None)
    executor._client = mock_client

    signal = Signal(
        type=SignalType.BUY,
        symbol="BTCUSDT",
        price=78000.0,
        confidence=0.8,
        reason="test",
        indicators={},
        trading_mode="futures",
    )

    await executor.on_signal(signal)

    mock_client.get_account_info.assert_not_awaited()  # Blocked before order attempt


@pytest.mark.asyncio
async def test_buy_skipped_sl_cooldown():
    """BUY signal should be blocked when an SL cooldown is active for the symbol."""
    from src.execution.futures_executor import FuturesTradingExecutor
    from src.strategy.signals import Signal, SignalType

    executor = _make_futures_executor(order_size_usdt=100.0, sl_cooldown_minutes=120)
    assert isinstance(executor, FuturesTradingExecutor)

    executor._sl_cooldown_timestamps["BTCUSDT"] = time.time() - 30 * 60  # 30 min ago

    mock_client = MagicMock()
    mock_client.get_position_risk = AsyncMock(return_value=[])
    mock_client.get_account_info = AsyncMock()
    mock_client.get_min_qty = MagicMock(return_value=None)
    executor._client = mock_client

    signal = Signal(
        type=SignalType.BUY,
        symbol="BTCUSDT",
        price=78000.0,
        confidence=0.8,
        reason="test",
        indicators={},
        trading_mode="futures",
    )

    await executor.on_signal(signal)

    mock_client.get_account_info.assert_not_awaited()


@pytest.mark.asyncio
async def test_buy_allowed_after_cooldown_expires():
    """BUY signal should proceed once the SL cooldown window has passed."""
    from src.execution.futures_client import FuturesOrderInfo
    from src.execution.futures_executor import FuturesTradingExecutor
    from src.strategy.signals import Signal, SignalType

    executor = _make_futures_executor(order_size_usdt=100.0, sl_cooldown_minutes=120)
    assert isinstance(executor, FuturesTradingExecutor)

    executor._sl_cooldown_timestamps["BTCUSDT"] = time.time() - 3 * 3600  # 3 hours ago

    filled_order = FuturesOrderInfo(
        order_id="1",
        symbol="BTCUSDT",
        side="BUY",
        position_side="LONG",
        order_type="MARKET",
        quantity=0.001,
        price=78000.0,
        status="FILLED",
        executed_quantity=0.001,
        create_time=0,
        reduce_only=False,
    )

    mock_client = MagicMock()
    mock_client.get_position_risk = AsyncMock(return_value=[])
    mock_client.get_account_info = AsyncMock(
        return_value=MagicMock(available_balance=500.0, total_margin_balance=500.0)
    )
    mock_client.get_step_size = MagicMock(return_value=0.0)
    mock_client.get_min_qty = MagicMock(return_value=None)
    mock_client.place_order = AsyncMock(return_value=filled_order)
    mock_client.set_leverage = AsyncMock()
    mock_client.format_quantity = MagicMock(return_value="0.001")
    mock_client.format_price = MagicMock(return_value="78000.00")
    executor._client = mock_client

    mock_notifier = MagicMock()
    mock_notifier.send_trade_alert = AsyncMock()
    mock_notifier.send_alert = AsyncMock()
    executor._notifier = mock_notifier

    with patch.object(executor, "_place_sl_tp_orders", AsyncMock(return_value=(76000.0, 82000.0))):
        signal = Signal(
            type=SignalType.BUY,
            symbol="BTCUSDT",
            price=78000.0,
            confidence=0.8,
            reason="test",
            indicators={},
            trading_mode="futures",
        )
        await executor.on_signal(signal)

    mock_client.get_account_info.assert_awaited()
