from __future__ import annotations

from datetime import UTC, datetime, timedelta

from src.utils.production_drift_sentinel import (
    IGNORED_CONFIG_FILES,
    RepoSnapshot,
    ServiceSnapshot,
    SignalSnapshot,
    TimerSnapshot,
    TimerState,
    WatchedServiceSnapshot,
    analyze_drift,
    collect_local_config_hashes,
    collect_remote_service_snapshot,
    collect_remote_timer_snapshot,
    epoch_to_iso_timestamp,
    parse_iso_timestamp,
    parse_sha256_lines,
    run_remote_command,
)


def test_parse_sha256_lines_extracts_file_names() -> None:
    text = "abc123  config/settings.yaml\ndef456  config/settings.sentiment_macro.yaml\n"
    parsed = parse_sha256_lines(text)

    assert parsed["settings.yaml"] == "abc123"
    assert parsed["settings.sentiment_macro.yaml"] == "def456"


def test_parse_sha256_lines_ignores_research_only_configs() -> None:
    ignored_name = next(iter(IGNORED_CONFIG_FILES))
    text = f"abc123  config/settings.yaml\ndef456  config/{ignored_name}\n"

    parsed = parse_sha256_lines(text)

    assert parsed["settings.yaml"] == "abc123"
    assert ignored_name not in parsed


def test_collect_local_config_hashes_ignores_research_only_configs(tmp_path) -> None:
    (tmp_path / "settings.yaml").write_text("mode: paper\n", encoding="utf-8")
    ignored_name = next(iter(IGNORED_CONFIG_FILES))
    (tmp_path / ignored_name).write_text("mode: paper\n", encoding="utf-8")

    hashes = collect_local_config_hashes(tmp_path)

    assert "settings.yaml" in hashes
    assert ignored_name not in hashes


def test_collect_remote_service_snapshot_uses_production_compose(monkeypatch) -> None:
    commands = []

    def fake_run_remote_command(
        host: str, remote_dir: str, command: str, ssh_config: str | None = None
    ) -> str:
        assert ssh_config is None
        commands.append(command)
        return "agent_sentiment_macro\n" if "config --services" in command else ""

    monkeypatch.setattr(
        "src.utils.production_drift_sentinel.run_remote_command",
        fake_run_remote_command,
    )

    snapshot = collect_remote_service_snapshot("host", "/srv/app")

    assert snapshot.all_services == ["agent_sentiment_macro"]
    assert commands == [
        "docker compose -f docker-compose.prod.yml config --services",
        "docker compose -f docker-compose.prod.yml ps --status running --services",
    ]


def test_run_remote_command_passes_explicit_ssh_config(monkeypatch) -> None:
    commands = []

    def fake_run_command(command: list[str], cwd=None, timeout: int = 15) -> str:
        commands.append(command)
        return ""

    monkeypatch.setattr("src.utils.production_drift_sentinel.run_command", fake_run_command)

    run_remote_command("host", "/srv/app", "git status", ssh_config="~/.ssh/config")

    assert commands == [["ssh", "-F", "~/.ssh/config", "host", "cd /srv/app && git status"]]


def test_run_remote_command_can_inspect_production_locally(monkeypatch) -> None:
    commands = []

    def fake_run_command(command: list[str], cwd=None, timeout: int = 15) -> str:
        commands.append(command)
        return ""

    monkeypatch.setattr("src.utils.production_drift_sentinel.run_command", fake_run_command)

    run_remote_command(None, "/srv/app", "git status")

    assert commands == [["bash", "-lc", "cd /srv/app && git status"]]


def test_collect_remote_timer_snapshot_reads_enabled_and_active(monkeypatch) -> None:
    commands = []

    def fake_run_remote_command(
        host: str, remote_dir: str, command: str, ssh_config: str | None = None
    ) -> str:
        commands.append(command)
        if "is-enabled" in command:
            return "enabled"
        if "is-active" in command:
            return "active"
        if "--property=Result" in command:
            return "success"
        if "--property=ExecMainStatus" in command:
            return "0"
        return "1780359302.0"

    monkeypatch.setattr(
        "src.utils.production_drift_sentinel.run_remote_command",
        fake_run_remote_command,
    )

    snapshot = collect_remote_timer_snapshot("host", "/srv/app", timers=("report.timer",))

    assert snapshot == TimerSnapshot(
        timers=[
            TimerState(
                timer="report.timer",
                enabled="enabled",
                active="active",
                service_result="success",
                exec_main_status="0",
                latest_report_at="2026-06-02T00:15:02+00:00",
                max_report_age_hours=36,
            )
        ]
    )
    assert commands == [
        "systemctl is-enabled report.timer 2>/dev/null || true",
        "systemctl is-active report.timer 2>/dev/null || true",
        "systemctl show report.service --property=Result --value",
        "systemctl show report.service --property=ExecMainStatus --value",
        "find data/reports -maxdepth 1 -type f -name 'paper-validation-report-*.json' -printf '%T@\\n' 2>/dev/null | sort -nr | head -n 1",
    ]


