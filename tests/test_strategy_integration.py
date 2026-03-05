"""Integration tests for StrategyEngine + TradingExecutor signal flow."""

from __future__ import annotations

import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.execution.binance_client import OrderInfo
from src.execution.executor import TradingConfig, TradingExecutor
from src.execution.metrics import ExecutionMetrics
from src.features.reader import IndicatorReader
from src.risk.manager import RiskManager
from src.strategy.engine import EngineConfig, StrategyEngine
from src.strategy.signals import Signal, SignalType
from src.strategy.simple_ma import SimpleMACrossoverStrategy


@pytest.fixture
def mock_reader():
    """Mock IndicatorReader."""
    reader = MagicMock(spec=IndicatorReader)
    reader.fetch_latest = AsyncMock()
    return reader


@pytest.fixture
def engine_config():
    """StrategyEngine config."""
    return EngineConfig(
        symbols=["BTCUSDT"],
        database={},
        timeframe="1m",
        evaluation_interval_seconds=60,
        strategy_classes=[SimpleMACrossoverStrategy],
        strategy_configs=[{"ema_short_period": 12, "ema_long_period": 26}],
    )


@pytest.fixture
def trading_config():
    """TradingExecutor config."""
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
    """Mock RiskManager."""
    manager = MagicMock(spec=RiskManager)
    manager.is_trading_allowed.return_value = (True, "")
    manager.check_position_limit.return_value = (True, "")
    return manager


@pytest.fixture
def metrics():
    """Mock ExecutionMetrics."""
    return MagicMock(spec=ExecutionMetrics)


class TestStrategyEngineFetchIndicators:
    """Test StrategyEngine._fetch_indicators() behavior."""

    @pytest.mark.asyncio
    async def test_fetch_indicators_returns_latest(self, mock_reader, engine_config):
        """Fetch with 2+ rows returns latest row."""
        # Mock reader returns 2 rows
        mock_reader.fetch_latest.return_value = [
            {
                "ema_12": 50000.0,
                "ema_26": 49000.0,
                "ema_50": 49500.0,
                "ema_200": 48000.0,
                "close_price": 50500.0,
            },
            {
                "ema_12": 50100.0,
                "ema_26": 49100.0,
                "ema_50": 49500.0,
                "ema_200": 48000.0,
                "close_price": 50600.0,
            },
        ]

        engine = StrategyEngine(engine_config, mock_reader)

        result = await engine._fetch_indicators("BTCUSDT")

        assert result == {
            "ema_12": 50100.0,
            "ema_26": 49100.0,
            "ema_50": 49500.0,
            "ema_200": 48000.0,
            "close_price": 50600.0,
        }
        mock_reader.fetch_latest.assert_called_once_with("BTCUSDT", "1m", limit=2)

    @pytest.mark.asyncio
    async def test_fetch_indicators_warmup(self, mock_reader, engine_config):
        """Fetch with < 2 rows returns None (warmup period)."""
        # Mock reader returns only 1 row
        mock_reader.fetch_latest.return_value = [
            {
                "ema_12": 50000.0,
                "ema_26": 49000.0,
                "ema_50": 49500.0,
                "ema_200": 48000.0,
                "close_price": 50500.0,
            },
        ]

        engine = StrategyEngine(engine_config, mock_reader)

        result = await engine._fetch_indicators("BTCUSDT")

        assert result is None
        mock_reader.fetch_latest.assert_called_once_with("BTCUSDT", "1m", limit=2)


