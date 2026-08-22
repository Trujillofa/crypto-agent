from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta

from src.backtest.cost_overrides import (
    CostBook,
    effective_futures_funding_rate_per_bar,
)
from src.backtest.metrics import calculate_backtest_metrics
from src.backtest.models import BacktestConfig, BacktestResult, Trade
from src.backtest.sentiment_replay import ReplaySentimentScorer
from src.backtest.sizing import calculate_futures_order_quantity
from src.backtest.timeframes import timeframe_hours
from src.features.reader import FundingSettlement, IndicatorReader
from src.strategy.aggregator import SignalAggregator
from src.strategy.base import BaseStrategy
from src.strategy.basis_premium_filter import apply_basis_premium_gate
from src.strategy.cross_venue_dislocation import apply_cross_venue_dislocation_gate
from src.strategy.sentiment_mean_reversion import SentimentMeanReversionStrategy
from src.strategy.session_liquidity import apply_session_liquidity_gate
from src.strategy.signals import Signal, SignalType
from src.utils.logger import get_logger


@dataclass(frozen=True)
class _PendingSignal:
    signal: Signal
    signal_time: str
    atr: float


class BacktestEngine:
    """Engine to simulate trading strategies on historical data."""

    def __init__(self, config: BacktestConfig, reader: IndicatorReader) -> None:
        self._config = config
        self._cost_book = CostBook(
            fee_rate=config.fee_rate,
            slippage_pct=config.slippage_pct,
            futures_funding_rate=config.futures_funding_rate,
            funding_cadence=config.funding_cadence,
            fixed_notional_usdt=config.fixed_notional_usdt,
            quantity_step_size=config.quantity_step_size,
            min_notional_usdt=config.min_notional_usdt,
        )
        self._reader = reader
        self._logger = get_logger(self.__class__.__name__)
        self._aggregator = SignalAggregator(config.aggregator_config)

        self._cash = config.initial_capital
        self._position_qty = 0.0
        self._position_entry_price = 0.0
        self._position_entry_fee = 0.0
        self._position_entry_time = ""
        self._position_atr = 0.0
        self._position_sl_price = 0.0
        self._position_tp_price = 0.0
        self._position_high_water_mark = 0.0
        self._position_margin_used = 0.0
        self._position_funding_paid = 0.0
        self._position_signal_time: str | None = None
        self._position_fill_source = "signal_close"
        self._equity_curve: list[float] = []
        self._trades: list[Trade] = []
        self._blocked_buy_count = 0
        self._basis_blocked_buy_count = 0
        self._dislocation_blocked_buy_count = 0
        self._queued_signal_count = 0
        self._unfilled_signal_count = 0
        self._funding_settlement_count = 0
        self._last_data: list[Mapping[str, object]] = []
        self._last_settlements: list[FundingSettlement] = []

    @property
    def data_fingerprint(self) -> str | None:
        """Fingerprint the exact ordered input rows from the most recent run."""
        if not self._last_data:
            return None
        # Delayed to avoid making the simulator depend on artifact I/O.
        from src.backtest.artifacts import fingerprint_rows

        return fingerprint_rows(self._last_data)

    @property
    def funding_fingerprint(self) -> str | None:
        """Fingerprint exact funding events consumed by the latest v2 run."""
        if not self._last_settlements:
            return None
        from src.backtest.artifacts import fingerprint_rows

        return fingerprint_rows(
            [
                {
                    "funding_time": settlement.funding_time,
                    "funding_rate": settlement.funding_rate,
                    "mark_price": settlement.mark_price,
                }
                for settlement in self._last_settlements
            ]
        )

    @staticmethod
    def _get_required_timeframes(strategy: BaseStrategy) -> dict[str, str]:
        required = getattr(strategy, "REQUIRED_TIMEFRAMES", {})
        return required if required else {}

    def _validate_strategy_timeframes(
        self, strategies: list[BaseStrategy]
    ) -> dict[str, str] | None:
        mtf_requirements = [
            self._get_required_timeframes(strategy)
            for strategy in strategies
            if self._get_required_timeframes(strategy)
        ]

        if not mtf_requirements:
            return None

        if len(mtf_requirements) != len(strategies):
            raise ValueError(
                "Mixed multi-timeframe and single-timeframe backtest strategy sets are not supported"
            )

        first = mtf_requirements[0]
        for requirement in mtf_requirements[1:]:
            if requirement != first:
                raise ValueError(
                    "Backtest strategies must declare identical REQUIRED_TIMEFRAMES when using multi-timeframe mode"
                )

        return first

    def _resolved_cost_audit(self) -> dict[str, object]:
        round_trip_cost_pct = (
            2.0 * (self._cost_book.fee_rate + self._cost_book.slippage_pct) * 100.0
        )
        effective_funding = (
            effective_futures_funding_rate_per_bar(
                self._cost_book.futures_funding_rate,
                self._config.timeframe,
                cadence=self._cost_book.funding_cadence,
            )
            if self._config.futures_mode
            else 0.0
        )
        return {
            "fee_rate": self._cost_book.fee_rate,
            "slippage_pct": self._cost_book.slippage_pct,
            "round_trip_cost_pct": round_trip_cost_pct,
            "funding_cadence": self._cost_book.funding_cadence,
            "futures_funding_rate_base": self._cost_book.futures_funding_rate,
            "effective_futures_funding_rate_per_bar": effective_funding,
            "futures_mode": self._config.futures_mode,
            "execution_profile": self._config.execution_profile,
            "apply_global_trend_filter": self._config.apply_global_trend_filter,
            "global_trend_filter_active": self._config.apply_global_trend_filter,
            "global_trend_filter_buffer_pct": self._config.global_trend_filter_buffer_pct,
            "global_trend_filter_source": self._config.global_trend_filter_source,
            "config_global_trend_filter_enabled": self._config.config_global_trend_filter_enabled,
        }

    async def run(self) -> BacktestResult:
        """Execute the backtest."""
        timeframe_hours(self._config.timeframe)
        self._logger.info(f"Starting backtest for {self._config.symbol}...")
        self._logger.info("Resolved backtest config audit: %s", self._resolved_cost_audit())

        # Instantiate strategies first to check if any require MTF
        strategies = []
        replay_scorer = None
        if self._config.replay_sentiment_path:
            replay_scorer = ReplaySentimentScorer(
                self._config.replay_sentiment_path,
                max_age_seconds=self._config.replay_sentiment_max_age_seconds,
            )
        for idx, cls in enumerate(self._config.strategy_classes):
            cfg = None
            if idx < len(self._config.strategy_configs):
                cfg = self._config.strategy_configs[idx]
            strategy = cls(cfg)
            if replay_scorer is not None and isinstance(strategy, SentimentMeanReversionStrategy):
                strategy.set_scorer(replay_scorer)
            strategies.append(strategy)

        mtf_timeframes = self._validate_strategy_timeframes(strategies)

        if mtf_timeframes:
            # Multi-timeframe backtest
            entry_tf = mtf_timeframes.get("entry", self._config.timeframe)
            regime_tf = mtf_timeframes.get("regime", "4h")
            timeframe_hours(entry_tf)
            timeframe_hours(regime_tf)

            self._logger.info(f"Multi-timeframe mode: entry={entry_tf}, regime={regime_tf}")

            data = await self._reader.fetch_multi_timeframe(
                symbol=self._config.symbol,
                entry_timeframe=entry_tf,
                regime_timeframe=regime_tf,
                start_time=self._config.start_date,
                end_time=self._config.end_date,
            )
        else:
            # Single-timeframe backtest (original behavior)
            data = await self._reader.fetch_range(
                self._config.symbol,
                self._config.timeframe,
                self._config.start_date,
                self._config.end_date,
            )

        if not data:
            self._logger.warning("No data found for backtest range")
            return self._create_empty_result()

        self._last_data = data
        self._logger.info(f"Loaded {len(data)} data points")

        if self._config.execution_profile == "execution_parity_v2":
            if self._config.allow_short and not self._config.futures_mode:
                raise ValueError("execution_parity_v2 does not support synthetic spot shorts")
            settlements = (
                await self._reader.fetch_funding_settlements(
                    self._config.symbol,
                    self._config.start_date,
                    self._config.end_date,
                )
                if self._config.futures_mode
                else []
            )
            self._last_settlements = settlements
            return await self._run_execution_parity_v2(data, strategies, settlements, replay_scorer)

        for row in data:
            current_time = str(row["time"])
            current_price = row["close_price"]
            high_price = row.get("high_price", current_price)
            low_price = row.get("low_price", current_price)

            if self._position_qty != 0:
                self._apply_funding(current_time, current_price)
                if self._check_liquidation(current_time, current_price):
                    continue
                if self._config.use_executor_exit_model:
                    if self._check_executor_exit(current_time, high_price, low_price):
                        continue
                else:
                    if self._check_fixed_exit(current_time, high_price, low_price):
                        continue
                if self._check_time_stop(current_time, current_price):
                    continue

            signals = []
            for strategy in strategies:
                try:
                    sig = await strategy.evaluate(self._config.symbol, row)
                    signals.append(sig)
                except Exception as e:
                    self._logger.error(f"Strategy error at {current_time}: {e}")

            final_signal = self._aggregator.aggregate(
                self._config.symbol, signals, ema_200=row.get("ema_200")
            )

            if self._config.apply_global_trend_filter and final_signal.type == SignalType.BUY:
                ema_200 = row.get("ema_200")
                buffer_pct = self._config.global_trend_filter_buffer_pct
                if ema_200 is not None and current_price < ema_200 * (1 - buffer_pct):
                    final_signal = Signal(
                        type=SignalType.HOLD,
                        symbol=final_signal.symbol,
                        price=final_signal.price,
                        confidence=0.0,
                        reason=(
                            f"Blocked by Global Trend Filter (Price < {buffer_pct * 100:.1f}% below EMA200)"
                        ),
                        indicators=final_signal.indicators,
                        trading_mode=final_signal.trading_mode,
                    )

            if final_signal.type == SignalType.BUY:
                final_signal, blocked = apply_session_liquidity_gate(
                    final_signal,
                    row["time"],
                    self._config.session_liquidity_router,
                )
                if blocked:
                    self._blocked_buy_count += 1
                    self._logger.info(
                        "Blocked by Session Liquidity Router at %s: %s",
                        current_time,
                        final_signal.reason,
                    )

            if final_signal.type == SignalType.BUY:
                final_signal, basis_blocked = apply_basis_premium_gate(
                    final_signal,
                    row,
                    self._config.basis_premium_filter,
                )
                if basis_blocked:
                    self._basis_blocked_buy_count += 1
                    self._logger.info(
                        "Blocked by Basis Premium Filter at %s: %s",
                        current_time,
                        final_signal.reason,
                    )

            if final_signal.type == SignalType.BUY:
                final_signal, disloc_blocked = apply_cross_venue_dislocation_gate(
                    final_signal,
                    row,
                    self._config.cross_venue_dislocation,
                )
                if disloc_blocked:
                    self._dislocation_blocked_buy_count += 1
                    self._logger.info(
                        "Blocked by Cross Venue Dislocation Filter at %s: %s",
                        current_time,
                        final_signal.reason,
                    )

            atr = row.get("atr_14", 0.0)
            self._process_signal(final_signal, current_time, current_price, atr)

            self._equity_curve.append(self._calculate_equity(current_price))

        if self._position_qty != 0:
            last_price = data[-1]["close_price"]
            last_time = str(data[-1]["time"])
            self._close_position(last_time, last_price)

        if replay_scorer is not None:
            stats = replay_scorer.stats()
            self._logger.info(
                "Replay sentiment coverage: hits=%d misses=%d stale=%d loaded_obs=%d loaded_symbols=%d",
                stats["hits"],
                stats["misses"],
                stats["stale_misses"],
                stats["loaded_observations"],
                stats["loaded_symbols"],
            )

        return self._calculate_metrics()

    async def _run_execution_parity_v2(
        self,
        data: list[Mapping[str, object]],
        strategies: list[BaseStrategy],
        settlements: list[FundingSettlement],
        replay_scorer: ReplaySentimentScorer | None,
    ) -> BacktestResult:
        """Evaluate at bar close and fill actionable market orders at the next open."""
        self._validate_funding_settlements(data, settlements)
        pending: _PendingSignal | None = None
        settlement_index = 0

        for row in data:
            current_time = str(row["time"])
            current_dt = datetime.fromisoformat(current_time)
            open_price = float(row.get("open_price", row["close_price"]))
            close_price = float(row["close_price"])
            high_price = float(row.get("high_price", close_price))
            low_price = float(row.get("low_price", close_price))

            while (
                settlement_index < len(settlements)
                and settlements[settlement_index].funding_time <= current_dt
            ):
                self._apply_funding_settlement(settlements[settlement_index], open_price)
                settlement_index += 1

            if pending is not None:
                self._process_queued_signal(pending, current_time, open_price)
                pending = None

            if self._position_qty != 0:
                if self._check_liquidation(current_time, open_price):
                    self._equity_curve.append(self._calculate_equity(close_price))
                    continue
                if self._config.use_executor_exit_model:
                    self._check_executor_exit(current_time, high_price, low_price)
                else:
                    self._check_fixed_exit(current_time, high_price, low_price)
                if self._position_qty != 0:
                    self._check_time_stop(current_time, close_price)

            final_signal = await self._evaluate_signal(strategies, row, current_time, close_price)
            if self._is_actionable_signal(final_signal):
                pending = _PendingSignal(
                    signal=final_signal,
                    signal_time=current_time,
                    atr=float(row.get("atr_14") or 0.0),
                )
                self._queued_signal_count += 1

            self._equity_curve.append(self._calculate_equity(close_price))

        if pending is not None:
            self._unfilled_signal_count += 1
        if self._position_qty != 0:
            last_row = data[-1]
            self._close_position(
                str(last_row["time"]),
                float(last_row["close_price"]),
                reason="END_OF_DATA",
            )

        if replay_scorer is not None:
            stats = replay_scorer.stats()
            self._logger.info(
                "Replay sentiment coverage: hits=%d misses=%d stale=%d loaded_obs=%d loaded_symbols=%d",
                stats["hits"],
                stats["misses"],
                stats["stale_misses"],
                stats["loaded_observations"],
                stats["loaded_symbols"],
            )
        return self._calculate_metrics()

    async def _evaluate_signal(
        self,
        strategies: list[BaseStrategy],
        row: Mapping[str, object],
        current_time: str,
        current_price: float,
    ) -> Signal:
        signals: list[Signal] = []
        for strategy in strategies:
            try:
                signals.append(await strategy.evaluate(self._config.symbol, row))
            except Exception as exc:  # noqa: BLE001
                self._logger.error("Strategy error at %s: %s", current_time, exc)

        final_signal = self._aggregator.aggregate(
            self._config.symbol, signals, ema_200=row.get("ema_200")
        )
        if self._config.apply_global_trend_filter and final_signal.type == SignalType.BUY:
            ema_200 = row.get("ema_200")
            buffer_pct = self._config.global_trend_filter_buffer_pct
            if isinstance(ema_200, (int, float)) and current_price < ema_200 * (1 - buffer_pct):
                final_signal = Signal(
                    type=SignalType.HOLD,
                    symbol=final_signal.symbol,
                    price=final_signal.price,
                    confidence=0.0,
                    reason=f"Blocked by Global Trend Filter (Price < {buffer_pct * 100:.1f}% below EMA200)",
                    indicators=final_signal.indicators,
                    trading_mode=final_signal.trading_mode,
                )

        if final_signal.type == SignalType.BUY:
            final_signal, blocked = apply_session_liquidity_gate(
                final_signal, row["time"], self._config.session_liquidity_router
            )
            if blocked:
                self._blocked_buy_count += 1
        if final_signal.type == SignalType.BUY:
            final_signal, blocked = apply_basis_premium_gate(
                final_signal, row, self._config.basis_premium_filter
            )
            if blocked:
                self._basis_blocked_buy_count += 1
        if final_signal.type == SignalType.BUY:
            final_signal, blocked = apply_cross_venue_dislocation_gate(
                final_signal, row, self._config.cross_venue_dislocation
            )
            if blocked:
                self._dislocation_blocked_buy_count += 1
        return final_signal

    def _is_actionable_signal(self, signal: Signal) -> bool:
        if signal.type == SignalType.BUY:
            return self._position_qty <= 0
        if signal.type == SignalType.SELL:
            return (self._position_qty > 0 and not self._config.ignore_signal_sells) or (
                self._position_qty == 0 and self._config.allow_short
            )
        return False

    def _process_queued_signal(
        self, pending: _PendingSignal, timestamp: str, open_price: float
    ) -> None:
        signal = pending.signal
        if signal.type == SignalType.BUY:
            if self._position_qty == 0:
                self._open_long(
                    timestamp,
                    open_price,
                    pending.atr,
                    signal_time=pending.signal_time,
                    fill_source="next_bar_open",
                )
            elif self._position_qty < 0:
                self._close_position(timestamp, open_price, reason="SIGNAL")
        elif signal.type == SignalType.SELL:
            if self._position_qty > 0 and not self._config.ignore_signal_sells:
                self._close_position(timestamp, open_price, reason="SIGNAL")
            elif self._position_qty == 0 and self._config.allow_short:
                self._open_short(
                    timestamp,
                    open_price,
                    pending.atr,
                    signal_time=pending.signal_time,
                    fill_source="next_bar_open",
                )

    def _validate_funding_settlements(
        self, data: list[Mapping[str, object]], settlements: list[FundingSettlement]
    ) -> None:
        if not self._config.futures_mode:
            return
        start = datetime.fromisoformat(str(data[0]["time"]))
        end = datetime.fromisoformat(str(data[-1]["time"]))
        expected: set[datetime] = set()
        cursor = start.replace(hour=0, minute=0, second=0, microsecond=0)
        while cursor <= start:
            cursor += timedelta(hours=8)
        while cursor <= end:
            expected.add(cursor)
            cursor += timedelta(hours=8)
        actual = {settlement.funding_time for settlement in settlements}
        missing = sorted(expected - actual)
        if missing:
            raise ValueError(
                f"Missing historical funding settlements: {', '.join(item.isoformat() for item in missing)}"
            )

    def _apply_funding_settlement(
        self, settlement: FundingSettlement, fallback_price: float
    ) -> None:
        if self._position_qty == 0:
            return
        price = settlement.mark_price or fallback_price
        signed_cost = (
            abs(self._position_qty)
            * price
            * settlement.funding_rate
            * (1 if self._position_qty > 0 else -1)
        )
        self._cash -= signed_cost
        self._position_funding_paid += signed_cost
        self._funding_settlement_count += 1

    def _process_signal(
        self, signal: Signal, timestamp: str, price: float, atr: float = 0.0
    ) -> None:
        if signal.type == SignalType.BUY:
            if self._position_qty == 0:
                self._open_long(timestamp, price, atr)
            elif self._position_qty < 0:
                self._close_position(timestamp, price, reason="SIGNAL")

        elif signal.type == SignalType.SELL:
            if self._position_qty > 0 and not self._config.ignore_signal_sells:
                self._close_position(timestamp, price, reason="SIGNAL")
            elif self._position_qty == 0 and self._config.allow_short:
                self._open_short(timestamp, price, atr)

    def _check_fixed_exit(self, timestamp: str, high_price: float, low_price: float) -> bool:
        if self._config.stop_loss_pct > 0:
            if self._position_qty > 0:
                sl_price = self._position_entry_price * (1 - self._config.stop_loss_pct)
                if low_price <= sl_price:
                    self._close_position(timestamp, sl_price, reason="STOP_LOSS")
                    return True
            else:
                sl_price = self._position_entry_price * (1 + self._config.stop_loss_pct)
                if high_price >= sl_price:
                    self._close_position(timestamp, sl_price, reason="STOP_LOSS")
                    return True

        if self._config.take_profit_pct > 0:
            if self._position_qty > 0:
                tp_price = self._position_entry_price * (1 + self._config.take_profit_pct)
                if high_price >= tp_price:
                    self._close_position(timestamp, tp_price, reason="TAKE_PROFIT")
                    return True
            else:
                tp_price = self._position_entry_price * (1 - self._config.take_profit_pct)
                if low_price <= tp_price:
                    self._close_position(timestamp, tp_price, reason="TAKE_PROFIT")
                    return True
        return False

    def _check_executor_exit(self, timestamp: str, high_price: float, low_price: float) -> bool:
        if self._position_qty > 0:
            return self._check_executor_exit_long(timestamp, high_price, low_price)
        if self._position_qty < 0:
            return self._check_executor_exit_short(timestamp, high_price, low_price)
        return False

    def _check_executor_exit_long(
        self, timestamp: str, high_price: float, low_price: float
    ) -> bool:
        if self._position_atr > 0:
            if high_price > self._position_high_water_mark:
                self._position_high_water_mark = high_price

            trailing_activation = (
                self._position_entry_price + self._config.trailing_activate_atr * self._position_atr
            )
            if self._position_high_water_mark >= trailing_activation:
                trailing_sl = (
                    self._position_high_water_mark
                    - self._config.trailing_offset_atr * self._position_atr
                )
                if trailing_sl > self._position_sl_price:
                    self._position_sl_price = trailing_sl

            if self._position_sl_price > 0 and low_price <= self._position_sl_price:
                self._close_position(timestamp, self._position_sl_price, reason="STOP_LOSS")
                return True
            if self._position_tp_price > 0 and high_price >= self._position_tp_price:
                self._close_position(timestamp, self._position_tp_price, reason="TAKE_PROFIT")
                return True
            return False

        return self._check_fixed_exit(timestamp, high_price, low_price)

    def _check_executor_exit_short(
        self, timestamp: str, high_price: float, low_price: float
    ) -> bool:
        if self._position_atr > 0:
            if low_price < self._position_high_water_mark:
                self._position_high_water_mark = low_price

            trailing_activation = (
                self._position_entry_price - self._config.trailing_activate_atr * self._position_atr
            )
            if self._position_high_water_mark <= trailing_activation:
                trailing_sl = (
                    self._position_high_water_mark
                    + self._config.trailing_offset_atr * self._position_atr
                )
                if self._position_sl_price == 0.0 or trailing_sl < self._position_sl_price:
                    self._position_sl_price = trailing_sl

            if self._position_sl_price > 0 and high_price >= self._position_sl_price:
                self._close_position(timestamp, self._position_sl_price, reason="STOP_LOSS")
                return True
            if self._position_tp_price > 0 and low_price <= self._position_tp_price:
                self._close_position(timestamp, self._position_tp_price, reason="TAKE_PROFIT")
                return True
            return False

        return self._check_fixed_exit(timestamp, high_price, low_price)

    def _check_time_stop(self, timestamp: str, current_price: float) -> bool:
        if self._config.time_stop_minutes <= 0 or not self._position_entry_time:
            return False
        entry_dt = datetime.fromisoformat(str(self._position_entry_time))
        current_dt = datetime.fromisoformat(str(timestamp))
        elapsed_minutes = (current_dt - entry_dt).total_seconds() / 60
        if elapsed_minutes >= self._config.time_stop_minutes:
            self._close_position(timestamp, current_price, reason="TIME_STOP")
            return True
        return False

    def _calculate_entry_qty(self, entry_price: float, atr: float) -> float:
        if entry_price <= 0:
            return 0.0

        if self._config.futures_mode:
            leverage = max(self._config.futures_leverage, 1)
            max_qty = self._cash / (entry_price * ((1 / leverage) + self._cost_book.fee_rate))
            if self._config.use_atr_sizing and atr > 0:
                risk_amount = self._cash * self._config.risk_per_trade
                stop_distance = atr * self._config.atr_multiplier
                target_qty = risk_amount / stop_distance if stop_distance > 0 else 0.0
                quantity = min(target_qty, max_qty)
            else:
                quantity = max_qty
            return self._cap_fixed_notional(quantity, entry_price)

        if self._config.use_atr_sizing and atr > 0:
            risk_amount = self._cash * self._config.risk_per_trade
            stop_distance = atr * self._config.atr_multiplier
            target_qty = risk_amount / stop_distance if stop_distance > 0 else 0.0
            max_qty = (self._cash * (1 - self._cost_book.fee_rate)) / entry_price
            return self._cap_fixed_notional(min(target_qty, max_qty), entry_price)

        quantity = (self._cash * (1 - self._cost_book.fee_rate)) / entry_price
        return self._cap_fixed_notional(quantity, entry_price)

    def _cap_fixed_notional(self, quantity: float, entry_price: float) -> float:
        if self._cost_book.fixed_notional_usdt <= 0:
            capped_quantity = quantity
        else:
            capped_quantity = min(quantity, self._cost_book.fixed_notional_usdt / entry_price)
        return self._apply_quantity_step(capped_quantity, entry_price)

    def _apply_quantity_step(self, quantity: float, entry_price: float) -> float:
        if self._cost_book.quantity_step_size <= 0:
            return quantity
        return calculate_futures_order_quantity(
            order_size_usdt=quantity * entry_price,
            price=entry_price,
            quantity_step_size=self._cost_book.quantity_step_size,
            min_notional_usdt=self._cost_book.min_notional_usdt,
        )

    def _open_long(
        self,
        timestamp: str,
        price: float,
        atr: float,
        *,
        signal_time: str | None = None,
        fill_source: str = "signal_close",
    ) -> None:
        entry_price = price * (1 + self._cost_book.slippage_pct)
        qty = self._calculate_entry_qty(entry_price, atr)
        notional = qty * entry_price
        fee = notional * self._cost_book.fee_rate

        if self._config.futures_mode:
            margin = self._calculate_margin(notional)
            self._cash -= margin + fee
            self._position_margin_used = margin
            self._position_funding_paid = 0.0
        else:
            self._cash -= fee
        self._position_qty = qty
        self._position_entry_price = entry_price
        self._position_entry_fee = fee
        self._position_entry_time = timestamp
        self._position_signal_time = signal_time or timestamp
        self._position_fill_source = fill_source
        self._position_atr = atr if atr > 0 else 0.0
        self._position_high_water_mark = entry_price
        if self._config.use_executor_exit_model and atr > 0:
            self._position_sl_price = entry_price - self._config.sl_atr_multiplier * atr
            self._position_tp_price = entry_price + self._config.tp_atr_multiplier * atr
        else:
            self._position_sl_price = (
                entry_price * (1 - self._config.stop_loss_pct)
                if self._config.stop_loss_pct > 0
                else 0.0
            )
            self._position_tp_price = (
                entry_price * (1 + self._config.take_profit_pct)
                if self._config.take_profit_pct > 0
                else 0.0
            )

    def _open_short(
        self,
        timestamp: str,
        price: float,
        atr: float,
        *,
        signal_time: str | None = None,
        fill_source: str = "signal_close",
    ) -> None:
        entry_price = price * (1 - self._cost_book.slippage_pct)
        qty = self._calculate_entry_qty(entry_price, atr)
        notional = qty * entry_price
        fee = notional * self._cost_book.fee_rate

        if self._config.futures_mode:
            margin = self._calculate_margin(notional)
            self._cash -= margin + fee
            self._position_margin_used = margin
            self._position_funding_paid = 0.0
        else:
            self._cash -= fee
        self._position_qty = -qty
        self._position_entry_price = entry_price
        self._position_entry_fee = fee
        self._position_entry_time = timestamp
        self._position_signal_time = signal_time or timestamp
        self._position_fill_source = fill_source
        self._position_atr = atr if atr > 0 else 0.0
        self._position_high_water_mark = entry_price
        if self._config.use_executor_exit_model and atr > 0:
            self._position_sl_price = entry_price + self._config.sl_atr_multiplier * atr
            self._position_tp_price = entry_price - self._config.tp_atr_multiplier * atr
        else:
            self._position_sl_price = (
                entry_price * (1 + self._config.stop_loss_pct)
                if self._config.stop_loss_pct > 0
                else 0.0
            )
            self._position_tp_price = (
                entry_price * (1 - self._config.take_profit_pct)
                if self._config.take_profit_pct > 0
                else 0.0
            )

    def _close_position(self, timestamp: str, price: float, reason: str = "SIGNAL") -> None:
        is_long = self._position_qty > 0
        qty = abs(self._position_qty)
        margin_used = self._position_margin_used

        if is_long:
            exit_price = price * (1 - self._cost_book.slippage_pct)
            gross_pnl = (exit_price - self._position_entry_price) * qty
            trade_side = "BUY"
        else:
            exit_price = price * (1 + self._cost_book.slippage_pct)
            gross_pnl = (self._position_entry_price - exit_price) * qty
            trade_side = "SELL"

        exit_notional = qty * exit_price
        exit_fee = exit_notional * self._cost_book.fee_rate

        pnl = gross_pnl - self._position_entry_fee - exit_fee - self._position_funding_paid
        if self._config.futures_mode:
            self._cash += margin_used + gross_pnl - exit_fee
            total_cost = margin_used + self._position_entry_fee
        else:
            self._cash += gross_pnl - exit_fee
            entry_notional = qty * self._position_entry_price
            total_cost = entry_notional + self._position_entry_fee
        return_pct = (pnl / total_cost) * 100 if total_cost > 0 else 0.0

        trade = Trade(
            entry_time=self._position_entry_time,
            exit_time=timestamp,
            side=trade_side,
            entry_price=self._position_entry_price,
            exit_price=exit_price,
            quantity=qty,
            pnl=pnl,
            return_pct=return_pct,
            exit_reason=reason,
            margin_used=margin_used,
            signal_time=self._position_signal_time,
            fill_source=self._position_fill_source,
            funding_paid=self._position_funding_paid,
        )

        self._trades.append(trade)
        self._reset_position()

    def _reset_position(self) -> None:
        self._position_qty = 0.0
        self._position_entry_price = 0.0
        self._position_entry_fee = 0.0
        self._position_entry_time = ""
        self._position_atr = 0.0
        self._position_sl_price = 0.0
        self._position_tp_price = 0.0
        self._position_high_water_mark = 0.0
        self._position_margin_used = 0.0
        self._position_funding_paid = 0.0
        self._position_signal_time = None
        self._position_fill_source = "signal_close"

    def _calculate_margin(self, notional: float) -> float:
        leverage = max(self._config.futures_leverage, 1)
        return notional / leverage

    def _calculate_unrealized_pnl(self, current_price: float) -> float:
        if self._position_qty > 0:
            return (current_price - self._position_entry_price) * self._position_qty
        if self._position_qty < 0:
            return (self._position_entry_price - current_price) * abs(self._position_qty)
        return 0.0

    def _calculate_equity(self, current_price: float) -> float:
        unrealized_pnl = self._calculate_unrealized_pnl(current_price)
        if self._config.futures_mode:
            return self._cash + self._position_margin_used + unrealized_pnl
        return self._cash + unrealized_pnl

    def _apply_funding(self, timestamp: str, current_price: float) -> None:
        if not self._config.futures_mode or self._position_qty == 0:
            return

        per_bar_rate = effective_futures_funding_rate_per_bar(
            self._cost_book.futures_funding_rate,
            self._config.timeframe,
            cadence=self._cost_book.funding_cadence,
        )
        funding_cost = abs(self._position_qty) * current_price * per_bar_rate
        self._cash -= funding_cost
        self._position_funding_paid += funding_cost
        self._logger.debug(
            "Applied futures funding at %s: cost=%.6f price=%.6f qty=%.6f",
            timestamp,
            funding_cost,
            current_price,
            abs(self._position_qty),
        )

    def _check_liquidation(self, timestamp: str, current_price: float) -> bool:
        if not self._config.futures_mode or self._position_qty == 0:
            return False

        equity = self._calculate_equity(current_price)
        if equity > 0:
            return False

        self._logger.warning(
            "Liquidating futures position at %s: price=%.6f equity=%.6f margin_used=%.6f",
            timestamp,
            current_price,
            equity,
            self._position_margin_used,
        )
        self._close_position(timestamp, current_price, reason="LIQUIDATION")
        return True

    def _calculate_metrics(self) -> BacktestResult:
        return calculate_backtest_metrics(
            config=self._config,
            equity_curve=self._equity_curve,
            trades=self._trades,
            blocked_buy_count=self._blocked_buy_count,
            basis_blocked_buy_count=self._basis_blocked_buy_count,
            dislocation_blocked_buy_count=self._dislocation_blocked_buy_count,
            queued_signal_count=self._queued_signal_count,
            unfilled_signal_count=self._unfilled_signal_count,
            funding_settlement_count=self._funding_settlement_count,
        )

    def _create_empty_result(self) -> BacktestResult:
        return BacktestResult(
            total_return=0.0,
            total_return_pct=0.0,
            max_drawdown=0.0,
            win_rate=0.0,
            total_trades=0,
            trades=[],
            final_equity=self._config.initial_capital,
            sharpe_ratio=0.0,
            sortino_ratio=0.0,
            profit_factor=0.0,
            avg_win_loss_ratio=0.0,
            blocked_buy_count=self._blocked_buy_count,
            basis_blocked_buy_count=self._basis_blocked_buy_count,
            dislocation_blocked_buy_count=self._dislocation_blocked_buy_count,
            queued_signal_count=self._queued_signal_count,
            unfilled_signal_count=self._unfilled_signal_count,
            funding_settlement_count=self._funding_settlement_count,
        )
