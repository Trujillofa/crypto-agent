"""Actionability default-deny + runtime caps enforcement tests.

All 17 on uncaptured fixture MUST be non-ACTIONABLE (mostly BLOCKED_NEEDS_CAPTURE).
Caps must block >1000 total, >250 per, >3 concurrent.
"""

from __future__ import annotations

import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path

import pytest

from tools.incentive_ops.accounting import CapsExceeded, load_ledger, validate_caps
from tools.incentive_ops.actionability import check_actionability, check_all_actionability
from tools.incentive_ops.capture import _compute_sha256
from tools.incentive_ops.types import (
    Actionability,
    CaptureRecord,
    Classification,
    EVInputsRecord,
    EVReadiness,
    EVScenarioInputs,
    JurisdictionStatus,
    Mechanism,
    PilotCaps,
    ProgramRecord,
    ReviewerDecision,
    RewardType,
    SelectionCriteria,
    ValidationError,
    VerificationRecord,
)


def test_all_17_non_actionable_on_starter_fixture():
    res = check_all_actionability()
    assert len(res) == 17
    for pid, r in res.items():
        assert r.status != Actionability.ACTIONABLE, f"{pid} was ACTIONABLE"
        # most should be needs capture (some may be other if we had caps etc)
        assert "BLOCKED" in str(r.status)


def test_validate_caps_blocks_total():
    caps = PilotCaps()
    ledger = [{"id": "p1", "usd": 900}]
    ck = validate_caps(ledger, {"id": "p2", "usd": 150}, caps)
    assert not ck.ok
    assert "total" in (ck.reason or "").lower()


def test_validate_caps_blocks_per_program():
    caps = PilotCaps()
    ledger = [{"id": "p1", "usd": 200}]
    ck = validate_caps(ledger, {"id": "p1", "usd": 100}, caps)
    assert not ck.ok
    assert "per-program" in (ck.reason or "").lower()


def test_validate_caps_blocks_concurrent():
    caps = PilotCaps()
    ledger = [{"id": "a", "usd": 10}, {"id": "b", "usd": 10}, {"id": "c", "usd": 10}]
    ck = validate_caps(ledger, {"id": "d", "usd": 10}, caps)
    assert not ck.ok
    assert "concurrent" in (ck.reason or "").lower()


def test_validate_caps_ok_when_under():
    caps = PilotCaps()
    ledger = [{"id": "a", "usd": 100}]
    ck = validate_caps(ledger, {"id": "b", "usd": 100}, caps)
    assert ck.ok
    assert ck.concurrent_after == 2


# --- New tests for Phase-0 ops gates (blockers fixed) ---


def _mk_rec(
    pid: str, tier: Classification = Classification.IN_CORE, live: str = "LIVE"
) -> ProgramRecord:
    sel = SelectionCriteria(
        c1_fixed_or_capped="true",
        c2_terms_documented="true",
        c3_eligibility_public="true",
        c4_capital_bounded="true",
        c5_tail_named="true",
        c6_reward_rationale="true",
    )
    return ProgramRecord(
        id=pid,
        name=pid,
        official_source_url="https://ex",
        secondary_url=None,
        observed_at=datetime.now(UTC).date(),
        snapshot_sha256="deadbeef" * 8,
        distribution_mechanism=Mechanism.FIXED_PER_IDENTITY_CAP,
        reward_type=RewardType.ANNOUNCED_FIXED_TOKEN,
        capital_required="test",
        lockup_vesting="",
        eligibility_window="",
        kyc_required=False,
        jurisdiction_restrictions=False,
        sybil_policy="",
        chains_contracts="",
        exit_liquidity="",
        tail_risks=[],
        classification=tier,
        classification_reason="test",
        selection_criteria=sel,
        verification_status="MECH",
        live_round_status=live,
        review_expiry=datetime.now(UTC).date(),
    )


def _mk_cap(pid: str, intent_sha: str = "good") -> CaptureRecord:
    tmp = Path(tempfile.mkdtemp()) / f"test-{pid}.raw"
    content = (
        b"unit-test-evidence-bytes-" + os.urandom(16)
        if "wrong" not in intent_sha.lower()
        else b"wrong-content-to-make-ensure-pass-but-hash-mismatch-later"
    )
    tmp.write_bytes(content)
    use_sha = (
        "WRONG"
        if "wrong" in intent_sha.lower()
        else "fixedshaforpositiveunit" + os.urandom(4).hex()
    )
    return CaptureRecord(
        id=pid,
        snapshot_sha256=use_sha,
        captured_at=datetime.now(UTC),
        raw_path=str(tmp),
        source_url="https://ex",
    )


