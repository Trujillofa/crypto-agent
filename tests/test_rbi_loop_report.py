from __future__ import annotations

from pathlib import Path

from scripts.rbi_loop_report import _default_output_path, render_report, write_report


def _record() -> dict[str, object]:
    return {
        "generated_at": "2026-06-09T00:00:00+00:00",
        "lane_name": "basis-v0",
        "execute": False,
        "decision": {
            "action": "RUN_CHEAP_PROBE",
            "allowed": True,
            "reasons": ["lane brief exists; cheap-probe verdict missing"],
            "evidence": {
                "lane_brief": "docs/specs/basis-v0.md",
                "probe_verdict": None,
            },
        },
        "selected_command": None,
        "execution": None,
    }


def test_default_output_path_uses_lane_name() -> None:
    assert _default_output_path(_record()) == Path("docs/reports/rbi-loop-basis-v0.md")


def test_render_report_includes_decision_reasons_and_evidence() -> None:
    report = render_report(_record())

    assert "# RBI Loop Decision — basis-v0" in report
    assert "| Action | `RUN_CHEAP_PROBE` |" in report
    assert "- lane brief exists; cheap-probe verdict missing" in report
    assert "| lane_brief | docs/specs/basis-v0.md |" in report
    assert "No command execution was recorded." in report


def test_render_report_escapes_pipes_in_command_cell() -> None:
    record = _record()
    record["selected_command"] = "probe.py --a 1 | tee log"

    report = render_report(record)

    assert "| Selected command | `probe.py --a 1 \\| tee log` |" in report
    assert "1 | tee" not in report


def test_render_report_escapes_pipes_in_evidence_cell() -> None:
    record = _record()
    record["decision"]["evidence"]["lane_brief"] = "a | b"

    report = render_report(record)

    assert "| lane_brief | a \\| b |" in report


def test_write_report_creates_parent_directories(tmp_path: Path) -> None:
    output = tmp_path / "docs" / "reports" / "rbi-loop-basis-v0.md"

    write_report(output, "report\n")

    assert output.read_text(encoding="utf-8") == "report\n"
