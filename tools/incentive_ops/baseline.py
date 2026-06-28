"""baseline.py — Phase-0 14-day baseline orchestrator (atomic start, no capital).

Freezes registry universe, captures active-research sources with hash dedup,
writes matching pending verification sidecars, and records observations.
Does not approve programs, deploy capital, or use wallets/eligibility lookups.
"""

from __future__ import annotations

import fcntl
import hashlib
import os
import subprocess
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import yaml

from src.utils.logger import get_logger

from .actionability import check_all_actionability
from .capture import (
    SNAPSHOTS_ROOT,
    _compute_sha256,
    _ensure_dirs,
    _is_unverified,
    _write_raw_snapshot,
    _write_sidecar,
    fetch_raw,
    load_captures,
    load_ev_inputs,
    load_verifications,
    write_verification_sidecar,
)
from .classify import check_classification
from .registry import load_registry, validate_registry
from .types import (
    Actionability,
    CaptureError,
    CaptureRecord,
    Classification,
    JurisdictionStatus,
    ProgramRecord,
    ReviewerDecision,
    VerificationRecord,
)

logger = get_logger(__name__)

RUNS_ROOT = Path("research/a1-incentive-farming/runs")
LOCK_PATH = RUNS_ROOT / ".baseline.lock"
REGISTRY_PATH = "research/a1-incentive-farming/starter-registry-v0.yaml"
HANDOFF_PATH = "docs/specs/a1-phase0-tooling-handoff-v0.md"
SPEC_PATH = "docs/specs/a1-incentive-farming-pilot-v0.md"


class BaselineError(RuntimeError):
    """Baseline orchestration failure (fail closed)."""

    pass


@dataclass
class UniverseSplit:
    frozen_program_ids: list[str]
    active_research_program_ids: list[str]
    control_program_ids: list[str]


@dataclass
class CaptureTickResult:
    successes: list[str] = field(default_factory=list)
    failures: dict[str, str] = field(default_factory=dict)
    hash_changed: list[str] = field(default_factory=list)
    hash_unchanged: list[str] = field(default_factory=list)
    prior_hashes: dict[str, str] = field(default_factory=dict)
    new_hashes: dict[str, str] = field(default_factory=dict)


def _sha256_file(path: Path | str) -> str:
    p = Path(path)
    return hashlib.sha256(p.read_bytes()).hexdigest()


def _get_git_sha() -> str:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
            timeout=5,
        )
        return out.stdout.strip()
    except Exception:
        return "unknown"


