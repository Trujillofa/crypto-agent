"""Tests for Phase-0 baseline orchestrator (mocked HTTP, no real network)."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml
from click.testing import CliRunner

from tools.incentive_ops import baseline as blmod
from tools.incentive_ops.baseline import (
    BaselineError,
    baseline_close,
    baseline_start,
    baseline_status,
    baseline_tick,
    capture_with_dedup,
    is_active_research,
    split_universe,
)
from tools.incentive_ops.capture import load_verifications, write_verification_sidecar
from tools.incentive_ops.cli import cli
from tools.incentive_ops.registry import load_registry
from tools.incentive_ops.types import (
    Actionability,
    ActionabilityResult,
    JurisdictionStatus,
    ReviewerDecision,
    VerificationRecord,
)

FIXED_NOW = datetime(2026, 6, 27, 12, 0, 0, tzinfo=UTC)


@pytest.fixture
def baseline_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Path]:
    """Isolate runs/captures/snapshots/verifications under tmp_path."""
    repo = tmp_path / "repo"
    repo.mkdir()
    reg_src = Path("research/a1-incentive-farming/starter-registry-v0.yaml")
    handoff_src = Path("docs/specs/a1-phase0-tooling-handoff-v0.md")
    spec_src = Path("docs/specs/a1-incentive-farming-pilot-v0.md")

    reg_dst = repo / reg_src
    reg_dst.parent.mkdir(parents=True, exist_ok=True)
    reg_dst.write_text(reg_src.read_text(encoding="utf-8"), encoding="utf-8")
    (repo / handoff_src).parent.mkdir(parents=True, exist_ok=True)
    (repo / handoff_src).write_text(handoff_src.read_text(encoding="utf-8"), encoding="utf-8")
    (repo / spec_src).parent.mkdir(parents=True, exist_ok=True)
    (repo / spec_src).write_text(spec_src.read_text(encoding="utf-8"), encoding="utf-8")

    runs = repo / "research/a1-incentive-farming/runs"
    captures = repo / "research/a1-incentive-farming/captures"
    snapshots = repo / "research/a1-incentive-farming/snapshots"
    verifications = repo / "research/a1-incentive-farming/verifications"
    ev_inputs = repo / "research/a1-incentive-farming/ev_inputs"
    for d in (runs, captures, snapshots, verifications, ev_inputs):
        d.mkdir(parents=True, exist_ok=True)

    verif_src = Path("research/a1-incentive-farming/verifications")
    for yf in verif_src.glob("*.yaml"):
        (verifications / yf.name).write_text(yf.read_text(encoding="utf-8"), encoding="utf-8")

    monkeypatch.chdir(repo)
    monkeypatch.setattr(blmod, "RUNS_ROOT", runs)
    monkeypatch.setattr(blmod, "REGISTRY_PATH", str(reg_dst))
    monkeypatch.setattr(blmod, "HANDOFF_PATH", str(handoff_src))
    monkeypatch.setattr(blmod, "SPEC_PATH", str(spec_src))
    monkeypatch.setattr(blmod, "SNAPSHOTS_ROOT", snapshots)

    from tools.incentive_ops import capture as capmod

    monkeypatch.setattr(capmod, "CAPTURES_ROOT", captures)
    monkeypatch.setattr(capmod, "SNAPSHOTS_ROOT", snapshots)
    monkeypatch.setattr(capmod, "VERIFICATIONS_ROOT", verifications)
    monkeypatch.setattr(capmod, "EV_INPUTS_ROOT", ev_inputs)

    monkeypatch.setattr(blmod, "_get_git_sha", lambda: "abc123deadbeef")

    return {
        "repo": repo,
        "runs": runs,
        "captures": captures,
        "snapshots": snapshots,
        "verifications": verifications,
        "registry": reg_dst,
    }


def _mock_fetch(raw_by_url: dict[str, bytes]):
    def _fetch(url: str) -> bytes:
        if url not in raw_by_url:
            raise blmod.CaptureError(f"no mock for {url}")
        return raw_by_url[url]

    return _fetch


def _all_records():
    recs, _ = load_registry(warn=False)
    return recs


def test_split_universe_active_vs_control():
    recs = _all_records()
    split = split_universe(recs)
    assert len(split.frozen_program_ids) == 17
    assert set(split.frozen_program_ids) == set(split.active_research_program_ids) | set(
        split.control_program_ids
    )
    # archetype / synthetic / wrong tier excluded from active fetch
    assert "testnet-incentive-archetype" in split.control_program_ids
    assert "fixed-threshold-airdrop-archetype" in split.control_program_ids
    assert "undisclosed-sybil-points-farm-archetype" in split.control_program_ids
    assert "binance-launchpool" in split.control_program_ids
    assert "coinlist-token-sale" in split.active_research_program_ids
    assert "layer3-quests" in split.active_research_program_ids
    assert "coinbase-learning-rewards" in split.control_program_ids
    assert len(split.active_research_program_ids) == 6
    assert len(split.control_program_ids) == 11


@patch("tools.incentive_ops.baseline.fetch_raw")
def test_start_freezes_exact_utc_window(mock_fetch, baseline_env):
    recs = _all_records()
    raw = {r.official_source_url: b"<html>terms</html>" for r in recs if is_active_research(r)}
    mock_fetch.side_effect = lambda url: raw[url]

    manifest = baseline_start(duration_days=14, now=FIXED_NOW)

    assert manifest["status"] == "RUNNING"
    assert manifest["started_at_utc"] == "2026-06-27T12:00:00Z"
    assert manifest["planned_end_at_utc"] == "2026-07-11T12:00:00Z"
    assert manifest["duration_days"] == 14
    assert manifest["starting_git_sha"] == "abc123deadbeef"
    assert manifest["zero_capital"] is True
    assert manifest["wallets_used"] is False
    assert manifest["eligibility_lookups_used"] is False

    run_dir = baseline_env["runs"] / manifest["run_id"]
    assert (run_dir / "manifest.yaml").exists()
    assert (run_dir / "reports" / "day-0.md").exists()
    assert list((run_dir / "observations").glob("*.yaml"))


@patch("tools.incentive_ops.baseline.fetch_raw")
def test_second_start_refuses_while_running(mock_fetch, baseline_env):
    recs = _all_records()
    raw = {r.official_source_url: b"<html>terms</html>" for r in recs if is_active_research(r)}
    mock_fetch.side_effect = lambda url: raw[url]

    baseline_start(duration_days=14, now=FIXED_NOW)
    with pytest.raises(BaselineError, match="another RUNNING baseline"):
        baseline_start(duration_days=14, now=FIXED_NOW + timedelta(hours=1))


@patch("tools.incentive_ops.baseline._run_gates")
@patch("tools.incentive_ops.baseline.fetch_raw")
def test_start_failure_does_not_leave_running(mock_fetch, mock_gates, baseline_env):
    recs = _all_records()
    raw = {r.official_source_url: b"<html>terms</html>" for r in recs if is_active_research(r)}
    mock_fetch.side_effect = lambda url: raw[url]
    mock_gates.side_effect = BaselineError("simulated gate failure")

    with pytest.raises(BaselineError, match="simulated gate failure"):
        baseline_start(duration_days=14, now=FIXED_NOW)

    manifests = list(baseline_env["runs"].glob("*/manifest.yaml"))
    assert len(manifests) == 1
    data = yaml.safe_load(manifests[0].read_text(encoding="utf-8"))
    assert data["status"] == "ABORTED"
    assert data["status"] != "RUNNING"


def test_identical_hash_deduplicates_raw_storage(baseline_env):
    recs = _all_records()
    rec = next(r for r in recs if r.id == "coinlist-token-sale")
    body = b"<html>same content</html>"
    sha = hashlib.sha256(body).hexdigest()

    with patch("tools.incentive_ops.baseline.fetch_raw", return_value=body):
        cap1, _ = capture_with_dedup(rec, FIXED_NOW)
        cap2, _ = capture_with_dedup(rec, FIXED_NOW + timedelta(hours=1))

    assert cap1.snapshot_sha256 == cap2.snapshot_sha256 == sha
    assert cap1.raw_path == cap2.raw_path
    raw_files = list((baseline_env["snapshots"] / rec.id).glob("*.raw"))
    assert len(raw_files) == 1


def test_changed_hash_creates_new_immutable_artifact(baseline_env):
    recs = _all_records()
    rec = next(r for r in recs if r.id == "coinlist-token-sale")

    with patch("tools.incentive_ops.baseline.fetch_raw", return_value=b"v1"):
        cap1, _ = capture_with_dedup(rec, FIXED_NOW)
    with patch("tools.incentive_ops.baseline.fetch_raw", return_value=b"v2-different"):
        cap2, changed = capture_with_dedup(rec, FIXED_NOW + timedelta(hours=1), prior_cap=cap1)

    assert changed is True
    assert cap1.snapshot_sha256 != cap2.snapshot_sha256
    assert cap1.raw_path != cap2.raw_path
    raw_files = list((baseline_env["snapshots"] / rec.id).glob("*.raw"))
    assert len(raw_files) == 2


def test_verification_sidecar_pending_binding(baseline_env):
    from tools.incentive_ops.baseline import _make_pending_verification
    from tools.incentive_ops.capture import write_verification_sidecar

    recs = _all_records()
    rec = next(r for r in recs if r.id == "layer3-quests")

    with patch("tools.incentive_ops.baseline.fetch_raw", return_value=b"<html>layer3</html>"):
        cap, _ = capture_with_dedup(rec, FIXED_NOW)
        write_verification_sidecar(_make_pending_verification(cap))
    with patch("tools.incentive_ops.baseline.fetch_raw", return_value=b"<html>layer3-v2</html>"):
        cap2, _ = capture_with_dedup(rec, FIXED_NOW + timedelta(hours=2), prior_cap=cap)
        write_verification_sidecar(_make_pending_verification(cap2))

    vers = load_verifications(str(baseline_env["verifications"]))
    ver = vers[rec.id]
    assert ver.snapshot_sha256 == cap2.snapshot_sha256
    assert ver.terms_match_snapshot is False
    assert ver.live_round_open is False
    assert ver.reviewer_decision == ReviewerDecision.PENDING
    assert str(ver.jurisdiction_status) == "UNKNOWN"
    assert ver.verified_at is None
    assert ver.eligibility_open is None


@patch("tools.incentive_ops.baseline.fetch_raw")
def test_archetype_control_records_not_fetched(mock_fetch, baseline_env):
    recs = _all_records()
    active = [r for r in recs if is_active_research(r)]
    raw = {r.official_source_url: b"<html>x</html>" for r in active}
    mock_fetch.side_effect = lambda url: raw[url]

    with patch(
        "tools.incentive_ops.baseline.capture_with_dedup",
        wraps=capture_with_dedup,
    ) as mock_cap:
        baseline_start(duration_days=14, now=FIXED_NOW)
        captured_ids = {call.args[0].id for call in mock_cap.call_args_list}

    control_ids = {
        "testnet-incentive-archetype",
        "fixed-threshold-airdrop-archetype",
        "binance-launchpool",
        "undisclosed-sybil-points-farm-archetype",
    }
    assert captured_ids == set(split_universe(recs).active_research_program_ids)
    assert captured_ids.isdisjoint(control_ids)


def test_tick_without_start_fails(baseline_env):
    with pytest.raises(BaselineError, match="no RUNNING baseline"):
        baseline_tick(now=FIXED_NOW)


@patch("tools.incentive_ops.baseline.fetch_raw")
def test_tick_cannot_alter_frozen_universe(mock_fetch, baseline_env, monkeypatch):
    recs = _all_records()
    raw = {r.official_source_url: b"<html>x</html>" for r in recs if is_active_research(r)}
    mock_fetch.side_effect = lambda url: raw[url]

    baseline_start(duration_days=14, now=FIXED_NOW)

    # mutate registry on disk to simulate rule change
    reg_path = baseline_env["registry"]
    data = yaml.safe_load(reg_path.read_text(encoding="utf-8"))
    data["programs"][0]["classification"] = "REJECT"
    reg_path.write_text(yaml.safe_dump(data), encoding="utf-8")

    with pytest.raises(BaselineError, match="changed since baseline start"):
        baseline_tick(now=FIXED_NOW + timedelta(days=1))


@patch("tools.incentive_ops.baseline.check_all_actionability")
@patch("tools.incentive_ops.baseline.fetch_raw")
def test_actionable_result_aborts_start(mock_fetch, mock_action, baseline_env):
    recs = _all_records()
    raw = {r.official_source_url: b"<html>x</html>" for r in recs if is_active_research(r)}
    mock_fetch.side_effect = lambda url: raw[url]
    mock_action.return_value = {
        "evil": ActionabilityResult(status=Actionability.ACTIONABLE, reason="test"),
    }

    with pytest.raises(BaselineError):
        baseline_start(duration_days=14, now=FIXED_NOW)


@patch("tools.incentive_ops.baseline.fetch_raw")
def test_close_before_end_refuses(mock_fetch, baseline_env):
    recs = _all_records()
    raw = {r.official_source_url: b"<html>x</html>" for r in recs if is_active_research(r)}
    mock_fetch.side_effect = lambda url: raw[url]

    baseline_start(duration_days=14, now=FIXED_NOW)
    with pytest.raises(BaselineError, match="planned end"):
        baseline_close(abort=False, now=FIXED_NOW + timedelta(days=1))


def test_no_wallet_eligibility_capital_paths():
    """Baseline module must not import eligibility or touch capital paths."""
    import ast
    from pathlib import Path

    tree = ast.parse(Path("tools/incentive_ops/baseline.py").read_text(encoding="utf-8"))
    imported = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
        for alias in node.names
    }
    imported_modules = {
        node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom) and node.module
    }
    assert "eligibility" not in imported_modules
    assert "tools.incentive_ops.eligibility" not in imported_modules
    assert "fetch_eligibility" not in imported
    assert "validate_caps" not in imported


@patch("tools.incentive_ops.baseline.fetch_raw")
def test_baseline_status_reports_counts(mock_fetch, baseline_env):
    recs = _all_records()
    raw = {r.official_source_url: b"<html>x</html>" for r in recs if is_active_research(r)}
    mock_fetch.side_effect = lambda url: raw[url]

    baseline_start(duration_days=14, now=FIXED_NOW)
    st = baseline_status(now=FIXED_NOW + timedelta(days=1))

    assert st["elapsed_seconds"] == 86400
    assert st["remaining_seconds"] == 13 * 86400
    assert st["actionable_count"] == 0
    assert st["invariant_violations"] == []


@patch("tools.incentive_ops.baseline.fetch_raw")
def test_baseline_close_abort(mock_fetch, baseline_env):
    recs = _all_records()
    raw = {r.official_source_url: b"<html>x</html>" for r in recs if is_active_research(r)}
    mock_fetch.side_effect = lambda url: raw[url]

    manifest = baseline_start(duration_days=14, now=FIXED_NOW)
    result = baseline_close(abort=True, now=FIXED_NOW + timedelta(days=1))
    assert result["manifest"]["status"] == "ABORTED"
    run_dir = baseline_env["runs"] / manifest["run_id"]
    assert (run_dir / "reports" / "final.md").exists()


@patch("tools.incentive_ops.baseline.fetch_raw")
def test_cli_smoke_start_tick_status_close(mock_fetch, baseline_env):
    recs = _all_records()
    raw = {r.official_source_url: b"<html>x</html>" for r in recs if is_active_research(r)}
    mock_fetch.side_effect = lambda url: raw[url]

    runner = CliRunner()
    r1 = runner.invoke(cli, ["baseline", "start", "--days", "14"])
    assert r1.exit_code == 0, r1.output
    assert "Baseline started" in r1.output

    r2 = runner.invoke(cli, ["baseline", "tick"])
    assert r2.exit_code == 0, r2.output
    assert "Tick OK" in r2.output

    r3 = runner.invoke(cli, ["baseline", "status"])
    assert r3.exit_code == 0, r3.output
    assert "baseline status" in r3.output

    r4 = runner.invoke(cli, ["baseline", "close", "--abort"])
    assert r4.exit_code == 0, r4.output
    assert "ABORTED" in r4.output


@patch("tools.incentive_ops.baseline.fetch_raw")
def test_start_aborts_when_any_active_capture_fails(mock_fetch, baseline_env):
    recs = _all_records()
    active = [r for r in recs if is_active_research(r)]
    raw = {r.official_source_url: b"<html>ok</html>" for r in active}
    fail_url = active[0].official_source_url

    def _side_effect(url: str) -> bytes:
        if url == fail_url:
            raise blmod.CaptureError("simulated fetch failure")
        return raw[url]

    mock_fetch.side_effect = _side_effect

    with pytest.raises(BaselineError, match="capture failures"):
        baseline_start(duration_days=14, now=FIXED_NOW)

    manifests = list(baseline_env["runs"].glob("*/manifest.yaml"))
    data = yaml.safe_load(manifests[0].read_text(encoding="utf-8"))
    assert data["status"] == "ABORTED"


@patch("tools.incentive_ops.baseline.fetch_raw")
def test_approved_verification_sidecar_reports_invariant_violation(mock_fetch, baseline_env):
    recs = _all_records()
    raw = {r.official_source_url: b"<html>x</html>" for r in recs if is_active_research(r)}
    mock_fetch.side_effect = lambda url: raw[url]

    baseline_start(duration_days=14, now=FIXED_NOW)

    write_verification_sidecar(
        VerificationRecord(
            id="coinlist-token-sale",
            snapshot_sha256="deadbeef",
            terms_match_snapshot=True,
            live_round_open=True,
            reviewer_decision=ReviewerDecision.APPROVED,
            verified_at=FIXED_NOW,
            raw_evidence_path="fake",
            official_round_terms_url="https://coinlist.co/token-launches",
            captured_source_url="https://coinlist.co/token-launches",
            jurisdiction_status=JurisdictionStatus.ELIGIBLE,
        )
    )

    st = baseline_status(now=FIXED_NOW + timedelta(hours=1))
    assert any("reviewer_decision=APPROVED" in v for v in st["invariant_violations"])


@patch("tools.incentive_ops.baseline.fetch_raw")
def test_tick_rejects_handoff_and_spec_but_tolerates_git_change(
    mock_fetch, baseline_env, monkeypatch
):
    recs = _all_records()
    raw = {r.official_source_url: b"<html>x</html>" for r in recs if is_active_research(r)}
    mock_fetch.side_effect = lambda url: raw[url]

    handoff = baseline_env["repo"] / blmod.HANDOFF_PATH
    spec = baseline_env["repo"] / blmod.SPEC_PATH
    handoff_original = handoff.read_text(encoding="utf-8")
    spec_original = spec.read_text(encoding="utf-8")

    baseline_start(duration_days=14, now=FIXED_NOW)

    handoff.write_text(handoff_original + "\n# mutated\n", encoding="utf-8")
    with pytest.raises(BaselineError, match="handoff content changed"):
        baseline_tick(now=FIXED_NOW + timedelta(hours=1))

    handoff.write_text(handoff_original, encoding="utf-8")
    spec.write_text(spec_original + "\n# mutated\n", encoding="utf-8")
    with pytest.raises(BaselineError, match="spec content changed"):
        baseline_tick(now=FIXED_NOW + timedelta(hours=2))

    # An unrelated repo commit (different HEAD) must NOT break the baseline: the
    # gate-logic surface is frozen by content hash, not the whole-repo git SHA.
    spec.write_text(spec_original, encoding="utf-8")
    monkeypatch.setattr(blmod, "_get_git_sha", lambda: "different-sha")
    result = baseline_tick(now=FIXED_NOW + timedelta(hours=3))
    assert result["observation"]


@patch("tools.incentive_ops.baseline.fetch_raw")
def test_tick_rejects_tooling_and_allowlist_changes(
    mock_fetch, baseline_env, tmp_path, monkeypatch
):
    recs = _all_records()
    raw = {r.official_source_url: b"<html>x</html>" for r in recs if is_active_research(r)}
    mock_fetch.side_effect = lambda url: raw[url]

    # Point the frozen tooling/allowlist surface at controllable temp artifacts.
    tool_dir = tmp_path / "tooling"
    tool_dir.mkdir()
    mod = tool_dir / "mod.py"
    mod.write_text("RULES = 1\n", encoding="utf-8")
    allowlist = tmp_path / "endpoint_allowlist.yaml"
    allowlist.write_text("hosts: []\n", encoding="utf-8")
    monkeypatch.setattr(blmod, "TOOLING_DIR", tool_dir)
    monkeypatch.setattr(blmod, "ALLOWLIST_PATH", allowlist)

    baseline_start(duration_days=14, now=FIXED_NOW)

    # Mutating the gate-logic tooling aborts the run.
    mod.write_text("RULES = 2  # changed\n", encoding="utf-8")
    with pytest.raises(BaselineError, match="tooling content changed"):
        baseline_tick(now=FIXED_NOW + timedelta(hours=1))

    mod.write_text("RULES = 1\n", encoding="utf-8")
    allowlist.write_text("hosts: [evil.example]\n", encoding="utf-8")
    with pytest.raises(BaselineError, match="allowlist content changed"):
        baseline_tick(now=FIXED_NOW + timedelta(hours=2))


def test_same_second_raw_snapshots_do_not_collide(baseline_env):
    recs = _all_records()
    rec = next(r for r in recs if r.id == "coinlist-token-sale")

    with patch("tools.incentive_ops.baseline.fetch_raw", return_value=b"content-a"):
        cap_a, _ = capture_with_dedup(rec, FIXED_NOW)
    with patch("tools.incentive_ops.baseline.fetch_raw", return_value=b"content-b"):
        cap_b, _ = capture_with_dedup(rec, FIXED_NOW)

    assert cap_a.raw_path != cap_b.raw_path
    raw_files = list((baseline_env["snapshots"] / rec.id).glob("*.raw"))
    assert len(raw_files) == 2


@patch("tools.incentive_ops.baseline.fetch_raw")
def test_same_second_observations_do_not_collide(mock_fetch, baseline_env):
    from tools.incentive_ops.baseline import _write_observation

    recs = _all_records()
    raw = {r.official_source_url: b"<html>x</html>" for r in recs if is_active_research(r)}
    mock_fetch.side_effect = lambda url: raw[url]

    manifest = baseline_start(duration_days=14, now=FIXED_NOW)
    run_dir = baseline_env["runs"] / manifest["run_id"]
    manifest_data = yaml.safe_load((run_dir / "manifest.yaml").read_text(encoding="utf-8"))
    gate = {"classify_counts": {}, "actionability_counts": {}, "actionable_program_ids": []}
    cap = blmod.CaptureTickResult(successes=manifest_data["active_research_program_ids"])

    p1 = _write_observation(
        run_dir,
        kind="tick",
        capture_result=cap,
        gate_result=gate,
        manifest=manifest_data,
        now=FIXED_NOW,
    )
    cap2 = blmod.CaptureTickResult(successes=manifest_data["active_research_program_ids"][:-1])
    p2 = _write_observation(
        run_dir,
        kind="tick",
        capture_result=cap2,
        gate_result=gate,
        manifest=manifest_data,
        now=FIXED_NOW,
    )
    assert p1 != p2
    assert p1.exists() and p2.exists()


@patch("tools.incentive_ops.baseline.fetch_raw")
def test_start_refuses_when_lock_held(mock_fetch, baseline_env):
    import fcntl
    import os

    recs = _all_records()
    raw = {r.official_source_url: b"<html>x</html>" for r in recs if is_active_research(r)}
    mock_fetch.side_effect = lambda url: raw[url]

    baseline_env["runs"].mkdir(parents=True, exist_ok=True)
    lock_path = baseline_env["runs"] / ".baseline.lock"
    fd = os.open(lock_path, os.O_CREAT | os.O_RDWR)
    fcntl.flock(fd, fcntl.LOCK_EX)
    try:
        with pytest.raises(BaselineError, match="lock held"):
            baseline_start(duration_days=14, now=FIXED_NOW)
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


@patch("tools.incentive_ops.baseline.fetch_raw")
def test_day0_report_shows_running_status(mock_fetch, baseline_env):
    recs = _all_records()
    raw = {r.official_source_url: b"<html>x</html>" for r in recs if is_active_research(r)}
    mock_fetch.side_effect = lambda url: raw[url]

    manifest = baseline_start(duration_days=14, now=FIXED_NOW)
    report = (baseline_env["runs"] / manifest["run_id"] / "reports" / "day-0.md").read_text(
        encoding="utf-8"
    )
    assert "**Status:** RUNNING" in report


def test_reject_non_positive_days():
    with pytest.raises(BaselineError, match="duration_days must be positive"):
        baseline_start(duration_days=0, now=FIXED_NOW)

    runner = CliRunner()
    result = runner.invoke(cli, ["baseline", "start", "--days", "0"])
    assert result.exit_code == 1
    assert "must be positive" in result.output


def _approved_sidecar(pid: str = "coinlist-token-sale") -> VerificationRecord:
    return VerificationRecord(
        id=pid,
        snapshot_sha256="deadbeef",
        terms_match_snapshot=True,
        live_round_open=True,
        reviewer_decision=ReviewerDecision.APPROVED,
        verified_at=FIXED_NOW,
        raw_evidence_path="fake",
        official_round_terms_url="https://coinlist.co/token-launches",
        captured_source_url="https://coinlist.co/token-launches",
        jurisdiction_status=JurisdictionStatus.ELIGIBLE,
    )


def _read_reviewer_decision(verifications_dir: Path, pid: str) -> str:
    data = yaml.safe_load((verifications_dir / f"{pid}.yaml").read_text(encoding="utf-8"))
    return str(data["reviewer_decision"])


@patch("tools.incentive_ops.baseline.fetch_raw")
def test_tick_fails_on_approved_sidecar_without_overwriting(mock_fetch, baseline_env):
    recs = _all_records()
    raw = {r.official_source_url: b"<html>x</html>" for r in recs if is_active_research(r)}
    mock_fetch.side_effect = lambda url: raw[url]

    baseline_start(duration_days=14, now=FIXED_NOW)
    write_verification_sidecar(_approved_sidecar())

    with pytest.raises(BaselineError, match="invariant violations"):
        baseline_tick(now=FIXED_NOW + timedelta(hours=1))

    assert (
        _read_reviewer_decision(baseline_env["verifications"], "coinlist-token-sale") == "APPROVED"
    )


@patch("tools.incentive_ops.baseline.fetch_raw")
def test_close_fails_on_approved_sidecar_without_overwriting(mock_fetch, baseline_env):
    recs = _all_records()
    raw = {r.official_source_url: b"<html>x</html>" for r in recs if is_active_research(r)}
    mock_fetch.side_effect = lambda url: raw[url]

    baseline_start(duration_days=14, now=FIXED_NOW)
    write_verification_sidecar(_approved_sidecar())

    with pytest.raises(BaselineError, match="invariant violations"):
        baseline_close(abort=True, now=FIXED_NOW + timedelta(hours=1))

    assert (
        _read_reviewer_decision(baseline_env["verifications"], "coinlist-token-sale") == "APPROVED"
    )


@patch("tools.incentive_ops.baseline.fetch_raw")
def test_missing_frozen_verification_sidecar_is_invariant_violation(mock_fetch, baseline_env):
    recs = _all_records()
    raw = {r.official_source_url: b"<html>x</html>" for r in recs if is_active_research(r)}
    mock_fetch.side_effect = lambda url: raw[url]

    baseline_start(duration_days=14, now=FIXED_NOW)
    (baseline_env["verifications"] / "binance-launchpool.yaml").unlink()

    st = baseline_status(now=FIXED_NOW + timedelta(hours=1))
    assert any(
        "binance-launchpool: missing verification sidecar" in v for v in st["invariant_violations"]
    )

    with pytest.raises(BaselineError, match="missing verification sidecar"):
        baseline_tick(now=FIXED_NOW + timedelta(hours=2))


@patch("tools.incentive_ops.baseline.fetch_raw")
@patch("tools.incentive_ops.baseline._write_day0_report")
def test_day0_report_failure_never_persists_running(mock_report, mock_fetch, baseline_env):
    recs = _all_records()
    raw = {r.official_source_url: b"<html>x</html>" for r in recs if is_active_research(r)}
    mock_fetch.side_effect = lambda url: raw[url]
    mock_report.side_effect = OSError("simulated report write failure")

    with pytest.raises(BaselineError, match="simulated report write failure"):
        baseline_start(duration_days=14, now=FIXED_NOW)

    manifest_path = next(baseline_env["runs"].glob("*/manifest.yaml"))
    data = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    assert data["status"] != "RUNNING"
    assert data["status"] == "ABORTED"
