from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest

from src.utils.production_drift_sentinel import (
    DurableSignalSnapshot,
    DurableWatchedServiceSnapshot,
    RepoSnapshot,
    ServiceSnapshot,
    analyze_drift,
    build_durable_signal_snapshot,
    build_durable_watched_service_snapshot,
    collect_durable_signal_events,
    count_durable_ignored,
    count_durable_received,
    effective_stale_threshold,
    median_signal_interval_hours,
    resolve_service_agent_id,
    summarize_durable_signals,
)

DURABLE_STATUS = {"type": "__status__", "files": 3, "records": 12, "error": ""}


def _event(
    event_type: str,
    ts: str,
    agent_id: str,
    signal_type: str = "BUY",
    reason: str = "",
) -> dict[str, str]:
    return {
        "type": event_type,
        "ts": ts,
        "agent_id": agent_id,
        "signal_type": signal_type,
        "reason": reason,
    }


def test_summarize_durable_signals_counts_all_events() -> None:
    events = [
        _event("signal_received", "2026-10-02T11:44:20+00:00", "agent-a"),
        _event("signal_ignored", "2026-10-03T11:44:20+00:00", "agent-a"),
        _event("signal_received", "2026-10-01T11:44:20+00:00", "agent-b"),
    ]

    count, last = summarize_durable_signals(events)

    # Only actionable BUY/SELL votes count. The ignored event is not a
    # signal the strategy produced, it is a signal that was refused.
    assert count == 2
    assert last == "2026-10-02T11:44:20+00:00"


def test_summarize_durable_signals_filters_by_agent_id() -> None:
    events = [
        _event("signal_received", "2026-10-02T11:44:20+00:00", "agent-a"),
        _event("signal_received", "2026-10-09T11:44:20+00:00", "agent-b"),
    ]

    count, last = summarize_durable_signals(events, agent_id="agent-a")

    assert count == 1
    assert last == "2026-10-02T11:44:20+00:00"


def test_summarize_durable_signals_ignores_empty_timestamps() -> None:
    events = [
        _event("signal_received", "", "agent-a"),
        _event("signal_received", "2026-10-02T11:44:20+00:00", "agent-a"),
    ]

    count, last = summarize_durable_signals(events)

    assert count == 2
    assert last == "2026-10-02T11:44:20+00:00"


def test_summarize_durable_signals_returns_none_when_no_events() -> None:
    count, last = summarize_durable_signals([])

    assert count == 0
    assert last is None


def test_collect_durable_signal_events_parses_status_and_events(monkeypatch) -> None:
    commands = []

    def fake_run_remote_command(
        host: str | None,
        remote_dir: str,
        command: str,
        timeout: int = 20,
        ssh_config: str | None = None,
    ) -> str:
        commands.append(command)
        return "\n".join(
            [
                json.dumps({"type": "__status__", "files": 2, "records": 1, "error": ""}),
                json.dumps(
                    {
                        "type": "signal_received",
                        "ts": "2026-10-02T11:44:20+00:00",
                        "agent_id": "agent-a",
                    }
                ),
            ]
        )

    monkeypatch.setattr(
        "src.utils.production_drift_sentinel.run_remote_command",
        fake_run_remote_command,
    )

    events, status = collect_durable_signal_events(
        None,
        "/srv/app",
        ServiceSnapshot(all_services=["agent_a"], running_services=["agent_a"]),
    )

    assert status["files"] == "2"
    assert status["records"] == "1"
    assert len(events) == 1
    assert events[0]["agent_id"] == "agent-a"
    # The volume is not readable from the host, so the read runs inside a
    # container via `docker compose exec`.
    assert "exec -T agent_a python3" in commands[0]
    assert "/app/data/event_log_*.jsonl" in commands[0]


def test_collect_durable_signal_events_returns_empty_status_when_no_agent_runs(
    monkeypatch,
) -> None:
    def fake_run_remote_command(*args, **kwargs) -> str:  # noqa: ANN002, ANN003
        raise AssertionError("must not read the event log when no agent is running")

    monkeypatch.setattr(
        "src.utils.production_drift_sentinel.run_remote_command",
        fake_run_remote_command,
    )

    events, status = collect_durable_signal_events(
        None,
        "/srv/app",
        ServiceSnapshot(all_services=["agent_a"], running_services=[]),
    )

    assert events == []
    assert status == {}


