"""Tests for PaperExecutor exit logic (trailing stop, take profit, time stop)."""

from __future__ import annotations

import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.execution.metrics import ExecutionMetrics
from src.execution.paper_executor import (
    PaperExecutor,
    PaperPosition,
    PaperTradingConfig,
)
from src.portfolio.models import Position
from src.strategy.signals import Signal, SignalType


def _make_config(**overrides) -> PaperTradingConfig:
    defaults = {
        "enabled": True,
        "order_size_usdt": 200.0,
        "initial_balance": 10000.0,
        "symbols": ["BTCUSDT"],
        "trailing_stop_pct": 0.005,
        "take_profit_pct": 0.008,
        "time_stop_minutes": 60,
        "exit_check_interval": 5,
    }
    defaults.update(overrides)
    return PaperTradingConfig(**defaults)


def _make_executor(
    config: PaperTradingConfig | None = None,
    agent_id: str = "default",
) -> PaperExecutor:
    config = config or _make_config()
    risk_manager = MagicMock()
    risk_manager.is_trading_allowed.return_value = (True, "")
    risk_manager.check_position_limit.return_value = (True, "")
    risk_manager._config.position_limits.max_open_positions = 10
    risk_manager._config.position_limits.max_position_pct = 0.10
    metrics = MagicMock(spec=ExecutionMetrics)
    notifier = MagicMock()
    notifier.send_alert = AsyncMock()
    notifier.send_trade_alert = AsyncMock()
    notifier.__aenter__ = AsyncMock(return_value=notifier)
    notifier.__aexit__ = AsyncMock(return_value=False)
    portfolio_manager = MagicMock()
    portfolio_manager.open_position = AsyncMock()
    portfolio_manager.close_position = AsyncMock()
    return PaperExecutor(
        config=config,
        risk_manager=risk_manager,
        metrics=metrics,
        notifier=notifier,
        portfolio_manager=portfolio_manager,
        agent_id=agent_id,
    )


class TestPaperPositionHWM:
    def test_hwm_defaults_to_entry_price(self):
        pos = PaperPosition(
            symbol="BTCUSDT",
            side="LONG",
            quantity=0.01,
            entry_price=50000.0,
            open_time=time.time(),
        )
        assert pos.high_water_mark == 50000.0

    def test_hwm_explicit_value(self):
        pos = PaperPosition(
            symbol="BTCUSDT",
            side="LONG",
            quantity=0.01,
            entry_price=50000.0,
            open_time=time.time(),
            high_water_mark=51000.0,
        )
        assert pos.high_water_mark == 51000.0


