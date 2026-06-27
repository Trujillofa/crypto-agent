"""actionability.py — SEPARATE default-deny gate (classification is tier only).

Returns ACTIONABLE only if ALL of:
  1. Captured (real sha + captured_at sidecar, freshness ok)
  2. Verified (live_round_status confirms open + terms match captured)
  3. Tier in {IN_CORE, IN_CONDITIONAL}
  4. Promotion: no FALSE, no MAYBE (required TRUE), base EV > 0
  5. Caps: validate_caps would pass

Given starter fixture (no sidecars), MUST return non-ACTIONABLE for all 17.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from src.utils.logger import get_logger

from .accounting import validate_caps
from .capture import load_captures as _load_captures
from .registry import load_registry
from .types import (
    Actionability,
    ActionabilityResult,
    CaptureRecord,
    Classification,
    Criterion,
    PilotCaps,
    ProgramRecord,
)

logger = get_logger(__name__)

CAPTURE_FRESHNESS_DAYS = 14  # aligned to review window in fixture


# use imported _load_captures from capture


def _is_fresh_capture(cap: CaptureRecord, now: datetime | None = None) -> bool:
    now = now or datetime.now(UTC)
    captured = cap.captured_at
    if captured.tzinfo is None:  # tolerate naive sidecars; treat as UTC
        captured = captured.replace(tzinfo=UTC)
    age = now - captured
    return age <= timedelta(days=CAPTURE_FRESHNESS_DAYS)


def _is_verified(rec: ProgramRecord, cap: CaptureRecord | None) -> bool:
    # In Phase 0: live status must indicate open round. Snapshot match would require parsing
    # captured raw bytes in future. For v0 treat "LIVE" as verified proxy; UNVERIFIED blocks.
    if rec.live_round_status == "LIVE":
        return True
    if rec.live_round_status in ("UNVERIFIED", "NOT_LIVE_REFERENCE_ONLY", "NOT_A_REAL_PROGRAM"):
        return False
    return False


def _passes_tier(rec: ProgramRecord) -> bool:
    return rec.classification in (Classification.IN_CORE, Classification.IN_CONDITIONAL)


def _passes_promotion(rec: ProgramRecord, base_ev_positive: bool) -> bool:
    sc = rec.selection_criteria
    crits = [
        sc.c1_fixed_or_capped,
        sc.c2_terms_documented,
        sc.c3_eligibility_public,
        sc.c4_capital_bounded,
        sc.c5_tail_named,
        sc.c6_reward_rationale,
    ]
    if any(c == Criterion.FALSE for c in crits):
        return False
    # required criteria TRUE not MAYBE (na is acceptable for some)
    if any(c == Criterion.MAYBE for c in crits):
        return False
    return base_ev_positive


def check_actionability(
    rec: ProgramRecord,
    captures: dict[str, CaptureRecord] | None = None,
    ledger: list[dict] | None = None,
    caps: PilotCaps | None = None,
    base_ev_positive: bool = False,
) -> ActionabilityResult:
    """Core gate. captures keyed by id. ledger for caps (list of committed entries)."""
    captures = captures or {}
    caps = caps or PilotCaps()
    cap = captures.get(rec.id)

    # 1. Captured?
    if cap is None or cap.snapshot_sha256 == "PENDING_TOOL_CAPTURE" or not _is_fresh_capture(cap):
        return ActionabilityResult(
            status=Actionability.BLOCKED_NEEDS_CAPTURE,
            reason="no valid fresh capture sidecar",
            details={"id": rec.id},
        )

    # 2. Verified?
    if not _is_verified(rec, cap):
        return ActionabilityResult(
            status=Actionability.BLOCKED_UNVERIFIED,
            reason=f"live_round_status={rec.live_round_status}",
            details={"id": rec.id},
        )

    # 3. Tier?
    if not _passes_tier(rec):
        return ActionabilityResult(
            status=Actionability.BLOCKED_TIER,
            reason=f"tier={rec.classification} not actionable",
            details={"id": rec.id, "tier": str(rec.classification)},
        )

    # 4. Promotion?
    if not _passes_promotion(rec, base_ev_positive):
        return ActionabilityResult(
            status=Actionability.BLOCKED_PROMOTION,
            reason="selection criteria or base EV failed",
            details={"id": rec.id},
        )

    # 5. Caps? (use accounting)
    cap_check = validate_caps(ledger or [], {"id": rec.id, "usd": 100.0}, caps)  # placeholder usd
    if not cap_check.ok:
        return ActionabilityResult(
            status=Actionability.BLOCKED_CAPS,
            reason=cap_check.reason or "caps exceeded",
            details={"id": rec.id, "check": cap_check},
        )

    return ActionabilityResult(
        status=Actionability.ACTIONABLE,
        reason="passed all gates",
        details={"id": rec.id},
    )


def check_all_actionability(
    records: list[ProgramRecord] | None = None,
    captures: dict[str, CaptureRecord] | None = None,
    ledger: list[dict] | None = None,
    caps: PilotCaps | None = None,
) -> dict[str, ActionabilityResult]:
    """Run gate over registry (or given). Default: all should be non-ACTIONABLE on starter."""
    if records is None:
        records, _ = load_registry(warn=False)
    caps_dict = captures or _load_captures()
    out: dict[str, ActionabilityResult] = {}
    for rec in records:
        out[rec.id] = check_actionability(rec, caps_dict, ledger, caps, base_ev_positive=False)
    return out


def main_actionability() -> dict[str, str]:
    res = check_all_actionability()
    summary = {pid: str(r.status) for pid, r in res.items()}
    non_act = all(s != str(Actionability.ACTIONABLE) for s in summary.values())
    logger.info("actionability: all_non_actionable=%s count=%d", non_act, len(summary))
    return summary
