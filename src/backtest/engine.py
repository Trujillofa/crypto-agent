from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime

from src.backtest.sentiment_replay import ReplaySentimentScorer
from src.features.reader import IndicatorReader
from src.strategy.aggregator import SignalAggregator
from src.strategy.base import BaseStrategy
from src.strategy.sentiment_mean_reversion import SentimentMeanReversionStrategy
from src.strategy.signals import Signal, SignalType
from src.utils.logger import get_logger


@dataclass
class BacktestConfig:
    """Configuration for a backtest run."""

    symbol: str
    timeframe: str
    start_date: str  # ISO 8601
    end_date: str  # ISO 8601
    initial_capital: float = 10000.0
    fee_rate: float = 0.001  # 0.1%
    stop_loss_pct: float = 0.0  # 0.0 = disabled
    take_profit_pct: float = 0.0  # 0.0 = disabled
    sl_atr_multiplier: float = 2.0
    tp_atr_multiplier: float = 4.5
    trailing_activate_atr: float = 1.5
    trailing_offset_atr: float = 1.0
    slippage_pct: float = 0.001  # 0.1% slippage per trade
    risk_per_trade: float = 0.02  # 2% risk of equity per trade (used if use_atr_sizing=True)
    use_atr_sizing: bool = False
    atr_multiplier: float = 1.5  # Stop distance = 1.5 * ATR
    apply_global_trend_filter: bool = True
    global_trend_filter_buffer_pct: float = 0.0
    allow_short: bool = False
    use_executor_exit_model: bool = False
    ignore_signal_sells: bool = False
    strategy_classes: list[type[BaseStrategy]] = field(default_factory=list)
    strategy_configs: list[Mapping[str, object] | None] = field(default_factory=list)
    aggregator_config: Mapping[str, object] = field(default_factory=dict)
    time_stop_minutes: float = 0  # 0 = disabled
    replay_sentiment_path: str | None = None
    replay_sentiment_max_age_seconds: float | None = None
    futures_mode: bool = False
    futures_leverage: int = 5
    futures_funding_rate: float = 0.0001
    fixed_notional_usdt: float = 0.0  # 0 = size from available capital
    quantity_step_size: float = 0.0  # 0 = ideal fractional quantity
    min_notional_usdt: float = 0.0  # 0 = disabled


@dataclass
class Trade:
    """Record of a simulated trade."""

    entry_time: str
    exit_time: str
    side: str
    entry_price: float
    exit_price: float
    quantity: float
    pnl: float
    return_pct: float
    exit_reason: str = "SIGNAL"
    margin_used: float = 0.0


@dataclass
class BacktestResult:
    """Results of a backtest."""

    total_return: float
    total_return_pct: float
    max_drawdown: float
    win_rate: float
    total_trades: int
    trades: list[Trade]
    final_equity: float
    sharpe_ratio: float
    sortino_ratio: float
    profit_factor: float
    avg_win_loss_ratio: float


