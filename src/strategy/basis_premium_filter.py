"""Perp basis/premium crowding filter for backtest entry gating (v0)."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace

from src.strategy.signals import Signal, SignalType

BASIS_FILTER_BLOCK_REASON = "Blocked by Basis Premium Filter"


@dataclass(frozen=True)
class BasisPremiumFilterConfig:
    enabled: bool = False
    exchange: str = "binance_usdm"
    tail_metric: str = "basis_bps"
    positive_tail_pct: float = 0.05
    block_positive_tail_longs: bool = True
    missing_data_policy: str = "allow"
    calibrated_positive_threshold: float | None = None


def parse_basis_premium_filter(raw: object) -> BasisPremiumFilterConfig:
    """Parse strategy.basis_premium_filter from YAML."""
    if raw is None:
        return BasisPremiumFilterConfig()
    if not isinstance(raw, Mapping):
        return BasisPremiumFilterConfig()

    tail_metric = str(raw.get("tail_metric", "basis_bps")).strip()
    if tail_metric not in {"basis_bps", "premium_index"}:
        tail_metric = "basis_bps"

    missing_policy = str(raw.get("missing_data_policy", "allow")).strip().lower()
    if missing_policy not in {"allow", "block"}:
        missing_policy = "allow"

    return BasisPremiumFilterConfig(
        enabled=bool(raw.get("enabled", False)),
        exchange=str(raw.get("exchange", "binance_usdm")),
        tail_metric=tail_metric,
        positive_tail_pct=float(raw.get("positive_tail_pct", 0.05)),
        block_positive_tail_longs=bool(raw.get("block_positive_tail_longs", True)),
        missing_data_policy=missing_policy,
        calibrated_positive_threshold=None,
    )


def with_calibrated_threshold(
    config: BasisPremiumFilterConfig,
    threshold: float | None,
) -> BasisPremiumFilterConfig:
    """Return config copy with train-window calibrated threshold."""
    return replace(config, calibrated_positive_threshold=threshold)


def compute_positive_tail_threshold(
    values: Sequence[float],
    positive_tail_pct: float,
) -> float | None:
    """Threshold for top tail (e.g. 0.05 → 95th percentile)."""
    if not values:
        return None
    if positive_tail_pct <= 0 or positive_tail_pct >= 1:
        raise ValueError("positive_tail_pct must be in (0, 1)")
    ordered = sorted(values)
    percentile = (1.0 - positive_tail_pct) * 100.0
    rank = (len(ordered) - 1) * percentile / 100.0
    low = int(rank)
    high = min(low + 1, len(ordered) - 1)
    weight = rank - low
    return ordered[low] * (1.0 - weight) + ordered[high] * weight


def metric_value_from_row(
    row: Mapping[str, object],
    tail_metric: str,
) -> float | None:
    key = "basis_bps" if tail_metric == "basis_bps" else "premium_index"
    raw = row.get(key)
    if raw is None:
        return None
    return float(raw)


def should_block_buy(
    *,
    basis_bps: float | None,
    premium_index: float | None,
    config: BasisPremiumFilterConfig,
) -> tuple[bool, str]:
    """Return (blocked, reason). Default missing data → allow for v0."""
    if not config.enabled or not config.block_positive_tail_longs:
        return False, ""

    threshold = config.calibrated_positive_threshold
    if threshold is None:
        return False, ""

    if config.tail_metric == "basis_bps":
        value = basis_bps
        label = "basis_bps"
    else:
        value = premium_index
        label = "premium_index"

    if value is None:
        if config.missing_data_policy == "block":
            return (
                True,
                f"{BASIS_FILTER_BLOCK_REASON}: missing {label} (policy=block)",
            )
        return False, ""

    if value >= threshold:
        tail_pct = config.positive_tail_pct * 100.0
        return (
            True,
            (
                f"{BASIS_FILTER_BLOCK_REASON}: {label}={value:.6f} >= "
                f"threshold={threshold:.6f} (top {tail_pct:.1f}% train tail)"
            ),
        )
    return False, ""


def apply_basis_premium_gate(
    signal: Signal,
    row: Mapping[str, object],
    config: BasisPremiumFilterConfig,
) -> tuple[Signal, bool]:
    """Return (signal, blocked). blocked=True when BUY converted to HOLD."""
    if signal.type != SignalType.BUY:
        return signal, False

    basis_bps = metric_value_from_row(row, "basis_bps")
    premium_index = metric_value_from_row(row, "premium_index")
    blocked, reason = should_block_buy(
        basis_bps=basis_bps,
        premium_index=premium_index,
        config=config,
    )
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