def test_collect_durable_signal_events_ignores_non_json_output(monkeypatch) -> None:
    def fake_run_remote_command(*args, **kwargs) -> str:  # noqa: ANN002, ANN003
        return "some noise\nnot json\n" + json.dumps(
            {"type": "__status__", "files": 1, "records": 0, "error": ""}
        )

    monkeypatch.setattr(
        "src.utils.production_drift_sentinel.run_remote_command",
        fake_run_remote_command,
    )

    events, status = collect_durable_signal_events(
        None,
        "/srv/app",
        ServiceSnapshot(all_services=["agent_a"], running_services=["agent_a"]),
    )

    assert events == []
    assert status["files"] == "1"


def test_build_durable_signal_snapshot_marks_read_failure() -> None:
    snapshot = build_durable_signal_snapshot([], {})

    assert snapshot.read_ok is False
    assert snapshot.read_error != ""
    assert snapshot.event_count == 0


def test_build_durable_signal_snapshot_marks_success() -> None:
    events = [_event("signal_received", "2026-10-02T11:44:20+00:00", "agent-a")]

    snapshot = build_durable_signal_snapshot(events, DURABLE_STATUS)

    assert snapshot.read_ok is True
    assert snapshot.event_count == 1
    assert snapshot.last_signal_at == "2026-10-02T11:44:20+00:00"
    assert snapshot.files_scanned == 3


def test_build_durable_watched_service_snapshot_filters_by_agent() -> None:
    events = [
        _event("signal_received", "2026-10-02T11:44:20+00:00", "agent-a"),
        _event("signal_received", "2026-10-09T11:44:20+00:00", "agent-b"),
    ]

    snapshot = build_durable_watched_service_snapshot(
        "agent_sol_sparse",
        "agent-a",
        events,
        DURABLE_STATUS,
    )

    assert snapshot.agent_id == "agent-a"
    assert snapshot.event_count == 1
    assert snapshot.last_signal_at == "2026-10-02T11:44:20+00:00"
    assert snapshot.read_ok is True


def test_resolve_service_agent_id_reads_environ(monkeypatch) -> None:
    commands = []

    def fake_run_remote_command(
        host: str | None,
        remote_dir: str,
        command: str,
        timeout: int = 20,
        ssh_config: str | None = None,
    ) -> str:
        commands.append(command)
        return "sol-trend-pullback-sparse\n"

    monkeypatch.setattr(
        "src.utils.production_drift_sentinel.run_remote_command",
        fake_run_remote_command,
    )

    assert resolve_service_agent_id(None, "/srv/app", "agent_sol_sparse") == (
        "sol-trend-pullback-sparse"
    )
    assert "exec -T agent_sol_sparse printenv AGENT_ID" in commands[0]


def test_analyze_drift_uses_durable_signal_source_when_available() -> None:
    # The staleness check compares the signal age against the wall clock, so
    # the timestamp must be relative to now. A hardcoded date turns this test
    # into a time bomb: it passed while the date was fresh and started
    # failing 24h later, with no code change.
    last_signal_at = (datetime.now(UTC) - timedelta(hours=1)).isoformat()

    findings = analyze_drift(
        expected_branch="main",
        local_repo=None,
        remote_repo=RepoSnapshot(branch="main", commit="a" * 40, dirty=False),
        local_config_hashes={},
        remote_config_hashes={},
        service_snapshot=None,
        signal_snapshot=None,
        watched_service_snapshot=None,
        remote_error=None,
        signal_stale_hours=24,
        durable_signal_snapshot=DurableSignalSnapshot(
            event_count=4,
            last_signal_at=last_signal_at,
            read_ok=True,
            files_scanned=3,
        ),
    )

    codes = {finding.code for finding in findings}
    assert "SIGNAL_DROUGHT" not in codes
    assert "SIGNAL_STALE" not in codes


def test_analyze_drift_reports_durable_read_failure_as_error() -> None:
    findings = analyze_drift(
        expected_branch="main",
        local_repo=None,
        remote_repo=RepoSnapshot(branch="main", commit="a" * 40, dirty=False),
        local_config_hashes={},
        remote_config_hashes={},
        service_snapshot=None,
        signal_snapshot=None,
        watched_service_snapshot=None,
        remote_error=None,
        signal_stale_hours=24,
        durable_signal_snapshot=DurableSignalSnapshot(
            event_count=0,
            last_signal_at=None,
            read_ok=False,
            read_error="volume not mounted",
        ),
    )

    codes = {finding.code for finding in findings}
    # A broken instrument must not look like a quiet market.
    assert "DURABLE_SIGNAL_LOG_UNREADABLE" in codes
    assert "SIGNAL_DROUGHT" not in codes