def _mk_ver(
    pid: str,
    sha: str,
    terms=True,
    live_o=True,
    dec=ReviewerDecision.APPROVED,
    verified_at=None,
    jurisdiction=JurisdictionStatus.ELIGIBLE,
) -> VerificationRecord:
    return VerificationRecord(
        id=pid,
        snapshot_sha256=sha,
        terms_match_snapshot=terms,
        live_round_open=live_o,
        reviewer_decision=dec,
        verified_at=verified_at or datetime.now(UTC),
        official_round_terms_url="https://ex/round",
        captured_source_url="https://ex",
        raw_evidence_path="tmp.raw",
        jurisdiction_status=jurisdiction,
    )


def _mk_ev(pid: str, cap: float = 80.0, readiness=EVReadiness.READY) -> EVInputsRecord:
    inp = EVScenarioInputs(
        p_eligibility=0.8,
        p_distribution=0.7,
        reward_qty=100.0,
        realizable_price=0.5,
        liquidity_vesting_haircut=0.6,
        base_yield=10.0,
        gas_bridge_fees=2.0,
        capital=cap,
        days=10.0,
        benchmark_apy=0.05,
        expected_loss_reserve=1.0,
        manual_hours=1.0,
        hourly_rate=10.0,
        reward_announced=True,
    )
    # good provenance for all material (for positive path)
    prov = dict.fromkeys(
        [
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
        ],
        "evidence from official terms snapshot at verification time",
    )
    return EVInputsRecord(
        id=pid,
        inputs=inp,
        reward_type=RewardType.ANNOUNCED_FIXED_TOKEN,
        readiness=readiness,
        provenance=prov,
    )


def test_verification_must_bind_hash_and_id(tmp_path):
    rec = _mk_rec("p1")
    # real bytes for ensure, actual sha for cap; ver uses different to trigger bind fail
    rawf = tmp_path / "bind.raw"
    rawf.write_bytes(b"bind-test-bytes-for-sha-mismatch")
    act_sha = _compute_sha256(rawf.read_bytes())
    cap = CaptureRecord(
        id="p1",
        snapshot_sha256=act_sha,
        captured_at=datetime.now(UTC),
        raw_path=str(rawf),
        source_url="ex",
    )
    ver = _mk_ver("p1", "WRONG")
    # no patch on ensure; will pass bytes, fail on ver sha != cap sha
    res = check_actionability(rec, {"p1": cap}, {"p1": ver}, {"p1": _mk_ev("p1")})
    assert res.status == Actionability.BLOCKED_UNVERIFIED
    assert "hash" in res.reason.lower() or "match" in res.reason.lower()


def test_positive_actionability_path_with_approved_verif_real_capital_ready_ev(tmp_path):
    """Full binding path with real bytes + actual computed SHA, no mock for ensure (blocker #7)."""
    rec = _mk_rec("p1")
    # real bytes + actual sha
    raw_file = tmp_path / "real-round-terms.raw"
    content = b"official-round-terms-page-content-for-test-evidence-0123456789abcdef"
    raw_file.write_bytes(content)
    actual_sha = _compute_sha256(content)
    cap = CaptureRecord(
        id="p1",
        snapshot_sha256=actual_sha,
        captured_at=datetime.now(UTC),
        raw_path=str(raw_file),
        source_url="https://ex/round",
    )
    ver = VerificationRecord(
        id="p1",
        snapshot_sha256=actual_sha,
        terms_match_snapshot=True,
        live_round_open=True,
        reviewer_decision=ReviewerDecision.APPROVED,
        verified_at=datetime.now(UTC),
        raw_evidence_path=str(raw_file),
        official_round_terms_url="https://ex/round",
        captured_source_url="https://ex/round",
        jurisdiction_status=JurisdictionStatus.ELIGIBLE,
    )
    ev = _mk_ev("p1", cap=80.0, readiness=EVReadiness.READY)
    # NO patch -- full real evidence + sha bind + ensure + audit + prov
    res = check_actionability(
        rec, {"p1": cap}, {"p1": ver}, {"p1": ev}, ledger=[{"id": "other", "usd": 10}]
    )
    assert res.status == Actionability.ACTIONABLE