def test_collect_remote_timer_snapshot_uses_hourly_sentinel_artifact_policy(monkeypatch) -> None:
    commands = []

    def fake_run_remote_command(
        host: str, remote_dir: str, command: str, ssh_config: str | None = None
    ) -> str:
        commands.append(command)
        if "is-enabled" in command:
            return "enabled"
        if "is-active" in command:
            return "active"
        if "--property=Result" in command:
            return "success"
        if "--property=ExecMainStatus" in command:
            return "0"
        return "1780359302.0"

    monkeypatch.setattr(
        "src.utils.production_drift_sentinel.run_remote_command",
        fake_run_remote_command,
    )

    snapshot = collect_remote_timer_snapshot(
        "host",
        "/srv/app",
        timers=("crypto-agent-production-drift-sentinel.timer",),
    )

    assert snapshot.timers[0].max_report_age_hours == 2
    assert (
        "find data/reports -maxdepth 1 -type f "
        "-name 'production-drift-sentinel-*.json' -printf '%T@\\n' "
        "2>/dev/null | sort -nr | head -n 1"
    ) in commands


def test_epoch_to_iso_timestamp_returns_none_for_missing_artifact() -> None:
    assert epoch_to_iso_timestamp("") is None


def test_analyze_drift_detects_remote_branch_mismatch_and_dirty() -> None:
    findings = analyze_drift(
        expected_branch="main",
        local_repo=RepoSnapshot(branch="feat/x", commit="a" * 40, dirty=True),
        remote_repo=RepoSnapshot(branch="feat/y", commit="b" * 40, dirty=True),
        local_config_hashes={"settings.yaml": "111"},
        remote_config_hashes={"settings.yaml": "111"},
        service_snapshot=ServiceSnapshot(all_services=["agent"], running_services=["agent"]),
        signal_snapshot=SignalSnapshot(
            signal_count=1, last_signal_at=None, saw_strategy_cycle=True
        ),
        watched_service_snapshot=None,
        remote_error=None,
        signal_stale_hours=24,
    )

    codes = {finding.code for finding in findings}
    assert "REMOTE_BRANCH_MISMATCH" in codes
    assert "LOCAL_DIRTY_WORKTREE" in codes
    assert "REMOTE_DIRTY_WORKTREE" in codes
    assert "LOCAL_REMOTE_COMMIT_DRIFT" in codes


def test_analyze_drift_detects_config_and_service_drift() -> None:
    findings = analyze_drift(
        expected_branch="main",
        local_repo=RepoSnapshot(branch="main", commit="a" * 40, dirty=False),
        remote_repo=RepoSnapshot(branch="main", commit="a" * 40, dirty=False),
        local_config_hashes={"settings.yaml": "111", "settings.sentiment_macro.yaml": "222"},
        remote_config_hashes={"settings.yaml": "999"},
        service_snapshot=ServiceSnapshot(
            all_services=["agent", "timescaledb"],
            running_services=["timescaledb"],
        ),
        signal_snapshot=SignalSnapshot(
            signal_count=0, last_signal_at=None, saw_strategy_cycle=True
        ),
        watched_service_snapshot=None,
        remote_error=None,
        signal_stale_hours=24,
    )

    codes = {finding.code for finding in findings}
    assert "CONFIG_HASH_DRIFT" in codes
    assert "REMOTE_CONFIG_MISSING" in codes
    assert "SERVICES_NOT_RUNNING" in codes
    assert "SIGNAL_DROUGHT" in codes


