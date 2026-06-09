from __future__ import annotations

from pathlib import Path

import yaml

from scripts.rbi_loop_batch import (
    discover_manifests,
    render_markdown_summary,
    run_batch,
    summarize_batch,
)


def _write_manifest(path: Path, *, lane_name: str, brief: Path) -> None:
    payload = {
        "lane_name": lane_name,
        "lane_brief": str(brief),
        "decision_output": str(path.parent / f"{lane_name}.decision.json"),
        "report_output": str(path.parent / f"{lane_name}.md"),
        "commands": {"RUN_CHEAP_PROBE": "python scripts/probe_basis_premium.py"},
    }
    path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")


def test_discover_manifests_skips_example_by_default(tmp_path: Path) -> None:
    (tmp_path / "rbi_loop.example.yaml").write_text("lane_name: example\n", encoding="utf-8")
    wanted = tmp_path / "rbi_loop.basis.yaml"
    wanted.write_text("lane_name: basis\n", encoding="utf-8")

    assert discover_manifests(str(tmp_path / "rbi_loop.*.yaml")) == [wanted]
    assert len(discover_manifests(str(tmp_path / "rbi_loop.*.yaml"), include_example=True)) == 2


def test_summarize_batch_counts_actions_and_blocks() -> None:
    summary = summarize_batch(
        [
            {
                "record": {
                    "decision": {
                        "action": "RUN_CHEAP_PROBE",
                        "allowed": True,
                    },
                    "execution": None,
                }
            },
            {
                "record": {
                    "decision": {
                        "action": "CLOSE_LANE",
                        "allowed": False,
                    },
                    "execution": None,
                }
            },
        ]
    )

    assert summary["lane_count"] == 2
    assert summary["blocked_count"] == 1
    assert summary["actions"] == {"RUN_CHEAP_PROBE": 1, "CLOSE_LANE": 1}


def test_render_markdown_summary_lists_lanes() -> None:
    markdown = render_markdown_summary(
        {
            "generated_at": "2026-06-09T00:00:00+00:00",
            "lane_count": 1,
            "blocked_count": 0,
            "failed_execution_count": 0,
            "results": [
                {
                    "decision_output": "decision.json",
                    "report_output": "report.md",
                    "record": {
                        "lane_name": "basis-v0",
                        "decision": {"action": "RUN_CHEAP_PROBE", "allowed": True},
                    },
                }
            ],
        }
    )

    assert "# RBI Loop Batch Summary" in markdown
    assert "| basis-v0 | `RUN_CHEAP_PROBE` | True |" in markdown


def test_run_batch_processes_multiple_manifests(tmp_path: Path) -> None:
    brief = tmp_path / "brief.md"
    brief.write_text("# Brief\n", encoding="utf-8")
    first = tmp_path / "rbi_loop.first.yaml"
    second = tmp_path / "rbi_loop.second.yaml"
    _write_manifest(first, lane_name="first", brief=brief)
    _write_manifest(second, lane_name="second", brief=brief)

    summary = run_batch([first, second])

    assert summary["lane_count"] == 2
    assert summary["blocked_count"] == 0
    assert summary["actions"] == {"RUN_CHEAP_PROBE": 2}
    assert (tmp_path / "first.decision.json").is_file()
    assert (tmp_path / "second.md").is_file()