def _iso_utc(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_utc(s: str) -> datetime:
    return datetime.fromisoformat(str(s).replace("Z", "+00:00")).astimezone(UTC)


def _rel_path(path: Path) -> str:
    try:
        return str(path.relative_to(Path.cwd()))
    except ValueError:
        return str(path)


def _is_archetype_or_synthetic(verification_status: str) -> bool:
    v = verification_status.upper()
    return "ARCHETYPE" in v or "SYNTHETIC" in v


def is_active_research(rec: ProgramRecord) -> bool:
    """Records eligible for official-source refetch during baseline."""
    if rec.classification not in (Classification.IN_CORE, Classification.IN_CONDITIONAL):
        return False
    if _is_archetype_or_synthetic(rec.verification_status):
        return False
    if rec.live_round_status in ("NOT_LIVE_REFERENCE_ONLY", "NOT_A_REAL_PROGRAM"):
        return False
    return True


def split_universe(records: list[ProgramRecord]) -> UniverseSplit:
    frozen = sorted(r.id for r in records)
    active = sorted(r.id for r in records if is_active_research(r))
    control = sorted(set(frozen) - set(active))
    return UniverseSplit(
        frozen_program_ids=frozen,
        active_research_program_ids=active,
        control_program_ids=control,
    )


def find_running_manifest(runs_root: Path | None = None) -> Path | None:
    root = runs_root or RUNS_ROOT
    if not root.exists():
        return None
    for manifest_path in sorted(root.glob("*/manifest.yaml"), reverse=True):
        try:
            data = yaml.safe_load(manifest_path.read_text(encoding="utf-8")) or {}
            if data.get("status") == "RUNNING":
                return manifest_path
        except Exception:
            logger.warning("unreadable manifest %s", manifest_path)
    return None


def load_manifest(manifest_path: Path | str) -> dict[str, Any]:
    p = Path(manifest_path)
    data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise BaselineError(f"manifest must be mapping: {p}")
    return data


def save_manifest(manifest_path: Path | str, data: dict[str, Any]) -> None:
    """Atomically replace manifest via temp file (POSIX rename)."""
    p = Path(manifest_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".yaml.tmp")
    tmp.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    tmp.replace(p)


@contextmanager
def _baseline_start_lock() -> Iterator[None]:
    """Exclusive non-blocking lock for baseline start (prevents concurrent starts)."""
    RUNS_ROOT.mkdir(parents=True, exist_ok=True)
    fd = os.open(LOCK_PATH, os.O_CREAT | os.O_RDWR, 0o644)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as e:
            raise BaselineError("another baseline start in progress (lock held)") from e
        yield
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def _find_raw_by_hash(pid: str, sha: str) -> str | None:
    pid_dir = SNAPSHOTS_ROOT / pid
    if not pid_dir.exists():
        return None
    for raw_file in sorted(pid_dir.glob("*.raw")):
        if _compute_sha256(raw_file.read_bytes()) == sha:
            return _rel_path(raw_file)
    return None


def _make_pending_verification(cap: CaptureRecord) -> VerificationRecord:
    return VerificationRecord(
        id=cap.id,
        snapshot_sha256=cap.snapshot_sha256,
        terms_match_snapshot=False,
        live_round_open=False,
        reviewer_decision=ReviewerDecision.PENDING,
        verified_at=None,
        raw_evidence_path=cap.raw_path,
        official_round_terms_url=cap.source_url,
        captured_source_url=cap.source_url,
        jurisdiction_status=JurisdictionStatus.UNKNOWN,
        eligibility_open=None,
        eligibility_close=None,
        claim_date=None,
        vesting_end=None,
    )


def capture_with_dedup(
    rec: ProgramRecord,
    captured_at: datetime,
    *,
    prior_cap: CaptureRecord | None = None,
) -> tuple[CaptureRecord, bool]:
    """Fetch, deduplicate by raw sha, write capture sidecar. Returns (cap, hash_changed)."""
    if _is_unverified(rec.official_source_url):
        raise CaptureError(f"{rec.id}: official_source_url is UNVERIFIED")

    raw = fetch_raw(rec.official_source_url)
    sha = _compute_sha256(raw)
    prior_sha = prior_cap.snapshot_sha256 if prior_cap else None
    hash_changed = prior_sha is not None and prior_sha != sha

    existing_path = _find_raw_by_hash(rec.id, sha)
    if existing_path:
        raw_path = existing_path
    else:
        _ensure_dirs()
        raw_path = _rel_path(_write_raw_snapshot(rec.id, captured_at, raw))

    cap = CaptureRecord(
        id=rec.id,
        snapshot_sha256=sha,
        captured_at=captured_at,
        raw_path=raw_path,
        source_url=rec.official_source_url,
    )
    _write_sidecar(rec.id, cap)
    return cap, hash_changed if prior_sha else False


def _run_gates() -> dict[str, Any]:
    """Validate, classify --check, actionability. Abort if any ACTIONABLE."""
    validate_registry(REGISTRY_PATH)
    ok, _, mismatches, counts = check_classification(REGISTRY_PATH)
    if not ok:
        raise BaselineError(f"classify --check failed: {mismatches}")
    action_results = check_all_actionability()
    actionable = [
        pid for pid, res in action_results.items() if res.status == Actionability.ACTIONABLE
    ]
    if actionable:
        raise BaselineError(f"ACTIONABLE records detected: {actionable}")
    status_counts: dict[str, int] = {}
    for res in action_results.values():
        k = str(res.status)
        status_counts[k] = status_counts.get(k, 0) + 1
    return {
        "classify_counts": counts,
        "actionability_counts": status_counts,
        "actionable_program_ids": actionable,
    }


_INVARIANT_EXPECTATIONS: dict[str, bool] = {
    "zero_capital": True,
    "wallets_used": False,
    "eligibility_lookups_used": False,
    "reviewer_decisions_locked_pending": True,
    "rule_changes_allowed": False,
}


def _check_reviewer_decisions_locked(frozen_ids: list[str]) -> list[str]:
    """Inspect all frozen verification sidecars; each must exist and be PENDING."""
    vers = load_verifications()
    violations: list[str] = []
    for pid in frozen_ids:
        ver = vers.get(pid)
        if ver is None:
            violations.append(f"{pid}: missing verification sidecar")
            continue
        if ver.reviewer_decision != ReviewerDecision.PENDING:
            violations.append(f"{pid}: reviewer_decision={ver.reviewer_decision} (must be PENDING)")
    return violations


def _assert_invariants(manifest: dict[str, Any]) -> list[str]:
    violations: list[str] = []
    for key, expected in _INVARIANT_EXPECTATIONS.items():
        if manifest.get(key) is not expected:
            violations.append(f"{key}={manifest.get(key)} expected {expected}")
    if manifest.get("reviewer_decisions_locked_pending"):
        violations.extend(_check_reviewer_decisions_locked(manifest.get("frozen_program_ids", [])))
    return violations


def _require_invariants(manifest: dict[str, Any]) -> None:
    violations = _assert_invariants(manifest)
    if violations:
        raise BaselineError(f"invariant violations: {violations}")


def _require_all_active_captures(capture_result: CaptureTickResult, active_ids: list[str]) -> None:
    if capture_result.failures:
        raise BaselineError(f"capture failures for active research: {capture_result.failures}")
    missing = sorted(set(active_ids) - set(capture_result.successes))
    if missing:
        raise BaselineError(f"active research capture incomplete; missing: {missing}")


def _capture_active_subset(
    records: list[ProgramRecord],
    active_ids: list[str],
    captured_at: datetime,
) -> CaptureTickResult:
    result = CaptureTickResult()
    id_set = set(active_ids)
    prior = load_captures()
    existing_vers = load_verifications()
    for rec in records:
        if rec.id not in id_set:
            continue
        prior_cap = prior.get(rec.id)
        if prior_cap:
            result.prior_hashes[rec.id] = prior_cap.snapshot_sha256
        try:
            cap, changed = capture_with_dedup(rec, captured_at, prior_cap=prior_cap)
            result.successes.append(rec.id)
            result.new_hashes[rec.id] = cap.snapshot_sha256
            if prior_cap and cap.snapshot_sha256 == prior_cap.snapshot_sha256:
                result.hash_unchanged.append(rec.id)
            elif changed or prior_cap is None:
                result.hash_changed.append(rec.id)
            else:
                result.hash_unchanged.append(rec.id)
            existing = existing_vers.get(rec.id)
            if existing is None or existing.reviewer_decision == ReviewerDecision.PENDING:
                write_verification_sidecar(_make_pending_verification(cap))
        except (CaptureError, Exception) as e:
            result.failures[rec.id] = str(e)
            logger.warning("capture failed %s: %s", rec.id, e)
    return result


def _count_verification_states() -> dict[str, int]:
    vers = load_verifications()
    verified = sum(1 for v in vers.values() if v.reviewer_decision == ReviewerDecision.APPROVED)
    pending = sum(1 for v in vers.values() if v.reviewer_decision == ReviewerDecision.PENDING)
    return {"verified": verified, "unverified": len(vers) - verified, "pending": pending}


def _count_ev_readiness() -> dict[str, int]:
    evs = load_ev_inputs()
    ready = sum(1 for e in evs.values() if str(e.readiness) == "READY")
    return {"ready": ready, "unready": len(evs) - ready}


def _write_observation(
    run_dir: Path,
    *,
    kind: str,
    capture_result: CaptureTickResult,
    gate_result: dict[str, Any],
    manifest: dict[str, Any],
    now: datetime,
) -> Path:
    obs_dir = run_dir / "observations"
    obs_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "timestamp_utc": _iso_utc(now),
        "kind": kind,
        "captures": {
            "successes": capture_result.successes,
            "failures": capture_result.failures,
            "hash_changed": capture_result.hash_changed,
            "hash_unchanged": capture_result.hash_unchanged,
            "prior_hashes": capture_result.prior_hashes,
            "new_hashes": capture_result.new_hashes,
        },
        "verification_counts": _count_verification_states(),
        "ev_counts": _count_ev_readiness(),
        "actionability": gate_result,
        "invariants": {
            "zero_capital": manifest.get("zero_capital", True),
            "wallets_used": manifest.get("wallets_used", False),
            "eligibility_lookups_used": manifest.get("eligibility_lookups_used", False),
            "rule_changes_allowed": manifest.get("rule_changes_allowed", False),
        },
    }
    out = _collision_safe_observation_path(obs_dir, now, kind, payload)
    out.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    return out


