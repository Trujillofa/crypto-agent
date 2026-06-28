"""actionability.py — SEPARATE default-deny gate (classification is tier only).

Returns ACTIONABLE only if ALL of:
  1. Captured (real sha + captured_at + raw bytes durable evidence)
  2. Verified (VerificationRecord: terms_match + live_round_open + reviewer_decision==APPROVED)
  3. Tier in {IN_CORE, IN_CONDITIONAL}
  4. Promotion: no FALSE, no MAYBE (required TRUE), typed base EV > 0 (from EVInputsRecord)
  5. Caps: validate_caps with actual proposed capital + ledger

All records remain NON-ACTIONABLE on Day-0 while reviewer_decision=PENDING.
No network, no eligibility, no wallets, no capital movement.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from src.utils.logger import get_logger

from .accounting import validate_caps
from .capture import (
    ensure_raw_evidence,
    load_ev_inputs,
    load_verifications,
)
from .capture import (
    load_captures as _load_captures,
)
from .ev import compute_ev
from .registry import load_registry
from .types import (
    Actionability,
    ActionabilityResult,
    CaptureRecord,
    Classification,
    Criterion,
    EVInputsRecord,
    EVReadiness,
    JurisdictionStatus,
    PilotCaps,
    ProgramRecord,
    ReviewerDecision,
    VerificationRecord,
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


def _is_verified(
    rec: ProgramRecord,
    cap: CaptureRecord | None,
    ver: VerificationRecord | None,
) -> tuple[bool, str]:
    """Consume typed VerificationRecord (from sidecar) + durable raw evidence.

    Returns (ok, reason). reviewer_decision must be APPROVED; PENDING forces block.
    """
    if cap is None:
        return False, "no capture"
    try:
        ensure_raw_evidence(cap)  # enforces retained bytes (hash alone insufficient)
    except Exception as e:
        return False, f"raw evidence fail: {e}"
    if ver is None:
        return False, "no verification sidecar"
    # Cryptographic + identity binding to capture (blocker #2)
    if ver.id != rec.id:
        return False, "verification id does not match record"
    if cap is not None and ver.snapshot_sha256 != cap.snapshot_sha256:
        return False, "verification snapshot_sha256 does not match capture"
    if ver.snapshot_sha256 == "PENDING_TOOL_CAPTURE":
        return False, "verification has no real snapshot hash"
    if not ver.terms_match_snapshot:
        return False, "terms do not match snapshot"
    if not ver.live_round_open:
        return False, "live_round not open per verification"
    if ver.reviewer_decision != ReviewerDecision.APPROVED:
        return False, f"reviewer_decision={ver.reviewer_decision} (must be APPROVED)"

    # Audit field enforcement (blocker #3)
    if ver.verified_at is None:
        return False, "verified_at is null (human verification timestamp required)"
    if not ver.official_round_terms_url or not ver.captured_source_url or not ver.raw_evidence_path:
        return (
            False,
            "missing required audit fields (official_round_terms_url, captured_source_url, raw_evidence_path)",
        )
    if cap is not None:
        if ver.captured_source_url != cap.source_url:
            return False, "captured_source_url does not match capture source"
        if ver.raw_evidence_path != cap.raw_path:
            return False, "raw_evidence_path does not match capture"
    if ver.official_round_terms_url != ver.captured_source_url:
        return False, "official_round_terms_url != captured_source_url"
    j = ver.jurisdiction_status
    if isinstance(j, str):
        try:
            j = JurisdictionStatus(str(j).upper())
        except Exception:
            j = JurisdictionStatus.UNKNOWN
    if j != JurisdictionStatus.ELIGIBLE:
        return False, f"jurisdiction_status={j} (must be ELIGIBLE; UNKNOWN/INELIGIBLE blocks)"

    # Verif sidecar supersedes stale registry live_round_status (blocker #5)
    return True, "verified"


def _passes_tier(rec: ProgramRecord) -> bool:
    return rec.classification in (Classification.IN_CORE, Classification.IN_CONDITIONAL)


def _passes_promotion(rec: ProgramRecord, base_ev: float) -> bool:
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
    # required criteria TRUE not MAYBE
    if any(c == Criterion.MAYBE for c in crits):
        return False
    return base_ev > 0.0


def _has_acceptable_provenance(evrec: EVInputsRecord | None) -> bool:
    """For READY, every material input must have specific evidence-based provenance.
    Reject UNKNOWN, placeholder, 'from sidecar', 'test', missing, etc. (blocker #2)
    """
    if not evrec or evrec.readiness != EVReadiness.READY:
        return False
    prov = evrec.provenance or {}
    material = [
        "p_eligibility",
        "p_distribution",
        "reward_qty",
        "realizable_price",
        "liquidity_vesting_haircut",
        "base_yield",
        "gas_bridge_fees",
        "capital",
        "days",
        "benchmark_apy",
        "expected_loss_reserve",
        "manual_hours",
        "hourly_rate",
    ]
    bad = (
        "unknown",
        "placeholder",
        "from sidecar",
        "sourced",
        "test",
        "day-0",
        "conservative",
        "structure",
    )
    for f in material:
        val = str(prov.get(f, "")).lower()
        if not val or any(b in val for b in bad):
            return False
    return True


def check_actionability(
    rec: ProgramRecord,
    captures: dict[str, CaptureRecord] | None = None,
    verifications: dict[str, VerificationRecord] | None = None,
    ev_inputs: dict[str, EVInputsRecord] | None = None,
    ledger: list[dict] | None = None,
    caps: PilotCaps | None = None,
    proposed_capital: float | None = None,
) -> ActionabilityResult:
    """Core gate. Rewired for typed verif + EV sidecars + reviewer + actual capital + durable raw.

    proposed_capital / ev sidecar capital used; missing/<=0 blocks caps.
    """
    captures = captures or {}
    verifications = verifications or {}
    ev_inputs = ev_inputs or {}
    caps = caps or PilotCaps()
    cap = captures.get(rec.id)
    ver = verifications.get(rec.id)
    evrec = ev_inputs.get(rec.id)

    # 1. Captured + durable raw?
    if cap is None or cap.snapshot_sha256 == "PENDING_TOOL_CAPTURE" or not _is_fresh_capture(cap):
        return ActionabilityResult(
            status=Actionability.BLOCKED_NEEDS_CAPTURE,
            reason="no valid fresh capture sidecar",
            details={"id": rec.id},
        )

    # 2. Verified (consumes VerificationRecord + reviewer_decision + raw bytes)?
    ok, vreason = _is_verified(rec, cap, ver)
    if not ok:
        return ActionabilityResult(
            status=Actionability.BLOCKED_UNVERIFIED,
            reason=vreason,
            details={"id": rec.id, "live_round_status": rec.live_round_status},
        )

    # 3. Tier?
    if not _passes_tier(rec):
        return ActionabilityResult(
            status=Actionability.BLOCKED_TIER,
            reason=f"tier={rec.classification} not actionable",
            details={"id": rec.id, "tier": str(rec.classification)},
        )

    # 4. Promotion? (typed base EV from sidecar inputs)
    base_ev = 0.0
    readiness_val = getattr(evrec, "readiness", EVReadiness.UNREADY)
    if (
        evrec is not None
        and readiness_val == EVReadiness.READY
        and _has_acceptable_provenance(evrec)
    ):
        try:
            ev_res = compute_ev(evrec.inputs, reward_type=evrec.reward_type)
            base_ev = float(ev_res.get("net_ev", 0.0))
        except Exception as e:
            logger.warning("ev compute fail for %s: %s", rec.id, e)
    if not _passes_promotion(rec, base_ev):
        reason = f"selection criteria or base EV={base_ev:.2f} failed"
        if not (evrec and readiness_val == EVReadiness.READY and _has_acceptable_provenance(evrec)):
            reason += " (EV inputs not READY with full acceptable provenance)"
        return ActionabilityResult(
            status=Actionability.BLOCKED_PROMOTION,
            reason=reason,
            details={
                "id": rec.id,
                "base_ev": base_ev,
                "readiness": str(readiness_val) if evrec else None,
            },
        )

    # 5. Caps? use EV-sidecar capital; missing or <=0 must block (blocker #3)
    if evrec is not None:
        usd = evrec.inputs.capital
    else:
        usd = proposed_capital if proposed_capital is not None else None
    if usd is None or usd <= 0:
        return ActionabilityResult(
            status=Actionability.BLOCKED_CAPS,
            reason="missing or non-positive capital; cannot evaluate caps",
            details={"id": rec.id, "proposed_usd": usd},
        )
    cap_check = validate_caps(ledger or [], {"id": rec.id, "usd": usd}, caps)
    if not cap_check.ok:
        return ActionabilityResult(
            status=Actionability.BLOCKED_CAPS,
            reason=cap_check.reason or "caps exceeded",
            details={"id": rec.id, "check": cap_check, "proposed_usd": usd},
        )

    return ActionabilityResult(
        status=Actionability.ACTIONABLE,
        reason="passed all gates",
        details={"id": rec.id, "base_ev": base_ev},
    )


def check_all_actionability(
    records: list[ProgramRecord] | None = None,
    captures: dict[str, CaptureRecord] | None = None,
    verifications: dict[str, VerificationRecord] | None = None,
    ev_inputs: dict[str, EVInputsRecord] | None = None,
    ledger: list[dict] | None = None,
    caps: PilotCaps | None = None,
) -> dict[str, ActionabilityResult]:
    """Run gate over registry. Loads sidecars if not supplied. All must stay non-ACTIONABLE
    while reviewer_decision remains PENDING (Day-0 invariant).
    """
    if records is None:
        records, _ = load_registry(warn=False)
    caps_dict = captures or _load_captures()
    ver_dict = verifications or load_verifications()
    ev_dict = ev_inputs or load_ev_inputs()
    led = ledger if ledger is not None else []
    out: dict[str, ActionabilityResult] = {}
    for rec in records:
        evrec = ev_dict.get(rec.id)
        # Use EV-sidecar capital for meaningful cap eval; missing/zero will block in caps gate (blocker #3)
        prop_cap = evrec.inputs.capital if evrec else None
        out[rec.id] = check_actionability(
            rec,
            caps_dict,
            ver_dict,
            ev_dict,
            led,
            caps,
            proposed_capital=prop_cap,
        )
    return out


def main_actionability(ledger: list[dict] | None = None) -> dict[str, str]:
    res = check_all_actionability(ledger=ledger)
    summary = {pid: str(r.status) for pid, r in res.items()}
    non_act = all(s != str(Actionability.ACTIONABLE) for s in summary.values())
    logger.info("actionability: all_non_actionable=%s count=%d", non_act, len(summary))
    return summary
