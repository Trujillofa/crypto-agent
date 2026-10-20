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

IGNORED_CONFIG_FILES = {"settings.autoresearch.yaml"}
PRODUCTION_COMPOSE = "docker compose -f docker-compose.prod.yml"
PAPER_VALIDATION_REPORT_GLOB = "data/reports/paper-validation-report-*.json"
PRODUCTION_DRIFT_SENTINEL_REPORT_GLOB = "data/reports/production-drift-sentinel-*.json"
REQUIRED_SYSTEMD_TIMERS = (
    "crypto-agent-paper-validation-report.timer",
    "crypto-agent-production-drift-sentinel.timer",
)
TIMER_ARTIFACT_POLICIES = {
    "crypto-agent-paper-validation-report.timer": (PAPER_VALIDATION_REPORT_GLOB, 36),
    "crypto-agent-production-drift-sentinel.timer": (PRODUCTION_DRIFT_SENTINEL_REPORT_GLOB, 2),
}

# Durable signal events, read from the shared agent-data volume.
#
# Docker logs are the wrong source for "has this strategy traded lately":
# they only cover the life of the current container, so a deploy silently
# resets the lookback window, and "Consensus Signal" is only logged for
# non-HOLD decisions. The event log in the shared volume outlives deploys
# and records every signal the paper executor received.
DURABLE_SIGNAL_EVENT_TYPES = ("signal_received", "signal_ignored")

# The paper executor logs a guard refusal with this prefix. The other
# "ignored" reasons are by-design behaviour for a long-only spot account.
GUARD_BLOCK_REASON_PREFIX = "Guard blocked"
DURABLE_EVENT_LOG_GLOB = "/app/data/event_log_*.jsonl"
DURABLE_EVENT_LOG_DIR = "/app/data"


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
class TimerState:
    timer: str
    enabled: str
    active: str
    service_result: str
    exec_main_status: str
    latest_report_at: str | None
    max_report_age_hours: int


@dataclass(frozen=True)
class TimerSnapshot:
    timers: list[TimerState]


@dataclass(frozen=True)
class SignalSnapshot:
    signal_count: int
    last_signal_at: str | None
    saw_strategy_cycle: bool


@dataclass(frozen=True)
class DurableSignalSnapshot:
    """Signal activity read from the shared agent-data volume.

    Unlike SignalSnapshot, this survives a container redeploy, so it is
    the trustworthy source for "has this strategy traded lately".
    """

    event_count: int
    last_signal_at: str | None
    read_ok: bool
    read_error: str = ""
    files_scanned: int = 0
    ignored_count: int = 0
    received_count: int = 0
    guard_block_count: int = 0
    median_interval_hours: float | None = None


@dataclass(frozen=True)
class WatchedServiceSnapshot:
    service: str
    exists: bool
    running: bool
    signal_count: int
    last_signal_at: str | None
    saw_strategy_cycle: bool
    paper_order_count: int
    last_paper_order_at: str | None


@dataclass(frozen=True)
class DurableWatchedServiceSnapshot:
    """Durable signal activity for one watched service."""

    service: str
    agent_id: str
    event_count: int
    last_signal_at: str | None
    read_ok: bool
    read_error: str = ""
    median_interval_hours: float | None = None


@dataclass(frozen=True)
class DriftReport:
    local_repo: RepoSnapshot | None
    remote_repo: RepoSnapshot | None
    local_config_hashes: dict[str, str]
    remote_config_hashes: dict[str, str]
    service_snapshot: ServiceSnapshot | None
    timer_snapshot: TimerSnapshot | None
    signal_snapshot: SignalSnapshot | None
    watched_service_snapshot: WatchedServiceSnapshot | None
    findings: list[Finding]
    durable_signal_snapshot: DurableSignalSnapshot | None = None
    durable_watched_service_snapshot: DurableWatchedServiceSnapshot | None = None


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


def run_remote_command(
    host: str | None,
    remote_dir: str,
    command: str,
    timeout: int = 20,
    ssh_config: str | None = None,
) -> str:
    remote_script = f"cd {shlex.quote(remote_dir)} && {command}"
    if host is None:
        return run_command(["bash", "-lc", remote_script], timeout=timeout)

    ssh_command = ["ssh"]
    if ssh_config:
        ssh_command.extend(["-F", ssh_config])
    return run_command([*ssh_command, host, remote_script], timeout=timeout)