def _collision_safe_observation_path(
    obs_dir: Path, now: datetime, kind: str, payload: dict[str, Any]
) -> Path:
    ts = now.strftime("%Y%m%dT%H%M%SZ")
    digest = hashlib.sha256(yaml.safe_dump(payload, sort_keys=True).encode()).hexdigest()[:12]
    base = f"{ts}_{kind}_{digest}"
    candidate = obs_dir / f"{base}.yaml"
    seq = 0
    while candidate.exists():
        seq += 1
        candidate = obs_dir / f"{base}_{seq}.yaml"
    return candidate


def _write_day0_report(
    run_dir: Path,
    manifest: dict[str, Any],
    capture_result: CaptureTickResult,
    gate_result: dict[str, Any],
) -> Path:
    reports = run_dir / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Baseline Day-0 Report",
        "",
        f"**Run ID:** {manifest['run_id']}",
        f"**Status:** {manifest['status']}",
        f"**Planned window:** {_iso_utc(_parse_utc(manifest['started_at_utc']))} → "
        f"{_iso_utc(_parse_utc(manifest['planned_end_at_utc']))}",
        "",
        "## Frozen universe",
        f"- All programs ({len(manifest['frozen_program_ids'])}): "
        f"{', '.join(manifest['frozen_program_ids'])}",
        f"- Active research ({len(manifest['active_research_program_ids'])}): "
        f"{', '.join(manifest['active_research_program_ids'])}",
        f"- Controls ({len(manifest['control_program_ids'])}): "
        f"{', '.join(manifest['control_program_ids'])}",
        "",
        "## Captures (active research only)",
        f"- Successes: {len(capture_result.successes)}",
        f"- Failures: {len(capture_result.failures)}",
        f"- Hash changed: {capture_result.hash_changed or 'none'}",
        f"- Hash unchanged: {capture_result.hash_unchanged or 'none'}",
        "",
        "## Gates",
        f"- Classify counts: {gate_result.get('classify_counts', {})}",
        f"- Actionability counts: {gate_result.get('actionability_counts', {})}",
        f"- ACTIONABLE: {gate_result.get('actionable_program_ids', [])}",
        "",
        "## Invariants",
        "- zero_capital: true",
        "- wallets_used: false",
        "- eligibility_lookups_used: false",
        "- reviewer_decisions_locked_pending: true",
        "- rule_changes_allowed: false",
        "",
        "No programs approved. No capital deployed.",
    ]
    out = reports / "day-0.md"
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return out


