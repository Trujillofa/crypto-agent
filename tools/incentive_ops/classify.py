"""classify.py — rule-based tier derivation (classification ONLY).

Implements planner-authoritative top-to-bottom rules. Must reproduce the
17 recorded labels in starter-registry-v0.yaml exactly for --check.
Classification is independent of actionability.
"""

from __future__ import annotations

from src.utils.logger import get_logger

from .registry import load_registry
from .types import (
    Classification,
    ClassificationResult,
    Criterion,
    Mechanism,
    ProgramRecord,
)

logger = get_logger(__name__)

REJECT_MECHANISMS = {Mechanism.LP_MARKET_MAKING, Mechanism.VOTE_INCENTIVE}


def _sybil_indicates_weak(sybil: str) -> bool:
    s = sybil.lower()
    return "weak" in s or "multi-wallet" in s or "multi wallet" in s


def _mentions_leverage_or_recursive(text: str) -> bool:
    t = (text or "").lower()
    return "leverage" in t or "recursive" in t or "recurse" in t


def _contract_exposure_unbounded(rec: ProgramRecord) -> bool:
    # Strict: only explicit unbounded language triggers here (YIELD/DEFER legitimately have c4=false).
    # Other REJECTs caught by mech / sybil / c2.
    t = " ".join(rec.tail_risks + [rec.capital_required, rec.sybil_policy, rec.notes or ""]).lower()
    return bool("unbounded" in t or "uncapped" in t or "infinite" in t)


def classify_record(rec: ProgramRecord) -> ClassificationResult:
    """Return derived classification + rule. Does NOT consider captures or caps."""

    # Rule 1: REJECT gates
    if rec.distribution_mechanism in REJECT_MECHANISMS:
        return ClassificationResult(
            derived_label=Classification.REJECT,
            rule_fired="reject_mechanism",
            matches_recorded=False,  # filled by caller
            recorded_label=rec.classification,
            diff=None,
        )
    if _sybil_indicates_weak(rec.sybil_policy):
        return ClassificationResult(
            derived_label=Classification.REJECT,
            rule_fired="reject_weak_sybil",
            matches_recorded=False,
            recorded_label=rec.classification,
            diff=None,
        )
    if rec.selection_criteria.c2_terms_documented == Criterion.FALSE:
        return ClassificationResult(
            derived_label=Classification.REJECT,
            rule_fired="reject_undisclosed_terms",
            matches_recorded=False,
            recorded_label=rec.classification,
            diff=None,
        )
    reason_and_notes = (rec.classification_reason or "") + " " + (rec.notes or "")
    if _mentions_leverage_or_recursive(reason_and_notes):
        return ClassificationResult(
            derived_label=Classification.REJECT,
            rule_fired="reject_leverage_recursive",
            matches_recorded=False,
            recorded_label=rec.classification,
            diff=None,
        )
    if _contract_exposure_unbounded(rec):
        return ClassificationResult(
            derived_label=Classification.REJECT,
            rule_fired="reject_unbounded_exposure",
            matches_recorded=False,
            recorded_label=rec.classification,
            diff=None,
        )

    # Rule 2: DEFER
    if rec.distribution_mechanism == Mechanism.PROPORTIONAL_POINTS:
        return ClassificationResult(
            derived_label=Classification.DEFER,
            rule_fired="defer_proportional_points",
            matches_recorded=False,
            recorded_label=rec.classification,
            diff=None,
        )

    # Rule 3: YIELD_ONLY
    if rec.distribution_mechanism in {Mechanism.PRO_RATA_CAPITAL, Mechanism.CAPPED_CASHBACK}:
        return ClassificationResult(
            derived_label=Classification.YIELD_ONLY,
            rule_fired="yield_only_pro_rata_or_cashback",
            matches_recorded=False,
            recorded_label=rec.classification,
            diff=None,
        )

    # Rule 4: IN_CONDITIONAL
    if rec.distribution_mechanism == Mechanism.LABOR_TASK:
        return ClassificationResult(
            derived_label=Classification.IN_CONDITIONAL,
            rule_fired="in_conditional_labor_task",
            matches_recorded=False,
            recorded_label=rec.classification,
            diff=None,
        )

    # Rule 5: IN_CORE
    if (
        rec.distribution_mechanism == Mechanism.FIXED_PER_IDENTITY_CAP
        and rec.selection_criteria.c1_fixed_or_capped == Criterion.TRUE
        and rec.selection_criteria.c2_terms_documented != Criterion.FALSE
    ):
        return ClassificationResult(
            derived_label=Classification.IN_CORE,
            rule_fired="in_core_fixed_cap_c1_true_c2_ok",
            matches_recorded=False,
            recorded_label=rec.classification,
            diff=None,
        )

    # Rule 6: default REJECT
    return ClassificationResult(
        derived_label=Classification.REJECT,
        rule_fired="reject_unclassifiable_or_incomplete",
        matches_recorded=False,
        recorded_label=rec.classification,
        diff=None,
    )


def classify_all(records: list[ProgramRecord]) -> list[ClassificationResult]:
    results = []
    for rec in records:
        res = classify_record(rec)
        matches = res.derived_label == rec.classification
        diff = None
        if not matches:
            diff = f"{rec.id}: recorded={rec.classification} derived={res.derived_label} rule={res.rule_fired}"
        res = ClassificationResult(
            derived_label=res.derived_label,
            rule_fired=res.rule_fired,
            matches_recorded=matches,
            recorded_label=rec.classification,
            diff=diff,
        )
        results.append(res)
    return results


def check_classification(
    path: str | None = None,
) -> tuple[bool, list[ClassificationResult], list[str], dict[str, int]]:
    """Run against starter (or given) registry. Returns (all_match, results, mismatches, counts)."""
    reg_path = path or "research/a1-incentive-farming/starter-registry-v0.yaml"
    records, _ = load_registry(reg_path, warn=False)
    results = classify_all(records)
    mismatches = [r.diff for r in results if r.diff]
    all_match = len(mismatches) == 0
    counts: dict[str, int] = {}
    for r in results:
        k = str(r.derived_label)
        counts[k] = counts.get(k, 0) + 1
    return all_match, results, mismatches, counts


def main_check() -> int:
    """For direct or CLI use. 0 on exact match. Caller prints human summary."""
    ok, results, mismatches, counts = check_classification()
    # return status; cli prints
    return 0 if ok else 1
