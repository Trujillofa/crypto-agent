from __future__ import annotations

from src.utils.production_drift_sentinel import (
    RepoSnapshot,
    ServiceSnapshot,
    SignalSnapshot,
    analyze_drift,
    parse_iso_timestamp,
    parse_sha256_lines,
)


def test_parse_sha256_lines_extracts_file_names() -> None:
    text = "abc123  config/settings.yaml\ndef456  config/settings.agent2.yaml\n"
    parsed = parse_sha256_lines(text)

    assert parsed["settings.yaml"] == "abc123"
    assert parsed["settings.agent2.yaml"] == "def456"


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
        local_config_hashes={"settings.yaml": "111", "settings.agent2.yaml": "222"},
        remote_config_hashes={"settings.yaml": "999"},
        service_snapshot=ServiceSnapshot(
            all_services=["agent", "timescaledb"],
            running_services=["timescaledb"],
        ),
        signal_snapshot=SignalSnapshot(
            signal_count=0, last_signal_at=None, saw_strategy_cycle=True
        ),
        remote_error=None,
        signal_stale_hours=24,
    )

    codes = {finding.code for finding in findings}
    assert "CONFIG_HASH_DRIFT" in codes
    assert "REMOTE_CONFIG_MISSING" in codes
    assert "SERVICES_NOT_RUNNING" in codes
    assert "SIGNAL_DROUGHT" in codes


def test_remote_error_short_circuits_remote_analysis() -> None:
    findings = analyze_drift(
        expected_branch="main",
        local_repo=RepoSnapshot(branch="main", commit="a" * 40, dirty=False),
        remote_repo=None,
        local_config_hashes={},
        remote_config_hashes={},
        service_snapshot=None,
        signal_snapshot=None,
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
        remote_error=None,
        signal_stale_hours=24,
        remote_checks_enabled=False,
    )

    codes = {finding.code for finding in findings}
    assert "REMOTE_REPO_UNAVAILABLE" not in codes
