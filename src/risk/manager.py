from __future__ import annotations

import asyncio
import json
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from src.core.event_log import EventLog
from src.notifications.telegram import TelegramNotifier
from src.utils.logger import get_logger


@dataclass
class PositionLimits:
    max_position_pct: float = 0.10
    max_open_positions: int = 5


@dataclass
class LossLimits:
    max_daily_loss_pct: float = 0.05
    max_drawdown_pct: float = 0.15
    max_single_loss_pct: float = 0.02


@dataclass
class CircuitBreakers:
    consecutive_losses: int = 5
    api_errors: int = 3
    latency_spike_ms: int = 5000


@dataclass
class KillSwitch:
    enabled: bool = True
    telegram_confirm: bool = True
    auto_reset_minutes: int = 0  # 0 = disabled; only active in paper mode


@dataclass
class FuturesLimits:
    """Futures-specific risk limits."""

    max_leverage: int = 10  # Max allowed leverage (hard cap 20 in code)
    liquidation_buffer_pct: float = 5.0  # Block orders if within X% of liq
    max_daily_loss_pct: float = 5.0  # Separate futures daily loss limit
    max_margin_usage_pct: float = 50.0  # Warn if margin usage exceeds this
    margin_mode: str = "isolated"  # isolated only for MVP
    position_mode: str = "one-way"  # one-way only for MVP


@dataclass
class RiskConfig:
    position_limits: PositionLimits = field(default_factory=PositionLimits)
    loss_limits: LossLimits = field(default_factory=LossLimits)
    circuit_breakers: CircuitBreakers = field(default_factory=CircuitBreakers)
    kill_switch: KillSwitch = field(default_factory=KillSwitch)
    futures_limits: FuturesLimits = field(default_factory=FuturesLimits)