class BacktestEngine:
    """Engine to simulate trading strategies on historical data."""

    def __init__(self, config: BacktestConfig, reader: IndicatorReader) -> None:
        self._config = config
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
        self._equity_curve: list[float] = []
        self._trades: list[Trade] = []

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

    async def run(self) -> BacktestResult:
        """Execute the backtest."""
        self._logger.info(f"Starting backtest for {self._config.symbol}...")

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

        self._logger.info(f"Loaded {len(data)} data points")

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
        if self._position_qty <= 0:
            return self._check_fixed_exit(timestamp, high_price, low_price)

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
            max_qty = self._cash / (entry_price * ((1 / leverage) + self._config.fee_rate))
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
            max_qty = (self._cash * (1 - self._config.fee_rate)) / entry_price
            return self._cap_fixed_notional(min(target_qty, max_qty), entry_price)

        quantity = (self._cash * (1 - self._config.fee_rate)) / entry_price
        return self._cap_fixed_notional(quantity, entry_price)

    def _cap_fixed_notional(self, quantity: float, entry_price: float) -> float:
        if self._config.fixed_notional_usdt <= 0:
            capped_quantity = quantity
        else:
            capped_quantity = min(quantity, self._config.fixed_notional_usdt / entry_price)
        return self._apply_quantity_step(capped_quantity, entry_price)

    def _apply_quantity_step(self, quantity: float, entry_price: float) -> float:
        step = self._config.quantity_step_size
        if step <= 0:
            return quantity
        truncated = math.floor(quantity / step) * step
        if truncated * entry_price < self._config.min_notional_usdt:
            return truncated + step
        return truncated

    def _open_long(self, timestamp: str, price: float, atr: float) -> None:
        entry_price = price * (1 + self._config.slippage_pct)
        qty = self._calculate_entry_qty(entry_price, atr)
        notional = qty * entry_price
        fee = notional * self._config.fee_rate

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

    def _open_short(self, timestamp: str, price: float, atr: float) -> None:
        entry_price = price * (1 - self._config.slippage_pct)
        qty = self._calculate_entry_qty(entry_price, atr)
        notional = qty * entry_price
        fee = notional * self._config.fee_rate

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
        self._position_atr = atr if atr > 0 else 0.0
        self._position_sl_price = 0.0
        self._position_tp_price = 0.0
        self._position_high_water_mark = entry_price

    def _close_position(self, timestamp: str, price: float, reason: str = "SIGNAL") -> None:
        is_long = self._position_qty > 0
        qty = abs(self._position_qty)
        margin_used = self._position_margin_used

        if is_long:
            exit_price = price * (1 - self._config.slippage_pct)
            gross_pnl = (exit_price - self._position_entry_price) * qty
            trade_side = "BUY"
        else:
            exit_price = price * (1 + self._config.slippage_pct)
            gross_pnl = (self._position_entry_price - exit_price) * qty
            trade_side = "SELL"

        exit_notional = qty * exit_price
        exit_fee = exit_notional * self._config.fee_rate

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

        funding_cost = abs(self._position_qty) * current_price * self._config.futures_funding_rate
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
        import math

        final_equity = (
            self._equity_curve[-1] if self._equity_curve else self._config.initial_capital
        )
        total_return = final_equity - self._config.initial_capital
        total_return_pct = (total_return / self._config.initial_capital) * 100

        # Trade Stats
        wins = [t for t in self._trades if t.pnl > 0]
        losses = [t for t in self._trades if t.pnl <= 0]

        win_rate = (len(wins) / len(self._trades)) * 100 if self._trades else 0.0

        gross_profit = sum(t.pnl for t in wins)
        gross_loss = abs(sum(t.pnl for t in losses))
        if gross_loss > 0:
            profit_factor = gross_profit / gross_loss
        elif gross_profit > 0:
            profit_factor = float("inf")
        else:
            profit_factor = 0.0

        avg_win = (gross_profit / len(wins)) if wins else 0.0
        avg_loss = (gross_loss / len(losses)) if losses else 0.0
        avg_win_loss_ratio = (
            (avg_win / avg_loss) if avg_loss > 0 else float("inf") if avg_win > 0 else 0.0
        )

        # Drawdown
        peak = self._config.initial_capital
        max_dd = 0.0
        for equity in self._equity_curve:
            if equity > peak:
                peak = equity
            dd = (peak - equity) / peak
            if dd > max_dd:
                max_dd = dd

        # Advanced Metrics (Sharpe/Sortino)
        # Calculate periodic returns from equity curve
        sharpe_ratio = 0.0
        sortino_ratio = 0.0

        if len(self._equity_curve) > 1:
            returns = []
            for i in range(1, len(self._equity_curve)):
                prev = self._equity_curve[i - 1]
                curr = self._equity_curve[i]
                if prev > 0:
                    returns.append((curr - prev) / prev)
                else:
                    returns.append(0.0)

            if returns:
                # Calculate mean and std manually to avoid numpy dependency
                mean_return = sum(returns) / len(returns)
                variance = sum((x - mean_return) ** 2 for x in returns) / len(returns)
                std_return = math.sqrt(variance)

                # Annualize based on configured timeframe
                _tf_minutes = {
                    "1m": 1,
                    "3m": 3,
                    "5m": 5,
                    "15m": 15,
                    "30m": 30,
                    "1h": 60,
                    "2h": 120,
                    "4h": 240,
                    "6h": 360,
                    "8h": 480,
                    "12h": 720,
                    "1d": 1440,
                    "3d": 4320,
                    "1w": 10080,
                }
                tf_min = _tf_minutes.get(self._config.timeframe, 1)
                periods_per_year = int(365 * 24 * 60 / tf_min)

                if std_return > 0:
                    sharpe_ratio = (mean_return / std_return) * math.sqrt(periods_per_year)

                # Sortino (Downside deviation)
                negative_returns = [r for r in returns if r < 0]
                if negative_returns:
                    downside_variance = sum(x**2 for x in negative_returns) / len(
                        returns
                    )  # Downside deviation uses total N
                    downside_std = math.sqrt(downside_variance)
                    if downside_std > 0:
                        sortino_ratio = (mean_return / downside_std) * math.sqrt(periods_per_year)

        return BacktestResult(
            total_return=total_return,
            total_return_pct=total_return_pct,
            max_drawdown=max_dd,
            win_rate=win_rate,
            total_trades=len(self._trades),
            trades=self._trades,
            final_equity=final_equity,
            sharpe_ratio=sharpe_ratio,
            sortino_ratio=sortino_ratio,
            profit_factor=profit_factor,
            avg_win_loss_ratio=avg_win_loss_ratio,
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
        )
