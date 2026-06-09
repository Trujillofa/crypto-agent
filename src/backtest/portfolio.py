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
from src.strategy.signals import SignalType


@dataclass(frozen=True)
class PortfolioReplayConfig:
    symbols: list[str]
    timeframe: str
    start_date: str
    end_date: str
    strategy_classes: list[type[BaseStrategy]]
    strategy_configs: list[Mapping[str, object] | None] = field(default_factory=list)
    aggregator_config: Mapping[str, object] = field(default_factory=dict)
    replay_sentiment_path: str | None = None
    replay_sentiment_max_age_seconds: float | None = None
    global_trend_filter_buffer_pct: float = 0.0
    max_concurrent_longs: int = 1
    sl_cooldown_minutes: float = 0.0
    order_size_usdt: float = 100.0
    fee_rate: float = 0.0005
    slippage_pct: float = 0.001
    sl_atr_multiplier: float = 2.0
    tp_atr_multiplier: float = 3.5
    trailing_activate_atr: float = 1.5
    trailing_offset_atr: float = 1.0
    time_stop_minutes: float = 0.0


@dataclass(frozen=True)
class PortfolioReplayTrade:
    symbol: str
    entry_time: str
    exit_time: str
    entry_price: float
    exit_price: float
    quantity: float
    pnl: float
    return_pct: float
    exit_reason: str


@dataclass(frozen=True)
class PortfolioReplayResult:
    trades: list[PortfolioReplayTrade]
    skipped_slot_buys: int
    skipped_cooldown_buys: int
    total_pnl: float
    win_rate: float
    profit_factor: float


@dataclass
class _OpenLong:
    symbol: str
    entry_time: str
    entry_price: float
    quantity: float
    atr: float
    sl_price: float
    tp_price: float
    high_water_mark: float
    entry_fee: float