class TestEvaluateExit:
    def _make_position(self, entry_price=50000.0, hwm=50000.0, open_time=None):
        return PaperPosition(
            symbol="BTCUSDT",
            side="LONG",
            quantity=0.01,
            entry_price=entry_price,
            open_time=open_time or time.time(),
            high_water_mark=hwm,
        )

    def test_trailing_stop_triggers(self):
        executor = _make_executor()
        # HWM = 50000, trailing_stop_pct = 0.5%
        # Threshold = 50000 * 0.995 = 49750
        pos = self._make_position(entry_price=50000.0, hwm=50000.0)
        result = executor._evaluate_exit(pos, current_price=49700.0, now=time.time())
        assert result is not None
        assert "TRAILING_STOP" in result

    def test_trailing_stop_does_not_trigger_above_threshold(self):
        executor = _make_executor()
        pos = self._make_position(entry_price=50000.0, hwm=50000.0)
        # Price at 49800 is above 49750 threshold
        result = executor._evaluate_exit(pos, current_price=49800.0, now=time.time())
        assert result is None

    def test_trailing_stop_with_elevated_hwm(self):
        executor = _make_executor()
        # HWM rose to 51000, threshold = 51000 * 0.995 = 50745
        pos = self._make_position(entry_price=50000.0, hwm=51000.0)
        result = executor._evaluate_exit(pos, current_price=50700.0, now=time.time())
        assert result is not None
        assert "TRAILING_STOP" in result

    def test_take_profit_triggers(self):
        executor = _make_executor()
        # entry = 50000, tp_pct = 0.8%, threshold = 50000 * 1.008 = 50400
        pos = self._make_position(entry_price=50000.0, hwm=50500.0)
        result = executor._evaluate_exit(pos, current_price=50400.0, now=time.time())
        assert result is not None
        assert "TAKE_PROFIT" in result

    def test_take_profit_does_not_trigger_below_threshold(self):
        executor = _make_executor()
        pos = self._make_position(entry_price=50000.0, hwm=50300.0)
        result = executor._evaluate_exit(pos, current_price=50300.0, now=time.time())
        assert result is None

    def test_time_stop_triggers(self):
        executor = _make_executor(_make_config(time_stop_minutes=60))
        # Position opened 61 minutes ago
        pos = self._make_position(
            entry_price=50000.0,
            hwm=50000.0,
            open_time=time.time() - 61 * 60,
        )
        result = executor._evaluate_exit(pos, current_price=50000.0, now=time.time())
        assert result is not None
        assert "TIME_STOP" in result

    def test_time_stop_does_not_trigger_before_limit(self):
        executor = _make_executor(_make_config(time_stop_minutes=60))
        # Position opened 30 minutes ago
        pos = self._make_position(
            entry_price=50000.0,
            hwm=50000.0,
            open_time=time.time() - 30 * 60,
        )
        result = executor._evaluate_exit(pos, current_price=50000.0, now=time.time())
        assert result is None

    def test_no_exit_when_conditions_not_met(self):
        executor = _make_executor()
        pos = self._make_position(
            entry_price=50000.0,
            hwm=50100.0,
            open_time=time.time() - 10 * 60,
        )
        # Price is within all thresholds
        result = executor._evaluate_exit(pos, current_price=50050.0, now=time.time())
        assert result is None

    def test_trailing_stop_priority_over_take_profit(self):
        """When both conditions could fire, trailing stop is checked first."""
        # This tests evaluation order: trailing stop checked before TP
        executor = _make_executor(
            _make_config(
                trailing_stop_pct=0.01,  # 1%
                take_profit_pct=0.001,  # 0.1% (very tight)
            )
        )
        # Entry 50000, HWM 51000
        # Trail threshold: 51000 * 0.99 = 50490
        # TP threshold: 50000 * 1.001 = 50050
        # Price 50000 < 50490 → trailing stop fires first
        pos = self._make_position(entry_price=50000.0, hwm=51000.0)
        result = executor._evaluate_exit(pos, current_price=50000.0, now=time.time())
        assert result is not None
        assert "TRAILING_STOP" in result


