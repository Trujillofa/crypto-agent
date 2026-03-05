from __future__ import annotations

import hashlib
import json
import re
import shlex
import subprocess
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any


class Severity(StrEnum):
    ERROR = "error"
    WARNING = "warning"
    INFO = "info"


@dataclass(frozen=True)
class Finding:
    severity: Severity
    code: str
    message: str
    recommendation: str


@dataclass(frozen=True)
class RepoSnapshot:
    branch: str
    commit: str
    dirty: bool


@dataclass(frozen=True)
class ServiceSnapshot:
    all_services: list[str]
    running_services: list[str]


@dataclass(frozen=True)
class SignalSnapshot:
    signal_count: int
    last_signal_at: str | None
    saw_strategy_cycle: bool


@dataclass(frozen=True)
class DriftReport:
    local_repo: RepoSnapshot | None
    remote_repo: RepoSnapshot | None
    local_config_hashes: dict[str, str]
    remote_config_hashes: dict[str, str]
    service_snapshot: ServiceSnapshot | None
    signal_snapshot: SignalSnapshot | None
    findings: list[Finding]


def run_command(command: list[str], cwd: Path | None = None, timeout: int = 15) -> str:
    result = subprocess.run(
        command,
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    return result.stdout.strip()


def run_remote_command(host: str, remote_dir: str, command: str, timeout: int = 20) -> str:
    remote_script = f"cd {shlex.quote(remote_dir)} && {command}"
    return run_command(["ssh", host, remote_script], timeout=timeout)


def collect_repo_snapshot(cwd: Path | None = None) -> RepoSnapshot:
    branch = run_command(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=cwd)
    commit = run_command(["git", "rev-parse", "HEAD"], cwd=cwd)
    dirty = bool(run_command(["git", "status", "--porcelain"], cwd=cwd))
    return RepoSnapshot(branch=branch, commit=commit, dirty=dirty)


def collect_remote_repo_snapshot(host: str, remote_dir: str) -> RepoSnapshot:
    branch = run_remote_command(host, remote_dir, "git rev-parse --abbrev-ref HEAD")
    commit = run_remote_command(host, remote_dir, "git rev-parse HEAD")
    dirty = bool(run_remote_command(host, remote_dir, "git status --porcelain"))
    return RepoSnapshot(branch=branch, commit=commit, dirty=dirty)


def collect_local_config_hashes(config_dir: Path = Path("config")) -> dict[str, str]:
    hashes: dict[str, str] = {}
    for path in sorted(config_dir.glob("settings*.yaml")):
        hashes[path.name] = sha256_file(path)
    return hashes


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(8192), b""):
            digest.update(chunk)
    return digest.hexdigest()


def collect_remote_config_hashes(host: str, remote_dir: str) -> dict[str, str]:
    output = run_remote_command(
        host,
        remote_dir,
        'for f in config/settings*.yaml; do [ -f "$f" ] && sha256sum "$f"; done',
    )
    return parse_sha256_lines(output)


def parse_sha256_lines(text: str) -> dict[str, str]:
    hashes: dict[str, str] = {}
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        parts = stripped.split(maxsplit=1)
        if len(parts) != 2:
            continue
        digest, file_path = parts
        normalized = Path(file_path.strip()).name
        hashes[normalized] = digest
    return hashes


def collect_remote_service_snapshot(host: str, remote_dir: str) -> ServiceSnapshot:
    all_services_output = run_remote_command(
        host,
        remote_dir,
        "docker compose config --services",
    )
    running_services_output = run_remote_command(
        host,
        remote_dir,
        "docker compose ps --status running --services",
    )

    all_services = [line.strip() for line in all_services_output.splitlines() if line.strip()]
    running_services = [
        line.strip() for line in running_services_output.splitlines() if line.strip()
    ]

    return ServiceSnapshot(all_services=all_services, running_services=running_services)


def collect_remote_signal_snapshot(
    host: str,
    remote_dir: str,
    service_snapshot: ServiceSnapshot,
    tail_lines: int = 500,
) -> SignalSnapshot:
    agent_services = [name for name in service_snapshot.all_services if name.startswith("agent")]
    if not agent_services:
        return SignalSnapshot(signal_count=0, last_signal_at=None, saw_strategy_cycle=False)

    rendered_services = " ".join(shlex.quote(service) for service in agent_services)
    logs = run_remote_command(
        host,
        remote_dir,
        f"docker compose logs --tail {tail_lines} --timestamps --no-log-prefix {rendered_services}",
        timeout=30,
    )

    signal_lines = [line for line in logs.splitlines() if "Consensus Signal" in line]
    saw_strategy_cycle = any("Strategy cycle:" in line for line in logs.splitlines())

    last_signal_at: str | None = None
    if signal_lines:
        last_signal_at = extract_timestamp(signal_lines[-1])

    return SignalSnapshot(
        signal_count=len(signal_lines),
        last_signal_at=last_signal_at,
        saw_strategy_cycle=saw_strategy_cycle,
    )


def extract_timestamp(line: str) -> str | None:
    match = re.search(
        r"(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?)", line
    )
    if not match:
        return None
    return match.group(1)


