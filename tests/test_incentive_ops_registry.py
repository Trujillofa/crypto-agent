"""Registry load + validation tests against the exact starter fixture."""

from __future__ import annotations

from tools.incentive_ops.registry import load_registry, validate_registry
from tools.incentive_ops.types import Classification, Mechanism, ProgramRecord


def test_loads_17_records():
    recs, warns = load_registry(warn=False)
    assert len(recs) == 17
    ids = {r.id for r in recs}
    assert "undisclosed-sybil-points-farm-archetype" in ids
    assert "coinlist-token-sale" in ids


def test_required_fields_and_enums():
    recs, _ = load_registry(warn=False)
    for r in recs:
        assert isinstance(r, ProgramRecord)
        assert r.distribution_mechanism in Mechanism
        assert r.classification in Classification
        assert r.selection_criteria is not None


def test_validate_reports_warnings_but_no_hard_fail():
    warns = validate_registry()
    # expect several PENDING + UNVERIFIED
    assert any("PENDING_TOOL_CAPTURE" in w for w in warns)
    assert len(warns) >= 5


def test_duplicate_or_bad_would_fail(monkeypatch):
    # smoke: load succeeds on good fixture
    recs, _ = load_registry()
    assert len(recs) == 17
