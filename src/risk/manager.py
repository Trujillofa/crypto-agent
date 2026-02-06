from __future__ import annotations

import asyncio
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from src.notifications.telegram import TelegramNotifier
from src.utils.logger import get_logger


@dataclass
class PositionLimits:
    max_position_pct: float = 0.10
    max_leverage: int = 3
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


@dataclass
class RiskConfig:
    position_limits: PositionLimits = field(default_factory=PositionLimits)
    loss_limits: LossLimits = field(default_factory=LossLimits)
    circuit_breakers: CircuitBreakers = field(default_factory=CircuitBreakers)
    kill_switch: KillSwitch = field(default_factory=KillSwitch)


class RiskManager:
    """Risk management system with circuit breakers and kill switch."""

    def __init__(
        self,
        config_path: Path | None = None,
        notifier: TelegramNotifier | None = None,
    ) -> None:
        self._logger = get_logger(self.__class__.__name__)
        self._config = self._load_config(config_path)
        self._notifier = notifier or TelegramNotifier()
        self._logger.info("Risk manager initialized")

        # Trading state tracking
        self._positions: dict[str, dict[str, Any]] = {}
        self._daily_pnl: float = 0.0
        self._peak_balance: float = 0.0
        self._consecutive_losses: int = 0
        self._api_error_count: int = 0
        self._latency_readings: deque[float] = deque(maxlen=100)
        self._kill_switch_triggered: bool = False
        self._circuit_breakers: dict[str, bool] = {
            "consecutive_losses": False,
            "api_errors": False,
            "latency": False,
            "daily_loss": False,
            "drawdown": False,
        }

        # Track daily reset
        self._last_reset: float = time.time()

        # Pending notifications (for async sending)
        self._pending_notifications: asyncio.Queue[tuple[str, str]] = asyncio.Queue()

    def _load_config(self, config_path: Path | None = None) -> RiskConfig:
        """Load risk configuration from YAML file."""
        if config_path is None:
            config_path = Path("config/risk.yaml")

        if not config_path.exists():
            self._logger.warning(
                f"Risk config not found at {config_path}, using defaults"
            )
            return RiskConfig()

        with config_path.open("r", encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}

        return RiskConfig(
            position_limits=PositionLimits(**raw.get("position_limits", {})),
            loss_limits=LossLimits(**raw.get("loss_limits", {})),
            circuit_breakers=CircuitBreakers(**raw.get("circuit_breakers", {})),
            kill_switch=KillSwitch(**raw.get("kill_switch", {})),
        )

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
        max_position_value = (
            portfolio_value * self._config.position_limits.max_position_pct
        )

        if quantity_usdt > max_position_value:
            return False, (
                f"Position size {quantity_usdt:.2f} USDT exceeds max "
                f"({self._config.position_limits.max_position_pct * 100:.1f}% = {max_position_value:.2f} USDT)"
            )

        return True, "OK"

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
            self._logger.error(
                f"API error circuit breaker: {self._api_error_count} errors"
            )
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

    def is_trading_allowed(self) -> tuple[bool, str]:
        """Check if trading is currently allowed."""
        if self._kill_switch_triggered:
            return False, "Kill switch is active"

        active_breakers = [k for k, v in self._circuit_breakers.items() if v]
        if active_breakers:
            return False, f"Circuit breakers active: {', '.join(active_breakers)}"

        return True, "Trading allowed"

    def get_risk_summary(self) -> dict[str, Any]:
        """Get current risk status summary."""
        return {
            "kill_switch_active": self._kill_switch_triggered,
            "circuit_breakers": self._circuit_breakers,
            "daily_pnl": self._daily_pnl,
            "consecutive_losses": self._consecutive_losses,
            "api_errors": self._api_error_count,
            "open_positions": len(self._positions),
            "avg_latency_ms": sum(self._latency_readings) / len(self._latency_readings)
            if self._latency_readings
            else 0,
        }

    async def monitor_loop(self) -> None:
        """Background monitoring loop for risk checks."""
        while True:
            try:
                self.reset_daily_metrics()

                # Process pending notifications
                await self._process_notifications()

                is_allowed, reason = self.is_trading_allowed()
                if not is_allowed:
                    self._logger.warning(f"Trading blocked: {reason}")

                await asyncio.sleep(60)  # Check every minute
            except Exception as exc:
                self._logger.error(f"Risk monitor error: {exc}")
                await asyncio.sleep(60)

    async def _process_notifications(self) -> None:
        """Process and send pending notifications."""
        while not self._pending_notifications.empty():
            try:
                notification_type, details = self._pending_notifications.get_nowait()

                if notification_type == "kill_switch":
                    await self._notifier.send_kill_switch_alert(details)
                elif notification_type == "circuit_breaker":
                    await self._notifier.send_circuit_breaker_alert(details)

            except asyncio.QueueEmpty:
                break
            except Exception as exc:
                self._logger.error(f"Failed to send notification: {exc}")