def parse_iso_timestamp(value: str) -> datetime | None:
    normalized = value.strip()
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None

    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def analyze_drift(
    *,
    expected_branch: str,
    local_repo: RepoSnapshot | None,
    remote_repo: RepoSnapshot | None,
    local_config_hashes: dict[str, str],
    remote_config_hashes: dict[str, str],
    service_snapshot: ServiceSnapshot | None,
    signal_snapshot: SignalSnapshot | None,
    remote_error: str | None,
    signal_stale_hours: int,
    remote_checks_enabled: bool = True,
) -> list[Finding]:
    findings: list[Finding] = []

    if local_repo is None:
        findings.append(
            Finding(
                severity=Severity.ERROR,
                code="LOCAL_REPO_UNAVAILABLE",
                message="Unable to inspect local git state",
                recommendation="Run from the repository root and ensure git is available.",
            )
        )
    else:
        if local_repo.dirty:
            findings.append(
                Finding(
                    severity=Severity.WARNING,
                    code="LOCAL_DIRTY_WORKTREE",
                    message="Local repository has uncommitted changes",
                    recommendation="Commit or stash local changes before comparing deploy parity.",
                )
            )
        if local_repo.branch != expected_branch:
            findings.append(
                Finding(
                    severity=Severity.INFO,
                    code="LOCAL_BRANCH_NON_STANDARD",
                    message=f"Local branch is '{local_repo.branch}', expected '{expected_branch}'",
                    recommendation="Use this only for feature testing; deploy from the expected branch.",
                )
            )

    if not remote_checks_enabled:
        return findings

    if remote_error is not None:
        findings.append(
            Finding(
                severity=Severity.ERROR,
                code="REMOTE_UNREACHABLE",
                message=f"Failed to inspect remote host: {remote_error}",
                recommendation="Verify SSH access and rerun the sentinel.",
            )
        )
        return findings

    if remote_repo is None:
        findings.append(
            Finding(
                severity=Severity.ERROR,
                code="REMOTE_REPO_UNAVAILABLE",
                message="Remote repository snapshot missing",
                recommendation="Check remote path and git installation on server.",
            )
        )
        return findings

    if remote_repo.branch != expected_branch:
        findings.append(
            Finding(
                severity=Severity.ERROR,
                code="REMOTE_BRANCH_MISMATCH",
                message=f"Remote branch is '{remote_repo.branch}', expected '{expected_branch}'",
                recommendation="Switch server checkout to expected branch before trading.",
            )
        )

    if remote_repo.dirty:
        findings.append(
            Finding(
                severity=Severity.WARNING,
                code="REMOTE_DIRTY_WORKTREE",
                message="Remote repository has uncommitted changes",
                recommendation="Reset or commit server-side edits to keep deployments reproducible.",
            )
        )

    if local_repo is not None and local_repo.commit != remote_repo.commit:
        findings.append(
            Finding(
                severity=Severity.WARNING,
                code="LOCAL_REMOTE_COMMIT_DRIFT",
                message=(
                    "Local and remote commits differ "
                    f"({local_repo.commit[:8]} != {remote_repo.commit[:8]})"
                ),
                recommendation="Deploy or pull so both environments run the same commit.",
            )
        )

    for name in sorted(set(local_config_hashes) | set(remote_config_hashes)):
        local_hash = local_config_hashes.get(name)
        remote_hash = remote_config_hashes.get(name)
        if local_hash is None:
            findings.append(
                Finding(
                    severity=Severity.WARNING,
                    code="LOCAL_CONFIG_MISSING",
                    message=f"Local config file missing: {name}",
                    recommendation="Restore the file locally or remove it from server intentionally.",
                )
            )
            continue
        if remote_hash is None:
            findings.append(
                Finding(
                    severity=Severity.WARNING,
                    code="REMOTE_CONFIG_MISSING",
                    message=f"Remote config file missing: {name}",
                    recommendation="Sync configuration files before launch.",
                )
            )
            continue
        if local_hash != remote_hash:
            findings.append(
                Finding(
                    severity=Severity.WARNING,
                    code="CONFIG_HASH_DRIFT",
                    message=f"Config drift detected for {name}",
                    recommendation="Sync this config between local and production.",
                )
            )

    if service_snapshot is not None:
        missing = sorted(
            set(service_snapshot.all_services) - set(service_snapshot.running_services)
        )
        if missing:
            findings.append(
                Finding(
                    severity=Severity.ERROR,
                    code="SERVICES_NOT_RUNNING",
                    message=f"Services not running: {', '.join(missing)}",
                    recommendation="Restart failed services and inspect container logs.",
                )
            )

    if signal_snapshot is not None:
        if signal_snapshot.saw_strategy_cycle and signal_snapshot.signal_count == 0:
            findings.append(
                Finding(
                    severity=Severity.WARNING,
                    code="SIGNAL_DROUGHT",
                    message="Strategy cycles are running but no consensus signals were found in recent logs",
                    recommendation=(
                        "Review strategy and aggregator thresholds; run config doctor and check filters."
                    ),
                )
            )

        if signal_snapshot.last_signal_at:
            parsed = parse_iso_timestamp(signal_snapshot.last_signal_at)
            if parsed is not None:
                age_hours = (datetime.now(UTC) - parsed).total_seconds() / 3600
                if age_hours > signal_stale_hours:
                    findings.append(
                        Finding(
                            severity=Severity.WARNING,
                            code="SIGNAL_STALE",
                            message=(
                                "Latest consensus signal is stale "
                                f"({age_hours:.1f}h old at {signal_snapshot.last_signal_at})"
                            ),
                            recommendation=(
                                "Inspect recent market conditions and strategy thresholds for over-filtering."
                            ),
                        )
                    )

    return findings


def report_to_json(report: DriftReport) -> str:
    payload: dict[str, Any] = {
        "local_repo": asdict(report.local_repo) if report.local_repo else None,
        "remote_repo": asdict(report.remote_repo) if report.remote_repo else None,
        "local_config_hashes": report.local_config_hashes,
        "remote_config_hashes": report.remote_config_hashes,
        "service_snapshot": asdict(report.service_snapshot) if report.service_snapshot else None,
        "signal_snapshot": asdict(report.signal_snapshot) if report.signal_snapshot else None,
        "findings": [asdict(finding) for finding in report.findings],
    }
    return json.dumps(payload, indent=2)