def test_analyze_drift_reports_durable_drought_and_staleness() -> None:
    findings = analyze_drift(
        expected_branch="main",
        local_repo=None,
        remote_repo=RepoSnapshot(branch="main", commit="a" * 40, dirty=False),
        local_config_hashes={},
        remote_config_hashes={},
        service_snapshot=None,
        signal_snapshot=None,
        watched_service_snapshot=None,
        remote_error=None,
        signal_stale_hours=24,
        durable_signal_snapshot=DurableSignalSnapshot(
            event_count=0,
            last_signal_at=None,
            read_ok=True,
            files_scanned=3,
        ),
        durable_watched_service_snapshot=DurableWatchedServiceSnapshot(
            service="agent_sol_sparse",
            agent_id="sol-trend-pullback-sparse",
            event_count=0,
            last_signal_at=None,
            read_ok=True,
        ),
    )

    codes = {finding.code for finding in findings}
    assert "SIGNAL_DROUGHT" in codes
    assert "WATCH_SERVICE_SIGNAL_DROUGHT" in codes


def test_analyze_drift_does_not_double_report_log_findings() -> None:
    """With a durable source available, the log-based drought finding is
    suppressed so the report does not list the same problem twice."""
    from src.utils.production_drift_sentinel import SignalSnapshot, WatchedServiceSnapshot

    findings = analyze_drift(
        expected_branch="main",
        local_repo=None,
        remote_repo=RepoSnapshot(branch="main", commit="a" * 40, dirty=False),
        local_config_hashes={},
        remote_config_hashes={},
        service_snapshot=None,
        signal_snapshot=SignalSnapshot(
            signal_count=0, last_signal_at=None, saw_strategy_cycle=True
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
        durable_signal_snapshot=DurableSignalSnapshot(
            event_count=0,
            last_signal_at=None,
            read_ok=True,
            files_scanned=3,
        ),
        durable_watched_service_snapshot=DurableWatchedServiceSnapshot(
            service="agent_sol_sparse",
            agent_id="sol-trend-pullback-sparse",
            event_count=0,
            last_signal_at=None,
            read_ok=True,
        ),
    )

    drought_findings = [f for f in findings if f.code == "WATCH_SERVICE_SIGNAL_DROUGHT"]
    assert len(drought_findings) == 1


def test_count_durable_ignored_counts_only_guard_blocks() -> None:
    """A by-design ignore in a spot-only account must not count as
    over-filtering, or every healthy long-only agent would be flagged."""
    events = [
        _event("signal_received", "2026-10-02T11:44:20+00:00", "agent-a", "BUY"),
        _event(
            "signal_ignored",
            "2026-10-03T11:44:20+00:00",
            "agent-a",
            "SELL",
            "Guard blocked: risk guard",
        ),
        _event(
            "signal_ignored",
            "2026-10-04T11:44:20+00:00",
            "agent-a",
            "SELL",
            "strategy_sell_ignored_spot_mode",
        ),
        _event(
            "signal_ignored",
            "2026-10-05T11:44:20+00:00",
            "agent-a",
            "SELL",
            "strategy_sell_exits_via_sl_tp_only",
        ),
    ]

    assert count_durable_ignored(events) == 1
    assert count_durable_ignored(events, agent_id="agent-b") == 0
    assert count_durable_received(events) == 1


def test_build_durable_signal_snapshot_counts_ignored() -> None:
    events = [
        _event("signal_received", "2026-10-02T11:44:20+00:00", "agent-a", "BUY"),
        _event(
            "signal_ignored",
            "2026-10-03T11:44:20+00:00",
            "agent-a",
            "SELL",
            "Guard blocked: risk guard",
        ),
    ]

    snapshot = build_durable_signal_snapshot(events, DURABLE_STATUS)

    assert snapshot.event_count == 2
    assert snapshot.received_count == 1
    assert snapshot.guard_block_count == 1
    assert snapshot.ignored_count == 1


def test_analyze_drift_flags_over_filtering() -> None:
    """Signals produced but refused by a guard need a different fix than
    a strategy that stopped producing signals."""
    findings = analyze_drift(
        expected_branch="main",
        local_repo=None,
        remote_repo=RepoSnapshot(branch="main", commit="a" * 40, dirty=False),
        local_config_hashes={},
        remote_config_hashes={},
        service_snapshot=None,
        signal_snapshot=None,
        watched_service_snapshot=None,
        remote_error=None,
        signal_stale_hours=24,
        durable_signal_snapshot=DurableSignalSnapshot(
            event_count=10,
            last_signal_at=(datetime.now(UTC) - timedelta(hours=1)).isoformat(),
            read_ok=True,
            files_scanned=3,
            ignored_count=8,
            received_count=2,
            guard_block_count=8,
        ),
    )

    codes = {finding.code for finding in findings}
    assert "SIGNAL_OVER_FILTERED" in codes
    assert "SIGNAL_DROUGHT" not in codes


def test_analyze_drift_does_not_flag_over_filtering_when_signals_flow() -> None:
    findings = analyze_drift(
        expected_branch="main",
        local_repo=None,
        remote_repo=RepoSnapshot(branch="main", commit="a" * 40, dirty=False),
        local_config_hashes={},
        remote_config_hashes={},
        service_snapshot=None,
        signal_snapshot=None,
        watched_service_snapshot=None,
        remote_error=None,
        signal_stale_hours=24,
        durable_signal_snapshot=DurableSignalSnapshot(
            event_count=10,
            last_signal_at=(datetime.now(UTC) - timedelta(hours=1)).isoformat(),
            read_ok=True,
            files_scanned=3,
            ignored_count=1,
            received_count=9,
            guard_block_count=1,
        ),
    )

    codes = {finding.code for finding in findings}
    assert "SIGNAL_OVER_FILTERED" not in codes


def test_median_interval_uses_the_agents_own_cadence() -> None:
    """The 4h panic-block agent signals about every nine days, so a
    fixed 72-hour threshold would report it stale almost permanently."""
    events = [
        _event("signal_received", "2026-08-28T11:00:00+00:00", "agent-a", "BUY"),
        _event("signal_received", "2026-09-06T11:00:00+00:00", "agent-a", "BUY"),
        _event("signal_received", "2026-09-15T11:00:00+00:00", "agent-a", "BUY"),
        _event("signal_received", "2026-09-24T11:00:00+00:00", "agent-a", "BUY"),
        _event("signal_received", "2026-10-03T11:00:00+00:00", "agent-a", "BUY"),
    ]

    assert median_signal_interval_hours(events) == pytest.approx(216.0)


def test_median_interval_needs_at_least_two_signals() -> None:
    events = [_event("signal_received", "2026-10-08T11:00:00+00:00", "agent-a", "BUY")]

    assert median_signal_interval_hours(events) is None


def test_median_interval_filters_by_agent() -> None:
    events = [
        _event("signal_received", "2026-10-01T00:00:00+00:00", "agent-a", "BUY"),
        _event("signal_received", "2026-10-01T12:00:00+00:00", "agent-b", "BUY"),
    ]

    assert median_signal_interval_hours(events, agent_id="agent-b") is None


def test_effective_threshold_adapts_to_a_slow_cadence() -> None:
    """A nine-day cadence must not trip a 72-hour staleness threshold."""
    assert effective_stale_threshold(72, 216.0) == pytest.approx(648.0)


def test_effective_threshold_falls_back_when_cadence_is_unknown() -> None:
    assert effective_stale_threshold(72, None) == 72.0
    assert effective_stale_threshold(72, 0) == 72.0


def test_effective_threshold_never_goes_below_configured() -> None:
    """A fast cadence must not make the instrument noisier than before."""
    assert effective_stale_threshold(72, 1.0) == 72.0


def test_stale_finding_uses_cadence_adaptive_threshold() -> None:
    """A signal that is older than the configured threshold but within
    the agent's own cadence must not be reported as stale."""
    findings = analyze_drift(
        expected_branch="main",
        local_repo=None,
        remote_repo=RepoSnapshot(branch="main", commit="a" * 40, dirty=False),
        local_config_hashes={},
        remote_config_hashes={},
        service_snapshot=None,
        signal_snapshot=None,
        watched_service_snapshot=None,
        remote_error=None,
        signal_stale_hours=72,
        durable_signal_snapshot=DurableSignalSnapshot(
            event_count=3,
            last_signal_at=(datetime.now(UTC) - timedelta(hours=1)).isoformat(),
            read_ok=True,
            files_scanned=3,
            received_count=3,
            guard_block_count=0,
            median_interval_hours=216.0,
        ),
    )

    codes = {finding.code for finding in findings}
    assert "SIGNAL_STALE" not in codes


def test_median_interval_does_not_mix_agents() -> None:
    """Two different agents signalling an hour apart is not a fast
    cadence for either one of them."""
    events = [
        _event("signal_received", "2026-10-01T00:00:00+00:00", "agent-a", "BUY"),
        _event("signal_received", "2026-10-01T01:00:00+00:00", "agent-b", "BUY"),
        _event("signal_received", "2026-10-10T00:00:00+00:00", "agent-a", "BUY"),
        _event("signal_received", "2026-10-10T01:00:00+00:00", "agent-b", "BUY"),
        _event("signal_received", "2026-10-19T00:00:00+00:00", "agent-a", "BUY"),
        _event("signal_received", "2026-10-19T01:00:00+00:00", "agent-b", "BUY"),
        _event("signal_received", "2026-10-28T00:00:00+00:00", "agent-a", "BUY"),
        _event("signal_received", "2026-10-28T01:00:00+00:00", "agent-b", "BUY"),
        _event("signal_received", "2026-11-06T00:00:00+00:00", "agent-a", "BUY"),
        _event("signal_received", "2026-11-06T01:00:00+00:00", "agent-b", "BUY"),
    ]

    # Per agent each gap is 216h. Mixed into one series the gaps would
    # alternate between 1h and 215h, which would read as a fast cadence.
    assert median_signal_interval_hours(events) == pytest.approx(216.0)


def test_cadence_needs_a_minimum_number_of_samples() -> None:
    """A single gap is not a cadence.

    The stale threshold is derived from the agent's own signal spacing and
    then applied to those same signals. With only 2 signals there is one
    gap, so the threshold inherits that signal's age and the instrument
    warns forever without having learned the agent's real cadence.
    """
    two = [
        _event("signal_received", "2026-04-25T15:17:33+00:00", "agent-a", "BUY"),
        _event("signal_received", "2026-05-05T18:59:20+00:00", "agent-a", "BUY"),
    ]
    assert median_signal_interval_hours(two) is None

    # With enough samples the cadence is established.
    five = [
        _event("signal_received", f"2026-04-{day:02d}T12:00:00+00:00", "agent-a", "BUY")
        for day in (1, 2, 3, 4, 5)
    ]
    assert median_signal_interval_hours(five) == pytest.approx(24.0)


def test_hold_votes_do_not_count_as_signals() -> None:
    """The executor logs `signal_received` for HOLD votes too. Counting
    those would make a dormant strategy look busy."""
    events = [
        _event("signal_received", "2026-10-20T00:00:00+00:00", "agent-a", "HOLD"),
        _event("signal_received", "2026-10-20T04:00:00+00:00", "agent-a", "HOLD"),
        _event("signal_received", "2026-10-20T08:00:00+00:00", "agent-a", "BUY"),
    ]

    assert count_durable_received(events) == 1
    count, newest = summarize_durable_signals(events)
    assert count == 1
    assert newest == "2026-10-20T08:00:00+00:00"


def test_hold_votes_do_not_affect_the_cadence() -> None:
    """HOLD votes arrive every cycle, so including them would make the
    agent look like it signals constantly."""
    events = [
        _event("signal_received", "2026-10-01T00:00:00+00:00", "agent-a", "HOLD"),
        _event("signal_received", "2026-10-01T04:00:00+00:00", "agent-a", "HOLD"),
        _event("signal_received", "2026-10-10T00:00:00+00:00", "agent-a", "BUY"),
        _event("signal_received", "2026-10-19T00:00:00+00:00", "agent-a", "SELL"),
        _event("signal_received", "2026-10-28T00:00:00+00:00", "agent-a", "BUY"),
        _event("signal_received", "2026-11-06T00:00:00+00:00", "agent-a", "SELL"),
        _event("signal_received", "2026-11-15T00:00:00+00:00", "agent-a", "BUY"),
    ]

    # The HOLD votes must not contribute gaps: with them included the series
    # would be dominated by 4h spacing and read as a fast cadence.
    assert median_signal_interval_hours(events) == pytest.approx(216.0)