def test_blocks_on_pending_reviewer_decision(tmp_path):
    rec = _mk_rec("p1")
    rawf = tmp_path / "pending.raw"
    rawf.write_bytes(b"pending-decision-test-bytes")
    act = _compute_sha256(rawf.read_bytes())
    cap = CaptureRecord(
        id="p1",
        snapshot_sha256=act,
        captured_at=datetime.now(UTC),
        raw_path=str(rawf),
        source_url="ex",
    )
    ver = _mk_ver("p1", act, dec=ReviewerDecision.PENDING)
    ev = _mk_ev("p1")
    res = check_actionability(rec, {"p1": cap}, {"p1": ver}, {"p1": ev})
    assert res.status == Actionability.BLOCKED_UNVERIFIED
    assert "PENDING" in res.reason


def test_ev_unready_blocks_promotion(tmp_path):
    rec = _mk_rec("p1")
    rawf = tmp_path / "unready.raw"
    rawf.write_bytes(b"unready-ev-test-bytes")
    act = _compute_sha256(rawf.read_bytes())
    cap = CaptureRecord(
        id="p1",
        snapshot_sha256=act,
        captured_at=datetime.now(UTC),
        raw_path=str(rawf),
        source_url="https://ex/round",
    )
    ver = VerificationRecord(
        id="p1",
        snapshot_sha256=act,
        terms_match_snapshot=True,
        live_round_open=True,
        reviewer_decision=ReviewerDecision.APPROVED,
        verified_at=datetime.now(UTC),
        raw_evidence_path=str(rawf),
        official_round_terms_url="https://ex/round",
        captured_source_url="https://ex/round",
        jurisdiction_status=JurisdictionStatus.ELIGIBLE,
    )
    ev = _mk_ev("p1", readiness=EVReadiness.UNREADY)
    res = check_actionability(rec, {"p1": cap}, {"p1": ver}, {"p1": ev})
    assert res.status == Actionability.BLOCKED_PROMOTION
    assert "not READY with full acceptable provenance" in res.reason


def test_missing_capital_blocks_caps(tmp_path):
    rec = _mk_rec("p1")
    rawf = tmp_path / "cap.raw"
    rawf.write_bytes(b"cap0-test-bytes")
    act = _compute_sha256(rawf.read_bytes())
    cap = CaptureRecord(
        id="p1",
        snapshot_sha256=act,
        captured_at=datetime.now(UTC),
        raw_path=str(rawf),
        source_url="https://ex/round",
    )
    ver = VerificationRecord(
        id="p1",
        snapshot_sha256=act,
        terms_match_snapshot=True,
        live_round_open=True,
        reviewer_decision=ReviewerDecision.APPROVED,
        verified_at=datetime.now(UTC),
        raw_evidence_path=str(rawf),
        official_round_terms_url="https://ex/round",
        captured_source_url="https://ex/round",
        jurisdiction_status=JurisdictionStatus.ELIGIBLE,
    )
    ev = _mk_ev("p1", cap=0.0, readiness=EVReadiness.READY)
    res = check_actionability(rec, {"p1": cap}, {"p1": ver}, {"p1": ev})
    assert res.status == Actionability.BLOCKED_CAPS
    assert "capital" in res.reason.lower()


def test_load_ledger_supplied_missing_raises():
    with pytest.raises(Exception) as exc:  # ValidationError
        load_ledger("/tmp/does-not-exist-for-test-xyz.yaml")
    assert "does not exist" in str(exc.value) or "missing" in str(exc.value).lower()


def test_verif_supersedes_registry_unverified(tmp_path):
    rec = _mk_rec("p1", live="UNVERIFIED")  # stale registry
    rawf = tmp_path / "super.raw"
    rawf.write_bytes(b"supersede-registry-test")
    act = _compute_sha256(rawf.read_bytes())
    cap = CaptureRecord(
        id="p1",
        snapshot_sha256=act,
        captured_at=datetime.now(UTC),
        raw_path=str(rawf),
        source_url="https://ex/round",
    )
    ver = VerificationRecord(
        id="p1",
        snapshot_sha256=act,
        terms_match_snapshot=True,
        live_round_open=True,
        reviewer_decision=ReviewerDecision.APPROVED,
        verified_at=datetime.now(UTC),
        raw_evidence_path=str(rawf),
        official_round_terms_url="https://ex/round",
        captured_source_url="https://ex/round",
        jurisdiction_status=JurisdictionStatus.ELIGIBLE,
    )
    ev = _mk_ev("p1")
    res = check_actionability(rec, {"p1": cap}, {"p1": ver}, {"p1": ev})
    assert res.status == Actionability.ACTIONABLE  # supersedes


