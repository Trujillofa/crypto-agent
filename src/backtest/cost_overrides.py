"""Per-run backtest cost profiles for cost-realism experiments.

Does not change BacktestConfig defaults; callers pass an explicit CostProfile.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Literal

TIMEFRAME_HOURS: dict[str, float] = {
    "1m": 1.0 / 60.0,
    "5m": 5.0 / 60.0,
    "15m": 0.25,
    "30m": 0.5,
    "1h": 1.0,
    "2h": 2.0,
    "4h": 4.0,
    "6h": 6.0,
    "8h": 8.0,
    "12h": 12.0,
    "1d": 24.0,
    "3d": 72.0,
    "1w": 168.0,
}

FundingCadence = Literal["per_bar", "scaled_8h"]
CostPassName = Literal["legacy", "realistic"]

LEGACY_FEE_RATE = 0.001
LEGACY_SLIPPAGE_PCT = 0.001
REALISTIC_FEE_RATE = 0.0004
REALISTIC_SLIPPAGE_PCT = 0.0002
DEFAULT_FUTURES_FUNDING_RATE = 0.0001


@dataclass(frozen=True)
class CostProfile:
    """Explicit per-run cost and behavior knobs."""

    name: CostPassName
    fee_rate: float
    slippage_pct: float
    apply_global_trend_filter: bool
    funding_cadence: FundingCadence
    base_futures_funding_rate: float = DEFAULT_FUTURES_FUNDING_RATE

    @property
    def round_trip_cost_pct(self) -> float:
        return 2.0 * (self.fee_rate + self.slippage_pct) * 100.0

    def effective_futures_funding_rate(self, timeframe: str) -> float:
        return effective_futures_funding_rate_per_bar(
            self.base_futures_funding_rate,
            timeframe,
            cadence=self.funding_cadence,
        )

    def to_audit_dict(self, *, timeframe: str, futures_mode: bool) -> dict[str, object]:
        payload = asdict(self)
        payload["round_trip_cost_pct"] = self.round_trip_cost_pct
        payload["funding_method"] = (
            "scale per-bar rate by tf_hours/8 (equivalent to 8h settlement cadence)"
            if self.funding_cadence == "scaled_8h"
            else "charge base rate every bar (legacy engine behavior)"
        )
        payload["effective_futures_funding_rate"] = (
            self.effective_futures_funding_rate(timeframe) if futures_mode else 0.0
        )
        return payload


def effective_futures_funding_rate_per_bar(
    base_rate: float,
    timeframe: str,
    *,
    cadence: FundingCadence = "scaled_8h",
) -> float:
    """Per-bar futures funding charge for the given cadence.

    ``scaled_8h`` scales the 8h settlement rate by ``timeframe_hours / 8`` so sub-8h
    backtests do not overcharge funding every bar.
    """
    if cadence == "per_bar":
        return base_rate
    tf_hours = TIMEFRAME_HOURS.get(timeframe)
    if tf_hours is None:
        raise ValueError(f"Unsupported timeframe for funding scale: {timeframe}")
    return base_rate * (tf_hours / 8.0)


def legacy_cost_profile(*, apply_global_trend_filter: bool = True) -> CostProfile:
    return CostProfile(
        name="legacy",
        fee_rate=LEGACY_FEE_RATE,
        slippage_pct=LEGACY_SLIPPAGE_PCT,
        apply_global_trend_filter=apply_global_trend_filter,
        funding_cadence="per_bar",
    )


def realistic_cost_profile(*, apply_global_trend_filter: bool = False) -> CostProfile:
    return CostProfile(
        name="realistic",
        fee_rate=REALISTIC_FEE_RATE,
        slippage_pct=REALISTIC_SLIPPAGE_PCT,
        apply_global_trend_filter=apply_global_trend_filter,
        funding_cadence="scaled_8h",
    )
