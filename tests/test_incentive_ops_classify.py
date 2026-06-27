"""classify --check must reproduce exactly the recorded labels on fixture.

Counts: 5 IN_CORE, 4 IN_CONDITIONAL, 3 YIELD_ONLY, 2 DEFER, 3 REJECT.
Synthetic undisclosed must REJECT.
"""

from __future__ import annotations

from tools.incentive_ops.classify import check_classification
from tools.incentive_ops.types import Classification


def test_classify_reproduces_all_labels_exactly():
    ok, results, mismatches, counts = check_classification()
    assert ok, f"Mismatches: {mismatches}"
    assert counts.get(str(Classification.IN_CORE), 0) == 5
    assert counts.get(str(Classification.IN_CONDITIONAL), 0) == 4
    assert counts.get(str(Classification.YIELD_ONLY), 0) == 3
    assert counts.get(str(Classification.DEFER), 0) == 2
    assert counts.get(str(Classification.REJECT), 0) == 3
    reject_recs = [r for r in results if r.derived_label == Classification.REJECT]
    assert len(reject_recs) == 3


def test_synthetic_undisclosed_rejects():
    ok, results, _, _ = check_classification()
    assert ok
    # synthetic hits either weak_sybil or undisclosed_terms (order: sybil before c2)
    has_undisclosed_reject = any(
        "undisclosed" in (r.rule_fired or "") or "weak_sybil" in (r.rule_fired or "")
        for r in results
        if r.derived_label == Classification.REJECT
    )
    assert has_undisclosed_reject