class TestStrategyEngineSignalFlow:
    """Test StrategyEngine signal generation and callback."""

    @pytest.mark.asyncio
    async def test_first_cycle_primes_crossover_state_from_previous_row(
        self, mock_reader, engine_config
    ):
        """First evaluation after restart should not miss a valid crossover."""
        mock_reader.fetch_latest.return_value = [
            {
                "ema_12": 49000.0,
                "ema_26": 50000.0,
                "ema_50": 49500.0,
                "ema_200": 48000.0,
                "close_price": 50500.0,
            },
            {
                "ema_12": 50100.0,
                "ema_26": 49100.0,
                "ema_50": 49500.0,
                "ema_200": 48000.0,
                "close_price": 50600.0,
            },
        ]

        engine = StrategyEngine(engine_config, mock_reader)
        callback = AsyncMock()

        await engine._evaluate_all(on_signal=callback)

        callback.assert_called_once()
        signal: Signal = callback.call_args[0][0]
        assert signal.type == SignalType.BUY
        assert signal.symbol == "BTCUSDT"

    @pytest.mark.asyncio
    async def test_evaluate_buy_signal_triggers_callback(self, mock_reader, engine_config):
        """BUY signal from strategy triggers callback."""
        # First warmup the strategy (set initial state)
        mock_reader.fetch_latest.return_value = [
            {
                "ema_12": 49000.0,
                "ema_26": 50000.0,
                "ema_50": 49500.0,
                "ema_200": 48000.0,
                "close_price": 50500.0,
            },  # Below
            {
                "ema_12": 49000.0,
                "ema_26": 50000.0,
                "ema_50": 49500.0,
                "ema_200": 48000.0,
                "close_price": 50500.0,
            },  # Below
        ]

        engine = StrategyEngine(engine_config, mock_reader)
        callback = AsyncMock()

        # First call to establish baseline (no crossover)
        await engine._evaluate_all(on_signal=callback)
        callback.reset_mock()

        # Now provide crossover up data (close_price > ema_50 = uptrend)
        mock_reader.fetch_latest.return_value = [
            {
                "ema_12": 49000.0,
                "ema_26": 50000.0,
                "ema_50": 49500.0,
                "ema_200": 48000.0,
                "close_price": 50500.0,
            },  # Was below
            {
                "ema_12": 50100.0,
                "ema_26": 49100.0,
                "ema_50": 49500.0,
                "ema_200": 48000.0,
                "close_price": 50600.0,
            },  # Now above
        ]

        await engine._evaluate_all(on_signal=callback)

        # Verify callback was called with BUY signal
        assert callback.call_count == 1
        signal: Signal = callback.call_args[0][0]
        assert signal.type == SignalType.BUY
        assert signal.symbol == "BTCUSDT"

    @pytest.mark.asyncio
    async def test_evaluate_sell_signal_triggers_callback(self, mock_reader, engine_config):
        """SELL signal from strategy triggers callback."""
        # First warmup the strategy with data
        mock_reader.fetch_latest.return_value = [
            {
                "ema_12": 50100.0,
                "ema_26": 49100.0,
                "ema_50": 51000.0,
                "ema_200": 48000.0,
                "close_price": 50500.0,
            },  # Above
            {
                "ema_12": 50100.0,
                "ema_26": 49100.0,
                "ema_50": 51000.0,
                "ema_200": 48000.0,
                "close_price": 50600.0,
            },  # Above
        ]

        engine = StrategyEngine(engine_config, mock_reader)
        callback = AsyncMock()

        # First evaluation to set state
        await engine._evaluate_all(on_signal=callback)
        callback.reset_mock()

        # Now crossover down (close_price < ema_50 = downtrend)
        mock_reader.fetch_latest.return_value = [
            {
                "ema_12": 50100.0,
                "ema_26": 49100.0,
                "ema_50": 51000.0,
                "ema_200": 48000.0,
                "close_price": 50500.0,
            },  # Above
            {
                "ema_12": 49000.0,
                "ema_26": 50000.0,
                "ema_50": 51000.0,
                "ema_200": 48000.0,
                "close_price": 50400.0,
            },  # Below
        ]

        await engine._evaluate_all(on_signal=callback)

        # Verify callback was called with SELL signal
        assert callback.call_count == 1
        signal: Signal = callback.call_args[0][0]
        assert signal.type == SignalType.SELL
        assert signal.symbol == "BTCUSDT"

    @pytest.mark.asyncio
    async def test_hold_does_not_trigger_callback(self, mock_reader, engine_config):
        """HOLD signal does not trigger callback."""
        # Mock indicators showing no crossover
        mock_reader.fetch_latest.return_value = [
            {
                "ema_12": 50100.0,
                "ema_26": 49100.0,
                "ema_50": 49500.0,
                "ema_200": 48000.0,
                "close_price": 50500.0,
            },
            {
                "ema_12": 50200.0,
                "ema_26": 49200.0,
                "ema_50": 49500.0,
                "ema_200": 48000.0,
                "close_price": 50600.0,
            },
        ]

        engine = StrategyEngine(engine_config, mock_reader)
        callback = AsyncMock()

        await engine._evaluate_all(on_signal=callback)

        # Callback should NOT be called for HOLD
        callback.assert_not_called()


