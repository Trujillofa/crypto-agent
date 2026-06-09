from __future__ import annotations

import json
from pathlib import Path

import yaml

from scripts.rbi_loop_from_manifest import read_manifest, run_manifest_step


def test_read_manifest_requires_mapping(tmp_path: Path) -> None:
    manifest = tmp_path / "manifest.yaml"
    manifest.write_text("lane_name: basis-v0\n", encoding="utf-8")

    assert read_manifest(manifest) == {"lane_name": "basis-v0"}


def test_run_manifest_step_writes_decision_and_report(tmp_path: Path) -> None:
    brief = tmp_path / "brief.md"
    brief.write_text("# Brief\n", encoding="utf-8")
    decision_output = tmp_path / "decision.json"
    report_output = tmp_path / "report.md"
    manifest = {
        "lane_name": "basis-v0",
        "lane_brief": str(brief),
        "decision_output": str(decision_output),
        "report_output": str(report_output),
        "commands": {
            "RUN_CHEAP_PROBE": "python scripts/probe_basis_premium.py",
        },
    }

    result = run_manifest_step(manifest)

    assert result["decision_output"] == str(decision_output)
    assert result["report_output"] == str(report_output)
    record = json.loads(decision_output.read_text(encoding="utf-8"))
    assert record["lane_name"] == "basis-v0"
    assert record["decision"]["action"] == "RUN_CHEAP_PROBE"
    assert record["selected_command"] == "python scripts/probe_basis_premium.py"
    assert "# RBI Loop Decision" in report_output.read_text(encoding="utf-8")


def test_example_manifest_is_valid_yaml() -> None:
    path = Path("config/autoresearch/rbi_loop.example.yaml")
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))

    assert payload["lane_name"] == "example-data-first-lane"
    assert "RUN_AUTORESEARCH" in payload["commands"]
