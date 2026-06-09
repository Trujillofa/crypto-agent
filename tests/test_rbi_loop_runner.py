from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from scripts.rbi_loop_guard import ACTION_RUN_AUTORESEARCH, LoopDecision
from scripts.rbi_loop_runner import (
    _command_for_decision,
    _default_decision_output,
    _run_command,
    build_decision_record,
    write_decision_record,
)


def test_default_decision_output_is_lane_scoped() -> None:
    assert _default_decision_output("basis-v0") == Path("research/rbi_loop/basis-v0/decision.json")


def test_command_for_decision_selects_matching_action_command() -> None:
    args = argparse.Namespace(
        probe_command=None,
        autoresearch_command="python scripts/autoresearch_loop.py --dry-run",
        bootstrap_command=None,
        overlap_command=None,
    )
    decision = LoopDecision(
        action=ACTION_RUN_AUTORESEARCH,
        allowed=True,
        reasons=[],
        evidence={},
    )

    assert _command_for_decision(args, decision) == "python scripts/autoresearch_loop.py --dry-run"


def test_build_decision_record_keeps_decision_and_execution() -> None:
    decision = LoopDecision(
        action=ACTION_RUN_AUTORESEARCH,
        allowed=True,
        reasons=["cheap probe passed"],
        evidence={"probe_verdict": "HAS_PULSE"},
    )
    execution = {
        "status": "completed",
        "returncode": 0,
        "duration_seconds": 0.01,
        "command": "python -V",
        "stdout_tail": ["Python 3.11.14"],
        "stderr_tail": [],
    }

    record = build_decision_record(
        lane_name="basis-v0",
        decision=decision,
        execute=True,
        command="python -V",
        execution=execution,
    )

    assert record["lane_name"] == "basis-v0"
    assert record["decision"]["action"] == ACTION_RUN_AUTORESEARCH
    assert record["selected_command"] == "python -V"
    assert record["execution"] == execution


def test_write_decision_record_creates_parent_directories(tmp_path: Path) -> None:
    output = tmp_path / "research" / "rbi_loop" / "lane" / "decision.json"
    record = {"lane_name": "lane", "decision": {"action": "RUN_CHEAP_PROBE"}}

    write_decision_record(output, record)

    assert json.loads(output.read_text(encoding="utf-8")) == record


def test_run_command_returns_completion_result() -> None:
    result = _run_command(
        f"{sys.executable} -c \"print('ok')\"",
        timeout_seconds=10,
    )

    assert result["status"] == "completed"
    assert result["returncode"] == 0
    assert result["stdout_tail"] == ["ok"]