def collect_repo_snapshot(cwd: Path | None = None) -> RepoSnapshot:
    branch = run_command(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=cwd)
    commit = run_command(["git", "rev-parse", "HEAD"], cwd=cwd)
    dirty = bool(run_command(["git", "status", "--porcelain"], cwd=cwd))
    return RepoSnapshot(branch=branch, commit=commit, dirty=dirty)


def collect_remote_repo_snapshot(
    host: str | None, remote_dir: str, ssh_config: str | None = None
) -> RepoSnapshot:
    branch = run_remote_command(
        host, remote_dir, "git rev-parse --abbrev-ref HEAD", ssh_config=ssh_config
    )
    commit = run_remote_command(host, remote_dir, "git rev-parse HEAD", ssh_config=ssh_config)
    dirty = bool(
        run_remote_command(host, remote_dir, "git status --porcelain", ssh_config=ssh_config)
    )
    return RepoSnapshot(branch=branch, commit=commit, dirty=dirty)


def collect_local_config_hashes(config_dir: Path = Path("config")) -> dict[str, str]:
    hashes: dict[str, str] = {}
    for path in sorted(config_dir.glob("settings*.yaml")):
        if path.name in IGNORED_CONFIG_FILES:
            continue
        hashes[path.name] = sha256_file(path)
    return hashes


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(8192), b""):
            digest.update(chunk)
    return digest.hexdigest()


