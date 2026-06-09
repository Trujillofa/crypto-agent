#!/usr/bin/env python3
"""Run one RBI loop step from a lane manifest."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.rbi_loop_guard import _read_json, decide_loop_action
from scripts.rbi_loop_report import render_report, write_report
from scripts.rbi_loop_runner import (
    _default_decision_output,
    _run_command,
    build_decision_record,
    write_decision_record,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run one RBI loop step from a YAML manifest.")
    parser.add_argument("--manifest", required=True, help="YAML lane manifest path.")
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Execute the selected command from the manifest. Default only records decision/report.",
    )
    parser.add_argument(
        "--no-report",
        action="store_true",
        help="Skip Markdown report rendering.",
    )
    return parser.parse_args()


def read_manifest(path: Path) -> dict[str, Any]:
    payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(payload, dict):
        raise ValueError(f"Expected mapping in {path}")
    return payload


def _optional_json(path: str | None) -> dict[str, Any] | None:
    return _read_json(Path(path)) if path else None


def _required_str(manifest: dict[str, Any], key: str) -> str:
    value = manifest.get(key)
    if not value:
        raise ValueError(f"manifest missing required field: {key}")
    return str(value)


def _commands(manifest: dict[str, Any]) -> dict[str, str]:
    raw = manifest.get("commands", {})
    if not isinstance(raw, dict):
        raise ValueError("manifest.commands must be a mapping")
    return {str(key): str(value) for key, value in raw.items() if value}


def _selected_command(action: str, commands: dict[str, str]) -> str | None:
    return commands.get(action)


def _decision_output(manifest: dict[str, Any], lane_name: str) -> Path:
    return Path(str(manifest.get("decision_output") or _default_decision_output(lane_name)))


def _report_output(manifest: dict[str, Any], lane_name: str) -> Path:
    return Path(
        str(manifest.get("report_output") or Path("docs/reports") / f"rbi-loop-{lane_name}.md")
    )


def run_manifest_step(
    manifest: dict[str, Any],
    *,
    execute: bool = False,
    render_markdown: bool = True,
) -> dict[str, Any]:
    lane_name = _required_str(manifest, "lane_name")
    lane_brief = manifest.get("lane_brief")
    probe_verdict = manifest.get("probe_verdict")
    last_result = (
        _optional_json(str(manifest["last_result"])) if manifest.get("last_result") else None
    )
    overlap_report = (
        _optional_json(str(manifest["overlap_report"])) if manifest.get("overlap_report") else None
    )

    decision = decide_loop_action(
        lane_brief=str(lane_brief) if lane_brief else None,
        probe_verdict=str(probe_verdict) if probe_verdict else None,
        last_result=last_result,
        overlap_report=overlap_report,
    )
    command = _selected_command(decision.action, _commands(manifest))
    execution = None
    if execute and decision.allowed and command:
        timeout_seconds = int(manifest.get("timeout_seconds", 1800))
        execution = _run_command(command, timeout_seconds=timeout_seconds)

    record = build_decision_record(
        lane_name=lane_name,
        decision=decision,
        execute=execute,
        command=command,
        execution=execution,
    )
    decision_output = _decision_output(manifest, lane_name)
    write_decision_record(decision_output, record)

    report_output = None
    if render_markdown:
        report_output = _report_output(manifest, lane_name)
        write_report(report_output, render_report(record))

    result = {
        "decision_output": str(decision_output),
        "report_output": str(report_output) if report_output else None,
        "record": record,
    }
    return result


def main() -> None:
    args = parse_args()
    result = run_manifest_step(
        read_manifest(Path(args.manifest)),
        execute=args.execute,
        render_markdown=not args.no_report,
    )
    print(json.dumps(result, indent=2))
    decision = result["record"]["decision"]
    if not decision["allowed"]:
        raise SystemExit(1)
    execution = result["record"].get("execution")
    if execution and int(execution["returncode"]) != 0:
        raise SystemExit(int(execution["returncode"]))


if __name__ == "__main__":
    main()