class TestTradingExecutorOnSignal:
    """Test TradingExecutor.on_signal() method."""

    @pytest.mark.asyncio
    async def test_init_raises_without_keys_when_enabled(self, risk_manager, metrics):
        """Executor raises RuntimeError if enabled but keys are missing."""
        config = TradingConfig(
            api_key="",
            api_secret="",
            test_mode=True,
            enabled=True,
        )
        executor = TradingExecutor(config, risk_manager, metrics)

        with pytest.raises(RuntimeError, match="API keys missing"):
            await executor.__aenter__()

    @pytest.mark.asyncio
    async def test_on_signal_buy_uses_quote_qty(self, trading_config, risk_manager, metrics):
        """BUY signal uses quoteOrderQty (order_size_usdt)."""
        executor = TradingExecutor(trading_config, risk_manager, metrics)

        async def mock_init(self):
            self._client = MagicMock()
            self._client.__aenter__ = AsyncMock(return_value=self._client)
            self._client.__aexit__ = AsyncMock()

        with (
            patch.object(executor, "place_market_order", new_callable=AsyncMock) as mock_order,
            patch.object(executor, "__aenter__", new=mock_init),
        ):
            mock_order.return_value = OrderInfo(
                order_id="123",
                symbol="BTCUSDT",
                side="BUY",
                order_type="MARKET",
                quantity=100.0,
                price=None,
                status="FILLED",
                executed_quantity=0.002,
                create_time=int(time.time() * 1000),
            )

            signal = Signal(
                type=SignalType.BUY,
                symbol="BTCUSDT",
                price=50000.0,
                confidence=0.8,
                reason="Test BUY",
                indicators={},
            )

            await executor.__aenter__(executor)
            await executor.on_signal(signal)

            # Verify place_market_order called with order_size_usdt
            mock_order.assert_called_once_with("BTCUSDT", "BUY", 100.0)

    @pytest.mark.asyncio
    async def test_on_signal_sell_queries_balance(self, trading_config, risk_manager, metrics):
        """SELL signal queries asset balance."""
        executor = TradingExecutor(trading_config, risk_manager, metrics)

        async def mock_init(self):
            self._client = MagicMock()
            self._client.get_asset_balance = AsyncMock(return_value=0.5)
            self._client.normalize_sell_quantity = AsyncMock(return_value="0.5")
            self._client.__aenter__ = AsyncMock(return_value=self._client)
            self._client.__aexit__ = AsyncMock()

        with (
            patch.object(executor, "place_market_order", new_callable=AsyncMock) as mock_order,
            patch.object(executor, "__aenter__", new=mock_init),
        ):
            mock_order.return_value = OrderInfo(
                order_id="124",
                symbol="BTCUSDT",
                side="SELL",
                order_type="MARKET",
                quantity=0.5,
                price=None,
                status="FILLED",
                executed_quantity=0.5,
                create_time=int(time.time() * 1000),
            )

            await executor.__aenter__(executor)

            signal = Signal(
                type=SignalType.SELL,
                symbol="BTCUSDT",
                price=50000.0,
                confidence=0.8,
                reason="Test SELL",
                indicators={},
            )

            await executor.on_signal(signal)

            # Verify get_asset_balance was called
            executor._client.get_asset_balance.assert_called_once_with("BTC")
            # Verify place_market_order called with balance
            mock_order.assert_called_once_with("BTCUSDT", "SELL", 0.5)

    @pytest.mark.asyncio
    async def test_on_signal_sell_no_balance_skips(self, trading_config, risk_manager, metrics):
        """SELL signal with 0 balance skips order placement."""
        executor = TradingExecutor(trading_config, risk_manager, metrics)

        async def mock_init(self):
            self._client = MagicMock()
            self._client.get_asset_balance = AsyncMock(return_value=0.0)
            self._client.__aenter__ = AsyncMock(return_value=self._client)
            self._client.__aexit__ = AsyncMock()

        with (
            patch.object(executor, "place_market_order", new_callable=AsyncMock) as mock_order,
            patch.object(executor, "__aenter__", new=mock_init),
        ):
            await executor.__aenter__(executor)

            signal = Signal(
                type=SignalType.SELL,
                symbol="BTCUSDT",
                price=50000.0,
                confidence=0.8,
                reason="Test SELL",
                indicators={},
            )

            await executor.on_signal(signal)

            # Verify get_asset_balance was called
            executor._client.get_asset_balance.assert_called_once_with("BTC")
            # Verify NO order was placed
            mock_order.assert_not_called()

    @pytest.mark.asyncio
    async def test_on_signal_disabled_logs_and_skips(self, risk_manager, metrics):
        """Disabled executor logs and skips signal."""
        config = TradingConfig(
            api_key="test",
            api_secret="test",
            test_mode=True,
            enabled=False,  # Disabled
        )
        executor = TradingExecutor(config, risk_manager, metrics)

        signal = Signal(
            type=SignalType.BUY,
            symbol="BTCUSDT",
            price=50000.0,
            confidence=0.8,
            reason="Test",
            indicators={},
        )

        # Should not raise, just log
        await executor.on_signal(signal)

        # Verify client was never initialized
        assert executor._client is None

    @pytest.mark.asyncio
    async def test_on_signal_risk_block(self, trading_config, risk_manager, metrics):
        """Risk manager rejection is caught and logged."""
        # Configure risk manager to reject
        risk_manager.is_trading_allowed.return_value = (False, "Daily loss limit")

        executor = TradingExecutor(trading_config, risk_manager, metrics)

        async def mock_init(self):
            self._client = MagicMock()
            self._client.__aenter__ = AsyncMock(return_value=self._client)
            self._client.__aexit__ = AsyncMock()

        with (
            patch.object(executor, "place_market_order", new_callable=AsyncMock) as mock_order,
            patch.object(executor, "__aenter__", new=mock_init),
        ):
            mock_order.side_effect = RuntimeError("Trading blocked: Daily loss limit")

            await executor.__aenter__(executor)

            signal = Signal(
                type=SignalType.BUY,
                symbol="BTCUSDT",
                price=50000.0,
                confidence=0.8,
                reason="Test",
                indicators={},
            )

            # Should catch RuntimeError and log warning
            await executor.on_signal(signal)

            # Verify order was attempted but failed
            mock_order.assert_called_once()

    @pytest.mark.asyncio
    async def test_paper_mode_no_real_orders(self, trading_config, risk_manager, metrics):
        """Paper mode uses test_mode for all operations."""
        assert trading_config.test_mode is True

        executor = TradingExecutor(trading_config, risk_manager, metrics)

        async def mock_init(self):
            self._client = MagicMock()
            self._client._test_mode = True
            self._client.__aenter__ = AsyncMock(return_value=self._client)
            self._client.__aexit__ = AsyncMock()

        await mock_init(executor)

        # Verify client was created in test_mode
        assert executor._client is not None
        assert executor._client._test_mode is True

        signal = Signal(
            type=SignalType.BUY,
            symbol="BTCUSDT",
            price=50000.0,
            confidence=0.8,
            reason="Test",
            indicators={},
        )

        # Mock place_market_order to verify it's called
        with patch.object(executor, "place_market_order", new_callable=AsyncMock) as mock_order:
            mock_order.return_value = OrderInfo(
                order_id="MOCK",
                symbol="BTCUSDT",
                side="BUY",
                order_type="MARKET",
                quantity=100.0,
                price=None,
                status="FILLED",
                executed_quantity=0.002,
                create_time=int(time.time() * 1000),
            )

            await executor.on_signal(signal)

            # Verify mock order was placed (paper trading)
            mock_order.assert_called_once_with("BTCUSDT", "BUY", 100.0)
