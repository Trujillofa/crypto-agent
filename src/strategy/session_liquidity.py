"""UTC session windows for liquidity-based entry gating."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime

from src.strategy.signals import Signal, SignalType

# Disjoint UTC windows [start_hour, end_hour) — aligned with probe v0.
DEFAULT_WINDOWS: dict[str, tuple[int, int]] = {
    "asia": (0, 8),
    "europe": (8, 16),
    "americas": (16, 24),
}

SESSION_ROUTER_BLOCK_REASON = "Blocked by Session Liquidity Router (outside allowed UTC windows)"


@dataclass(frozen=True)
class SessionLiquidityRouterConfig:
    enabled: bool = False
    allowed_windows: tuple[str, ...] = ("americas",)
    block_entries_outside_windows: bool = True


def parse_session_liquidity_router(raw: object) -> SessionLiquidityRouterConfig:
    """Parse strategy.session_liquidity_router from YAML."""
    if raw is None:
        return SessionLiquidityRouterConfig()
    if not isinstance(raw, Mapping):
        return SessionLiquidityRouterConfig()

    enabled = bool(raw.get("enabled", False))
    block_outside = bool(raw.get("block_entries_outside_windows", True))
    windows_raw = raw.get("allowed_windows", ["americas"])
    if not isinstance(windows_raw, list) or not windows_raw:
        allowed: tuple[str, ...] = ("americas",)
    else:
        allowed = tuple(str(item).strip().lower() for item in windows_raw if str(item).strip())

    if not allowed:
        allowed = ("americas",)

    return SessionLiquidityRouterConfig(
        enabled=enabled,
        allowed_windows=allowed,
        block_entries_outside_windows=block_outside,
    )


def utc_hour_from_time(bar_time: datetime | str) -> int:
    if isinstance(bar_time, datetime):
        return bar_time.hour
    parsed = datetime.fromisoformat(str(bar_time).replace("Z", "+00:00"))
    if parsed.tzinfo is not None:
        parsed = parsed.replace(tzinfo=None)
    return parsed.hour


def hour_in_window(utc_hour: int, start: int, end: int) -> bool:
    if start < end:
        return start <= utc_hour < end
    return utc_hour >= start or utc_hour < end


def session_for_hour(
    utc_hour: int,
    windows: Mapping[str, tuple[int, int]] = DEFAULT_WINDOWS,
) -> str | None:
    for name, (start, end) in windows.items():
        if hour_in_window(utc_hour, start, end):
            return name
    return None


def is_entry_allowed(
    utc_hour: int,
    allowed_windows: tuple[str, ...],
    *,
    windows: Mapping[str, tuple[int, int]] = DEFAULT_WINDOWS,
) -> bool:
    if not allowed_windows:
        return True
    current = session_for_hour(utc_hour, windows)
    if current is None:
        return False
    return current in allowed_windows


def apply_session_liquidity_gate(
    signal: Signal,
    bar_time: datetime | str,
    router: SessionLiquidityRouterConfig,
) -> tuple[Signal, bool]:
    """Return (signal, blocked). blocked=True when BUY was converted to HOLD."""
    if not router.enabled or not router.block_entries_outside_windows:
        return signal, False
    if signal.type != SignalType.BUY:
        return signal, False

    utc_hour = utc_hour_from_time(bar_time)
    if is_entry_allowed(utc_hour, router.allowed_windows):
        return signal, False

    windows_label = ", ".join(router.allowed_windows)
    return (
        Signal(
            type=SignalType.HOLD,
            symbol=signal.symbol,
            price=signal.price,
            confidence=0.0,
            reason=f"{SESSION_ROUTER_BLOCK_REASON}: allowed={windows_label}, utc_hour={utc_hour}",
            indicators=signal.indicators,
            trading_mode=signal.trading_mode,
        ),
        True,
    )
