#!/usr/bin/env python3
"""Run supervised RBI loop steps for multiple lane manifests."""

from __future__ import annotations

import argparse
import glob
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.rbi_loop_from_manifest import read_manifest, run_manifest_step


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run one RBI loop step across manifests.")
    parser.add_argument(
        "--glob",
        default="config/autoresearch/rbi_loop.*.yaml",
        help="Manifest glob to process.",
    )
    parser.add_argument(
        "--include-example",
        action="store_true",
        help="Include rbi_loop.example.yaml in the batch.",
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Execute selected commands. Default only records decisions/reports.",
    )
    parser.add_argument(
        "--summary-output",
        default="research/rbi_loop/batch-summary.json",
        help="JSON summary output path.",
    )
    parser.add_argument(
        "--markdown-output",
        default="docs/reports/rbi-loop-batch-summary.md",
        help="Markdown summary output path.",
    )
    return parser.parse_args()


def discover_manifests(pattern: str, *, include_example: bool = False) -> list[Path]:
    paths = sorted(Path(match) for match in glob.glob(pattern))
    if include_example:
        return paths
    return [path for path in paths if path.name != "rbi_loop.example.yaml"]


def summarize_batch(results: list[dict[str, Any]]) -> dict[str, Any]:
    actions: dict[str, int] = {}
    blocked = 0
    failed_execution = 0
    for item in results:
        decision = item["record"]["decision"]
        action = str(decision["action"])
        actions[action] = actions.get(action, 0) + 1
        if not decision["allowed"]:
            blocked += 1
        execution = item["record"].get("execution")
        if execution and int(execution["returncode"]) != 0:
            failed_execution += 1
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "lane_count": len(results),
        "blocked_count": blocked,
        "failed_execution_count": failed_execution,
        "actions": actions,
        "results": results,
    }


def render_markdown_summary(summary: dict[str, Any]) -> str:
    lines = [
        "# RBI Loop Batch Summary",
        "",
        "| Field | Value |",
        "|---|---|",
        f"| Generated at | {summary['generated_at']} |",
        f"| Lane count | {summary['lane_count']} |",
        f"| Blocked count | {summary['blocked_count']} |",
        f"| Failed execution count | {summary['failed_execution_count']} |",
        "",
        "## Lanes",
        "",
        "| Lane | Action | Allowed | Decision | Report |",
        "|---|---|---:|---|---|",
    ]
    for item in summary["results"]:
        record = item["record"]
        decision = record["decision"]
        lines.append(
            f"| {record['lane_name']} | `{decision['action']}` | {decision['allowed']} | "
            f"`{item['decision_output']}` | `{item.get('report_output') or ''}` |"
        )
    lines.append("")
    return "\n".join(lines)


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def run_batch(
    manifests: list[Path],
    *,
    execute: bool = False,
) -> dict[str, Any]:
    results = []
    for manifest_path in manifests:
        manifest = read_manifest(manifest_path)
        result = run_manifest_step(manifest, execute=execute, render_markdown=True)
        result["manifest_path"] = str(manifest_path)
        results.append(result)
    return summarize_batch(results)


def main() -> None:
    args = parse_args()
    manifests = discover_manifests(args.glob, include_example=args.include_example)
    summary = run_batch(manifests, execute=args.execute)
    write_json(Path(args.summary_output), summary)
    write_text(Path(args.markdown_output), render_markdown_summary(summary))
    print(json.dumps(summary, indent=2))
    if summary["blocked_count"] or summary["failed_execution_count"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
