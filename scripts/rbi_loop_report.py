#!/usr/bin/env python3
"""Render an RBI loop decision JSON as a lane report."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Render an RBI loop decision report.")
    parser.add_argument("--decision", required=True, help="Path to rbi_loop_runner decision JSON.")
    parser.add_argument(
        "--output",
        help="Markdown output path. Defaults to docs/reports/rbi-loop-<lane>.md.",
    )
    return parser.parse_args()


def _read_decision(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Expected JSON object in {path}")
    return payload


def _escape_cell(text: str) -> str:
    """Escape characters that would break a Markdown table cell."""
    return text.replace("\\", "\\\\").replace("|", "\\|").replace("\n", "<br>")


def _default_output_path(record: dict[str, Any]) -> Path:
    lane_name = str(record.get("lane_name", "unknown-lane"))
    return Path("docs") / "reports" / f"rbi-loop-{lane_name}.md"


def _table_rows(mapping: dict[str, Any]) -> list[str]:
    rows = []
    for key, value in mapping.items():
        if isinstance(value, (dict, list)):
            rendered = f"`{_escape_cell(json.dumps(value, sort_keys=True))}`"
        elif value is None:
            rendered = ""
        else:
            rendered = _escape_cell(str(value))
        rows.append(f"| {_escape_cell(str(key))} | {rendered} |")
    return rows


def render_report(record: dict[str, Any]) -> str:
    lane_name = str(record.get("lane_name", "unknown-lane"))
    generated_at = str(record.get("generated_at", ""))
    decision = record.get("decision")
    if not isinstance(decision, dict):
        raise ValueError("decision record missing decision object")
    evidence = decision.get("evidence") if isinstance(decision.get("evidence"), dict) else {}
    reasons = decision.get("reasons") if isinstance(decision.get("reasons"), list) else []
    execution = record.get("execution")

    lines = [
        f"# RBI Loop Decision — {lane_name}",
        "",
        "| Field | Value |",
        "|---|---|",
        f"| Generated at | {generated_at} |",
        f"| Action | `{_escape_cell(str(decision.get('action', '')))}` |",
        f"| Allowed | {decision.get('allowed', False)} |",
        f"| Execute requested | {record.get('execute', False)} |",
        f"| Selected command | `{_escape_cell(str(record.get('selected_command') or ''))}` |",
        "",
        "## Reasons",
        "",
    ]
    if reasons:
        lines.extend(f"- {reason}" for reason in reasons)
    else:
        lines.append("- (none recorded)")

    lines.extend(["", "## Evidence", "", "| Field | Value |", "|---|---|"])
    lines.extend(_table_rows(evidence))

    lines.extend(["", "## Execution", ""])
    if isinstance(execution, dict):
        lines.extend(["| Field | Value |", "|---|---|"])
        lines.extend(_table_rows(execution))
    else:
        lines.append("No command execution was recorded.")

    lines.extend(
        [
            "",
            "## Next Action",
            "",
            str(decision.get("action", "UNKNOWN")),
            "",
        ]
    )
    return "\n".join(lines)


def write_report(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def main() -> None:
    args = parse_args()
    record = _read_decision(Path(args.decision))
    output = Path(args.output) if args.output else _default_output_path(record)
    write_report(output, render_report(record))
    print(output)


if __name__ == "__main__":
    main()