def _write_final_report(
    run_dir: Path,
    manifest: dict[str, Any],
    capture_result: CaptureTickResult,
    gate_result: dict[str, Any],
    violations: list[str],
) -> Path:
    reports = run_dir / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Baseline Final Report",
        "",
        f"**Run ID:** {manifest['run_id']}",
        f"**Final status:** {manifest['status']}",
        f"**Started:** {manifest.get('started_at_utc')}",
        f"**Ended:** {manifest.get('actual_end_at_utc')}",
        "",
        "## Last tick captures",
        f"- Successes: {len(capture_result.successes)}",
        f"- Failures: {capture_result.failures}",
        f"- Hash changed: {capture_result.hash_changed}",
        "",
        "## Final gates",
        f"- Actionability: {gate_result.get('actionability_counts', {})}",
        "",
        "## Invariant violations",
        f"{violations or 'none'}",
        "",
        "No programs approved. No capital deployed.",
    ]
    out = reports / "final.md"
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return out


def _build_manifest_skeleton(
    run_id: str,
    *,
    duration_days: int,
    universe: UniverseSplit,
    now: datetime,
    started_at: datetime,
    planned_end: datetime,
) -> dict[str, Any]:
    return {
        "run_id": run_id,
        "status": "PENDING",
        "created_at_utc": _iso_utc(now),
        "started_at_utc": _iso_utc(started_at),
        "planned_end_at_utc": _iso_utc(planned_end),
        "actual_end_at_utc": None,
        "duration_days": duration_days,
        "starting_git_sha": _get_git_sha(),
        "registry_path": REGISTRY_PATH,
        "registry_sha256": _sha256_file(REGISTRY_PATH),
        "handoff_path": HANDOFF_PATH,
        "handoff_sha256": _sha256_file(HANDOFF_PATH),
        "spec_path": SPEC_PATH,
        "spec_sha256": _sha256_file(SPEC_PATH),
        "frozen_program_ids": universe.frozen_program_ids,
        "active_research_program_ids": universe.active_research_program_ids,
        "control_program_ids": universe.control_program_ids,
        "zero_capital": True,
        "wallets_used": False,
        "eligibility_lookups_used": False,
        "reviewer_decisions_locked_pending": True,
        "rule_changes_allowed": False,
    }


