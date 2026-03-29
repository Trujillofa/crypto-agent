from __future__ import annotations

import asyncio
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from src.core.event_log import EventLog
from src.db.pool import get_pool
from src.execution.metrics import ExecutionMetrics
from src.notifications.telegram import TelegramNotifier
from src.portfolio.manager import PortfolioManager
from src.risk.manager import RiskManager
from src.strategy.signals import Signal, SignalType
from src.utils.logger import get_logger


@dataclass
class PaperPosition:
    """A simulated open position."""

    symbol: str
    side: str  # "LONG" or "SHORT"
    quantity: float  # base asset units
    entry_price: float
    open_time: float
    atr_at_entry: float = 0.0  # ATR(14) value at entry
    sl_price: float = 0.0  # computed at entry: entry - sl_mult * ATR
    tp_price: float = 0.0  # computed at entry: entry + tp_mult * ATR
    high_water_mark: float = 0.0  # for trailing stop tracking

    def __post_init__(self) -> None:
        if self.high_water_mark == 0.0:
            self.high_water_mark = self.entry_price

    @property
    def notional(self) -> float:
        return self.quantity * self.entry_price

    def pnl(self, current_price: float) -> float:
        if self.side == "LONG":
            return (current_price - self.entry_price) * self.quantity
        return (self.entry_price - current_price) * self.quantity


@dataclass
class PaperTradingConfig:
    """Configuration for paper trading executor."""

    enabled: bool = True
    order_size_usdt: float = 200.0
    initial_balance: float = 10000.0
    symbols: list[str] = field(default_factory=list)
    futures_symbols: list[str] = field(default_factory=list)
    futures_leverage: int = 3
    fee_rate_spot: float = 0.001  # 0.1% per trade (Binance spot taker)
    fee_rate_futures: float = 0.0004  # 0.04% per trade (Binance futures taker)
    stop_loss_pct: float = 0.03  # fallback if ATR unavailable
    take_profit_pct: float = 0.06  # fallback if ATR unavailable
    # ATR-based SL/TP (primary)
    sl_atr_multiplier: float = 2.0  # SL = entry - 2.0 * ATR
    tp_atr_multiplier: float = 4.5  # TP = entry + 4.5 * ATR
    trailing_activate_atr: float = 1.5  # activate trailing after +1.5 * ATR profit
    trailing_offset_atr: float = 1.0  # trail SL at highest - 1.0 * ATR
    # Fixed-pct exit fallbacks (used when ATR unavailable)
    trailing_stop_pct: float = 0.005  # 0.5% trailing stop
    time_stop_minutes: float = 60  # max position hold time in minutes
    exit_check_interval: int = 5  # seconds between exit checks
    # ATR-based position sizing
    use_atr_sizing: bool = False  # if True, size by (equity × risk_pct) / (atr × multiplier)
    atr_multiplier: float = 1.0  # stop distance multiplier for sizing calc
    risk_per_trade_pct: float = 0.02  # fraction of equity risked per trade
    # Paper/live parity
    allow_short_entry: bool = False  # match LONG-only MVP behavior of live futures