class TestCheckExits:
    @pytest.mark.asyncio
    async def test_hwm_updates_as_price_rises(self):
        executor = _make_executor()
        # Manually insert a position
        executor._positions["BTCUSDT:spot"] = PaperPosition(
            symbol="BTCUSDT",
            side="LONG",
            quantity=0.004,
            entry_price=50000.0,
            open_time=time.time(),
            high_water_mark=50000.0,
        )
        # Mock price fetch returning higher price (no exit triggered)
        executor._fetch_latest_price = AsyncMock(return_value=50200.0)
        await executor._check_exits()

        # HWM should be updated, position still open
        assert executor._positions["BTCUSDT:spot"].high_water_mark == 50200.0
        assert "BTCUSDT:spot" in executor._positions

    @pytest.mark.asyncio
    async def test_exit_triggers_sell_and_removes_position(self):
        executor = _make_executor()
        executor._positions["BTCUSDT:spot"] = PaperPosition(
            symbol="BTCUSDT",
            side="LONG",
            quantity=0.004,
            entry_price=50000.0,
            open_time=time.time(),
            high_water_mark=50500.0,
        )
        # Price dropped below trailing stop: 50500 * 0.995 = 50247.5
        executor._fetch_latest_price = AsyncMock(return_value=50200.0)
        await executor._check_exits()

        # Position should be closed
        assert "BTCUSDT:spot" not in executor._positions
        assert executor._trade_count == 1

    @pytest.mark.asyncio
    async def test_exit_constructs_correct_signal(self):
        executor = _make_executor()
        executor._positions["ETHUSDT:futures"] = PaperPosition(
            symbol="ETHUSDT",
            side="LONG",
            quantity=0.1,
            entry_price=3000.0,
            open_time=time.time() - 120 * 60,  # 2 hours ago
            high_water_mark=3000.0,
        )
        executor._fetch_latest_price = AsyncMock(return_value=3000.0)

        # Should trigger time stop (open > 60 min)
        with patch.object(executor, "_handle_sell", new_callable=AsyncMock) as mock_sell:
            await executor._check_exits()
            mock_sell.assert_called_once()
            signal = mock_sell.call_args[0][0]
            assert signal.type == SignalType.SELL
            assert signal.symbol == "ETHUSDT"
            assert signal.price == 3000.0
            assert signal.trading_mode == "futures"
            assert "TIME_STOP" in signal.reason

    @pytest.mark.asyncio
    async def test_stop_loss_exit_fills_at_threshold_not_observed_price(self):
        executor = _make_executor()
        executor._positions["BTCUSDT:spot"] = PaperPosition(
            symbol="BTCUSDT",
            side="LONG",
            quantity=0.004,
            entry_price=50000.0,
            open_time=time.time(),
            atr_at_entry=100.0,
            sl_price=49800.0,
            tp_price=50500.0,
            high_water_mark=50000.0,
        )
        executor._fetch_latest_price = AsyncMock(return_value=49500.0)

        with patch.object(executor, "_handle_sell", new_callable=AsyncMock) as mock_sell:
            await executor._check_exits()
            mock_sell.assert_called_once()
            signal = mock_sell.call_args[0][0]
            assert signal.reason == "STOP_LOSS"
            assert signal.price == 49800.0

    @pytest.mark.asyncio
    async def test_take_profit_exit_fills_at_threshold_not_observed_price(self):
        executor = _make_executor()
        executor._positions["BTCUSDT:spot"] = PaperPosition(
            symbol="BTCUSDT",
            side="LONG",
            quantity=0.004,
            entry_price=50000.0,
            open_time=time.time(),
            atr_at_entry=100.0,
            sl_price=49800.0,
            tp_price=50500.0,
            high_water_mark=50000.0,
        )
        executor._fetch_latest_price = AsyncMock(return_value=50750.0)

        with patch.object(executor, "_handle_sell", new_callable=AsyncMock) as mock_sell:
            await executor._check_exits()
            mock_sell.assert_called_once()
            signal = mock_sell.call_args[0][0]
            assert signal.reason == "TAKE_PROFIT"
            assert signal.price == 50500.0

    @pytest.mark.asyncio
    async def test_skips_position_when_price_unavailable(self):
        executor = _make_executor()
        executor._positions["BTCUSDT:spot"] = PaperPosition(
            symbol="BTCUSDT",
            side="LONG",
            quantity=0.004,
            entry_price=50000.0,
            open_time=time.time(),
        )
        executor._fetch_latest_price = AsyncMock(return_value=None)
        await executor._check_exits()

        # Position should remain open
        assert "BTCUSDT:spot" in executor._positions

    @pytest.mark.asyncio
    async def test_multiple_positions_checked(self):
        executor = _make_executor()
        now = time.time()
        # Position 1: should trigger trailing stop
        executor._positions["BTCUSDT:spot"] = PaperPosition(
            symbol="BTCUSDT",
            side="LONG",
            quantity=0.004,
            entry_price=50000.0,
            open_time=now,
            high_water_mark=51000.0,
        )
        # Position 2: should NOT trigger (price is fine)
        executor._positions["ETHUSDT:spot"] = PaperPosition(
            symbol="ETHUSDT",
            side="LONG",
            quantity=0.1,
            entry_price=3000.0,
            open_time=now,
            high_water_mark=3000.0,
        )

        async def mock_price(symbol):
            if symbol == "BTCUSDT":
                return 50400.0  # Below 51000 * 0.995 = 50745 → trailing stop
            return 3001.0  # Fine for ETH

        executor._fetch_latest_price = AsyncMock(side_effect=mock_price)
        await executor._check_exits()

        assert "BTCUSDT:spot" not in executor._positions  # Closed
        assert "ETHUSDT:spot" in executor._positions  # Still open
        assert executor._positions["ETHUSDT:spot"].high_water_mark == 3001.0


