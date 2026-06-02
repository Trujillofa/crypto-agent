#!/usr/bin/env python3
"""Detect production drift across git, config, services, and signal activity."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.utils.production_drift_sentinel import (  # noqa: E402
    DriftReport,
    Severity,
    analyze_drift,
    collect_local_config_hashes,
    collect_remote_config_hashes,
    collect_remote_repo_snapshot,
    collect_remote_service_snapshot,
    collect_remote_signal_snapshot,
    collect_remote_timer_snapshot,
    collect_remote_watched_service_snapshot,
    collect_repo_snapshot,
    report_to_json,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Production drift sentinel")
    parser.add_argument("--expected-branch", default="main", help="Expected deploy branch")
    parser.add_argument("--remote-host", default="crypto-agent", help="SSH host alias")
    parser.add_argument("--remote-dir", default="/opt/crypto-agent", help="Remote repo path")
    parser.add_argument("--ssh-config", help="Optional OpenSSH config file passed with ssh -F")
    parser.add_argument("--signal-stale-hours", type=int, default=24)
    parser.add_argument("--log-tail", type=int, default=500)
    parser.add_argument(
        "--watch-service",
        help="Optional docker compose service to inspect separately (for example: agent_sol_sparse)",
    )
    parser.add_argument("--local-only", action="store_true", help="Skip remote checks")
    parser.add_argument("--json", action="store_true", help="Print JSON output")
    parser.add_argument(
        "--fail-on",
        choices=("none", "warning", "error"),
        default="error",
        help="Minimum severity that returns non-zero exit",
    )
    parser.add_argument(
        "--output-prefix",
        default="docs/reports/production-drift-sentinel",
        help="Path prefix for markdown/json artifacts",
    )
    return parser.parse_args()


def _exit_code(fail_on: str, findings: list[object]) -> int:
    has_error = any(getattr(finding, "severity", None) == Severity.ERROR for finding in findings)
    has_warning = any(
        getattr(finding, "severity", None) == Severity.WARNING for finding in findings
    )

    if fail_on == "none":
        return 0
    if fail_on == "warning":
        return 1 if (has_error or has_warning) else 0
    return 1 if has_error else 0


def _render_markdown(report: DriftReport, expected_branch: str) -> str:
    lines: list[str] = []
    lines.append("# Production Drift Sentinel Report")
    lines.append("")
    lines.append(f"- Generated: {datetime.now().isoformat()}")
    lines.append(f"- Expected branch: `{expected_branch}`")
    lines.append("")

    lines.append("## Repo State")
    lines.append("")
    if report.local_repo is not None:
        lines.append(
            "- Local: "
            f"branch={report.local_repo.branch}, "
            f"commit={report.local_repo.commit[:8]}, "
            f"dirty={report.local_repo.dirty}"
        )
    else:
        lines.append("- Local: unavailable")

    if report.remote_repo is not None:
        lines.append(
            "- Remote: "
            f"branch={report.remote_repo.branch}, "
            f"commit={report.remote_repo.commit[:8]}, "
            f"dirty={report.remote_repo.dirty}"
        )
    else:
        lines.append("- Remote: unavailable")

    lines.append("")
    lines.append("## Services")
    lines.append("")
    if report.service_snapshot is not None:
        running = set(report.service_snapshot.running_services)
        lines.append(f"- Total services: {len(report.service_snapshot.all_services)}")
        lines.append(f"- Running services: {len(report.service_snapshot.running_services)}")
        missing = [item for item in report.service_snapshot.all_services if item not in running]
        lines.append(f"- Missing: {', '.join(missing) if missing else 'none'}")
    else:
        lines.append("- Service snapshot unavailable")

    lines.append("")
    lines.append("## Required Timers")
    lines.append("")
    if report.timer_snapshot is not None:
        for timer in report.timer_snapshot.timers:
            lines.append(
                f"- {timer.timer}: enabled={timer.enabled}, active={timer.active}, "
                f"service_result={timer.service_result}, "
                f"exec_status={timer.exec_main_status}, "
                f"latest_report={timer.latest_report_at or 'none'}"
            )
    else:
        lines.append("- Timer snapshot unavailable")

    lines.append("")
    lines.append("## Signal Activity")
    lines.append("")
    if report.signal_snapshot is not None:
        lines.append(f"- Signal count in lookback: {report.signal_snapshot.signal_count}")
        lines.append(f"- Strategy cycle seen: {report.signal_snapshot.saw_strategy_cycle}")
        lines.append(f"- Last signal timestamp: {report.signal_snapshot.last_signal_at or 'none'}")
    else:
        lines.append("- Signal snapshot unavailable")

    if report.watched_service_snapshot is not None:
        lines.append("")
        lines.append("## Watched Service")
        lines.append("")
        lines.append(f"- Service: {report.watched_service_snapshot.service}")
        lines.append(f"- Exists: {report.watched_service_snapshot.exists}")
        lines.append(f"- Running: {report.watched_service_snapshot.running}")
        lines.append(f"- Strategy cycle seen: {report.watched_service_snapshot.saw_strategy_cycle}")
        lines.append(
            f"- Consensus signals in lookback: {report.watched_service_snapshot.signal_count}"
        )
        lines.append(
            f"- Last consensus signal: {report.watched_service_snapshot.last_signal_at or 'none'}"
        )
        lines.append(
            f"- Paper order logs in lookback: {report.watched_service_snapshot.paper_order_count}"
        )
        lines.append(
            f"- Last paper order log: {report.watched_service_snapshot.last_paper_order_at or 'none'}"
        )

    lines.append("")
    lines.append("## Findings")
    lines.append("")
    if report.findings:
        for finding in report.findings:
            lines.append(
                f"- [{finding.severity.upper()}] {finding.code}: {finding.message}"
                f" | Action: {finding.recommendation}"
            )
    else:
        lines.append("- No drift findings")

    lines.append("")
    return "\n".join(lines)


def main() -> int:
    args = parse_args()

    local_repo = None
    local_config_hashes: dict[str, str] = {}
    try:
        local_repo = collect_repo_snapshot(Path.cwd())
        local_config_hashes = collect_local_config_hashes()
    except Exception as exc:  # noqa: BLE001
        local_repo = None
        local_config_hashes = {}
        local_error = str(exc)
    else:
        local_error = None

    remote_repo = None
    remote_config_hashes: dict[str, str] = {}
    service_snapshot = None
    timer_snapshot = None
    signal_snapshot = None
    watched_service_snapshot = None
    remote_error = None

    if not args.local_only:
        try:
            remote_repo = collect_remote_repo_snapshot(
                args.remote_host, args.remote_dir, ssh_config=args.ssh_config
            )
            remote_config_hashes = collect_remote_config_hashes(
                args.remote_host, args.remote_dir, ssh_config=args.ssh_config
            )
            service_snapshot = collect_remote_service_snapshot(
                args.remote_host, args.remote_dir, ssh_config=args.ssh_config
            )
            timer_snapshot = collect_remote_timer_snapshot(
                args.remote_host, args.remote_dir, ssh_config=args.ssh_config
            )
            signal_snapshot = collect_remote_signal_snapshot(
                args.remote_host,
                args.remote_dir,
                service_snapshot,
                tail_lines=args.log_tail,
                ssh_config=args.ssh_config,
            )
            if args.watch_service:
                watched_service_snapshot = collect_remote_watched_service_snapshot(
                    args.remote_host,
                    args.remote_dir,
                    service_snapshot,
                    args.watch_service,
                    tail_lines=args.log_tail,
                    ssh_config=args.ssh_config,
                )
        except Exception as exc:  # noqa: BLE001
            remote_error = str(exc)

    findings = analyze_drift(
        expected_branch=args.expected_branch,
        local_repo=local_repo,
        remote_repo=remote_repo,
        local_config_hashes=local_config_hashes,
        remote_config_hashes=remote_config_hashes,
        service_snapshot=service_snapshot,
        timer_snapshot=timer_snapshot,
        signal_snapshot=signal_snapshot,
        watched_service_snapshot=watched_service_snapshot,
        remote_error=remote_error,
        signal_stale_hours=args.signal_stale_hours,
        remote_checks_enabled=not args.local_only,
    )

    if local_error:
        from src.utils.production_drift_sentinel import Finding

        findings.insert(
            0,
            Finding(
                severity=Severity.ERROR,
                code="LOCAL_INSPECTION_FAILED",
                message=f"Failed local inspection: {local_error}",
                recommendation="Ensure this command runs in a git checkout with readable config files.",
            ),
        )

    report = DriftReport(
        local_repo=local_repo,
        remote_repo=remote_repo,
        local_config_hashes=local_config_hashes,
        remote_config_hashes=remote_config_hashes,
        service_snapshot=service_snapshot,
        timer_snapshot=timer_snapshot,
        signal_snapshot=signal_snapshot,
        watched_service_snapshot=watched_service_snapshot,
        findings=findings,
    )

    if args.json:
        print(report_to_json(report))
    else:
        print("Production Drift Sentinel")
        print(f"Expected branch: {args.expected_branch}")
        if report.local_repo is not None:
            print(
                "Local: "
                f"branch={report.local_repo.branch} "
                f"commit={report.local_repo.commit[:8]} "
                f"dirty={report.local_repo.dirty}"
            )
        if report.remote_repo is not None:
            print(
                "Remote: "
                f"branch={report.remote_repo.branch} "
                f"commit={report.remote_repo.commit[:8]} "
                f"dirty={report.remote_repo.dirty}"
            )

        print("Findings:")
        if report.findings:
            for finding in report.findings:
                print(
                    f"- [{finding.severity.upper()}] {finding.code}: {finding.message}\n"
                    f"  action: {finding.recommendation}"
                )
        else:
            print("- none")

    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    prefix = Path(args.output_prefix)
    prefix.parent.mkdir(parents=True, exist_ok=True)
    json_path = prefix.parent / f"{prefix.name}-{timestamp}.json"
    markdown_path = prefix.parent / f"{prefix.name}-{timestamp}.md"

    with json_path.open("w", encoding="utf-8") as handle:
        payload = json.loads(report_to_json(report))
        payload["expected_branch"] = args.expected_branch
        payload["generated_at"] = datetime.now().isoformat()
        json.dump(payload, handle, indent=2)

    markdown = _render_markdown(report, args.expected_branch)
    with markdown_path.open("w", encoding="utf-8") as handle:
        handle.write(markdown)

    print(f"Markdown report: {markdown_path}")
    print(f"JSON report: {json_path}")

    return _exit_code(args.fail_on, report.findings)


if __name__ == "__main__":
    raise SystemExit(main())