def test_analyze_drift_detects_unhealthy_required_timer() -> None:
    findings = analyze_drift(
        expected_branch="main",
        local_repo=RepoSnapshot(branch="main", commit="a" * 40, dirty=False),
        remote_repo=RepoSnapshot(branch="main", commit="a" * 40, dirty=False),
        local_config_hashes={"settings.yaml": "111"},
        remote_config_hashes={"settings.yaml": "111"},
        service_snapshot=ServiceSnapshot(all_services=["agent"], running_services=["agent"]),
        timer_snapshot=TimerSnapshot(
            timers=[
                TimerState(
                    timer="crypto-agent-paper-validation-report.timer",
                    enabled="disabled",
                    active="inactive",
                    service_result="success",
                    exec_main_status="0",
                    latest_report_at=datetime.now(UTC).isoformat(),
                    max_report_age_hours=36,
                )
            ]
        ),
        signal_snapshot=SignalSnapshot(
            signal_count=1, last_signal_at=None, saw_strategy_cycle=True
        ),
        watched_service_snapshot=None,
        remote_error=None,
        signal_stale_hours=24,
    )

    codes = {finding.code for finding in findings}
    assert "REQUIRED_TIMER_NOT_HEALTHY" in codes


def test_analyze_drift_detects_failed_timer_service_and_missing_report() -> None:
    findings = analyze_drift(
        expected_branch="main",
        local_repo=RepoSnapshot(branch="main", commit="a" * 40, dirty=False),
        remote_repo=RepoSnapshot(branch="main", commit="a" * 40, dirty=False),
        local_config_hashes={},
        remote_config_hashes={},
        service_snapshot=None,
        timer_snapshot=TimerSnapshot(
            timers=[
                TimerState(
                    timer="report.timer",
                    enabled="enabled",
                    active="active",
                    service_result="failed",
                    exec_main_status="1",
                    latest_report_at=None,
                    max_report_age_hours=36,
                )
            ]
        ),
        signal_snapshot=None,
        watched_service_snapshot=None,
        remote_error=None,
        signal_stale_hours=24,
    )

    codes = {finding.code for finding in findings}
    assert "REQUIRED_TIMER_SERVICE_FAILED" in codes
    assert "TIMER_REPORT_MISSING" in codes


def test_analyze_drift_detects_stale_paper_validation_report() -> None:
    stale_report = (datetime.now(UTC) - timedelta(hours=37)).isoformat()
    findings = analyze_drift(
        expected_branch="main",
        local_repo=RepoSnapshot(branch="main", commit="a" * 40, dirty=False),
        remote_repo=RepoSnapshot(branch="main", commit="a" * 40, dirty=False),
        local_config_hashes={},
        remote_config_hashes={},
        service_snapshot=None,
        timer_snapshot=TimerSnapshot(
            timers=[
                TimerState(
                    timer="report.timer",
                    enabled="enabled",
                    active="active",
                    service_result="success",
                    exec_main_status="0",
                    latest_report_at=stale_report,
                    max_report_age_hours=36,
                )
            ]
        ),
        signal_snapshot=None,
        watched_service_snapshot=None,
        remote_error=None,
        signal_stale_hours=24,
    )

    codes = {finding.code for finding in findings}
    assert "TIMER_REPORT_STALE" in codes


def test_remote_error_short_circuits_remote_analysis() -> None:
    findings = analyze_drift(
        expected_branch="main",
        local_repo=RepoSnapshot(branch="main", commit="a" * 40, dirty=False),
        remote_repo=None,
        local_config_hashes={},
        remote_config_hashes={},
        service_snapshot=None,
        signal_snapshot=None,
        watched_service_snapshot=None,
        remote_error="ssh timeout",
        signal_stale_hours=24,
    )

    codes = {finding.code for finding in findings}
    assert "REMOTE_UNREACHABLE" in codes


def test_parse_iso_timestamp_handles_z_suffix() -> None:
    parsed = parse_iso_timestamp("2026-03-05T10:11:12Z")
    assert parsed is not None
    assert parsed.isoformat().startswith("2026-03-05T10:11:12")


def test_local_only_mode_skips_remote_errors() -> None:
    findings = analyze_drift(
        expected_branch="main",
        local_repo=RepoSnapshot(branch="main", commit="a" * 40, dirty=False),
        remote_repo=None,
        local_config_hashes={},
        remote_config_hashes={},
        service_snapshot=None,
        signal_snapshot=None,
        watched_service_snapshot=None,
        remote_error=None,
        signal_stale_hours=24,
        remote_checks_enabled=False,
    )

    codes = {finding.code for finding in findings}
    assert "REMOTE_REPO_UNAVAILABLE" not in codes


