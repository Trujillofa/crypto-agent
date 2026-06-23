"""Tests for Path 2 Gate 0 attestation script."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.probe_path2_gate0_attestation import (
    GATE0_QUESTION,
    build_attestation,
    run_attestation,
)


def test_build_attestation_includes_gate0_question_and_named_advantage() -> None:
    payload = build_attestation(
        lane_name="path2-illiquid-venue",
        brief_path=Path("docs/specs/path2-illiquid-venue-gate0.md"),
        named_advantage="illiquid venue microstructure where the book is thin",
        infra_attested=False,
    )
    assert payload["gate0_question"] == GATE0_QUESTION
    assert "credible, defensible information/latency/access asymmetry" in payload["gate0_question"]
    assert payload["named_advantage"] == "illiquid venue microstructure where the book is thin"
    assert payload["rbi_probe_verdict"] is None
    assert payload["gate0_status"] == "OPEN_PENDING_INFRA"


def test_run_attestation_writes_json(tmp_path: Path) -> None:
    brief = tmp_path / "brief.md"
    brief.write_text("# brief\n", encoding="utf-8")
    output = tmp_path / "gate0-attestation.json"

    result = run_attestation(
        lane_name="path2-test",
        brief=brief,
        named_advantage="illiquid venue microstructure where the book is thin",
        output=output,
    )

    assert result["status"] == "written"
    assert output.is_file()
    saved = json.loads(output.read_text(encoding="utf-8"))
    assert saved["lane_name"] == "path2-test"
    assert saved["gate0_question"] == GATE0_QUESTION


def test_run_attestation_idempotent_without_force(tmp_path: Path) -> None:
    brief = tmp_path / "brief.md"
    brief.write_text("# brief\n", encoding="utf-8")
    output = tmp_path / "gate0-attestation.json"

    first = run_attestation(
        lane_name="path2-test",
        brief=brief,
        named_advantage="illiquid venue microstructure where the book is thin",
        output=output,
    )
    second = run_attestation(
        lane_name="path2-test",
        brief=brief,
        named_advantage="illiquid venue microstructure where the book is thin",
        output=output,
    )

    assert first["status"] == "written"
    assert second["status"] == "already_attested"
    assert (
        json.loads(output.read_text(encoding="utf-8"))["generated_at"]
        == first["attestation"]["generated_at"]
    )


def test_run_attestation_requires_brief(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        run_attestation(
            lane_name="path2-test",
            brief=tmp_path / "missing.md",
            named_advantage="illiquid venue microstructure where the book is thin",
            output=tmp_path / "out.json",
        )
