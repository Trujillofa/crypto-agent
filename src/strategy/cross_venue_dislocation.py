"""Cross-venue dislocation filter for backtest entry gating (v0).

Mirrors src/strategy/basis_premium_filter.py structure exactly:
- Config dataclass
- parse_... with safe defaults + clamping
- apply_..._gate(signal, row, config) -> (Signal, blocked: bool)
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from src.strategy.signals import Signal, SignalType

CROSS_VENUE_DISLOCATION_BLOCK_REASON = "Blocked by Cross Venue Dislocation Filter"


@dataclass(frozen=True)
class CrossVenueDislocationConfig:
    enabled: bool = False
    metric: str = "basis_spread"  # "basis_spread" | "premium_spread"
    mode: str = "require"  # "require" (enter only on dislocation) | "block" (block on dislocation)
    min_spread_bps: float = 5.0
    side: str = "both"  # "positive" | "negative" | "both"
    missing_data_policy: str = "allow"


def parse_cross_venue_dislocation(raw: object) -> CrossVenueDislocationConfig:
    """Parse strategy.cross_venue_dislocation from YAML (safe defaults)."""
    if raw is None:
        return CrossVenueDislocationConfig()
    if not isinstance(raw, Mapping):
        return CrossVenueDislocationConfig()

    metric = str(raw.get("metric", "basis_spread")).strip()
    if metric not in {"basis_spread", "premium_spread"}:
        metric = "basis_spread"

    mode = str(raw.get("mode", "require")).strip().lower()
    if mode not in {"require", "block"}:
        mode = "require"

    side = str(raw.get("side", "both")).strip().lower()
    if side not in {"positive", "negative", "both"}:
        side = "both"

    missing_policy = str(raw.get("missing_data_policy", "allow")).strip().lower()
    if missing_policy not in {"allow", "block"}:
        missing_policy = "allow"

    min_bps = float(raw.get("min_spread_bps", 5.0))
    if min_bps < 0:
        min_bps = 0.0

    return CrossVenueDislocationConfig(
        enabled=bool(raw.get("enabled", False)),
        metric=metric,
        mode=mode,
        min_spread_bps=min_bps,
        side=side,
        missing_data_policy=missing_policy,
    )


def _get_spread_value(row: Mapping[str, object], metric: str) -> float | None:
    key = (
        "cross_venue_basis_spread_bps" if metric == "basis_spread" else "cross_venue_premium_spread"
    )
    raw = row.get(key)
    if raw is None:
        return None
    return float(raw)


def should_block_buy(
    *,
    spread: float | None,
    config: CrossVenueDislocationConfig,
) -> tuple[bool, str]:
    """Return (blocked, reason). Mirrors basis filter missing policy (default allow)."""
    if not config.enabled:
        return False, ""

    if spread is None:
        if config.missing_data_policy == "block":
            return (
                True,
                f"{CROSS_VENUE_DISLOCATION_BLOCK_REASON}: missing spread (policy=block)",
            )
        return False, ""

    abs_spread = abs(spread)
    if abs_spread < config.min_spread_bps:
        is_dislocated = False
    else:
        if config.side == "positive":
            is_dislocated = spread > 0
        elif config.side == "negative":
            is_dislocated = spread < 0
        else:
            is_dislocated = True

    if config.mode == "require":
        # block if NOT dislocated (i.e. require the dislocation to allow BUY)
        if not is_dislocated:
            return (
                True,
                f"{CROSS_VENUE_DISLOCATION_BLOCK_REASON}: |spread|={abs_spread:.2f} < {config.min_spread_bps:.2f} or wrong side for require",
            )
        return False, ""
    else:
        # block mode: block BUY when dislocated
        if is_dislocated:
            return (
                True,
                f"{CROSS_VENUE_DISLOCATION_BLOCK_REASON}: |spread|={abs_spread:.2f} >= {config.min_spread_bps:.2f}",
            )
        return False, ""


def apply_cross_venue_dislocation_gate(
    signal: Signal,
    row: Mapping[str, object],
    config: CrossVenueDislocationConfig,
) -> tuple[Signal, bool]:
    """Return (signal, blocked). blocked=True when BUY converted to HOLD."""
    if signal.type != SignalType.BUY:
        return signal, False

    spread = _get_spread_value(row, config.metric)
    blocked, reason = should_block_buy(spread=spread, config=config)
    if not blocked:
        return signal, False

    return (
        Signal(
            type=SignalType.HOLD,
            symbol=signal.symbol,
            price=signal.price,
            confidence=0.0,
            reason=reason,
            indicators=signal.indicators,
            trading_mode=signal.trading_mode,
        ),
        True,
    )
