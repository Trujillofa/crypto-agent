"""Tests for FuturesTradingExecutor."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.execution.futures_client import (
    AlgoOrderInfo,
    BinanceFuturesApiError,
    FuturesOrderInfo,
    FuturesPositionInfo,
)
from src.execution.futures_executor import FuturesTradingConfig, FuturesTradingExecutor
from src.execution.metrics import ExecutionMetrics
from src.risk.manager import RiskManager
from src.strategy.signals import Signal, SignalType


def _make_order(
    order_id: str = "111",
    symbol: str = "BTCUSDT",
    side: str = "BUY",
    quantity: float = 0.01,
    price: float = 50000.0,
    status: str = "FILLED",
    reduce_only: bool = False,
    order_type: str = "MARKET",
) -> FuturesOrderInfo:
    return FuturesOrderInfo(
        order_id=order_id,
        symbol=symbol,
        side=side,
        position_side="LONG",
        order_type=order_type,
        quantity=quantity,
        price=price,
        status=status,
        executed_quantity=quantity,
        create_time=1234567890,
        reduce_only=reduce_only,
    )


def _make_algo_order(
    algo_id: str = "algo_111",
    symbol: str = "BTCUSDT",
    side: str = "SELL",
    order_type: str = "STOP_MARKET",
    trigger_price: float = 49000.0,
) -> AlgoOrderInfo:
    return AlgoOrderInfo(
        algo_id=algo_id,
        symbol=symbol,
        side=side,
        order_type=order_type,
        status="PENDING",
        trigger_price=trigger_price,
    )


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

        executor._client = MagicMock()

        with pytest.raises(RuntimeError) as exc_info:
            await executor.place_futures_order("BTCUSDT", "BUY", 0.01)

        assert "blocked" in str(exc_info.value).lower()

    @pytest.mark.asyncio
    async def test_place_futures_order_records_executed_quantity_in_portfolio(self, executor):
        """DB position quantity should match exchange filled quantity, not requested quantity."""
        mock_client = MagicMock()
        mock_client.get_account_info = AsyncMock(
            return_value=MagicMock(total_margin_balance=5000.0, available_balance=5000.0)
        )
        mock_client.place_order = AsyncMock(
            return_value=FuturesOrderInfo(
                order_id="eth_fill_1",
                symbol="ETHUSDT",
                side="BUY",
                position_side="LONG",
                order_type="MARKET",
                quantity=0.009670032130879488,
                price=2276.25,
                status="FILLED",
                executed_quantity=0.009,
                create_time=1234567890,
                reduce_only=False,
            )
        )
        executor._client = mock_client
        executor._portfolio_manager = AsyncMock()

        await executor.place_futures_order(
            symbol="ETHUSDT",
            side="BUY",
            quantity=0.009670032130879488,
            position_side="LONG",
            reduce_only=False,
        )

        executor._portfolio_manager.open_position.assert_awaited_once()
        open_kwargs = executor._portfolio_manager.open_position.await_args.kwargs
        assert open_kwargs["quantity"] == pytest.approx(0.009)

    @pytest.mark.asyncio
    async def test_on_signal_buy_opens_long(self, executor):
        """BUY signal opens LONG position and places SL/TP bracket orders."""
        mock_client = MagicMock()
        mock_client.get_step_size.return_value = 0.0
        mock_client.get_position_risk = AsyncMock(return_value=[])
        mock_client.get_account_info = AsyncMock(
            return_value=MagicMock(
                total_margin_balance=10000.0,
                available_balance=5000.0,
            )
        )
        mock_client.get_min_qty = MagicMock(return_value=None)
        mock_client.place_order = AsyncMock(
            side_effect=[
                _make_order(order_id="entry", side="BUY", status="FILLED"),
            ]
        )
        mock_client.place_algo_order = AsyncMock(
            side_effect=[
                _make_algo_order(algo_id="sl_order", order_type="STOP_MARKET"),
                _make_algo_order(algo_id="tp_order", order_type="TAKE_PROFIT_MARKET"),
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

        # Entry uses place_order, SL/TP use place_algo_order
        assert mock_client.place_order.call_count == 1
        assert mock_client.place_algo_order.call_count == 2

        # First call must be the market BUY entry
        entry_call = mock_client.place_order.call_args_list[0]
        assert entry_call.kwargs["symbol"] == "BTCUSDT"
        assert entry_call.kwargs["side"] == "BUY"
        assert entry_call.kwargs["position_side"] == "BOTH"  # one-way mode default
        assert entry_call.kwargs["reduce_only"] is False

        # SL/TP orders are tracked
        assert "BTCUSDT" in executor._sl_tp_orders

    @pytest.mark.asyncio
    async def test_buy_signal_places_sl_tp_with_atr(self, executor):
        """BUY signal with ATR places SL at entry - 2*ATR and TP at entry + 4.5*ATR."""
        entry_price = 700.0
        atr_14 = 10.0  # SL = 680, TP = 745

        mock_client = MagicMock()
        mock_client.get_step_size.return_value = 0.0
        mock_client.get_position_risk = AsyncMock(return_value=[])
        mock_client.get_account_info = AsyncMock(
            return_value=MagicMock(total_margin_balance=5000.0, available_balance=5000.0)
        )
        mock_client.get_min_qty = MagicMock(return_value=None)
        mock_client.place_order = AsyncMock(
            side_effect=[
                _make_order(order_id="entry", price=entry_price, status="FILLED"),
            ]
        )
        mock_client.place_algo_order = AsyncMock(
            side_effect=[
                _make_algo_order(algo_id="sl_111", order_type="STOP_MARKET"),
                _make_algo_order(algo_id="tp_222", order_type="TAKE_PROFIT_MARKET"),
            ]
        )
        executor._client = mock_client
        executor._notifier = AsyncMock()

        signal = Signal(
            type=SignalType.BUY,
            symbol="BTCUSDT",
            price=entry_price,
            confidence=0.9,
            reason="test",
            indicators={"atr_14": atr_14},
            trading_mode="futures",
        )

        await executor.on_signal(signal)

        assert mock_client.place_order.call_count == 1
        assert mock_client.place_algo_order.call_count == 2

        sl_call = mock_client.place_algo_order.call_args_list[0]
        assert sl_call.kwargs["order_type"] == "STOP_MARKET"
        assert sl_call.kwargs["trigger_price"] == entry_price - 2.0 * atr_14  # 680.0
        assert sl_call.kwargs["close_position"] is True
        assert sl_call.kwargs["position_side"] == "BOTH"  # one-way mode

        tp_call = mock_client.place_algo_order.call_args_list[1]
        assert tp_call.kwargs["order_type"] == "TAKE_PROFIT_MARKET"
        assert tp_call.kwargs["trigger_price"] == entry_price + 4.5 * atr_14  # 745.0
        assert tp_call.kwargs["close_position"] is True
        assert tp_call.kwargs["position_side"] == "BOTH"  # one-way mode

    @pytest.mark.asyncio
    async def test_buy_signal_uses_fixed_pct_fallback_when_no_atr(self, executor):
        """BUY signal with no ATR falls back to fixed-percentage SL/TP."""
        entry_price = 1000.0
        # defaults: stop_loss_pct=0.03 → SL=970, take_profit_pct=0.06 → TP=1060

        mock_client = MagicMock()
        mock_client.get_step_size.return_value = 0.0
        mock_client.get_position_risk = AsyncMock(return_value=[])
        mock_client.get_account_info = AsyncMock(
            return_value=MagicMock(total_margin_balance=5000.0, available_balance=5000.0)
        )
        mock_client.get_min_qty = MagicMock(return_value=None)
        mock_client.place_order = AsyncMock(
            side_effect=[
                _make_order(order_id="entry", price=entry_price, status="FILLED"),
            ]
        )
        mock_client.place_algo_order = AsyncMock(
            side_effect=[
                _make_algo_order(algo_id="sl_x", order_type="STOP_MARKET"),
                _make_algo_order(algo_id="tp_x", order_type="TAKE_PROFIT_MARKET"),
            ]
        )
        executor._client = mock_client
        executor._notifier = AsyncMock()

        signal = Signal(
            type=SignalType.BUY,
            symbol="BTCUSDT",
            price=entry_price,
            confidence=0.8,
            reason="test",
            indicators={},  # No ATR — forces fixed-pct path
            trading_mode="futures",
        )

        await executor.on_signal(signal)

        sl_call = mock_client.place_algo_order.call_args_list[0]
        assert sl_call.kwargs["order_type"] == "STOP_MARKET"
        assert abs(sl_call.kwargs["trigger_price"] - 970.0) < 0.01  # 1000 * (1 - 0.03)

        tp_call = mock_client.place_algo_order.call_args_list[1]
        assert tp_call.kwargs["order_type"] == "TAKE_PROFIT_MARKET"
        assert abs(tp_call.kwargs["trigger_price"] - 1060.0) < 0.01  # 1000 * (1 + 0.06)

    @pytest.mark.asyncio
    async def test_buy_ignored_when_position_already_open(self, executor):
        """BUY signal is skipped when a position already exists (no pyramiding)."""
        mock_client = MagicMock()
        mock_client.get_position_risk = AsyncMock(
            return_value=[
                FuturesPositionInfo(
                    symbol="BTCUSDT",
                    position_side="LONG",
                    position_amt=0.01,
                    entry_price=50000.0,
                    mark_price=51000.0,
                    liquidation_price=45000.0,
                    leverage=5,
                    isolated_margin=100.0,
                    unrealized_pnl=10.0,
                    notional_value=510.0,
                )
            ]
        )
        executor._client = mock_client
        executor._notifier = AsyncMock()

        signal = Signal(
            type=SignalType.BUY,
            symbol="BTCUSDT",
            price=51000.0,
            confidence=0.9,
            reason="test",
            indicators={},
            trading_mode="futures",
        )

        await executor.on_signal(signal)

        mock_client.place_order.assert_not_called()

    @pytest.mark.asyncio
    async def test_on_signal_sell_closes_long(self, executor):
        """SELL signal closes LONG position with reduceOnly."""
        from src.portfolio.models import Position

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
            return_value=_make_order(order_id="close", side="SELL", reduce_only=True)
        )
        executor._client = mock_client
        executor._notifier = AsyncMock()

        # Mock PortfolioManager with agent-owned position
        mock_pm = MagicMock()
        mock_pm.get_position = MagicMock(
            return_value=Position(
                symbol="BTCUSDT",
                quantity=0.01,
                entry_price=49000.0,
                position_side="LONG",
            )
        )
        executor._portfolio_manager = mock_pm

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

        # Verify get_position was called with market="futures"
        mock_pm.get_position.assert_called_once_with("BTCUSDT", market="futures")

        mock_client.place_order.assert_called_once()
        call_kwargs = mock_client.place_order.call_args.kwargs
        assert call_kwargs["symbol"] == "BTCUSDT"
        assert call_kwargs["side"] == "SELL"
        assert call_kwargs["position_side"] == "BOTH"  # one-way mode default
        assert call_kwargs["reduce_only"] is True
        assert call_kwargs["quantity"] == 0.01  # Agent-owned quantity, not full exchange position

    @pytest.mark.asyncio
    async def test_sell_cancels_sl_tp_before_close(self, executor):
        """SELL signal cancels tracked SL/TP orders before placing the market close."""
        from src.portfolio.models import Position

        mock_client = MagicMock()
        mock_client.get_position_risk = AsyncMock(
            return_value=[
                FuturesPositionInfo(
                    symbol="BTCUSDT",
                    position_side="LONG",
                    position_amt=0.005,
                    entry_price=700.0,
                    mark_price=750.0,
                    liquidation_price=500.0,
                    leverage=5,
                    isolated_margin=10.0,
                    unrealized_pnl=0.25,
                    notional_value=3.75,
                )
            ]
        )
        mock_client.get_account_info = AsyncMock(
            return_value=MagicMock(total_margin_balance=5000.0, available_balance=5000.0)
        )
        mock_client.place_order = AsyncMock(
            return_value=_make_order(order_id="close", side="SELL", reduce_only=True)
        )
        mock_client.cancel_order = AsyncMock(return_value={"orderId": "cancelled"})
        executor._client = mock_client
        executor._notifier = AsyncMock()

        # Mock PortfolioManager with agent-owned position
        mock_pm = MagicMock()
        mock_pm.get_position = MagicMock(
            return_value=Position(
                symbol="BTCUSDT",
                quantity=0.005,
                entry_price=700.0,
                position_side="LONG",
            )
        )
        executor._portfolio_manager = mock_pm

        # Pre-populate tracked SL/TP order IDs (as if a prior BUY placed them)
        executor._sl_tp_orders["BTCUSDT"] = {
            "sl_order_id": "sl_999",
            "tp_order_id": "tp_888",
        }

        signal = Signal(
            type=SignalType.SELL,
            symbol="BTCUSDT",
            price=750.0,
            confidence=0.8,
            reason="test",
            indicators={},
            trading_mode="futures",
        )

        await executor.on_signal(signal)

        # Verify get_position was called with market="futures"
        mock_pm.get_position.assert_called_once_with("BTCUSDT", market="futures")

        # Both SL and TP orders cancelled before market close
        assert mock_client.cancel_order.call_count == 2
        cancelled_ids = {c.args[1] for c in mock_client.cancel_order.call_args_list}
        assert cancelled_ids == {"sl_999", "tp_888"}

        # Market close order placed after cancellation
        mock_client.place_order.assert_called_once()
        assert mock_client.place_order.call_args.kwargs["reduce_only"] is True

        # SL/TP tracking cleared after SELL
        assert "BTCUSDT" not in executor._sl_tp_orders

    @pytest.mark.asyncio
    async def test_cancel_sl_tp_tolerates_already_filled_order(self, executor):
        """_cancel_sl_tp_orders does not raise when one order is already filled/cancelled."""
        mock_client = MagicMock()
        mock_client.cancel_order = AsyncMock(
            side_effect=[
                BinanceFuturesApiError(-2011, "Unknown order"),  # SL already filled
                {"orderId": "tp_888"},  # TP cancelled OK
            ]
        )
        executor._client = mock_client
        executor._sl_tp_orders["BTCUSDT"] = {
            "sl_order_id": "sl_999",
            "tp_order_id": "tp_888",
        }

        # Must not raise even though first cancel fails
        await executor._cancel_sl_tp_orders("BTCUSDT")

        assert mock_client.cancel_order.call_count == 2
        assert "BTCUSDT" not in executor._sl_tp_orders

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
        """Signals with trading_mode != 'futures' are silently ignored."""
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

        executor._client.place_order.assert_not_called()

    @pytest.mark.asyncio
    async def test_on_signal_sell_no_position(self, executor):
        """SELL signal with no open position is ignored."""
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

        mock_client.place_order.assert_not_called()

    @pytest.mark.asyncio
    async def test_hold_signal_is_ignored(self, executor):
        """HOLD signal must not trigger any API calls."""
        mock_client = MagicMock()
        executor._client = mock_client
        executor._notifier = AsyncMock()

        signal = Signal(
            type=SignalType.HOLD,
            symbol="BTCUSDT",
            price=50000.0,
            confidence=0.5,
            reason="no clear direction",
            indicators={},
            trading_mode="futures",
        )

        await executor.on_signal(signal)

        mock_client.place_order.assert_not_called()

    def test_calculate_quantity_from_price(self, executor):
        """_calculate_quantity returns order_size_usdt / price when step size is unknown."""
        # config.order_size_usdt = 100.0; step=0 means no LOT_SIZE adjustment
        mock_client = MagicMock()
        mock_client.get_step_size.return_value = 0.0
        executor._client = mock_client
        qty = executor._calculate_quantity("BTCUSDT", 500.0)
        assert abs(qty - 0.2) < 1e-9  # 100 / 500 = 0.2

    def test_calculate_quantity_bumps_when_truncation_drops_below_min_notional(self, executor):
        """_calculate_quantity adds one step when LOT_SIZE truncation drops notional below $20."""
        # 100 / 2340 = 0.04273..., floor to 0.001 step = 0.042 → notional $98.28 (well above $20)
        # Use a price where truncation would cause an issue: order_size=20, step=0.001, price=2340
        # raw=0.00854..., floor=0.008, notional=$18.72 < $20 → should add one step → 0.009
        mock_client = MagicMock()
        mock_client.get_step_size.return_value = 0.001
        executor._client = mock_client
        config_with_small_order = FuturesTradingConfig(
            api_key="test_key",
            api_secret="test_secret",
            test_mode=True,
            enabled=True,
            symbols=["ETHUSDT"],
            order_size_usdt=20.0,
        )
        executor._config = config_with_small_order
        qty = executor._calculate_quantity("ETHUSDT", 2340.0)
        assert qty == pytest.approx(0.009)  # 0.008 + 0.001 step
        assert qty * 2340.0 >= 20.0

    def test_calculate_quantity_zero_price_returns_zero(self, executor):
        """_calculate_quantity returns 0.0 when price is zero or negative."""
        mock_client = MagicMock()
        mock_client.get_step_size.return_value = 0.0
        executor._client = mock_client
        assert executor._calculate_quantity("BTCUSDT", 0.0) == 0.0
        assert executor._calculate_quantity("BTCUSDT", -1.0) == 0.0

    @pytest.mark.asyncio
    async def test_sl_tp_use_long_position_side_in_hedge_mode(self, executor):
        """In hedge mode, SL/TP orders use positionSide=LONG with closePosition=True."""
        executor._active_position_mode = "hedge"

        mock_client = MagicMock()
        mock_client.place_algo_order = AsyncMock(
            side_effect=[
                _make_algo_order(algo_id="sl_h", order_type="STOP_MARKET"),
                _make_algo_order(algo_id="tp_h", order_type="TAKE_PROFIT_MARKET"),
            ]
        )
        executor._client = mock_client

        await executor._place_sl_tp_orders("BTCUSDT", 1000.0, 0.01, 10.0)

        sl_call = mock_client.place_algo_order.call_args_list[0]
        assert sl_call.kwargs["position_side"] == "LONG"
        assert sl_call.kwargs["close_position"] is True

        tp_call = mock_client.place_algo_order.call_args_list[1]
        assert tp_call.kwargs["position_side"] == "LONG"
        assert tp_call.kwargs["close_position"] is True

    @pytest.mark.asyncio
    async def test_recover_open_positions_places_sl_tp(self, executor):
        """On startup, open positions with no tracked SL/TP get protective orders placed."""
        btc_position = FuturesPositionInfo(
            symbol="BTCUSDT",
            position_side="BOTH",
            position_amt=0.01,
            entry_price=50000.0,
            mark_price=50500.0,
            liquidation_price=45000.0,
            leverage=3,
            isolated_margin=100.0,
            unrealized_pnl=5.0,
            notional_value=505.0,
        )

        async def mock_pos_risk(symbol: str):
            return [btc_position] if symbol == "BTCUSDT" else []

        mock_client = MagicMock()
        mock_client.get_position_risk = mock_pos_risk
        mock_client.place_algo_order = AsyncMock(
            side_effect=[
                _make_algo_order(algo_id="sl_r", order_type="STOP_MARKET"),
                _make_algo_order(algo_id="tp_r", order_type="TAKE_PROFIT_MARKET"),
            ]
        )
        executor._client = mock_client

        await executor._recover_open_positions()

        # SL + TP placed for the open BTCUSDT position only
        assert mock_client.place_algo_order.call_count == 2
        assert "BTCUSDT" in executor._sl_tp_orders
        assert executor._sl_tp_orders["BTCUSDT"]["sl_order_id"] == "sl_r"
        assert executor._sl_tp_orders["BTCUSDT"]["tp_order_id"] == "tp_r"

    @pytest.mark.asyncio
    async def test_recover_skips_symbol_with_no_open_position(self, executor):
        """Recovery does nothing when there is no open position."""
        mock_client = MagicMock()
        mock_client.get_position_risk = AsyncMock(return_value=[])
        mock_client.place_order = AsyncMock()
        executor._client = mock_client

        await executor._recover_open_positions()

        mock_client.place_order.assert_not_called()
        assert executor._sl_tp_orders == {}

    @pytest.mark.asyncio
    async def test_recover_skips_symbol_already_tracked(self, executor):
        """Recovery does not double-place SL/TP when tracking already exists."""
        btc_position = FuturesPositionInfo(
            symbol="BTCUSDT",
            position_side="BOTH",
            position_amt=0.01,
            entry_price=50000.0,
            mark_price=50500.0,
            liquidation_price=45000.0,
            leverage=3,
            isolated_margin=100.0,
            unrealized_pnl=5.0,
            notional_value=505.0,
        )

        async def mock_pos_risk(symbol: str):
            return [btc_position] if symbol == "BTCUSDT" else []

        mock_client = MagicMock()
        mock_client.get_position_risk = mock_pos_risk
        mock_client.place_order = AsyncMock()
        executor._client = mock_client

        # BTCUSDT already tracked — skip; ETHUSDT has no position — skip
        executor._sl_tp_orders["BTCUSDT"] = {
            "sl_order_id": "existing_sl",
            "tp_order_id": "existing_tp",
        }

        await executor._recover_open_positions()

        mock_client.place_order.assert_not_called()

    @pytest.mark.asyncio
    async def test_monitor_clears_sl_tp_when_position_closed_by_exchange(self, executor):
        """_monitor_and_update cleans up _sl_tp_orders when exchange closed position via SL/TP."""
        mock_client = MagicMock()
        mock_client.get_position_risk = AsyncMock(return_value=[])  # No positions
        mock_client.get_account_info = AsyncMock(
            return_value=MagicMock(total_margin_balance=5000.0, available_balance=5000.0)
        )
        executor._client = mock_client
        executor._notifier = AsyncMock()

        # Pre-populate — SL/TP was active before exchange closed the position
        executor._sl_tp_orders["BTCUSDT"] = {"sl_order_id": "sl_1", "tp_order_id": "tp_1"}

        await executor._monitor_and_update()

        # Tracking should be cleaned up
        assert "BTCUSDT" not in executor._sl_tp_orders

    @pytest.mark.asyncio
    async def test_monitor_sends_close_notification_on_sl_close(self, executor):
        """send_trade_alert is called with pnl and close_reason='stop_loss' on exchange SL fill."""
        mock_client = MagicMock()
        mock_client.get_position_risk = AsyncMock(return_value=[])
        mock_client.get_account_info = AsyncMock(
            return_value=MagicMock(total_margin_balance=5000.0, available_balance=5000.0)
        )
        executor._client = mock_client
        notifier = AsyncMock()
        executor._notifier = notifier

        executor._sl_tp_orders["BTCUSDT"] = {"sl_order_id": "sl_1", "tp_order_id": "tp_1"}
        executor._positions["BTCUSDT"] = {
            "entry_price": 80000.0,
            "mark_price": 78000.0,  # close to SL, not TP
            "amount": 0.01,
            "unrealized_pnl": -20.0,
        }
        executor._sl_tp_prices["BTCUSDT"] = {"sl_price": 78100.0, "tp_price": 84000.0}

        await executor._monitor_and_update()

        notifier.send_trade_alert.assert_awaited_once()
        call_kwargs = notifier.send_trade_alert.call_args.kwargs
        assert call_kwargs["symbol"] == "BTCUSDT"
        assert call_kwargs["close_reason"] == "stop_loss"
        assert call_kwargs["pnl"] == pytest.approx((78000.0 - 80000.0) * 0.01)
        assert "BTCUSDT" not in executor._sl_tp_orders

    @pytest.mark.asyncio
    async def test_monitor_sends_close_notification_on_tp_close(self, executor):
        """send_trade_alert is called with pnl and close_reason='take_profit' on exchange TP fill."""
        mock_client = MagicMock()
        mock_client.get_position_risk = AsyncMock(return_value=[])
        mock_client.get_account_info = AsyncMock(
            return_value=MagicMock(total_margin_balance=5000.0, available_balance=5000.0)
        )
        executor._client = mock_client
        notifier = AsyncMock()
        executor._notifier = notifier

        executor._sl_tp_orders["BTCUSDT"] = {"sl_order_id": "sl_1", "tp_order_id": "tp_1"}
        executor._positions["BTCUSDT"] = {
            "entry_price": 80000.0,
            "mark_price": 84100.0,  # close to TP, not SL
            "amount": 0.01,
            "unrealized_pnl": 41.0,
        }
        executor._sl_tp_prices["BTCUSDT"] = {"sl_price": 78000.0, "tp_price": 84000.0}

        await executor._monitor_and_update()

        notifier.send_trade_alert.assert_awaited_once()
        call_kwargs = notifier.send_trade_alert.call_args.kwargs
        assert call_kwargs["symbol"] == "BTCUSDT"
        assert call_kwargs["close_reason"] == "take_profit"
        assert call_kwargs["pnl"] == pytest.approx((84100.0 - 80000.0) * 0.01)
        assert "BTCUSDT" not in executor._sl_tp_orders

    @pytest.mark.asyncio
    async def test_liquidation_buffer_check(self, executor):
        """Test that liquidation buffer check fires when position is near liquidation."""
        mock_client = MagicMock()
        mock_client.get_position_risk = AsyncMock(
            return_value=[
                FuturesPositionInfo(
                    symbol="BTCUSDT",
                    position_side="BOTH",
                    position_amt=0.1,
                    entry_price=50000.0,
                    mark_price=45500.0,  # Close to liquidation
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
            False,
            "LONG position within 5.0% of liquidation",
        )

        executor._notifier = AsyncMock()

        await executor._monitor_and_update()

        # Called once per symbol (BTCUSDT and ETHUSDT both return the same mocked position)
        assert executor._risk_manager.check_liquidation_buffer.call_count == 2
        first_call = executor._risk_manager.check_liquidation_buffer.call_args_list[0]
        assert first_call.kwargs["position_side"] == "LONG"

    @pytest.mark.asyncio
    async def test_leverage_check_blocks_excessive_leverage(self, executor):
        """Excessive leverage is blocked before placing any order."""
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

    @pytest.mark.asyncio
    async def test_sell_with_one_way_mode_both_side(self, executor):
        """SELL signal with one-way mode (position_side='BOTH') correctly normalizes side."""
        from src.portfolio.models import Position

        mock_client = MagicMock()
        mock_client.get_position_risk = AsyncMock(
            return_value=[
                FuturesPositionInfo(
                    symbol="BTCUSDT",
                    position_side="BOTH",  # One-way mode
                    position_amt=0.01,  # Positive = LONG
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
            return_value=_make_order(order_id="close", side="SELL", reduce_only=True)
        )
        executor._client = mock_client
        executor._notifier = AsyncMock()

        # Mock PortfolioManager with agent-owned LONG position
        mock_pm = MagicMock()
        mock_pm.get_position = MagicMock(
            return_value=Position(
                symbol="BTCUSDT",
                quantity=0.01,
                entry_price=49000.0,
                position_side="LONG",
            )
        )
        executor._portfolio_manager = mock_pm

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

        # Verify get_position was called with market="futures"
        mock_pm.get_position.assert_called_once_with("BTCUSDT", market="futures")

        # Should close position despite exchange reporting "BOTH" side
        mock_client.place_order.assert_called_once()
        call_kwargs = mock_client.place_order.call_args.kwargs
        assert call_kwargs["symbol"] == "BTCUSDT"
        assert call_kwargs["side"] == "SELL"
        assert call_kwargs["position_side"] == "BOTH"  # one-way mode
        assert call_kwargs["reduce_only"] is True
        assert call_kwargs["quantity"] == 0.01  # Agent-owned quantity