class TestOnSignalEntryPath:
    """Regression tests for the on_signal → _handle_buy entry path.

    These tests exist to catch incomplete field renames in PaperPosition —
    the kind of bug where the dataclass definition is updated but one or more
    constructor call-sites are not.
    """

    def _make_buy_signal(self, atr: float = 0.0) -> Signal:
        from src.strategy.signals import SignalType

        return Signal(
            type=SignalType.BUY,
            symbol="BTCUSDT",
            price=50000.0,
            confidence=1.0,
            reason="test",
            indicators={"atr_14": atr},
            trading_mode="spot",
        )

    @pytest.mark.asyncio
    async def test_buy_with_atr_creates_position_with_high_water_mark(self):
        """BUY signal with ATR data must create PaperPosition without crashing.

        Regression: highest_price kwarg was passed after high_water_mark rename,
        causing PaperPosition.__init__() to raise TypeError on every BUY.
        """
        executor = _make_executor(
            _make_config(
                sl_atr_multiplier=2.0,
                tp_atr_multiplier=4.5,
                trailing_activate_atr=1.5,
                trailing_offset_atr=1.0,
            )
        )
        signal = self._make_buy_signal(atr=500.0)

        await executor.on_signal(signal)

        pos = executor._positions.get("BTCUSDT:spot")
        assert pos is not None, "Position must be created on BUY signal"
        assert pos.high_water_mark == pytest.approx(50000.0)
        assert pos.sl_price == pytest.approx(50000.0 - 2.0 * 500.0)
        assert pos.tp_price == pytest.approx(50000.0 + 4.5 * 500.0)

    @pytest.mark.asyncio
    async def test_buy_without_atr_falls_back_to_pct(self):
        """BUY signal with atr_14=0 uses fixed-pct SL/TP fallback."""
        executor = _make_executor(_make_config(stop_loss_pct=0.02, take_profit_pct=0.05))
        signal = self._make_buy_signal(atr=0.0)

        await executor.on_signal(signal)

        pos = executor._positions.get("BTCUSDT:spot")
        assert pos is not None
        assert pos.high_water_mark == pytest.approx(50000.0)
        assert pos.sl_price == pytest.approx(50000.0 * 0.98)
        assert pos.tp_price == pytest.approx(50000.0 * 1.05)

    @pytest.mark.asyncio
    async def test_buy_scopes_position_key_when_agent_id_set(self):
        executor = _make_executor(agent_id="agent2")
        signal = self._make_buy_signal(atr=0.0)

        await executor.on_signal(signal)

        assert "agent2::BTCUSDT:spot" in executor._positions
        executor._risk_manager.register_open_position.assert_called_once()
        call_args = executor._risk_manager.register_open_position.call_args[0]
        assert call_args[0] == "agent2::BTCUSDT:spot"

    @pytest.mark.asyncio
    async def test_buy_records_market_separately_in_portfolio_manager(self):
        executor = _make_executor()
        signal = self._make_buy_signal(atr=0.0)

        await executor.on_signal(signal)

        executor._portfolio_manager.open_position.assert_awaited_once_with(
            symbol="BTCUSDT",
            quantity=pytest.approx(0.004),
            price=50000.0,
            market="spot",
        )
        executor._risk_manager.check_position_limit.assert_called_once_with(
            "BTCUSDT:spot",
            pytest.approx(200.0),
            pytest.approx(10000.0),
        )

    @pytest.mark.asyncio
    async def test_buy_blocks_when_position_limit_exceeded(self):
        executor = _make_executor(_make_config(order_size_usdt=1500.0))
        executor._risk_manager.check_position_limit.return_value = (
            False,
            "Position size exceeds configured max",
        )
        signal = self._make_buy_signal(atr=0.0)

        await executor.on_signal(signal)

        assert "BTCUSDT:spot" not in executor._positions
        executor._portfolio_manager.open_position.assert_not_awaited()
        executor._risk_manager.register_open_position.assert_not_called()
        executor._notifier.send_alert.assert_awaited_once()
        assert "exceeds configured max" in executor._notifier.send_alert.await_args.args[0]

    @pytest.mark.asyncio
    async def test_futures_sell_from_flat_blocked_by_default_long_only_parity(self):
        executor = _make_executor(_make_config(futures_symbols=["BTCUSDT"]))
        signal = Signal(
            type=SignalType.SELL,
            symbol="BTCUSDT",
            price=50000.0,
            confidence=0.8,
            reason="Test short blocked",
            indicators={"atr_14": 250.0},
            trading_mode="futures",
        )

        await executor.on_signal(signal)

        pos = executor._positions.get("BTCUSDT:futures")
        assert pos is None  # No short opened — LONG-only parity

    @pytest.mark.asyncio
    async def test_futures_sell_from_flat_opens_short_position(self):
        executor = _make_executor(_make_config(futures_symbols=["BTCUSDT"], allow_short_entry=True))
        signal = Signal(
            type=SignalType.SELL,
            symbol="BTCUSDT",
            price=50000.0,
            confidence=0.8,
            reason="Test short",
            indicators={"atr_14": 250.0},
            trading_mode="futures",
        )

        await executor.on_signal(signal)

        pos = executor._positions.get("BTCUSDT:futures")
        assert pos is not None
        assert pos.side == "SHORT"
        assert pos.sl_price == pytest.approx(50000.0 + 2.0 * 250.0)
        assert pos.tp_price == pytest.approx(50000.0 - 4.5 * 250.0)
        executor._portfolio_manager.open_position.assert_awaited_once_with(
            symbol="BTCUSDT",
            quantity=pytest.approx(0.004),
            price=50000.0,
            market="futures",
            position_side="SHORT",
        )

    @pytest.mark.asyncio
    async def test_futures_short_atr_sizing_caps_quantity_before_open(self):
        executor = _make_executor(
            _make_config(
                futures_symbols=["BTCUSDT"],
                use_atr_sizing=True,
                allow_short_entry=True,
            )
        )
        signal = Signal(
            type=SignalType.SELL,
            symbol="BTCUSDT",
            price=50000.0,
            confidence=0.8,
            reason="ATR sized short",
            indicators={"atr_14": 250.0},
            trading_mode="futures",
        )

        await executor.on_signal(signal)

        pos = executor._positions.get("BTCUSDT:futures")
        assert pos is not None
        assert pos.quantity == pytest.approx(0.02)
        executor._risk_manager.check_position_limit.assert_called_once_with(
            "BTCUSDT:futures",
            pytest.approx(1000.0),
            pytest.approx(10000.0),
        )

    @pytest.mark.asyncio
    async def test_spot_buy_atr_sizing_caps_quantity_before_open(self):
        executor = _make_executor(
            _make_config(
                use_atr_sizing=True,
            )
        )
        signal = self._make_buy_signal(atr=250.0)

        await executor.on_signal(signal)

        pos = executor._positions.get("BTCUSDT:spot")
        assert pos is not None
        assert pos.quantity == pytest.approx(0.02)
        executor._risk_manager.check_position_limit.assert_called_once_with(
            "BTCUSDT:spot",
            pytest.approx(1000.0),
            pytest.approx(10000.0),
        )

    @pytest.mark.asyncio
    async def test_futures_short_blocks_when_position_limit_exceeded(self):
        executor = _make_executor(_make_config(futures_symbols=["BTCUSDT"], allow_short_entry=True))
        executor._risk_manager.check_position_limit.return_value = (
            False,
            "Position size exceeds configured max",
        )
        signal = Signal(
            type=SignalType.SELL,
            symbol="BTCUSDT",
            price=50000.0,
            confidence=0.8,
            reason="Short blocked by risk",
            indicators={},
            trading_mode="futures",
        )

        await executor.on_signal(signal)

        assert "BTCUSDT:futures" not in executor._positions
        executor._portfolio_manager.open_position.assert_not_awaited()
        executor._risk_manager.register_open_position.assert_not_called()
        executor._notifier.send_alert.assert_awaited_once()
        assert "exceeds configured max" in executor._notifier.send_alert.await_args.args[0]

    @pytest.mark.asyncio
    async def test_spot_sell_from_flat_is_still_ignored(self):
        executor = _make_executor()
        signal = Signal(
            type=SignalType.SELL,
            symbol="BTCUSDT",
            price=50000.0,
            confidence=0.8,
            reason="No spot shorting",
            indicators={},
            trading_mode="spot",
        )

        await executor.on_signal(signal)

        assert "BTCUSDT:spot" not in executor._positions
        executor._portfolio_manager.open_position.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_short_close_records_buy_trade_alert(self):
        executor = _make_executor(_make_config(futures_symbols=["BTCUSDT"]))
        executor._positions["BTCUSDT:futures"] = PaperPosition(
            symbol="BTCUSDT",
            side="SHORT",
            quantity=0.01,
            entry_price=50000.0,
            open_time=time.time(),
            high_water_mark=49500.0,
        )
        signal = Signal(
            type=SignalType.BUY,
            symbol="BTCUSDT",
            price=49000.0,
            confidence=1.0,
            reason="take profit",
            indicators={},
            trading_mode="futures",
        )

        await executor._handle_sell(signal, market_tag="futures", is_futures=True)

        executor._portfolio_manager.close_position.assert_awaited_once_with(
            symbol="BTCUSDT",
            price=49000.0,
            market="futures",
            closing_side="BUY",
            realized_pnl_override=pytest.approx(9.804),
        )
        executor._notifier.send_trade_alert.assert_awaited_once()
        assert executor._notifier.send_trade_alert.await_args.kwargs["side"] == "BUY"

    @pytest.mark.asyncio
    async def test_futures_restore_uses_market_field_without_symbol_suffix(self):
        executor = _make_executor(
            _make_config(futures_symbols=["BTCUSDT"]),
            agent_id="agent2",
        )
        executor._portfolio_manager.get_all_positions.return_value = [
            Position(
                symbol="BTCUSDT",
                market="futures",
                quantity=0.01,
                entry_price=50000.0,
                position_side="LONG",
            )
        ]

        async with executor:
            assert "agent2::BTCUSDT:futures" in executor._positions

    @pytest.mark.asyncio
    async def test_futures_sell_records_market_separately_in_portfolio_manager(self):
        executor = _make_executor(_make_config(futures_symbols=["BTCUSDT"]))
        executor._positions["BTCUSDT:futures"] = PaperPosition(
            symbol="BTCUSDT",
            side="LONG",
            quantity=0.01,
            entry_price=50000.0,
            open_time=time.time(),
            high_water_mark=50000.0,
        )
        signal = Signal(
            type=SignalType.SELL,
            symbol="BTCUSDT",
            price=50500.0,
            confidence=1.0,
            reason="test",
            indicators={},
            trading_mode="futures",
        )

        await executor._handle_sell(signal, market_tag="futures", is_futures=True)

        executor._portfolio_manager.close_position.assert_awaited_once_with(
            symbol="BTCUSDT",
            price=50500.0,
            market="futures",
            closing_side="SELL",
            realized_pnl_override=pytest.approx(4.798),
        )