def collect_remote_config_hashes(
    host: str | None, remote_dir: str, ssh_config: str | None = None
) -> dict[str, str]:
    output = run_remote_command(
        host,
        remote_dir,
        'for f in config/settings*.yaml; do [ -f "$f" ] && sha256sum "$f"; done',
        ssh_config=ssh_config,
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
        if normalized in IGNORED_CONFIG_FILES:
            continue
        hashes[normalized] = digest
    return hashes


def collect_remote_service_snapshot(
    host: str | None, remote_dir: str, ssh_config: str | None = None
) -> ServiceSnapshot:
    all_services_output = run_remote_command(
        host,
        remote_dir,
        f"{PRODUCTION_COMPOSE} config --services",
        ssh_config=ssh_config,
    )
    running_services_output = run_remote_command(
        host,
        remote_dir,
        f"{PRODUCTION_COMPOSE} ps --status running --services",
        ssh_config=ssh_config,
    )

    all_services = [line.strip() for line in all_services_output.splitlines() if line.strip()]
    running_services = [
        line.strip() for line in running_services_output.splitlines() if line.strip()
    ]

    return ServiceSnapshot(all_services=all_services, running_services=running_services)


def collect_remote_timer_snapshot(
    host: str | None,
    remote_dir: str,
    timers: tuple[str, ...] = REQUIRED_SYSTEMD_TIMERS,
    ssh_config: str | None = None,
) -> TimerSnapshot:
    states: list[TimerState] = []
    for timer in timers:
        report_glob, max_report_age_hours = TIMER_ARTIFACT_POLICIES.get(
            timer, (PAPER_VALIDATION_REPORT_GLOB, 36)
        )
        rendered_timer = shlex.quote(timer)
        enabled = run_remote_command(
            host,
            remote_dir,
            f"systemctl is-enabled {rendered_timer} 2>/dev/null || true",
            ssh_config=ssh_config,
        )
        active = run_remote_command(
            host,
            remote_dir,
            f"systemctl is-active {rendered_timer} 2>/dev/null || true",
            ssh_config=ssh_config,
        )
        service = timer.removesuffix(".timer") + ".service"
        rendered_service = shlex.quote(service)
        service_result = run_remote_command(
            host,
            remote_dir,
            f"systemctl show {rendered_service} --property=Result --value",
            ssh_config=ssh_config,
        )
        exec_main_status = run_remote_command(
            host,
            remote_dir,
            f"systemctl show {rendered_service} --property=ExecMainStatus --value",
            ssh_config=ssh_config,
        )
        latest_report_epoch = run_remote_command(
            host,
            remote_dir,
            (
                f"find {shlex.quote(str(Path(report_glob).parent))} "
                f"-maxdepth 1 -type f -name {shlex.quote(Path(report_glob).name)} "
                "-printf '%T@\\n' 2>/dev/null | sort -nr | head -n 1"
            ),
            ssh_config=ssh_config,
        )
        latest_report_at = epoch_to_iso_timestamp(latest_report_epoch)
        states.append(
            TimerState(
                timer=timer,
                enabled=enabled or "unknown",
                active=active or "unknown",
                service_result=service_result or "unknown",
                exec_main_status=exec_main_status or "unknown",
                latest_report_at=latest_report_at,
                max_report_age_hours=max_report_age_hours,
            )
        )
    return TimerSnapshot(timers=states)


def epoch_to_iso_timestamp(value: str) -> str | None:
    try:
        epoch = float(value.strip())
    except ValueError:
        return None
    return datetime.fromtimestamp(epoch, tz=UTC).isoformat()


# The reader that runs inside the agent container. It prints one JSON object
# per line so the caller can parse it without depending on the container's
# python version or on the host having read access to the volume.
#
# The first line is always a status record. Without it, "the volume could
# not be read" and "the strategy produced no signals" would be
# indistinguishable, and a broken instrument would look exactly like a
# quiet market.
DURABLE_EVENT_READER = r"""
import glob, json, sys
status = {"type": "__status__", "files": 0, "records": 0, "error": ""}
out = []
try:
    paths = sorted(glob.glob(sys.argv[1]))
    status["files"] = len(paths)
    for path in paths:
        try:
            with open(path, "r", errors="replace") as handle:
                for line in handle:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        record = json.loads(line)
                    except Exception:
                        continue
                    if not isinstance(record, dict):
                        continue
                    event_type = record.get("type")
                    if event_type not in sys.argv[2].split(","):
                        continue
                    status["records"] += 1
                    payload = record.get("payload")
                    if not isinstance(payload, dict):
                        payload = {}
                    out.append({
                        "type": event_type,
                        "ts": record.get("ts") or "",
                        "agent_id": record.get("agent_id") or "",
                        "signal_type": payload.get("type") or payload.get("signal_type") or "",
                        "reason": str(payload.get("reason") or "")[:200],
                    })
        except Exception as exc:
            status["error"] = "%s: %s" % (path, exc)
except Exception as exc:
    status["error"] = str(exc)
print(json.dumps(status))
for item in out:
    print(json.dumps(item))
"""


def collect_durable_signal_events(
    host: str | None,
    remote_dir: str,
    service_snapshot: ServiceSnapshot,
    event_types: tuple[str, ...] = DURABLE_SIGNAL_EVENT_TYPES,
    ssh_config: str | None = None,
) -> tuple[list[dict[str, str]], dict[str, str]]:
    """Read signal events from the shared agent-data volume.

    The event log lives in a Docker volume that the host user cannot read
    directly, so the read is done inside a running agent container via
    `docker compose exec`.

    Returns (events, status). The status dict carries the reader's own
    status record: files scanned, records matched, and any error. When
    status is empty the read did not complete and the caller must not
    treat the empty event list as "no signals happened".
    """
    running_agents = [
        name for name in service_snapshot.running_services if name.startswith("agent")
    ]
    if not running_agents:
        return [], {}

    reader = DURABLE_EVENT_READER.strip()
    event_types_arg = ",".join(event_types)
    service = running_agents[0]
    command = (
        f"{PRODUCTION_COMPOSE} exec -T {shlex.quote(service)} "
        f"python3 -c {shlex.quote(reader)} "
        f"{shlex.quote(DURABLE_EVENT_LOG_GLOB)} {shlex.quote(event_types_arg)}"
    )

    output = run_remote_command(host, remote_dir, command, timeout=60, ssh_config=ssh_config)

    status: dict[str, str] = {}
    events: list[dict[str, str]] = []
    for line in output.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(record, dict):
            continue
        if record.get("type") == "__status__":
            status = {str(key): str(value) for key, value in record.items()}
            continue
        if record.get("type"):
            events.append(
                {
                    "type": str(record.get("type", "")),
                    "ts": str(record.get("ts", "")),
                    "agent_id": str(record.get("agent_id", "")),
                    "signal_type": str(record.get("signal_type", "")),
                    "reason": str(record.get("reason", "")),
                }
            )
    return events, status


def effective_stale_threshold(
    configured_hours: float,
    median_interval_hours: float | None,
    multiplier: float = 3.0,
) -> float:
    """Threshold for calling a signal stale, adapted to the agent's cadence.

    A fixed threshold is wrong for these strategies. The 4h panic-block
    agent signals roughly every nine days, so a 72-hour threshold would
    report it as stale almost permanently and recreate the exact
    false-alarm failure this instrument was repaired to fix.

    When the agent's own historical cadence is known, the threshold
    becomes a multiple of it. The configured value is still respected as
    an upper bound so a genuinely quiet market is still reported.

    Returns the larger of the two, because a threshold below the
    configured one would make the instrument noisier than it was.
    """
    if median_interval_hours is None or median_interval_hours <= 0:
        return float(configured_hours)
    return max(float(configured_hours), median_interval_hours * multiplier)


def summarize_durable_signals(
    events: list[dict[str, str]],
    agent_id: str | None = None,
) -> tuple[int, str | None]:
    """Return (count, newest timestamp) for signal events.

    When agent_id is given, only that agent's events are counted. A
    timestamp of "" is ignored so a malformed record cannot become the
    "newest" signal.
    """
    matching = [event for event in events if agent_id is None or event.get("agent_id") == agent_id]
    timestamps = [event.get("ts", "") for event in matching if event.get("ts")]
    if not timestamps:
        return len(matching), None
    return len(matching), max(timestamps)


def median_signal_interval_hours(
    events: list[dict[str, str]], agent_id: str | None = None
) -> float | None:
    """Median hours between consecutive signals, from the durable log.

    A fixed staleness threshold is wrong for these strategies: the 4h
    panic-block agent signals roughly every nine days, so a 72-hour
    threshold would report it as stale almost permanently. The agent's
    own historical cadence is the right yardstick.

    When agent_id is None the intervals are computed per agent and the
    median of those per-agent medians is returned. Mixing every agent's
    signals into one series would make the gap look tiny -- two
    different agents signalling an hour apart is not a fast cadence.

    Returns None when there is nothing to learn from, because a single
    signal gives no interval.
    """
    if agent_id is not None:
        return _median_interval_for(
            [event for event in events if event.get("agent_id") == agent_id]
        )

    by_agent: dict[str, list[dict[str, str]]] = {}
    for event in events:
        if event.get("type") != "signal_received" or not event.get("ts"):
            continue
        by_agent.setdefault(event.get("agent_id", ""), []).append(event)

    per_agent = [
        interval
        for interval in (_median_interval_for(group) for group in by_agent.values())
        if interval is not None
    ]
    if not per_agent:
        return None
    per_agent.sort()
    middle = len(per_agent) // 2
    if len(per_agent) % 2:
        return per_agent[middle]
    return (per_agent[middle - 1] + per_agent[middle]) / 2


def _median_interval_for(events: list[dict[str, str]]) -> float | None:
    """Median gap between consecutive signals in one series."""
    timestamps = sorted(event.get("ts", "") for event in events if event.get("ts"))
    if len(timestamps) < 2:
        return None

    parsed = []
    for value in timestamps:
        moment = parse_iso_timestamp(value)
        if moment is not None:
            parsed.append(moment)
    if len(parsed) < 2:
        return None

    gaps = sorted(
        (later - earlier).total_seconds() / 3600
        for earlier, later in zip(parsed, parsed[1:], strict=False)
        if later > earlier
    )
    if not gaps:
        return None

    middle = len(gaps) // 2
    if len(gaps) % 2:
        return gaps[middle]
    return (gaps[middle - 1] + gaps[middle]) / 2


def count_durable_received(
    events: list[dict[str, str]],
    agent_id: str | None = None,
) -> int:
    """Count signals the executor received."""
    return len(
        [
            event
            for event in events
            if event.get("type") == "signal_received"
            and (agent_id is None or event.get("agent_id") == agent_id)
        ]
    )


def count_durable_ignored(
    events: list[dict[str, str]],
    agent_id: str | None = None,
) -> int:
    """Count signals that were produced but then blocked by a guard.

    This is the over-filtering signature: the strategy keeps producing
    signals and the guards keep refusing them. A plain event count
    cannot tell that apart from a strategy that stopped producing.

    Only guard blocks count. The other "ignored" reasons are by-design
    behaviour -- a SELL in a spot-only account, or an exit that is
    handled by stop-loss/take-profit rather than by a signal. Counting
    those as over-filtering would flag every healthy long-only agent.
    """
    return len(
        [
            event
            for event in events
            if event.get("type") == "signal_ignored"
            and event.get("reason", "").startswith(GUARD_BLOCK_REASON_PREFIX)
            and (agent_id is None or event.get("agent_id") == agent_id)
        ]
    )


def resolve_service_agent_id(
    host: str | None,
    remote_dir: str,
    service: str,
    ssh_config: str | None = None,
) -> str:
    """Read AGENT_ID from a running service's environment.

    The event log is keyed by agent_id, not by compose service name, so
    the mapping has to be resolved at runtime rather than hardcoded.
    """
    command = f"{PRODUCTION_COMPOSE} exec -T {shlex.quote(service)} printenv AGENT_ID"
    output = run_remote_command(host, remote_dir, command, timeout=30, ssh_config=ssh_config)
    return output.strip().splitlines()[-1].strip() if output.strip() else ""


def build_durable_signal_snapshot(
    events: list[dict[str, str]],
    status: dict[str, str],
) -> DurableSignalSnapshot:
    received = count_durable_received(events)
    guard_blocks = count_durable_ignored(events)
    _, last = summarize_durable_signals(events)
    read_ok = bool(status)
    read_error = status.get("error", "") if status else "durable event log was not read"
    files = int(status.get("files", "0") or 0) if status else 0
    return DurableSignalSnapshot(
        event_count=len(events),
        last_signal_at=last,
        read_ok=read_ok,
        read_error=read_error,
        files_scanned=files,
        ignored_count=guard_blocks,
        received_count=received,
        guard_block_count=guard_blocks,
        median_interval_hours=median_signal_interval_hours(events),
    )


def build_durable_watched_service_snapshot(
    service: str,
    agent_id: str,
    events: list[dict[str, str]],
    status: dict[str, str],
) -> DurableWatchedServiceSnapshot:
    count, last = summarize_durable_signals(events, agent_id=agent_id or None)
    read_ok = bool(status)
    read_error = status.get("error", "") if status else "durable event log was not read"
    return DurableWatchedServiceSnapshot(
        service=service,
        agent_id=agent_id,
        event_count=count,
        last_signal_at=last,
        read_ok=read_ok,
        read_error=read_error,
        median_interval_hours=median_signal_interval_hours(events, agent_id=agent_id or None),
    )


def collect_remote_signal_snapshot(
    host: str | None,
    remote_dir: str,
    service_snapshot: ServiceSnapshot,
    tail_lines: int = 500,
    ssh_config: str | None = None,
) -> SignalSnapshot:
    agent_services = [name for name in service_snapshot.all_services if name.startswith("agent")]
    if not agent_services:
        return SignalSnapshot(signal_count=0, last_signal_at=None, saw_strategy_cycle=False)

    rendered_services = " ".join(shlex.quote(service) for service in agent_services)
    logs = run_remote_command(
        host,
        remote_dir,
        f"{PRODUCTION_COMPOSE} logs --tail {tail_lines} --timestamps --no-log-prefix "
        f"{rendered_services}",
        timeout=30,
        ssh_config=ssh_config,
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


def collect_remote_watched_service_snapshot(
    host: str | None,
    remote_dir: str,
    service_snapshot: ServiceSnapshot,
    service: str,
    tail_lines: int = 500,
    ssh_config: str | None = None,
) -> WatchedServiceSnapshot:
    exists = service in service_snapshot.all_services
    running = service in service_snapshot.running_services
    if not exists or not running:
        return WatchedServiceSnapshot(
            service=service,
            exists=exists,
            running=running,
            signal_count=0,
            last_signal_at=None,
            saw_strategy_cycle=False,
            paper_order_count=0,
            last_paper_order_at=None,
        )

    logs = run_remote_command(
        host,
        remote_dir,
        f"{PRODUCTION_COMPOSE} logs --tail {tail_lines} --timestamps --no-log-prefix "
        f"{shlex.quote(service)}",
        timeout=30,
        ssh_config=ssh_config,
    )
    lines = logs.splitlines()
    signal_lines = [line for line in lines if "Consensus Signal" in line]
    paper_order_lines = [
        line
        for line in lines
        if "Paper BUY " in line or "Paper SELL " in line or "Paper SHORT " in line
    ]
    saw_strategy_cycle = any("Strategy cycle:" in line for line in lines)

    last_signal_at: str | None = None
    if signal_lines:
        last_signal_at = extract_timestamp(signal_lines[-1])

    last_paper_order_at: str | None = None
    if paper_order_lines:
        last_paper_order_at = extract_timestamp(paper_order_lines[-1])

    return WatchedServiceSnapshot(
        service=service,
        exists=exists,
        running=running,
        signal_count=len(signal_lines),
        last_signal_at=last_signal_at,
        saw_strategy_cycle=saw_strategy_cycle,
        paper_order_count=len(paper_order_lines),
        last_paper_order_at=last_paper_order_at,
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
    timer_snapshot: TimerSnapshot | None = None,
    signal_snapshot: SignalSnapshot | None,
    watched_service_snapshot: WatchedServiceSnapshot | None,
    remote_error: str | None,
    signal_stale_hours: int,
    remote_checks_enabled: bool = True,
    durable_signal_snapshot: DurableSignalSnapshot | None = None,
    durable_watched_service_snapshot: DurableWatchedServiceSnapshot | None = None,
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

    if timer_snapshot is not None:
        for timer in timer_snapshot.timers:
            if timer.enabled != "enabled" or timer.active != "active":
                findings.append(
                    Finding(
                        severity=Severity.ERROR,
                        code="REQUIRED_TIMER_NOT_HEALTHY",
                        message=(
                            f"Required timer '{timer.timer}' is not healthy "
                            f"(enabled={timer.enabled}, active={timer.active})"
                        ),
                        recommendation="Enable and start the required production timer.",
                    )
                )
            if timer.service_result != "success" or timer.exec_main_status != "0":
                findings.append(
                    Finding(
                        severity=Severity.ERROR,
                        code="REQUIRED_TIMER_SERVICE_FAILED",
                        message=(
                            f"Required timer service for '{timer.timer}' failed "
                            f"(result={timer.service_result}, exec_status={timer.exec_main_status})"
                        ),
                        recommendation="Inspect the timer service journal and rerun the report.",
                    )
                )
            if timer.latest_report_at is None:
                findings.append(
                    Finding(
                        severity=Severity.ERROR,
                        code="TIMER_REPORT_MISSING",
                        message=f"No generated JSON report artifact was found for '{timer.timer}'",
                        recommendation="Run the report wrapper and verify host artifact persistence.",
                    )
                )
            else:
                parsed = parse_iso_timestamp(timer.latest_report_at)
                if parsed is not None:
                    age_hours = (datetime.now(UTC) - parsed).total_seconds() / 3600
                    if age_hours > timer.max_report_age_hours:
                        findings.append(
                            Finding(
                                severity=Severity.ERROR,
                                code="TIMER_REPORT_STALE",
                                message=(
                                    f"Latest report artifact for '{timer.timer}' is stale "
                                    f"({age_hours:.1f}h old at {timer.latest_report_at})"
                                ),
                                recommendation="Inspect the timer service and regenerate the report.",
                            )
                        )

    # Signal activity.
    #
    # The durable event log is authoritative when it could be read. Docker
    # logs are only a fallback: they are truncated by a container redeploy
    # and "Consensus Signal" is logged for non-HOLD decisions only, so a
    # log-only check reports a drought even when the strategy traded
    # yesterday.
    if durable_signal_snapshot is not None:
        if not durable_signal_snapshot.read_ok:
            findings.append(
                Finding(
                    severity=Severity.ERROR,
                    code="DURABLE_SIGNAL_LOG_UNREADABLE",
                    message=(
                        "Could not read the durable signal event log: "
                        f"{durable_signal_snapshot.read_error or 'unknown error'}"
                    ),
                    recommendation=(
                        "Check that the agent containers are running and that the shared "
                        "agent-data volume is mounted and readable from inside a container."
                    ),
                )
            )
        else:
            if durable_signal_snapshot.event_count == 0:
                findings.append(
                    Finding(
                        severity=Severity.WARNING,
                        code="SIGNAL_DROUGHT",
                        message=(
                            "No consensus signals have been recorded in the durable event log "
                            f"({durable_signal_snapshot.files_scanned} event log file(s) scanned)"
                        ),
                        recommendation=(
                            "Review strategy and aggregator thresholds; run config doctor and "
                            "check filters."
                        ),
                    )
                )

            if durable_signal_snapshot.last_signal_at:
                parsed = parse_iso_timestamp(durable_signal_snapshot.last_signal_at)
                if parsed is not None:
                    age_hours = (datetime.now(UTC) - parsed).total_seconds() / 3600
                    threshold = effective_stale_threshold(
                        signal_stale_hours,
                        durable_signal_snapshot.median_interval_hours,
                    )
                    if age_hours > threshold:
                        findings.append(
                            Finding(
                                severity=Severity.WARNING,
                                code="SIGNAL_STALE",
                                message=(
                                    "Latest durable consensus signal is stale "
                                    f"({age_hours:.1f}h old at "
                                    f"{durable_signal_snapshot.last_signal_at}, "
                                    f"threshold {threshold:.1f}h)"
                                ),
                                recommendation=(
                                    "Inspect recent market conditions and strategy thresholds "
                                    "for over-filtering."
                                ),
                            )
                        )

            # Over-filtering signature: signals are being produced and then
            # refused by a guard. A drought of produced signals and a
            # drought of executed signals look identical in an event count
            # alone, but they need opposite fixes.
            #
            # Only guard blocks count towards over-filtering. The other
            # ignored reasons are by-design behaviour for a long-only
            # spot account and are not a filtering problem.
            received = durable_signal_snapshot.received_count
            guard_blocks = durable_signal_snapshot.guard_block_count
            if received > 0 and guard_blocks >= received:
                findings.append(
                    Finding(
                        severity=Severity.WARNING,
                        code="SIGNAL_OVER_FILTERED",
                        message=(
                            f"{guard_blocks} durable signals were blocked by a guard versus "
                            f"{received} received; the guards are refusing most of what the "
                            "strategy produces"
                        ),
                        recommendation=(
                            "Inspect the guard and filter configuration for thresholds that "
                            "reject the signals the strategy generates."
                        ),
                    )
                )
    elif signal_snapshot is not None:
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

    if watched_service_snapshot is not None:
        if not watched_service_snapshot.exists:
            findings.append(
                Finding(
                    severity=Severity.ERROR,
                    code="WATCH_SERVICE_MISSING",
                    message=(
                        f"Watched service '{watched_service_snapshot.service}' "
                        "is not defined in docker compose"
                    ),
                    recommendation="Fix the service name or add the service before relying on it.",
                )
            )
        elif not watched_service_snapshot.running:
            findings.append(
                Finding(
                    severity=Severity.ERROR,
                    code="WATCH_SERVICE_NOT_RUNNING",
                    message=f"Watched service '{watched_service_snapshot.service}' is not running",
                    recommendation="Restart the service and inspect its container logs.",
                )
            )
        else:
            if not watched_service_snapshot.saw_strategy_cycle:
                findings.append(
                    Finding(
                        severity=Severity.WARNING,
                        code="WATCH_SERVICE_NO_STRATEGY_CYCLE",
                        message=(
                            f"No strategy cycle entries were found for "
                            f"'{watched_service_snapshot.service}' in recent logs"
                        ),
                        recommendation="Check the service logs and confirm the strategy engine loop is running.",
                    )
                )

            # The log-based signal findings are suppressed when the durable
            # log was read, because the durable snapshot reports the same
            # condition with a source that survives a redeploy.
            durable_available = (
                durable_watched_service_snapshot is not None
                and durable_watched_service_snapshot.read_ok
            )

            if (
                not durable_available
                and watched_service_snapshot.saw_strategy_cycle
                and watched_service_snapshot.signal_count == 0
            ):
                findings.append(
                    Finding(
                        severity=Severity.WARNING,
                        code="WATCH_SERVICE_SIGNAL_DROUGHT",
                        message=(
                            f"Watched service '{watched_service_snapshot.service}' is cycling "
                            "but no consensus signals were found in recent logs"
                        ),
                        recommendation=(
                            "Review the watched strategy thresholds and confirm recent market "
                            "conditions match the thesis."
                        ),
                    )
                )

            if not durable_available and watched_service_snapshot.last_signal_at:
                parsed = parse_iso_timestamp(watched_service_snapshot.last_signal_at)
                if parsed is not None:
                    age_hours = (datetime.now(UTC) - parsed).total_seconds() / 3600
                    if age_hours > signal_stale_hours:
                        findings.append(
                            Finding(
                                severity=Severity.WARNING,
                                code="WATCH_SERVICE_SIGNAL_STALE",
                                message=(
                                    f"Latest watched-service consensus signal is stale "
                                    f"({age_hours:.1f}h old at {watched_service_snapshot.last_signal_at})"
                                ),
                                recommendation=(
                                    "Inspect whether this sparse strategy is idle by design or "
                                    "whether conditions changed materially."
                                ),
                            )
                        )

    # Watched service, durable source. This supersedes the log-based
    # findings above when the durable log could be read, because the log
    # window is reset by every container redeploy.
    if durable_watched_service_snapshot is not None:
        watched = durable_watched_service_snapshot
        if not watched.read_ok:
            findings.append(
                Finding(
                    severity=Severity.ERROR,
                    code="DURABLE_WATCH_SERVICE_LOG_UNREADABLE",
                    message=(
                        f"Could not read the durable signal event log for "
                        f"'{watched.service}': {watched.read_error or 'unknown error'}"
                    ),
                    recommendation=(
                        "Check that the agent containers are running and that the shared "
                        "agent-data volume is mounted and readable from inside a container."
                    ),
                )
            )
        else:
            if watched.event_count == 0:
                findings.append(
                    Finding(
                        severity=Severity.WARNING,
                        code="WATCH_SERVICE_SIGNAL_DROUGHT",
                        message=(
                            f"Watched service '{watched.service}' (agent_id "
                            f"'{watched.agent_id or 'unknown'}') has no consensus signals "
                            "recorded in the durable event log"
                        ),
                        recommendation=(
                            "Review the watched strategy thresholds and confirm recent market "
                            "conditions match the thesis."
                        ),
                    )
                )

            if watched.last_signal_at:
                parsed = parse_iso_timestamp(watched.last_signal_at)
                if parsed is not None:
                    age_hours = (datetime.now(UTC) - parsed).total_seconds() / 3600
                    threshold = effective_stale_threshold(
                        signal_stale_hours,
                        watched.median_interval_hours,
                    )
                    if age_hours > threshold:
                        findings.append(
                            Finding(
                                severity=Severity.WARNING,
                                code="WATCH_SERVICE_SIGNAL_STALE",
                                message=(
                                    "Latest durable watched-service consensus signal is stale "
                                    f"({age_hours:.1f}h old at {watched.last_signal_at}, "
                                    f"threshold {threshold:.1f}h)"
                                ),
                                recommendation=(
                                    "Inspect whether this sparse strategy is idle by design or "
                                    "whether conditions changed materially."
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
        "timer_snapshot": asdict(report.timer_snapshot) if report.timer_snapshot else None,
        "signal_snapshot": asdict(report.signal_snapshot) if report.signal_snapshot else None,
        "watched_service_snapshot": (
            asdict(report.watched_service_snapshot) if report.watched_service_snapshot else None
        ),
        "durable_signal_snapshot": (
            asdict(report.durable_signal_snapshot) if report.durable_signal_snapshot else None
        ),
        "durable_watched_service_snapshot": (
            asdict(report.durable_watched_service_snapshot)
            if report.durable_watched_service_snapshot
            else None
        ),
        "findings": [asdict(finding) for finding in report.findings],
    }
    return json.dumps(payload, indent=2)
