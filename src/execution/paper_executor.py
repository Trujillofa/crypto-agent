"""Internal paper trading executor — simulates fills without Binance API."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

import asyncpg

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
    ) -> None:
        self._config = config
        self._risk_manager = risk_manager
        self._metrics = metrics
        self._notifier = notifier or TelegramNotifier()
        self._portfolio_manager = portfolio_manager
        self._db_config = db_config
        self._db_conn: asyncpg.Connection | None = None
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
        if self._db_conn:
            await self._db_conn.close()
            self._db_conn = None
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
        if not self._db_conn:
            self._logger.info("Exit monitor not started (no DB connection)")
            return

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
        """Check all open positions for exit conditions."""
        now = time.time()

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

            # Update high water mark
            if current_price > position.high_water_mark:
                position.high_water_mark = current_price

            # Check exit conditions
            exit_reason = self._evaluate_exit(position, current_price, now)
            if exit_reason is None:
                continue

            self._logger.info(
                "Exit triggered for %s: %s (price=%.4f, entry=%.4f, hwm=%.4f)",
                pos_key,
                exit_reason,
                current_price,
                position.entry_price,
                position.high_water_mark,
            )

            # Construct synthetic SELL signal and reuse existing sell handler
            synthetic_signal = Signal(
                type=SignalType.SELL,
                symbol=symbol,
                price=current_price,
                confidence=1.0,
                reason=exit_reason,
                indicators={},
                trading_mode=market_tag,
            )
            await self._handle_sell(synthetic_signal, market_tag, is_futures)

    def _evaluate_exit(
        self, position: PaperPosition, current_price: float, now: float
    ) -> str | None:
        """Return exit reason string if any condition triggers, else None."""
        # Trailing stop: price dropped below HWM * (1 - trail%)
        trail_threshold = position.high_water_mark * (1 - self._config.trailing_stop_pct)
        if current_price < trail_threshold:
            drop_pct = (1 - current_price / position.high_water_mark) * 100
            return f"TRAILING_STOP (hwm={position.high_water_mark:.4f}, drop={drop_pct:.2f}%)"

        # Take profit: price rose above entry * (1 + tp%)
        tp_threshold = position.entry_price * (1 + self._config.take_profit_pct)
        if current_price >= tp_threshold:
            gain_pct = (current_price / position.entry_price - 1) * 100
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
        if not self._db_conn:
            return None
        try:
            row = await self._db_conn.fetchrow(
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

        Strategy SELL signals are ignored — exits happen only via SL/TP/trailing
        from on_tick(). This prevents late strategy sells from interfering with
        ATR-based risk management.
        """
        if not self._config.enabled:
            return

        is_futures = signal.trading_mode == "futures"
        market_tag = "futures" if is_futures else "spot"

        try:
            if signal.type == SignalType.HOLD:
                return

            if signal.type == SignalType.BUY:
                await self._handle_buy(signal, market_tag, is_futures)
            elif signal.type == SignalType.SELL:
                self._logger.info(
                    "Strategy SELL ignored for %s [%s] — exits via SL/TP/trailing only",
                    signal.symbol,
                    market_tag,
                )
        except Exception as exc:  # noqa: BLE001
            self._logger.warning("Paper signal failed: %s — %s", signal.symbol, exc)
            await self._notifier.send_alert(
                f"<b>Paper signal failed</b> [{market_tag}]\n"
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
        reason = None

        if atr > 0:
            # ATR-based SL/TP
            # Update high water mark for trailing stop
            if current_price > position.high_water_mark:
                position.high_water_mark = current_price

            # Check trailing stop activation
            trailing_activation = entry_price + self._config.trailing_activate_atr * atr
            if position.high_water_mark >= trailing_activation:
                trailing_sl = position.high_water_mark - self._config.trailing_offset_atr * atr
                if trailing_sl > position.sl_price:
                    position.sl_price = trailing_sl

            if position.side == "LONG":
                if current_price <= position.sl_price:
                    reason = "STOP_LOSS"
                elif current_price >= position.tp_price:
                    reason = "TAKE_PROFIT"
            else:
                # SHORT: SL is above entry, TP is below entry
                if current_price >= position.sl_price:
                    reason = "STOP_LOSS"
                elif current_price <= position.tp_price:
                    reason = "TAKE_PROFIT"
        else:
            # Fallback to fixed percentage
            stop_loss_pct = self._config.stop_loss_pct
            take_profit_pct = self._config.take_profit_pct
            if stop_loss_pct <= 0 and take_profit_pct <= 0:
                return False

            if position.side == "LONG":
                if stop_loss_pct > 0 and current_price <= entry_price * (1 - stop_loss_pct):
                    reason = "STOP_LOSS"
                elif take_profit_pct > 0 and current_price >= entry_price * (1 + take_profit_pct):
                    reason = "TAKE_PROFIT"
            else:
                if stop_loss_pct > 0 and current_price >= entry_price * (1 + stop_loss_pct):
                    reason = "STOP_LOSS"
                elif take_profit_pct > 0 and current_price <= entry_price * (1 - take_profit_pct):
                    reason = "TAKE_PROFIT"

        if reason is None:
            return False

        sl_info = (
            f"sl={position.sl_price:.4f}" if atr > 0 else f"sl_pct={self._config.stop_loss_pct}"
        )
        tp_info = (
            f"tp={position.tp_price:.4f}" if atr > 0 else f"tp_pct={self._config.take_profit_pct}"
        )
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
        await self._notifier.send_alert(
            f"<b>Paper exit</b> [{market_tag}]\n"
            f"{position.symbol} {reason} — entry {entry_price:.4f} → {current_price:.4f}"
        )

        exit_signal = Signal(
            type=SignalType.SELL,
            symbol=position.symbol,
            price=current_price,
            confidence=1.0,
            reason=reason,
            indicators={},
            trading_mode="futures" if is_futures else "spot",
        )
        await self._handle_sell(exit_signal, market_tag, is_futures)
        return True

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
        else:
            quantity = order_usdt / signal.price
        if is_futures:
            # Futures uses margin = order_size / leverage
            margin_needed = order_usdt / self._config.futures_leverage
        else:
            margin_needed = order_usdt

        if margin_needed > self._balance:
            await self._notifier.send_alert(
                f"<b>Paper signal skipped</b> [{market_tag}]\n"
                f"{signal.symbol} BUY — insufficient balance "
                f"(need {margin_needed:.2f}, have {self._balance:.2f})"
            )
            return

        # Risk check
        is_allowed, reason = self._risk_manager.is_trading_allowed()
        if not is_allowed:
            await self._notifier.send_alert(
                f"<b>Paper signal blocked</b> [{market_tag}]\n" f"{signal.symbol} BUY — {reason}"
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
                await self._portfolio_manager.open_position(
                    symbol=pos_key,
                    quantity=quantity,
                    price=signal.price,
                )
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
        )

    async def _handle_sell(self, signal: Signal, market_tag: str, is_futures: bool) -> None:
        pos_key = self._position_key(signal.symbol, market_tag)
        position = self._positions.get(pos_key)

        if position is None:
            self._logger.debug("SELL ignored: no %s position for %s", market_tag, signal.symbol)
            return

        # Calculate PnL with fees
        gross_pnl = position.pnl(signal.price)
        if is_futures:
            gross_pnl *= self._config.futures_leverage

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
        self._risk_manager.record_trade(signal.symbol, net_pnl, self._balance)
        self._risk_manager.register_close_position(pos_key)

        # Record in portfolio DB for overseer visibility
        if self._portfolio_manager:
            try:
                await self._portfolio_manager.close_position(
                    symbol=pos_key,
                    price=signal.price,
                )
            except Exception as exc:  # noqa: BLE001
                self._logger.warning("Portfolio DB write failed (sell): %s", exc)

        leverage_text = f" ({self._config.futures_leverage}x)" if is_futures else ""
        self._logger.info(
            "Paper SELL %s [%s%s]: qty=%.6f @ %.4f, PnL=%.2f (fee=%.2f) USDT, balance=%.2f",
            signal.symbol,
            market_tag,
            leverage_text,
            position.quantity,
            signal.price,
            net_pnl,
            fee,
            self._balance,
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
            quantity=position.quantity,
            price=signal.price,
            pnl=net_pnl,
            market=f"paper-{market_tag}{leverage_text}",
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