class TestSignalIgnoredLogging:
    """Verify signal_ignored events only fire for actually ignored signals."""

    @pytest.mark.asyncio
    async def test_buy_signal_does_not_log_signal_ignored(self):
        executor = _make_executor()
        mock_event_log = AsyncMock()
        mock_event_log.log = AsyncMock()
        executor._event_log = mock_event_log

        signal = Signal(
            type=SignalType.BUY,
            symbol="BTCUSDT",
            price=50000.0,
            confidence=0.9,
            reason="Test BUY",
            indicators={"close_price": 50000.0, "atr_14": 500.0},
            trading_mode="futures",
        )

        await executor.on_signal(signal)

        # signal_received should be logged, but NOT signal_ignored
        logged_types = [call.args[0] for call in mock_event_log.log.call_args_list]
        assert "signal_received" in logged_types
        assert "signal_ignored" not in logged_types

    @pytest.mark.asyncio
    async def test_sell_from_flat_futures_logs_signal_ignored(self):
        executor = _make_executor()
        mock_event_log = AsyncMock()
        mock_event_log.log = AsyncMock()
        executor._event_log = mock_event_log

        signal = Signal(
            type=SignalType.SELL,
            symbol="BTCUSDT",
            price=50000.0,
            confidence=0.9,
            reason="Test SELL",
            indicators={"close_price": 50000.0},
            trading_mode="futures",
        )

        await executor.on_signal(signal)

        logged_types = [call.args[0] for call in mock_event_log.log.call_args_list]
        assert "signal_ignored" in logged_types
        # Check the reason is correct
        ignored_call = [
            call for call in mock_event_log.log.call_args_list if call.args[0] == "signal_ignored"
        ][0]
        assert ignored_call.args[1]["reason"] == "futures_sell_from_flat_long_only"