# --- New enforcement tests for NaN/Inf, READY provenance, verif audit fields (re-review blockers) ---


def test_ev_inputs_rejects_nan_and_inf():
    base = {
        "p_eligibility": 0.8,
        "p_distribution": 0.7,
        "reward_qty": 100.0,
        "realizable_price": 0.5,
        "liquidity_vesting_haircut": 0.6,
        "base_yield": 10.0,
        "gas_bridge_fees": 2.0,
        "days": 10.0,
        "benchmark_apy": 0.05,
        "expected_loss_reserve": 1.0,
        "manual_hours": 1.0,
        "hourly_rate": 10.0,
        "reward_announced": True,
    }
    with pytest.raises(ValidationError):
        EVScenarioInputs(capital=float("nan"), **base)
    with pytest.raises(ValidationError):
        EVScenarioInputs(capital=float("inf"), **base)
    with pytest.raises(ValidationError):
        EVScenarioInputs(capital=float("-inf"), **base)


def test_validate_caps_rejects_nan_inf():
    caps = PilotCaps()
    with pytest.raises(CapsExceeded):
        validate_caps([{"id": "p1", "usd": 100}], {"id": "p2", "usd": float("nan")}, caps)
    with pytest.raises(CapsExceeded):
        validate_caps([{"id": "p1", "usd": 100}], {"id": "p2", "usd": float("inf")}, caps)


def test_load_ledger_rejects_nan_inf_negative_gas_hours():
    import tempfile

    import yaml

    bad = [{"id": "p", "usd": 10, "gas_usd": float("nan"), "realized_usd": 0, "hours": 1}]
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "bad.yaml"
        p.write_text(yaml.safe_dump(bad))
        with pytest.raises(ValidationError) as exc:
            load_ledger(p)
        assert "NaN or Inf" in str(exc.value) or "not numeric" in str(exc.value)
    bad2 = [{"id": "p", "usd": 10, "gas_usd": -1, "realized_usd": 0, "hours": 1}]
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "bad2.yaml"
        p.write_text(yaml.safe_dump(bad2))
        with pytest.raises(ValidationError) as exc:
            load_ledger(p)
        assert "must be >= 0" in str(exc.value)


def test_ready_ev_requires_full_acceptable_provenance():
    rec = _mk_rec("p1")
    rawf = Path(tempfile.mkdtemp()) / "prov.raw"
    rawf.write_bytes(b"prov-test")
    act = _compute_sha256(rawf.read_bytes())
    cap = CaptureRecord(
        id="p1",
        snapshot_sha256=act,
        captured_at=datetime.now(UTC),
        raw_path=str(rawf),
        source_url="https://ex/round",
    )
    ver = VerificationRecord(
        id="p1",
        snapshot_sha256=act,
        terms_match_snapshot=True,
        live_round_open=True,
        reviewer_decision=ReviewerDecision.APPROVED,
        verified_at=datetime.now(UTC),
        raw_evidence_path=str(rawf),
        official_round_terms_url="https://ex/round",
        captured_source_url="https://ex/round",
        jurisdiction_status=JurisdictionStatus.ELIGIBLE,
    )
    # bad prov (minimal)
    ev_bad = EVInputsRecord(
        id="p1",
        inputs=_mk_ev("p1", readiness=EVReadiness.READY).inputs,
        reward_type=RewardType.ANNOUNCED_FIXED_TOKEN,
        readiness=EVReadiness.READY,
        provenance={"capital": "test"},
    )
    res = check_actionability(rec, {"p1": cap}, {"p1": ver}, {"p1": ev_bad})
    assert res.status == Actionability.BLOCKED_PROMOTION
    assert "provenance" in res.reason.lower()