class PortfolioReplayEngine:
    """Replay long-only strategy signals with the live futures portfolio slot rules."""

    def __init__(self, config: PortfolioReplayConfig, reader: IndicatorReader) -> None:
        if config.max_concurrent_longs != 1:
            raise ValueError("Portfolio replay currently supports max_concurrent_longs=1 only")
        self._config = config
        self._reader = reader
        self._aggregator = SignalAggregator(config.aggregator_config)
        self._position: _OpenLong | None = None
        self._last_stop_loss: dict[str, datetime] = {}
        self._trades: list[PortfolioReplayTrade] = []
        self._skipped_slot_buys = 0
        self._skipped_cooldown_buys = 0

    async def run(self) -> PortfolioReplayResult:
        replay_scorer = self._build_replay_scorer()
        strategies = self._build_strategies(replay_scorer)
        rows_by_symbol = {
            symbol: await self._reader.fetch_range(
                symbol,
                self._config.timeframe,
                self._config.start_date,
                self._config.end_date,
            )
            for symbol in self._config.symbols
        }
        rows_by_time: dict[str, dict[str, dict[str, float]]] = {}
        for symbol, rows in rows_by_symbol.items():
            for row in rows:
                rows_by_time.setdefault(str(row["time"]), {})[symbol] = row

        for timestamp in sorted(rows_by_time):
            rows = rows_by_time[timestamp]
            self._process_exit(timestamp, rows)
            for symbol in self._config.symbols:
                row = rows.get(symbol)
                if row is not None:
                    await self._process_signal_cycle(symbol, timestamp, row, strategies[symbol])

        if self._position is not None:
            rows = rows_by_symbol[self._position.symbol]
            if rows:
                self._close_position(
                    str(rows[-1]["time"]),
                    float(rows[-1]["close_price"]),
                    "END_OF_REPLAY",
                )
        return self._create_result()

    def _build_replay_scorer(self) -> ReplaySentimentScorer | None:
        if not self._config.replay_sentiment_path:
            return None
        return ReplaySentimentScorer(
            self._config.replay_sentiment_path,
            max_age_seconds=self._config.replay_sentiment_max_age_seconds,
        )

    def _build_strategies(
        self, replay_scorer: ReplaySentimentScorer | None
    ) -> dict[str, list[BaseStrategy]]:
        strategies_by_symbol = {}
        for symbol in self._config.symbols:
            strategies = []
            for index, strategy_class in enumerate(self._config.strategy_classes):
                strategy_config = (
                    self._config.strategy_configs[index]
                    if index < len(self._config.strategy_configs)
                    else None
                )
                strategy = strategy_class(strategy_config)
                if replay_scorer is not None and isinstance(
                    strategy, SentimentMeanReversionStrategy
                ):
                    strategy.set_scorer(replay_scorer)
                strategies.append(strategy)
            strategies_by_symbol[symbol] = strategies
        return strategies_by_symbol

    def _process_exit(self, timestamp: str, rows: Mapping[str, Mapping[str, float]]) -> None:
        position = self._position
        if position is None:
            return
        row = rows.get(position.symbol)
        if row is None:
            return
        high_price = float(row.get("high_price", row["close_price"]))
        low_price = float(row.get("low_price", row["close_price"]))
        position.high_water_mark = max(position.high_water_mark, high_price)
        trailing_activation = (
            position.entry_price + self._config.trailing_activate_atr * position.atr
        )
        if position.high_water_mark >= trailing_activation:
            position.sl_price = max(
                position.sl_price,
                position.high_water_mark - self._config.trailing_offset_atr * position.atr,
            )
        if low_price <= position.sl_price:
            self._close_position(timestamp, position.sl_price, "STOP_LOSS")
            return
        if high_price >= position.tp_price:
            self._close_position(timestamp, position.tp_price, "TAKE_PROFIT")
            return
        if self._config.time_stop_minutes > 0:
            elapsed = datetime.fromisoformat(timestamp) - datetime.fromisoformat(
                position.entry_time
            )
            if elapsed.total_seconds() / 60 >= self._config.time_stop_minutes:
                self._close_position(timestamp, float(row["close_price"]), "TIME_STOP")

    async def _process_signal_cycle(
        self,
        symbol: str,
        timestamp: str,
        row: Mapping[str, float],
        strategies: list[BaseStrategy],
    ) -> None:
        signals = [await strategy.evaluate(symbol, row) for strategy in strategies]
        signal = self._aggregator.aggregate(symbol, signals, ema_200=row.get("ema_200"))
        current_price = float(row["close_price"])
        if signal.type == SignalType.BUY:
            ema_200 = row.get("ema_200")
            if ema_200 is not None and current_price < float(ema_200) * (
                1 - self._config.global_trend_filter_buffer_pct
            ):
                return
            if self._position is not None:
                self._skipped_slot_buys += 1
                return
            last_stop = self._last_stop_loss.get(symbol)
            if last_stop is not None:
                elapsed = datetime.fromisoformat(timestamp) - last_stop
                if elapsed.total_seconds() / 60 < self._config.sl_cooldown_minutes:
                    self._skipped_cooldown_buys += 1
                    return
            self._open_position(symbol, timestamp, current_price, float(row.get("atr_14", 0.0)))
        elif (
            signal.type == SignalType.SELL
            and self._position is not None
            and self._position.symbol == symbol
        ):
            self._close_position(timestamp, current_price, "SIGNAL")

    def _open_position(self, symbol: str, timestamp: str, price: float, atr: float) -> None:
        entry_price = price * (1 + self._config.slippage_pct)
        quantity = self._config.order_size_usdt / entry_price
        self._position = _OpenLong(
            symbol=symbol,
            entry_time=timestamp,
            entry_price=entry_price,
            quantity=quantity,
            atr=atr,
            sl_price=entry_price - self._config.sl_atr_multiplier * atr,
            tp_price=entry_price + self._config.tp_atr_multiplier * atr,
            high_water_mark=entry_price,
            entry_fee=entry_price * quantity * self._config.fee_rate,
        )

    def _close_position(self, timestamp: str, price: float, reason: str) -> None:
        position = self._position
        if position is None:
            return
        exit_price = price * (1 - self._config.slippage_pct)
        exit_fee = exit_price * position.quantity * self._config.fee_rate
        pnl = (
            (exit_price - position.entry_price) * position.quantity - position.entry_fee - exit_fee
        )
        self._trades.append(
            PortfolioReplayTrade(
                symbol=position.symbol,
                entry_time=position.entry_time,
                exit_time=timestamp,
                entry_price=position.entry_price,
                exit_price=exit_price,
                quantity=position.quantity,
                pnl=pnl,
                return_pct=pnl / self._config.order_size_usdt * 100,
                exit_reason=reason,
            )
        )
        if reason == "STOP_LOSS":
            self._last_stop_loss[position.symbol] = datetime.fromisoformat(timestamp)
        self._position = None

    def _create_result(self) -> PortfolioReplayResult:
        wins = [trade.pnl for trade in self._trades if trade.pnl > 0]
        losses = [trade.pnl for trade in self._trades if trade.pnl <= 0]
        gross_loss = abs(sum(losses))
        return PortfolioReplayResult(
            trades=self._trades,
            skipped_slot_buys=self._skipped_slot_buys,
            skipped_cooldown_buys=self._skipped_cooldown_buys,
            total_pnl=sum(trade.pnl for trade in self._trades),
            win_rate=len(wins) / len(self._trades) * 100 if self._trades else 0.0,
            profit_factor=sum(wins) / gross_loss if gross_loss else math.inf if wins else 0.0,
        )