class PaperExecutor:
    """Simulates spot + futures trading entirely in memory.

    No Binance API calls. Fills happen at signal price.
    Tracks balances, positions, and PnL. Sends Telegram alerts.
    """

    def __init__(
        self,
        config: PaperTradingConfig,
        risk_manager: RiskManager,
        metrics: ExecutionMetrics,
        notifier: TelegramNotifier | None = None,
        portfolio_manager: PortfolioManager | None = None,
        db_config: Mapping[str, object] | None = None,
        agent_id: str = "default",
        event_log: EventLog | None = None,
    ) -> None:
        self._config = config
        self._risk_manager = risk_manager
        self._metrics = metrics
        self._notifier = notifier or TelegramNotifier()
        self._portfolio_manager = portfolio_manager
        self._db_config = db_config
        self._event_log = event_log
        self._agent_id = self._normalize_agent_id(agent_id)
        self._agent_id = self._normalize_agent_id(agent_id)
        self._position_prefix = "" if self._agent_id == "default" else f"{self._agent_id}::"
        self._logger = get_logger("PaperExecutor")

        # Simulated state
        self._balance: float = config.initial_balance
        self._positions: dict[str, PaperPosition] = {}  # symbol -> position
        self._trade_count: int = 0
        self._realized_pnl: float = 0.0
        self._total_fees: float = 0.0
        self._running = False

    @staticmethod
    def _normalize_agent_id(agent_id: str) -> str:
        normalized = "".join(
            ch if (ch.isalnum() or ch in {"-", "_"}) else "_"
            for ch in (agent_id or "default").strip()
        ).strip("_")
        return normalized or "default"

    def _position_key(self, symbol: str, market_tag: str) -> str:
        raw = f"{symbol}:{market_tag}"
        if not self._position_prefix:
            return raw
        return f"{self._position_prefix}{raw}"

    def _parse_position_key(self, pos_key: str) -> tuple[str, str]:
        raw_key = pos_key
        if self._position_prefix and raw_key.startswith(self._position_prefix):
            raw_key = raw_key[len(self._position_prefix) :]

        if ":" not in raw_key:
            return raw_key, "spot"

        symbol, market_tag = raw_key.rsplit(":", 1)
        return symbol, market_tag

    def _portfolio_value(self) -> float:
        """Estimate current account equity for paper risk checks."""
        portfolio_value = self._balance
        for pos_key, position in self._positions.items():
            _, market_tag = self._parse_position_key(pos_key)
            if market_tag == "futures":
                portfolio_value += position.notional / self._config.futures_leverage
            else:
                portfolio_value += position.notional
        return portfolio_value

    def _cap_atr_sized_order_usdt(self, symbol: str, market_tag: str, order_usdt: float) -> float:
        """Clamp ATR-sized orders to the configured max position notional."""
        max_position_pct = self._risk_manager._config.position_limits.max_position_pct
        max_position_value = self._portfolio_value() * max_position_pct
        capped_order_usdt = min(order_usdt, max_position_value)
        if capped_order_usdt < order_usdt:
            self._logger.info(
                "Capping ATR-sized paper order for %s [%s]: %.2f USDT -> %.2f USDT (max %.1f%% of portfolio)",
                symbol,
                market_tag,
                order_usdt,
                capped_order_usdt,
                max_position_pct * 100,
            )
        return capped_order_usdt

    async def _enforce_position_limit(
        self,
        pos_key: str,
        symbol: str,
        side: str,
        market_tag: str,
        order_usdt: float,
    ) -> bool:
        allowed, reason = self._risk_manager.check_position_limit(
            pos_key,
            order_usdt,
            self._portfolio_value(),
        )
        if allowed:
            return True

        await self._notifier.send_alert(
            f"<b>Paper signal blocked</b> [{market_tag}]\n{symbol} {side} — {reason}"
        )
        return False

    async def __aenter__(self) -> PaperExecutor:
        if not self._config.enabled:
            self._logger.info("PaperExecutor disabled")
            return self

        await self._notifier.__aenter__()
        self._metrics.start_trading()

        # Restore state from PortfolioManager if available
        restored_count = 0
        if self._portfolio_manager:
            for pos in self._portfolio_manager.get_all_positions():
                symbol_base = pos.symbol
                market_tag = pos.market
                if ":" in pos.symbol and pos.market == "spot":
                    # Backward-compatible fallback for historical rows that encoded
                    # market in the symbol suffix instead of the dedicated column.
                    symbol_base, market_tag = self._parse_position_key(pos.symbol)

                # Filter out positions that shouldn't be managed by this executor
                # (Simulated logic: if we have futures config, we manage futures; always manage spot)
                is_futures = market_tag == "futures"

                # Determine side
                side = pos.position_side or "LONG"

                # Create PaperPosition
                paper_pos = PaperPosition(
                    symbol=symbol_base,
                    side=side,
                    quantity=pos.quantity,
                    entry_price=pos.entry_price,
                    open_time=pos.entry_time.timestamp(),
                )

                # Add to simulated state
                position_key = self._position_key(symbol_base, market_tag)
                self._positions[position_key] = paper_pos
                restored_count += 1

                # Adjust balance (deduct margin used)
                notional = pos.quantity * pos.entry_price
                if is_futures:
                    leverage = pos.leverage or self._config.futures_leverage
                    margin_used = notional / leverage
                else:
                    margin_used = notional

                self._balance -= margin_used

        self._logger.info(
            "PaperExecutor initialized: balance=%.2f USDT, order_size=%.2f USDT, restored_positions=%d",
            self._balance,
            self._config.order_size_usdt,
            restored_count,
        )
        return self

    async def __aexit__(self, exc_type: object, exc: object, tb: object) -> None:
        self._running = False
        await self._notifier.__aexit__(exc_type, exc, tb)
        self._metrics.stop_trading()
        self._logger.info(
            "PaperExecutor stopped: balance=%.2f, realized_pnl=%.2f, fees=%.2f, trades=%d",
            self._balance,
            self._realized_pnl,
            self._total_fees,
            self._trade_count,
        )

    async def run(self) -> None:
        """Monitor open positions and trigger exits (trailing stop, TP, time stop)."""
        self._running = True
        self._logger.info("Exit monitor started: check every %ds", self._config.exit_check_interval)

        while self._running:
            try:
                await asyncio.sleep(self._config.exit_check_interval)
                if not self._positions:
                    continue
                await self._check_exits()
            except asyncio.CancelledError:
                break
            except Exception as exc:  # noqa: BLE001
                self._logger.warning("Exit monitor error: %s", exc)

    def stop(self) -> None:
        self._running = False

    async def _check_exits(self) -> None:
        """Check all open positions using the unified exit evaluator."""

        # Snapshot keys to avoid mutating dict during iteration
        for pos_key in list(self._positions):
            position = self._positions.get(pos_key)
            if position is None:
                continue

            symbol, market_tag = self._parse_position_key(pos_key)
            is_futures = market_tag == "futures"

            # Fetch latest price from DB
            current_price = await self._fetch_latest_price(symbol)
            if current_price is None:
                continue

            await self._check_position_exit(
                position, pos_key, current_price, market_tag, is_futures
            )

    def _evaluate_exit(
        self, position: PaperPosition, current_price: float, now: float
    ) -> str | None:
        """Compatibility helper for tests: delegates to unified exit evaluator."""
        return self._determine_exit_reason(position, current_price, now)

    def _determine_exit_reason(
        self, position: PaperPosition, current_price: float, now: float
    ) -> str | None:
        """Return exit reason string if any condition triggers, else None."""
        if position.side == "LONG":
            if current_price > position.high_water_mark:
                position.high_water_mark = current_price
        else:
            if current_price < position.high_water_mark:
                position.high_water_mark = current_price

        atr = position.atr_at_entry
        if atr > 0:
            if position.side == "LONG":
                trailing_activation = (
                    position.entry_price + self._config.trailing_activate_atr * atr
                )
                if position.high_water_mark >= trailing_activation:
                    trailing_sl = position.high_water_mark - self._config.trailing_offset_atr * atr
                    if trailing_sl > position.sl_price:
                        position.sl_price = trailing_sl
                if current_price <= position.sl_price:
                    return "STOP_LOSS"
                if current_price >= position.tp_price:
                    return "TAKE_PROFIT"

            else:
                trailing_activation = (
                    position.entry_price - self._config.trailing_activate_atr * atr
                )
                if position.high_water_mark <= trailing_activation:
                    trailing_sl = position.high_water_mark + self._config.trailing_offset_atr * atr
                    if position.sl_price == 0.0 or trailing_sl < position.sl_price:
                        position.sl_price = trailing_sl
                if current_price >= position.sl_price:
                    return "STOP_LOSS"
                if current_price <= position.tp_price:
                    return "TAKE_PROFIT"
        else:
            trailing_stop_pct = self._config.trailing_stop_pct
            stop_loss_pct = self._config.stop_loss_pct
            take_profit_pct = self._config.take_profit_pct

            if position.side == "LONG":
                if trailing_stop_pct > 0:
                    trail_threshold = position.high_water_mark * (1 - trailing_stop_pct)
                    if current_price < trail_threshold:
                        drop_pct = (1 - current_price / position.high_water_mark) * 100
                        return (
                            f"TRAILING_STOP (hwm={position.high_water_mark:.4f}, "
                            f"drop={drop_pct:.2f}%)"
                        )
                if stop_loss_pct > 0 and current_price <= position.entry_price * (
                    1 - stop_loss_pct
                ):
                    return "STOP_LOSS"
                if take_profit_pct > 0 and current_price >= position.entry_price * (
                    1 + take_profit_pct
                ):
                    gain_pct = (current_price / position.entry_price - 1) * 100
                    return f"TAKE_PROFIT (entry={position.entry_price:.4f}, gain={gain_pct:.2f}%)"
            else:
                if trailing_stop_pct > 0:
                    trail_threshold = position.high_water_mark * (1 + trailing_stop_pct)
                    if current_price > trail_threshold:
                        rebound_pct = (current_price / position.high_water_mark - 1) * 100
                        return (
                            f"TRAILING_STOP (lwm={position.high_water_mark:.4f}, "
                            f"rebound={rebound_pct:.2f}%)"
                        )
                if stop_loss_pct > 0 and current_price >= position.entry_price * (
                    1 + stop_loss_pct
                ):
                    return "STOP_LOSS"
                if take_profit_pct > 0 and current_price <= position.entry_price * (
                    1 - take_profit_pct
                ):
                    gain_pct = (1 - current_price / position.entry_price) * 100
                    return f"TAKE_PROFIT (entry={position.entry_price:.4f}, gain={gain_pct:.2f}%)"

        # Time stop: position open longer than configured minutes
        elapsed_minutes = (now - position.open_time) / 60
        if elapsed_minutes >= self._config.time_stop_minutes:
            return (
                f"TIME_STOP (open={elapsed_minutes:.0f}m, limit={self._config.time_stop_minutes}m)"
            )

        return None

    async def _fetch_latest_price(self, symbol: str) -> float | None:
        """Fetch the most recent close price from ohlcv table."""
        try:
            pool = get_pool()
            async with pool.acquire() as conn:
                row = await conn.fetchrow(
                    "SELECT close_price FROM ohlcv WHERE symbol = $1 ORDER BY time DESC LIMIT 1",
                    symbol,
                )
                if row:
                    return float(row["close_price"])
        except Exception as exc:  # noqa: BLE001
            self._logger.warning("Price fetch failed for %s: %s", symbol, exc)
        return None

    async def on_signal(self, signal: Signal) -> None:
        """Handle a trading signal by simulating the fill.

        Spot SELL signals from flat are ignored. Futures SELL signals from flat
        open short paper positions; strategy exits for existing positions still
        run via SL/TP/trailing so late signals do not override risk-managed exits.
        """
        if not self._config.enabled:
            return

        is_futures = signal.trading_mode == "futures"
        market_tag = "futures" if is_futures else "spot"

        if self._event_log:
            await self._event_log.log(
                "signal_received",
                {
                    "symbol": signal.symbol,
                    "type": signal.type.value,
                    "price": signal.price,
                    "reason": signal.reason,
                    "trading_mode": signal.trading_mode,
                },
            )

        try:
            if signal.type == SignalType.HOLD:
                return

            if signal.type == SignalType.BUY:
                await self._handle_buy(signal, market_tag, is_futures)
            elif signal.type == SignalType.SELL:
                pos_key = self._position_key(signal.symbol, market_tag)
                if is_futures and pos_key not in self._positions:
                    if self._config.allow_short_entry:
                        await self._handle_short_entry(signal, market_tag)
                    else:
                        self._logger.info(
                            "Paper SELL from flat ignored for %s [futures] — LONG-only parity (allow_short_entry=False)",
                            signal.symbol,
                        )
                        if self._event_log:
                            await self._event_log.log(
                                "signal_ignored",
                                {
                                    "symbol": signal.symbol,
                                    "reason": "futures_sell_from_flat_long_only",
                                    "market_tag": market_tag,
                                },
                            )
                else:
                    self._logger.info(
                        "Strategy SELL ignored for %s [%s] — exits via SL/TP/trailing only",
                        signal.symbol,
                        market_tag,
                    )
                    if self._event_log:
                        await self._event_log.log(
                            "signal_ignored",
                            {
                                "symbol": signal.symbol,
                                "reason": "strategy_sell_exits_via_sl_tp_only",
                                "market_tag": market_tag,
                            },
                        )
        except Exception as exc:  # noqa: BLE001
            self._logger.warning("Paper signal failed: %s — %s", signal.symbol, exc)
            await self._notifier.send_alert(
                f"🚨 <b>Signal Failed</b> [{market_tag}]\n"
                f"{signal.symbol} {signal.type.value} — {exc}"
            )

    async def on_tick(self, symbol: str, price: float, indicators: dict[str, float]) -> None:
        """Check SL/TP/trailing for all positions of this symbol. Called every cycle."""
        if not self._config.enabled:
            return

        for market_tag in ("spot", "futures"):
            pos_key = self._position_key(symbol, market_tag)
            position = self._positions.get(pos_key)
            if position is None:
                continue
            is_futures = market_tag == "futures"
            await self._check_position_exit(position, pos_key, price, market_tag, is_futures)

    async def _check_position_exit(
        self,
        position: PaperPosition,
        pos_key: str,
        current_price: float,
        market_tag: str,
        is_futures: bool,
    ) -> bool:
        """Check SL/TP/trailing stop for a single position. Returns True if exited."""
        entry_price = position.entry_price
        atr = position.atr_at_entry
        reason = self._determine_exit_reason(position, current_price, time.time())

        if reason is None:
            return False

        fill_price = self._resolve_exit_fill_price(position, current_price, reason)

        sl_info = (
            f"sl={position.sl_price:.4f}" if atr > 0 else f"sl_pct={self._config.stop_loss_pct}"
        )
        tp_info = (
            f"tp={position.tp_price:.4f}" if atr > 0 else f"tp_pct={self._config.take_profit_pct}"
        )
        if fill_price != current_price:
            self._logger.info(
                "Paper %s triggered for %s [%s]: entry=%.4f observed=%.4f fill=%.4f (%s, %s)",
                reason,
                position.symbol,
                market_tag,
                entry_price,
                current_price,
                fill_price,
                sl_info,
                tp_info,
            )
        else:
            self._logger.info(
                "Paper %s triggered for %s [%s]: entry=%.4f current=%.4f (%s, %s)",
                reason,
                position.symbol,
                market_tag,
                entry_price,
                current_price,
                sl_info,
                tp_info,
            )

        exit_signal = Signal(
            type=SignalType.SELL if position.side == "LONG" else SignalType.BUY,
            symbol=position.symbol,
            price=fill_price,
            confidence=1.0,
            reason=reason,
            indicators={},
            trading_mode="futures" if is_futures else "spot",
        )
        await self._handle_sell(exit_signal, market_tag, is_futures)
        return True

    def _resolve_exit_fill_price(
        self, position: PaperPosition, current_price: float, reason: str
    ) -> float:
        """Choose a paper fill price from the triggered threshold.

        Paper mode may only observe sparse sampled prices. For SL/TP and trailing
        exits, fill at the configured threshold instead of the later sampled close
        so alerts and PnL better reflect the intended risk model.
        """
        if reason.startswith(("STOP_LOSS", "TRAILING_STOP")) and position.sl_price > 0:
            return position.sl_price
        if reason.startswith("TAKE_PROFIT") and position.tp_price > 0:
            return position.tp_price
        return current_price

    async def _handle_buy(self, signal: Signal, market_tag: str, is_futures: bool) -> None:
        pos_key = self._position_key(signal.symbol, market_tag)

        if pos_key in self._positions:
            self._logger.info(
                "BUY ignored: already have %s position for %s",
                market_tag,
                signal.symbol,
            )
            return

        # Check balance — compute quantity and order notional
        order_usdt = self._config.order_size_usdt
        atr_14_for_size = signal.indicators.get("atr_14", 0.0)
        if self._config.use_atr_sizing and atr_14_for_size > 0:
            # ATR-based sizing: risk a fixed % of current equity per trade
            risk_amount = self._balance * self._config.risk_per_trade_pct
            stop_distance = atr_14_for_size * self._config.atr_multiplier
            target_qty = risk_amount / stop_distance
            max_qty = (self._balance * 0.98) / signal.price
            quantity = min(target_qty, max_qty)
            order_usdt = quantity * signal.price
            order_usdt = self._cap_atr_sized_order_usdt(signal.symbol, market_tag, order_usdt)
            quantity = order_usdt / signal.price
        else:
            quantity = order_usdt / signal.price
        if is_futures:
            # Futures uses margin = order_size / leverage
            margin_needed = order_usdt / self._config.futures_leverage
        else:
            margin_needed = order_usdt

        if margin_needed > self._balance:
            await self._notifier.send_alert(
                f"⚠️ <b>Signal Skipped</b> [{market_tag}]\n"
                f"{signal.symbol} BUY — insufficient balance "
                f"(need ${margin_needed:.2f}, have ${self._balance:.2f})"
            )
            return

        # Risk check
        is_allowed, reason = self._risk_manager.is_trading_allowed()
        if not is_allowed:
            if self._event_log:
                await self._event_log.log(
                    "risk_check_failed",
                    {"symbol": signal.symbol, "reason": reason, "stage": "paper_buy"},
                )
            if self._event_log:
                await self._event_log.log(
                    "risk_check_failed",
                    {"symbol": signal.symbol, "reason": reason, "stage": "paper_buy"},
                )
            await self._notifier.send_alert(
                f"🛑 <b>Signal Blocked</b> [{market_tag}]\n{signal.symbol} BUY — {reason}"
            )
            return

        # Position limit check (enforces max_open_positions across spot+futures)
        max_positions = self._risk_manager._config.position_limits.max_open_positions
        if len(self._positions) >= max_positions:
            self._logger.info(
                "BUY blocked: max open positions (%d) reached for %s [%s]",
                max_positions,
                signal.symbol,
                market_tag,
            )
            await self._notifier.send_alert(
                f"<b>Paper signal blocked</b> [{market_tag}]\n"
                f"{signal.symbol} BUY — max open positions ({max_positions}) reached"
            )
            if self._event_log:
                await self._event_log.log(
                    "risk_check_failed",
                    {
                        "symbol": signal.symbol,
                        "reason": "max_open_positions_reached",
                        "limit": max_positions,
                    },
                )
            return

        if not await self._enforce_position_limit(
            pos_key,
            signal.symbol,
            "BUY",
            market_tag,
            order_usdt,
        ):
            return

        # Simulate fill with fee
        fee_rate = self._config.fee_rate_futures if is_futures else self._config.fee_rate_spot
        fee = order_usdt * fee_rate
        self._balance -= margin_needed + fee
        self._total_fees += fee

        # Compute ATR-based SL/TP levels
        atr_14 = signal.indicators.get("atr_14", 0.0)
        entry_price = signal.price
        if atr_14 > 0:
            sl_price = entry_price - self._config.sl_atr_multiplier * atr_14
            tp_price = entry_price + self._config.tp_atr_multiplier * atr_14
        else:
            # Fallback to fixed pct
            sl_price = (
                entry_price * (1 - self._config.stop_loss_pct)
                if self._config.stop_loss_pct > 0
                else 0.0
            )
            tp_price = (
                entry_price * (1 + self._config.take_profit_pct)
                if self._config.take_profit_pct > 0
                else 0.0
            )

        self._positions[pos_key] = PaperPosition(
            symbol=signal.symbol,
            side="LONG",
            quantity=quantity,
            entry_price=entry_price,
            open_time=time.time(),
            atr_at_entry=atr_14,
            sl_price=sl_price,
            tp_price=tp_price,
            high_water_mark=entry_price,
        )
        self._trade_count += 1

        # Register with risk manager for position limit tracking
        self._risk_manager.register_open_position(
            pos_key,
            order_usdt,
            signal.price,
        )

        # Record in portfolio DB for overseer visibility
        if self._portfolio_manager:
            try:
                kwargs = {
                    "symbol": signal.symbol,
                    "quantity": quantity,
                    "price": signal.price,
                    "market": market_tag,
                }
                if is_futures:
                    kwargs["position_side"] = "LONG"
                await self._portfolio_manager.open_position(**kwargs)
            except Exception as exc:  # noqa: BLE001
                self._logger.warning("Portfolio DB write failed (buy): %s", exc)

        leverage_text = f" ({self._config.futures_leverage}x)" if is_futures else ""
        atr_text = (
            f", ATR={atr_14:.4f}, SL={sl_price:.4f}, TP={tp_price:.4f}"
            if atr_14 > 0
            else " (fixed % SL/TP)"
        )
        self._logger.info(
            "Paper BUY %s [%s%s]: qty=%.6f @ %.4f (%.2f USDT, fee=%.2f%s)",
            signal.symbol,
            market_tag,
            leverage_text,
            quantity,
            entry_price,
            order_usdt,
            fee,
            atr_text,
        )

        if self._event_log:
            await self._event_log.log(
                "order_filled",
                {
                    "symbol": signal.symbol,
                    "side": "BUY",
                    "quantity": quantity,
                    "price": entry_price,
                    "order_type": "MARKET",
                    "market": market_tag,
                    "fee": fee,
                    "notional": order_usdt,
                    "leverage": self._config.futures_leverage if is_futures else 1,
                },
            )

        self._metrics.record_order_placed(
            symbol=signal.symbol,
            order_type=f"PAPER_{market_tag.upper()}",
            status="FILLED",
            latency_seconds=0.0,
        )
        self._metrics.record_order_filled(signal.symbol, "BUY")

        await self._notifier.send_trade_alert(
            symbol=signal.symbol,
            side="BUY",
            quantity=quantity,
            price=signal.price,
            pnl=None,
            market=f"paper-{market_tag}{leverage_text}",
            stop_loss=sl_price if sl_price > 0 else None,
            take_profit=tp_price if tp_price > 0 else None,
            balance=self._balance,
        )

    async def _handle_short_entry(self, signal: Signal, market_tag: str) -> None:
        pos_key = self._position_key(signal.symbol, market_tag)
        if pos_key in self._positions:
            self._logger.info(
                "SHORT SELL ignored: already have %s position for %s",
                market_tag,
                signal.symbol,
            )
            return

        order_usdt = self._config.order_size_usdt
        atr_14_for_size = signal.indicators.get("atr_14", 0.0)
        if self._config.use_atr_sizing and atr_14_for_size > 0:
            risk_amount = self._balance * self._config.risk_per_trade_pct
            stop_distance = atr_14_for_size * self._config.atr_multiplier
            target_qty = risk_amount / stop_distance
            max_qty = (self._balance * 0.98) / signal.price
            quantity = min(target_qty, max_qty)
            order_usdt = quantity * signal.price
            order_usdt = self._cap_atr_sized_order_usdt(signal.symbol, market_tag, order_usdt)
            quantity = order_usdt / signal.price
        else:
            quantity = order_usdt / signal.price

        margin_needed = order_usdt / self._config.futures_leverage
        if margin_needed > self._balance:
            await self._notifier.send_alert(
                f"<b>Paper signal skipped</b> [{market_tag}]\n"
                f"{signal.symbol} SELL — insufficient balance "
                f"(need {margin_needed:.2f}, have {self._balance:.2f})"
            )
            return

        is_allowed, reason = self._risk_manager.is_trading_allowed()
        if not is_allowed:
            if self._event_log:
                await self._event_log.log(
                    "risk_check_failed",
                    {"symbol": signal.symbol, "reason": reason, "stage": "paper_short"},
                )
            await self._notifier.send_alert(
                f"<b>Paper signal blocked</b> [{market_tag}]\n{signal.symbol} SELL — {reason}"
            )
            return

        max_positions = self._risk_manager._config.position_limits.max_open_positions
        if len(self._positions) >= max_positions:
            self._logger.info(
                "SHORT SELL blocked: max open positions (%d) reached for %s [%s]",
                max_positions,
                signal.symbol,
                market_tag,
            )
            await self._notifier.send_alert(
                f"<b>Paper signal blocked</b> [{market_tag}]\n"
                f"{signal.symbol} SELL — max open positions ({max_positions}) reached"
            )
            if self._event_log:
                await self._event_log.log(
                    "risk_check_failed",
                    {
                        "symbol": signal.symbol,
                        "reason": "max_open_positions_reached",
                        "limit": max_positions,
                    },
                )
            return

        if not await self._enforce_position_limit(
            pos_key,
            signal.symbol,
            "SELL",
            market_tag,
            order_usdt,
        ):
            return

        fee = order_usdt * self._config.fee_rate_futures
        self._balance -= margin_needed + fee
        self._total_fees += fee

        atr_14 = signal.indicators.get("atr_14", 0.0)
        entry_price = signal.price
        if atr_14 > 0:
            sl_price = entry_price + self._config.sl_atr_multiplier * atr_14
            tp_price = entry_price - self._config.tp_atr_multiplier * atr_14
        else:
            sl_price = (
                entry_price * (1 + self._config.stop_loss_pct)
                if self._config.stop_loss_pct > 0
                else 0.0
            )
            tp_price = (
                entry_price * (1 - self._config.take_profit_pct)
                if self._config.take_profit_pct > 0
                else 0.0
            )

        self._positions[pos_key] = PaperPosition(
            symbol=signal.symbol,
            side="SHORT",
            quantity=quantity,
            entry_price=entry_price,
            open_time=time.time(),
            atr_at_entry=atr_14,
            sl_price=sl_price,
            tp_price=tp_price,
            high_water_mark=entry_price,
        )
        self._trade_count += 1

        self._risk_manager.register_open_position(
            pos_key,
            order_usdt,
            signal.price,
        )

        if self._portfolio_manager:
            try:
                await self._portfolio_manager.open_position(
                    symbol=signal.symbol,
                    quantity=quantity,
                    price=signal.price,
                    market=market_tag,
                    position_side="SHORT",
                )
            except Exception as exc:  # noqa: BLE001
                self._logger.warning("Portfolio DB write failed (short sell): %s", exc)

        atr_text = (
            f", ATR={atr_14:.4f}, SL={sl_price:.4f}, TP={tp_price:.4f}"
            if atr_14 > 0
            else " (fixed % SL/TP)"
        )
        self._logger.info(
            "Paper SHORT %s [%s (%dx)]: qty=%.6f @ %.4f (%.2f USDT, fee=%.2f%s)",
            signal.symbol,
            market_tag,
            self._config.futures_leverage,
            quantity,
            entry_price,
            order_usdt,
            fee,
            atr_text,
        )

        if self._event_log:
            await self._event_log.log(
                "order_filled",
                {
                    "symbol": signal.symbol,
                    "side": "SELL",
                    "quantity": quantity,
                    "price": entry_price,
                    "order_type": "MARKET",
                    "market": market_tag,
                    "fee": fee,
                    "notional": order_usdt,
                    "leverage": self._config.futures_leverage,
                    "is_short": True,
                },
            )

        self._metrics.record_order_placed(
            symbol=signal.symbol,
            order_type=f"PAPER_{market_tag.upper()}",
            status="FILLED",
            latency_seconds=0.0,
        )
        self._metrics.record_order_filled(signal.symbol, "SELL")

        await self._notifier.send_trade_alert(
            symbol=signal.symbol,
            side="SELL",
            quantity=quantity,
            price=signal.price,
            pnl=None,
            market=f"paper-{market_tag} ({self._config.futures_leverage}x)",
            stop_loss=sl_price if sl_price > 0 else None,
            take_profit=tp_price if tp_price > 0 else None,
            balance=self._balance,
        )

    async def _handle_sell(self, signal: Signal, market_tag: str, is_futures: bool) -> None:
        pos_key = self._position_key(signal.symbol, market_tag)
        position = self._positions.get(pos_key)

        if position is None:
            self._logger.debug("SELL ignored: no %s position for %s", market_tag, signal.symbol)
            return

        # Calculate PnL with fees
        gross_pnl = position.pnl(signal.price)

        fee_rate = self._config.fee_rate_futures if is_futures else self._config.fee_rate_spot
        sell_notional = position.quantity * signal.price
        fee = sell_notional * fee_rate
        net_pnl = gross_pnl - fee
        self._total_fees += fee

        # Close position
        if is_futures:
            margin_returned = position.notional / self._config.futures_leverage
        else:
            margin_returned = position.notional

        self._balance += margin_returned + net_pnl
        self._realized_pnl += net_pnl
        del self._positions[pos_key]
        self._trade_count += 1

        # Record in risk manager
        self._risk_manager.record_trade(signal.symbol, net_pnl, self._portfolio_value())
        self._risk_manager.register_close_position(pos_key)

        # Record in portfolio DB for overseer visibility
        if self._portfolio_manager:
            try:
                await self._portfolio_manager.close_position(
                    symbol=signal.symbol,
                    price=signal.price,
                    market=market_tag,
                    closing_side="BUY" if position.side == "SHORT" else "SELL",
                    realized_pnl_override=net_pnl,
                )
            except Exception as exc:  # noqa: BLE001
                self._logger.warning("Portfolio DB write failed (sell): %s", exc)

        leverage_text = f" ({self._config.futures_leverage}x)" if is_futures else ""
        close_side = "BUY" if position.side == "SHORT" else "SELL"
        self._logger.info(
            "Paper %s %s [%s%s]: qty=%.6f @ %.4f, PnL=%.2f (fee=%.2f) USDT, balance=%.2f",
            close_side,
            signal.symbol,
            market_tag,
            leverage_text,
            position.quantity,
            signal.price,
            net_pnl,
            fee,
            self._balance,
        )

        if self._event_log:
            await self._event_log.log(
                "order_filled",
                {
                    "symbol": signal.symbol,
                    "side": close_side,
                    "quantity": position.quantity,
                    "price": signal.price,
                    "order_type": "MARKET",
                    "market": market_tag,
                    "fee": fee,
                    "pnl": net_pnl,
                    "realized_pnl": net_pnl,
                    "close_reason": "signal" if signal.reason == "SIGNAL" else signal.reason,
                },
            )

        self._metrics.record_order_placed(
            symbol=signal.symbol,
            order_type=f"PAPER_{market_tag.upper()}",
            status="FILLED",
            latency_seconds=0.0,
        )
        self._metrics.record_order_filled(signal.symbol, "SELL")

        await self._notifier.send_trade_alert(
            symbol=signal.symbol,
            side=close_side,
            quantity=position.quantity,
            price=signal.price,
            pnl=net_pnl,
            market=f"paper-{market_tag}{leverage_text}",
            entry_price=position.entry_price,
            close_reason=signal.reason,
            balance=self._balance,
        )

    def get_positions(self) -> dict[str, Any]:
        """Return current positions for status reporting."""
        result = {}
        for key, pos in self._positions.items():
            symbol, market_tag = self._parse_position_key(key)
            display_key = f"{symbol}:{market_tag}"
            result[display_key] = {
                "side": pos.side,
                "quantity": pos.quantity,
                "entry_price": pos.entry_price,
                "notional": pos.notional,
            }
        return result

    @property
    def balance(self) -> float:
        return self._balance

    @property
    def realized_pnl(self) -> float:
        return self._realized_pnl

    @property
    def trade_count(self) -> int:
        return self._trade_count