class RiskManager:
    """Risk management system with circuit breakers and kill switch."""

    def __init__(
        self,
        config_path: Path | None = None,
        notifier: TelegramNotifier | None = None,
        state_path: Path | None = None,
        agent_id: str = "default",
        paper_mode: bool = False,
        event_log: EventLog | None = None,
    ) -> None:
        self._logger = get_logger(self.__class__.__name__)
        self._config = self._load_config(config_path)
        self._notifier = notifier or TelegramNotifier()
        self._agent_id = self._normalize_agent_id(agent_id)
        self._state_path = state_path or self._default_state_path(self._agent_id)
        self._paper_mode = paper_mode
        self._event_log = event_log
        self._logger.info(
            "Risk manager initialized (agent_id=%s, state=%s)",
            self._agent_id,
            self._state_path,
        )

        # Trading state tracking
        self._positions: dict[str, dict[str, Any]] = {}
        self._daily_pnl: float = 0.0
        self._peak_balance: float = 0.0
        self._consecutive_losses: int = 0
        self._api_error_count: int = 0
        self._latency_readings: deque[float] = deque(maxlen=100)
        self._kill_switch_triggered: bool = False
        self._kill_switch_triggered_at: float = 0.0
        self._reconciliation_block: str | None = None
        self._stale_anchor_warning_emitted: bool = False
        self._circuit_breakers: dict[str, bool] = {
            "consecutive_losses": False,
            "api_errors": False,
            "latency": False,
            "daily_loss": False,
            "drawdown": False,
        }

        # Track daily reset
        self._last_reset: float = time.time()

        # Load persisted state if available
        self._load_state()

        # Pending notifications (for async sending)
        self._pending_notifications: asyncio.Queue[tuple[str, str]] = asyncio.Queue()

    @staticmethod
    def _normalize_agent_id(agent_id: str) -> str:
        normalized = "".join(
            ch if (ch.isalnum() or ch in {"-", "_"}) else "_"
            for ch in (agent_id or "default").strip()
        ).strip("_")
        return normalized or "default"

    @staticmethod
    def _default_state_path(agent_id: str) -> Path:
        # Keep backward-compatible default file path for single-agent runs.
        if agent_id == "default":
            return Path("data/risk_state.json")
        return Path(f"data/risk_state_{agent_id}.json")

    def _load_config(self, config_path: Path | None = None) -> RiskConfig:
        """Load risk configuration from YAML file."""
        if config_path is None:
            config_path = Path("config/risk.yaml")

        if not config_path.exists():
            self._logger.warning(f"Risk config not found at {config_path}, using defaults")
            return RiskConfig()

        with config_path.open("r", encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}

        return RiskConfig(
            position_limits=PositionLimits(**raw.get("position_limits", {})),
            loss_limits=LossLimits(**raw.get("loss_limits", {})),
            circuit_breakers=CircuitBreakers(**raw.get("circuit_breakers", {})),
            kill_switch=KillSwitch(**raw.get("kill_switch", {})),
            futures_limits=FuturesLimits(**raw.get("futures_limits", {})),
        )

    def _load_state(self) -> None:
        """Load risk state from disk."""
        if not self._state_path.exists():
            return

        try:
            with self._state_path.open("r", encoding="utf-8") as f:
                state = json.load(f)

            self._positions = state.get("positions", {})
            self._daily_pnl = state.get("daily_pnl", 0.0)
            self._peak_balance = state.get("peak_balance", 0.0)
            self._consecutive_losses = state.get("consecutive_losses", 0)
            self._api_error_count = state.get("api_error_count", 0)
            self._kill_switch_triggered = state.get("kill_switch_triggered", False)
            self._kill_switch_triggered_at = state.get("kill_switch_triggered_at", 0.0)
            self._reconciliation_block = state.get("reconciliation_block")
            self._circuit_breakers = state.get("circuit_breakers", self._circuit_breakers)
            self._last_reset = state.get("last_reset", time.time())
            updated_at = state.get("updated_at")
            if updated_at:
                try:
                    updated_timestamp = datetime.fromisoformat(updated_at)
                    if updated_timestamp.tzinfo is None:
                        updated_timestamp = updated_timestamp.replace(tzinfo=UTC)
                    age_seconds = (datetime.now(UTC) - updated_timestamp).total_seconds()
                    if age_seconds >= 86400:
                        self._logger.info("Risk state older than 24h; resetting daily metrics")
                        self._daily_pnl = 0.0
                        self._consecutive_losses = 0
                        self._api_error_count = 0
                        self._last_reset = time.time()
                        self._save_state()
                except ValueError as exc:
                    self._logger.warning(
                        "Invalid updated_at timestamp in risk state: %s",
                        exc,
                    )

            self._logger.info("Loaded risk state from disk")

        except Exception as exc:
            self._logger.error(f"Failed to load risk state: {exc}")

    def _save_state(self) -> None:
        """Save risk state to disk."""
        try:
            self._state_path.parent.mkdir(parents=True, exist_ok=True)
            state = {
                "positions": self._positions,
                "daily_pnl": self._daily_pnl,
                "peak_balance": self._peak_balance,
                "consecutive_losses": self._consecutive_losses,
                "api_error_count": self._api_error_count,
                "kill_switch_triggered": self._kill_switch_triggered,
                "kill_switch_triggered_at": self._kill_switch_triggered_at,
                "reconciliation_block": self._reconciliation_block,
                "circuit_breakers": self._circuit_breakers,
                "last_reset": self._last_reset,
                "updated_at": datetime.now(UTC).isoformat(),
            }
            with self._state_path.open("w", encoding="utf-8") as f:
                json.dump(state, f, indent=2)
        except Exception as exc:
            self._logger.error(f"Failed to save risk state: {exc}")

    def check_position_limit(
        self, symbol: str, quantity_usdt: float, portfolio_value: float
    ) -> tuple[bool, str]:
        """Check if a new position would violate position limits.

        Args:
            symbol: Trading pair symbol
            quantity_usdt: Position size in USDT
            portfolio_value: Total portfolio value in USDT

        Returns:
            tuple[bool, str]: (Allowed, Reason)
        """
        if self._kill_switch_triggered:
            return False, "Kill switch is active"

        # Check max open positions
        if len(self._positions) >= self._config.position_limits.max_open_positions:
            if symbol not in self._positions:
                return (
                    False,
                    f"Max open positions ({self._config.position_limits.max_open_positions}) reached",
                )

        # Check position size vs portfolio percentage
        max_position_value = portfolio_value * self._config.position_limits.max_position_pct

        if quantity_usdt > max_position_value:
            return False, (
                f"Position size {quantity_usdt:.2f} USDT exceeds max "
                f"({self._config.position_limits.max_position_pct * 100:.1f}% = {max_position_value:.2f} USDT)"
            )

        return True, "OK"

    def register_open_position(self, symbol: str, quantity_usdt: float, price: float) -> None:
        """Register a newly opened position in risk manager."""
        self._positions[symbol] = {
            "entry_price": price,
            "quantity_usdt": quantity_usdt,
            "entry_time": time.time(),
        }
        self._save_state()

    def register_close_position(self, symbol: str) -> None:
        """Register a closed position in risk manager."""
        if symbol in self._positions:
            del self._positions[symbol]
            self._save_state()

    def record_trade(self, symbol: str, pnl: float, portfolio_value: float) -> None:
        """Record a completed trade for risk tracking."""
        self._daily_pnl += pnl

        # Track consecutive losses
        if pnl < 0:
            self._consecutive_losses += 1
            # Check max single loss
            loss_pct = abs(pnl) / portfolio_value
            if loss_pct > self._config.loss_limits.max_single_loss_pct:
                self._logger.error(
                    f"Max single loss exceeded: {loss_pct:.2%} > "
                    f"{self._config.loss_limits.max_single_loss_pct:.2%}"
                )
                self._trigger_circuit_breaker("max_single_loss")
        else:
            self._consecutive_losses = 0

        self._save_state()  # Persist PnL and counters

        # Check consecutive losses circuit breaker
        if self._consecutive_losses >= self._config.circuit_breakers.consecutive_losses:
            self._logger.error(
                f"Consecutive losses circuit breaker: {self._consecutive_losses} losses"
            )
            self._trigger_circuit_breaker("consecutive_losses")

        # Update peak balance and check drawdown
        current_balance = portfolio_value + self._daily_pnl
        if current_balance > self._peak_balance:
            self._peak_balance = current_balance

        drawdown = (
            (self._peak_balance - current_balance) / self._peak_balance
            if self._peak_balance > 0
            else 0
        )
        warning_threshold = self._config.loss_limits.max_drawdown_pct * 0.75
        if drawdown >= warning_threshold and drawdown < self._config.loss_limits.max_drawdown_pct:
            if not self._stale_anchor_warning_emitted:
                max_daily_loss_abs = portfolio_value * self._config.loss_limits.max_daily_loss_pct
                if abs(self._daily_pnl) <= max_daily_loss_abs:
                    self._logger.warning(
                        "Possible stale drawdown anchor: peak_balance=%.2f current_balance=%.2f drawdown=%.2f%%",
                        self._peak_balance,
                        current_balance,
                        drawdown * 100,
                    )
                    self._stale_anchor_warning_emitted = True
        else:
            self._stale_anchor_warning_emitted = False

        if drawdown > self._config.loss_limits.max_drawdown_pct:
            self._logger.error(f"Max drawdown exceeded: {drawdown:.2%}")
            self._trigger_circuit_breaker("drawdown")

        # Check daily loss limit
        daily_loss_pct = abs(min(0, self._daily_pnl)) / portfolio_value
        if daily_loss_pct > self._config.loss_limits.max_daily_loss_pct:
            self._logger.error(f"Daily loss limit exceeded: {daily_loss_pct:.2%}")
            self._trigger_circuit_breaker("daily_loss")

    def record_api_error(self) -> None:
        """Record an API error."""
        self._api_error_count += 1
        self._logger.warning(f"API error recorded (count: {self._api_error_count})")

        if self._api_error_count >= self._config.circuit_breakers.api_errors:
            self._logger.error(f"API error circuit breaker: {self._api_error_count} errors")
            self._trigger_circuit_breaker("api_errors")

    def record_latency(self, latency_ms: float) -> None:
        """Record a latency reading."""
        self._latency_readings.append(latency_ms)

        if latency_ms > self._config.circuit_breakers.latency_spike_ms:
            self._logger.warning(f"Latency spike detected: {latency_ms:.0f}ms")
            self._trigger_circuit_breaker("latency")

    def _trigger_circuit_breaker(self, reason: str) -> None:
        """Trigger a circuit breaker."""
        self._circuit_breakers[reason] = True
        self._save_state()  # Persist circuit breaker state
        self._logger.error(f"CIRCUIT BREAKER TRIGGERED: {reason}")
        if self._event_log:
            # Fire-and-forget async logging task since we are in a sync method
            # but running within an asyncio event loop
            try:
                loop = asyncio.get_running_loop()
                loop.create_task(
                    self._event_log.log(
                        event_type="risk_check",
                        payload={
                            "level": "error",
                            "action": "circuit_breaker_triggered",
                            "reason": reason,
                            "component": "RiskManager",
                        },
                    )
                )
            except RuntimeError:
                # No running loop (e.g. during sync tests)
                pass
            # We can't await here easily because this might be called from sync context.
            # But the whole app is async. _trigger_circuit_breaker is synchronous though.
            # Wait, RiskManager methods are synchronous (except monitor_loop).
            # I should make _trigger_circuit_breaker async OR use a task.
            # Since this is a critical event, firing a task is acceptable.
            # Actually, let's check if I can make it async.
            pass  # Will handle async logging below

        self._logger.error(f"CIRCUIT BREAKER TRIGGERED: {reason}")

        # Queue circuit breaker notification
        self._pending_notifications.put_nowait(("circuit_breaker", reason))

        if self._config.kill_switch.enabled:
            self._trigger_kill_switch(f"Circuit breaker: {reason}")

    def _trigger_kill_switch(self, reason: str) -> None:
        """Trigger the kill switch to stop all trading."""
        if self._kill_switch_triggered:
            return

        self._kill_switch_triggered = True
        self._kill_switch_triggered_at = time.time()
        self._save_state()  # Persist kill switch state
        self._logger.critical(f"KILL SWITCH ACTIVATED: {reason}")

        # Queue Telegram notification
        if self._config.kill_switch.telegram_confirm:
            self._pending_notifications.put_nowait(("kill_switch", reason))

    def reset_daily_metrics(self) -> None:
        """Reset daily metrics (call at midnight UTC)."""
        current_time = time.time()
        seconds_since_reset = current_time - self._last_reset

        if seconds_since_reset >= 86400:  # 24 hours
            self._logger.info("Resetting daily risk metrics")
            self._daily_pnl = 0.0
            self._consecutive_losses = 0
            self._api_error_count = 0
            self._last_reset = current_time
            self._save_state()  # Persist reset

    def is_trading_allowed(self) -> tuple[bool, str]:
        """Check if trading is currently allowed."""
        if self._reconciliation_block:
            return False, f"Reconciliation block: {self._reconciliation_block}"

        if self._kill_switch_triggered:
            return False, "Kill switch is active"

        active_breakers = [k for k, v in self._circuit_breakers.items() if v]
        if active_breakers:
            return False, f"Circuit breakers active: {', '.join(active_breakers)}"

        return True, "Trading allowed"

    def set_reconciliation_block(self, reason: str) -> None:
        """Block trading due to reconciliation divergence."""
        self._reconciliation_block = reason
        self._logger.error("Trading blocked by reconciliation: %s", reason)
        self._save_state()

    def clear_reconciliation_block(self) -> None:
        """Clear reconciliation block (manual resolution)."""
        if self._reconciliation_block:
            self._logger.warning("Reconciliation block cleared")
            self._reconciliation_block = None
            self._save_state()

    def get_risk_summary(self) -> dict[str, Any]:
        """Get current risk status summary."""
        return {
            "kill_switch_active": self._kill_switch_triggered,
            "reconciliation_block": self._reconciliation_block,
            "circuit_breakers": self._circuit_breakers,
            "daily_pnl": self._daily_pnl,
            "consecutive_losses": self._consecutive_losses,
            "api_errors": self._api_error_count,
            "open_positions": len(self._positions),
            "avg_latency_ms": (
                sum(self._latency_readings) / len(self._latency_readings)
                if self._latency_readings
                else 0
            ),
        }

    def clear_trading_blocks(
        self,
        reset_counters: bool = True,
        reset_peak_balance: bool = True,
    ) -> None:
        self._kill_switch_triggered = False
        self._kill_switch_triggered_at = 0.0
        self._reconciliation_block = None
        self._circuit_breakers = dict.fromkeys(self._circuit_breakers, False)
        if reset_counters:
            self._consecutive_losses = 0
            self._api_error_count = 0
        if reset_peak_balance:
            self._peak_balance = 0.0
            self._stale_anchor_warning_emitted = False
        self._save_state()
        self._logger.warning("Risk trading blocks manually cleared")

    async def monitor_loop(self) -> None:
        """Background monitoring loop for risk checks."""
        while True:
            try:
                self.reset_daily_metrics()

                # Auto-reset kill switch in paper mode after cooldown
                self._check_auto_reset()

                # Process pending notifications
                await self._process_notifications()

                is_allowed, reason = self.is_trading_allowed()
                if not is_allowed:
                    self._logger.warning(f"Trading blocked: {reason}")

                await asyncio.sleep(60)  # Check every minute
            except Exception as exc:
                self._logger.error(f"Risk monitor error: {exc}")
                await asyncio.sleep(60)

    def _check_auto_reset(self) -> None:
        """Auto-reset kill switch after cooldown (paper mode only)."""
        cooldown = self._config.kill_switch.auto_reset_minutes
        if (
            not self._paper_mode
            or cooldown <= 0
            or not self._kill_switch_triggered
            or self._kill_switch_triggered_at <= 0
        ):
            return

        elapsed = (time.time() - self._kill_switch_triggered_at) / 60.0
        if elapsed >= cooldown:
            self._logger.warning(
                "Auto-resetting kill switch after %.0f min cooldown (paper mode)",
                elapsed,
            )
            self.clear_trading_blocks()
            self._pending_notifications.put_nowait(
                ("circuit_breaker", "Kill switch auto-reset after cooldown (paper mode)")
            )

    async def _process_notifications(self) -> None:
        """Process and send pending notifications."""
        while not self._pending_notifications.empty():
            try:
                notification_type, details = self._pending_notifications.get_nowait()

                if notification_type == "kill_switch":
                    auto_reset = (
                        self._config.kill_switch.auto_reset_minutes if self._paper_mode else 0
                    )
                    await self._notifier.send_kill_switch_alert(
                        details, auto_reset_minutes=auto_reset
                    )
                elif notification_type == "circuit_breaker":
                    await self._notifier.send_circuit_breaker_alert(details)

            except asyncio.QueueEmpty:
                break
            except Exception as exc:
                self._logger.error(f"Failed to send notification: {exc}")

    # =========================================================================
    # FUTURES-SPECIFIC RISK METHODS
    # =========================================================================

    def check_liquidation_buffer(
        self,
        mark_price: float,
        liquidation_price: float,
        position_side: str,
        buffer_pct: float | None = None,
    ) -> tuple[bool, str]:
        """Check if position is within liquidation buffer zone.

        Blocks orders if position is within X% of liquidation price.

        Args:
            mark_price: Current mark price
            liquidation_price: Liquidation price for the position
            position_side: "LONG" or "SHORT"
            buffer_pct: Buffer percentage (uses config default if None)

        Returns:
            tuple[bool, str]: (Allowed, Reason)
        """
        if buffer_pct is None:
            buffer_pct = self._config.futures_limits.liquidation_buffer_pct

        buffer = buffer_pct / 100

        if position_side == "LONG":
            # For LONG: mark approaching liq from above
            # Danger zone: mark <= liq * (1 + buffer)
            threshold = liquidation_price * (1 + buffer)
            if mark_price <= threshold:
                return (
                    False,
                    f"LONG position within {buffer_pct}% of liquidation: "
                    f"mark={mark_price:.2f}, liq={liquidation_price:.2f}, "
                    f"threshold={threshold:.2f}",
                )
        else:  # SHORT
            # For SHORT: mark approaching liq from below
            # Danger zone: mark >= liq * (1 - buffer)
            threshold = liquidation_price * (1 - buffer)
            if mark_price >= threshold:
                return (
                    False,
                    f"SHORT position within {buffer_pct}% of liquidation: "
                    f"mark={mark_price:.2f}, liq={liquidation_price:.2f}, "
                    f"threshold={threshold:.2f}",
                )

        return True, "OK"

    def check_max_leverage(self, requested_leverage: int) -> tuple[bool, str]:
        """Check if requested leverage is within limits.

        Hard cap at 20x, configurable max at 10x by default.

        Args:
            requested_leverage: Requested leverage level

        Returns:
            tuple[bool, str]: (Allowed, Reason)
        """
        # Hard safety cap - never allow >20x
        if requested_leverage > 20:
            return (
                False,
                f"Leverage {requested_leverage}x exceeds hard safety cap of 20x",
            )

        # Configurable limit
        max_allowed = self._config.futures_limits.max_leverage
        if requested_leverage > max_allowed:
            return (
                False,
                f"Leverage {requested_leverage}x exceeds configured max {max_allowed}x",
            )

        return True, "OK"

    def check_margin_usage(self, used_margin: float, available_balance: float) -> tuple[bool, str]:
        """Check if margin usage is within safe limits.

        Warns if margin usage exceeds configured threshold.

        Args:
            used_margin: Currently used margin
            available_balance: Available balance for trading

        Returns:
            tuple[bool, str]: (Allowed, Reason)
        """
        if available_balance <= 0:
            return False, "No available balance for trading"

        margin_usage_pct = (used_margin / (used_margin + available_balance)) * 100
        max_usage = self._config.futures_limits.max_margin_usage_pct

        if margin_usage_pct > max_usage:
            return (
                False,
                f"Margin usage {margin_usage_pct:.1f}% exceeds safe threshold {max_usage}%",
            )

        return True, f"Margin usage: {margin_usage_pct:.1f}%"

    def check_futures_daily_loss(self, daily_pnl: float, account_value: float) -> tuple[bool, str]:
        """Check if futures daily loss is within limits.

        Separate from spot daily loss tracking.

        Args:
            daily_pnl: Daily profit/loss for futures positions
            account_value: Total futures account value

        Returns:
            tuple[bool, str]: (Allowed, Reason)
        """
        if account_value <= 0:
            return False, "Invalid account value"

        loss_pct = abs(min(0, daily_pnl)) / account_value * 100
        max_loss = self._config.futures_limits.max_daily_loss_pct

        if loss_pct > max_loss:
            return (
                False,
                f"Futures daily loss {loss_pct:.2f}% exceeds limit {max_loss}%",
            )

        return True, f"Futures daily PnL: {daily_pnl:.2f} USDT ({loss_pct:.2f}%)"

    def calculate_position_size_with_leverage(
        self, notional_value_usdt: float, leverage: int, available_balance: float
    ) -> tuple[bool, float, str]:
        """Calculate actual position size considering leverage and margin.

        Args:
            notional_value_usdt: Desired position notional value
            leverage: Leverage level to use
            available_balance: Available margin balance

        Returns:
            tuple[bool, float, str]: (Allowed, Margin Required, Reason)
        """
        # Check leverage limit first
        allowed, reason = self.check_max_leverage(leverage)
        if not allowed:
            return False, 0.0, reason

        # Calculate required margin
        required_margin = notional_value_usdt / leverage

        if required_margin > available_balance:
            return (
                False,
                required_margin,
                f"Insufficient margin: need {required_margin:.2f} USDT, have {available_balance:.2f} USDT",
            )

        return True, required_margin, f"Margin required: {required_margin:.2f} USDT"