def test_analyze_drift_detects_missing_watched_service() -> None:
    findings = analyze_drift(
        expected_branch="main",
        local_repo=RepoSnapshot(branch="main", commit="a" * 40, dirty=False),
        remote_repo=RepoSnapshot(branch="main", commit="a" * 40, dirty=False),
        local_config_hashes={"settings.yaml": "111"},
        remote_config_hashes={"settings.yaml": "111"},
        service_snapshot=ServiceSnapshot(
            all_services=["agent", "timescaledb"],
            running_services=["agent", "timescaledb"],
        ),
        signal_snapshot=SignalSnapshot(
            signal_count=1,
            last_signal_at="2026-03-11T10:00:00+00:00",
            saw_strategy_cycle=True,
        ),
        watched_service_snapshot=WatchedServiceSnapshot(
            service="agent_sol_sparse",
            exists=False,
            running=False,
            signal_count=0,
            last_signal_at=None,
            saw_strategy_cycle=False,
            paper_order_count=0,
            last_paper_order_at=None,
        ),
        remote_error=None,
        signal_stale_hours=24,
    )

    codes = {finding.code for finding in findings}
    assert "WATCH_SERVICE_MISSING" in codes


def test_analyze_drift_detects_watched_service_signal_drought() -> None:
    findings = analyze_drift(
        expected_branch="main",
        local_repo=RepoSnapshot(branch="main", commit="a" * 40, dirty=False),
        remote_repo=RepoSnapshot(branch="main", commit="a" * 40, dirty=False),
        local_config_hashes={"settings.yaml": "111"},
        remote_config_hashes={"settings.yaml": "111"},
        service_snapshot=ServiceSnapshot(
            all_services=["agent", "agent_sol_sparse", "timescaledb"],
            running_services=["agent", "agent_sol_sparse", "timescaledb"],
        ),
        signal_snapshot=SignalSnapshot(
            signal_count=2,
            last_signal_at="2026-03-11T10:00:00+00:00",
            saw_strategy_cycle=True,
        ),
        watched_service_snapshot=WatchedServiceSnapshot(
            service="agent_sol_sparse",
            exists=True,
            running=True,
            signal_count=0,
            last_signal_at=None,
            saw_strategy_cycle=True,
            paper_order_count=0,
            last_paper_order_at=None,
        ),
        remote_error=None,
        signal_stale_hours=24,
    )

    codes = {finding.code for finding in findings}
    assert "WATCH_SERVICE_SIGNAL_DROUGHT" in codes


def _load_sentinel_script():
    import importlib.util
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    spec = importlib.util.spec_from_file_location(
        "production_drift_sentinel_script",
        root / "scripts" / "production_drift_sentinel.py",
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _finding(severity: str, code: str, message: str) -> object:
    from types import SimpleNamespace

    return SimpleNamespace(severity=severity, code=code, message=message)


def test_dedupe_first_alert_passes_through_and_records_state(tmp_path) -> None:
    script = _load_sentinel_script()
    state = tmp_path / "state"
    findings = [_finding("error", "GIT_DRIFT", "branch mismatch")]

    result = script._apply_dedupe(1, state, script._findings_fingerprint(findings))

    assert result == 1
    assert state.exists()


def test_dedupe_suppresses_repeat_of_same_findings(tmp_path) -> None:
    script = _load_sentinel_script()
    state = tmp_path / "state"
    fingerprint = script._findings_fingerprint([_finding("error", "GIT_DRIFT", "branch mismatch")])

    assert script._apply_dedupe(1, state, fingerprint) == 1
    assert script._apply_dedupe(1, state, fingerprint) == 0


def test_dedupe_alerts_again_when_findings_change(tmp_path) -> None:
    script = _load_sentinel_script()
    state = tmp_path / "state"

    first = script._findings_fingerprint([_finding("error", "GIT_DRIFT", "branch mismatch")])
    second = script._findings_fingerprint(
        [_finding("error", "WATCH_SERVICE_SIGNAL_DROUGHT", "no signals")]
    )

    assert script._apply_dedupe(1, state, first) == 1
    assert script._apply_dedupe(1, state, second) == 1


def test_dedupe_clean_run_resets_state_so_next_drift_alerts(tmp_path) -> None:
    script = _load_sentinel_script()
    state = tmp_path / "state"
    fingerprint = script._findings_fingerprint([_finding("error", "GIT_DRIFT", "branch mismatch")])

    assert script._apply_dedupe(1, state, fingerprint) == 1
    assert script._apply_dedupe(0, state, script._findings_fingerprint([])) == 0
    assert not state.exists()
    assert script._apply_dedupe(1, state, fingerprint) == 1