def baseline_start(*, duration_days: int = 14, now: datetime | None = None) -> dict[str, Any]:
    """Atomically start a baseline run. Sets RUNNING only after all steps succeed."""
    if duration_days <= 0:
        raise BaselineError(f"duration_days must be positive, got {duration_days}")

    with _baseline_start_lock():
        if find_running_manifest():
            raise BaselineError("another RUNNING baseline exists; refuse second start")

        now = now or datetime.now(UTC)
        started_at = now
        planned_end = started_at + timedelta(days=duration_days)
        run_id = f"baseline-{now.strftime('%Y%m%dT%H%M%SZ')}"
        run_dir = RUNS_ROOT / run_id
        manifest_path = run_dir / "manifest.yaml"

        records, _ = load_registry(REGISTRY_PATH, warn=False)
        universe = split_universe(records)
        manifest = _build_manifest_skeleton(
            run_id,
            duration_days=duration_days,
            universe=universe,
            now=now,
            started_at=started_at,
            planned_end=planned_end,
        )
        save_manifest(manifest_path, manifest)

        try:
            capture_result = _capture_active_subset(
                records, universe.active_research_program_ids, now
            )
            _require_all_active_captures(capture_result, universe.active_research_program_ids)
            gate_result = _run_gates()
            if gate_result.get("actionable_program_ids"):
                raise BaselineError("ACTIONABLE records after gates")

            _require_invariants(manifest)

            running_manifest = dict(manifest)
            running_manifest["status"] = "RUNNING"
            _write_observation(
                run_dir,
                kind="day-0",
                capture_result=capture_result,
                gate_result=gate_result,
                manifest=running_manifest,
                now=now,
            )
            _write_day0_report(run_dir, running_manifest, capture_result, gate_result)

            manifest["status"] = "RUNNING"
            save_manifest(manifest_path, manifest)
            logger.info("baseline %s started RUNNING", run_id)
            return manifest
        except Exception as e:
            manifest["status"] = "ABORTED"
            manifest["actual_end_at_utc"] = _iso_utc(now)
            manifest["abort_reason"] = str(e)
            save_manifest(manifest_path, manifest)
            raise BaselineError(f"baseline start aborted: {e}") from e


def _get_running_manifest() -> tuple[Path, dict[str, Any]]:
    mp = find_running_manifest()
    if mp is None:
        raise BaselineError("no RUNNING baseline; start first")
    return mp, load_manifest(mp)


def _verify_frozen_artifacts(manifest: dict[str, Any]) -> None:
    """Reject tick/close if any frozen artifact or executing revision changed."""
    records, _ = load_registry(REGISTRY_PATH, warn=False)
    current = split_universe(records)
    if current.frozen_program_ids != manifest["frozen_program_ids"]:
        raise BaselineError("frozen_program_ids changed since baseline start")
    if current.active_research_program_ids != manifest["active_research_program_ids"]:
        raise BaselineError("active_research_program_ids changed since baseline start")
    if current.control_program_ids != manifest["control_program_ids"]:
        raise BaselineError("control_program_ids changed since baseline start")
    if _sha256_file(REGISTRY_PATH) != manifest["registry_sha256"]:
        raise BaselineError("registry content changed since baseline start")
    handoff_path = manifest.get("handoff_path", HANDOFF_PATH)
    if _sha256_file(handoff_path) != manifest["handoff_sha256"]:
        raise BaselineError("handoff content changed since baseline start")
    spec_path = manifest.get("spec_path", SPEC_PATH)
    if _sha256_file(spec_path) != manifest["spec_sha256"]:
        raise BaselineError("spec content changed since baseline start")
    if _get_git_sha() != manifest["starting_git_sha"]:
        raise BaselineError("git revision changed since baseline start")