def test_verif_audit_fields_enforced():
    rec = _mk_rec("p1")
    rawf = Path(tempfile.mkdtemp()) / "audit.raw"
    rawf.write_bytes(b"audit")
    act = _compute_sha256(rawf.read_bytes())
    cap = CaptureRecord(
        id="p1",
        snapshot_sha256=act,
        captured_at=datetime.now(UTC),
        raw_path=str(rawf),
        source_url="https://ex/round",
    )
    # missing url
    ver_bad = VerificationRecord(
        id="p1",
        snapshot_sha256=act,
        terms_match_snapshot=True,
        live_round_open=True,
        reviewer_decision=ReviewerDecision.APPROVED,
        verified_at=datetime.now(UTC),
        raw_evidence_path=str(rawf),
        official_round_terms_url=None,
        captured_source_url="https://ex/round",
        jurisdiction_status=JurisdictionStatus.ELIGIBLE,
    )
    ev = _mk_ev("p1", readiness=EVReadiness.READY)
    res = check_actionability(rec, {"p1": cap}, {"p1": ver_bad}, {"p1": ev})
    assert res.status == Actionability.BLOCKED_UNVERIFIED
    assert (
        "audit" in res.reason.lower()
        or "missing" in res.reason.lower()
        or "url" in res.reason.lower()
    )
    # unknown juris
    ver_unk = VerificationRecord(
        id="p1",
        snapshot_sha256=act,
        terms_match_snapshot=True,
        live_round_open=True,
        reviewer_decision=ReviewerDecision.APPROVED,
        verified_at=datetime.now(UTC),
        raw_evidence_path=str(rawf),
        official_round_terms_url="https://ex/round",
        captured_source_url="https://ex/round",
        jurisdiction_status=JurisdictionStatus.UNKNOWN,
    )
    res2 = check_actionability(rec, {"p1": cap}, {"p1": ver_unk}, {"p1": ev})
    assert res2.status == Actionability.BLOCKED_UNVERIFIED
    assert "jurisdiction" in res2.reason.lower()


# --- Financial gate negative tests (re-review) ---


def test_pilot_caps_rejects_invalid_max_concurrent():
    from tools.incentive_ops.types import ValidationError

    with pytest.raises(ValidationError):
        PilotCaps(max_concurrent=float("nan"))
    with pytest.raises(ValidationError):
        PilotCaps(max_concurrent=-1)
    # bool not int
    with pytest.raises(ValidationError):
        PilotCaps(max_concurrent=True)  # True is 1 but type check


def test_validate_caps_rejects_negative_ledger_and_candidate():
    caps = PilotCaps()
    # negative ledger
    with pytest.raises(CapsExceeded):
        validate_caps([{"id": "p1", "usd": -1000}], {"id": "p2", "usd": 250}, caps)
    # negative candidate
    with pytest.raises(CapsExceeded):
        validate_caps([{"id": "p1", "usd": 100}], {"id": "p2", "usd": -1}, caps)


def test_actionability_with_real_ledger_triggers_blocked_caps(tmp_path):
    """Positive path: real ledger commitments cause BLOCKED_CAPS via CLI/main path."""
    rec = _mk_rec("p1")
    rawf = tmp_path / "cli_ledger.raw"
    rawf.write_bytes(b"cli-ledger-test")
    act = _compute_sha256(rawf.read_bytes())
    cap = CaptureRecord(
        id="p1",
        snapshot_sha256=act,
        captured_at=datetime.now(UTC),
        raw_path=str(rawf),
        source_url="https://ex/round",
    )
    ver = VerificationRecord(
        id="p1",
        snapshot_sha256=act,
        terms_match_snapshot=True,
        live_round_open=True,
        reviewer_decision=ReviewerDecision.APPROVED,
        verified_at=datetime.now(UTC),
        raw_evidence_path=str(rawf),
        official_round_terms_url="https://ex/round",
        captured_source_url="https://ex/round",
        jurisdiction_status=JurisdictionStatus.ELIGIBLE,
    )
    ev = _mk_ev("p1", cap=100.0, readiness=EVReadiness.READY)
    # large existing ledger that +100 would exceed 1000 total or concurrent
    big_ledger = [{"id": f"o{i}", "usd": 400} for i in range(3)]  # 1200 >1000
    res = check_actionability(rec, {"p1": cap}, {"p1": ver}, {"p1": ev}, ledger=big_ledger)
    assert res.status == Actionability.BLOCKED_CAPS
    assert "total" in (res.reason or "").lower() or "caps" in (res.reason or "").lower()
