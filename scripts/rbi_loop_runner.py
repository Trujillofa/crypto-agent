#!/usr/bin/env python3
"""Run one supervised RBI loop step from guard artifacts."""

from __future__ import annotations

import argparse
import json
import shlex
import subprocess
import sys
import time
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.rbi_loop_guard import (
    ACTION_CHECK_OVERLAP,
    ACTION_RUN_AUTORESEARCH,
    ACTION_RUN_BOOTSTRAP_1000,
    ACTION_RUN_CHEAP_PROBE,
    LoopDecision,
    _read_json,
    decide_loop_action,
)

EXECUTABLE_ACTIONS = {
    ACTION_RUN_CHEAP_PROBE: "probe_command",
    ACTION_RUN_AUTORESEARCH: "autoresearch_command",
    ACTION_RUN_BOOTSTRAP_1000: "bootstrap_command",
    ACTION_CHECK_OVERLAP: "overlap_command",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Persist an RBI guard decision and optionally execute the next command."
    )
    parser.add_argument("--lane-name", required=True, help="Stable lane slug used in artifacts.")
    parser.add_argument("--lane-brief", help="Path to lane brief/spec/report.")
    parser.add_argument(
        "--probe-verdict",
        choices=("HAS_PULSE", "WEAK_EDGE", "NO_PULSE"),
        help="Cheap-probe verdict for the lane.",
    )
    parser.add_argument("--last-result", help="Path to autoresearch last_result.json.")
    parser.add_argument("--overlap-report", help="Path to overlap report JSON.")
    parser.add_argument(
        "--decision-output",
        help="Path for decision JSON. Defaults to research/rbi_loop/<lane-name>/decision.json.",
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Execute the command mapped to the guard action. Default only writes decision.",
    )
    parser.add_argument("--probe-command", help="Command for RUN_CHEAP_PROBE.")
    parser.add_argument("--autoresearch-command", help="Command for RUN_AUTORESEARCH.")
    parser.add_argument("--bootstrap-command", help="Command for RUN_BOOTSTRAP_1000.")
    parser.add_argument("--overlap-command", help="Command for CHECK_OVERLAP.")
    parser.add_argument("--timeout-seconds", type=int, default=1800)
    return parser.parse_args()


def _default_decision_output(lane_name: str) -> Path:
    return Path("research") / "rbi_loop" / lane_name / "decision.json"


def _command_for_decision(args: argparse.Namespace, decision: LoopDecision) -> str | None:
    attr = EXECUTABLE_ACTIONS.get(decision.action)
    if attr is None:
        return None
    value = getattr(args, attr)
    return str(value) if value else None


def _tail(text: str, *, limit: int = 40) -> list[str]:
    return text.strip().splitlines()[-limit:]


def _run_command(command: str, *, timeout_seconds: int) -> dict[str, Any]:
    started = time.monotonic()
    try:
        completed = subprocess.run(
            shlex.split(command),
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired as exc:
        duration = round(time.monotonic() - started, 3)
        stdout = exc.stdout if isinstance(exc.stdout, str) else ""
        stderr = exc.stderr if isinstance(exc.stderr, str) else ""
        return {
            "status": "timeout",
            "returncode": 124,
            "duration_seconds": duration,
            "command": command,
            "stdout_tail": _tail(stdout),
            "stderr_tail": _tail(stderr),
        }

    duration = round(time.monotonic() - started, 3)
    return {
        "status": "completed" if completed.returncode == 0 else "failed",
        "returncode": completed.returncode,
        "duration_seconds": duration,
        "command": command,
        "stdout_tail": _tail(completed.stdout),
        "stderr_tail": _tail(completed.stderr),
    }


def build_decision_record(
    *,
    lane_name: str,
    decision: LoopDecision,
    execute: bool,
    command: str | None,
    execution: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "lane_name": lane_name,
        "execute": execute,
        "decision": asdict(decision),
        "selected_command": command,
        "execution": execution,
    }


def write_decision_record(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")


def main() -> None:
    args = parse_args()
    last_result = _read_json(Path(args.last_result)) if args.last_result else None
    overlap_report = _read_json(Path(args.overlap_report)) if args.overlap_report else None
    decision = decide_loop_action(
        lane_brief=args.lane_brief,
        probe_verdict=args.probe_verdict,
        last_result=last_result,
        overlap_report=overlap_report,
    )
    command = _command_for_decision(args, decision)
    execution = None
    if args.execute and decision.allowed and command:
        execution = _run_command(command, timeout_seconds=args.timeout_seconds)

    output_path = (
        Path(args.decision_output)
        if args.decision_output
        else _default_decision_output(args.lane_name)
    )
    record = build_decision_record(
        lane_name=args.lane_name,
        decision=decision,
        execute=args.execute,
        command=command,
        execution=execution,
    )
    write_decision_record(output_path, record)
    print(json.dumps(record, indent=2))

    if not decision.allowed:
        raise SystemExit(1)
    if args.execute and command is None and decision.action in EXECUTABLE_ACTIONS:
        raise SystemExit(2)
    if execution and int(execution["returncode"]) != 0:
        raise SystemExit(int(execution["returncode"]))


if __name__ == "__main__":
    main()