def baseline_tick(*, now: datetime | None = None) -> dict[str, Any]:
    """Refetch active-research sources; record observation. Requires RUNNING manifest."""
    manifest_path, manifest = _get_running_manifest()
    if manifest.get("rule_changes_allowed"):
        raise BaselineError("rule_changes_allowed is true; refuse tick")

    _verify_frozen_artifacts(manifest)
    _require_invariants(manifest)
    now = now or datetime.now(UTC)
    records, _ = load_registry(REGISTRY_PATH, warn=False)

    capture_result = _capture_active_subset(records, manifest["active_research_program_ids"], now)
    gate_result = _run_gates()
    if gate_result.get("actionable_program_ids"):
        raise BaselineError("ACTIONABLE records detected during tick")

    obs = _write_observation(
        manifest_path.parent,
        kind="tick",
        capture_result=capture_result,
        gate_result=gate_result,
        manifest=manifest,
        now=now,
    )
    return {
        "observation": str(obs),
        "captures": capture_result,
        "gates": gate_result,
    }


def baseline_status(*, now: datetime | None = None) -> dict[str, Any]:
    """Report elapsed/remaining time and aggregate counts for the RUNNING baseline."""
    now = now or datetime.now(UTC)
    mp = find_running_manifest()
    if mp is None:
        latest = _latest_manifest()
        if latest is None:
            raise BaselineError("no baseline runs found")
        manifest = load_manifest(latest)
        status_note = f"no RUNNING baseline; showing latest ({manifest.get('status')})"
        manifest_path = latest
    else:
        manifest = load_manifest(mp)
        status_note = "RUNNING"
        manifest_path = mp

    started = _parse_utc(manifest["started_at_utc"])
    planned_end = _parse_utc(manifest["planned_end_at_utc"])
    elapsed = now - started
    remaining = planned_end - now

    violations = _assert_invariants(manifest)
    gate_result = check_all_actionability()
    actionable = sum(1 for r in gate_result.values() if r.status == Actionability.ACTIONABLE)
    status_counts: dict[str, int] = {}
    for res in gate_result.values():
        k = str(res.status)
        status_counts[k] = status_counts.get(k, 0) + 1

    obs_dir = manifest_path.parent / "observations"
    last_obs: dict[str, Any] = {}
    if obs_dir.exists():
        obs_files = sorted(obs_dir.glob("*.yaml"))
        if obs_files:
            last_obs = yaml.safe_load(obs_files[-1].read_text(encoding="utf-8")) or {}

    return {
        "status_note": status_note,
        "manifest": manifest,
        "elapsed_seconds": int(elapsed.total_seconds()),
        "remaining_seconds": int(remaining.total_seconds()),
        "planned_end_at_utc": manifest["planned_end_at_utc"],
        "verification_counts": _count_verification_states(),
        "ev_counts": _count_ev_readiness(),
        "actionability_counts": status_counts,
        "actionable_count": actionable,
        "invariant_violations": violations,
        "last_observation": last_obs,
    }


def _latest_manifest() -> Path | None:
    if not RUNS_ROOT.exists():
        return None
    manifests = sorted(RUNS_ROOT.glob("*/manifest.yaml"), reverse=True)
    return manifests[0] if manifests else None


def baseline_close(*, abort: bool = False, now: datetime | None = None) -> dict[str, Any]:
    """Close RUNNING baseline after final tick. Requires planned end unless --abort."""
    manifest_path, manifest = _get_running_manifest()
    now = now or datetime.now(UTC)
    planned_end = _parse_utc(manifest["planned_end_at_utc"])

    if not abort and now < planned_end:
        raise BaselineError(
            f"planned end {_iso_utc(planned_end)} not reached; use --abort to close early"
        )

    _require_invariants(manifest)
    tick_result = baseline_tick(now=now)
    violations = _assert_invariants(manifest)

    gate_result = tick_result["gates"]
    capture_result: CaptureTickResult = tick_result["captures"]

    manifest = load_manifest(manifest_path)
    manifest["status"] = "ABORTED" if abort else "COMPLETE"
    manifest["actual_end_at_utc"] = _iso_utc(now)
    save_manifest(manifest_path, manifest)

    _write_final_report(
        manifest_path.parent,
        manifest,
        capture_result,
        gate_result,
        violations,
    )
    return {"manifest": manifest, "tick": tick_result}
