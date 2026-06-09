from __future__ import annotations

from pathlib import Path

from scripts.rbi_loop_guard import (
    ACTION_CHECK_OVERLAP,
    ACTION_CLOSE_LANE,
    ACTION_ITERATE_OR_CLOSE,
    ACTION_READY_FOR_PAPER_REVIEW,
    ACTION_RUN_BOOTSTRAP_1000,
    ACTION_RUN_CHEAP_PROBE,
    ACTION_WRITE_LANE_BRIEF,
    decide_loop_action,
)


def _brief(tmp_path: Path) -> str:
    path = tmp_path / "lane.md"
    path.write_text("# Lane\n", encoding="utf-8")
    return str(path)


def _last_result(
    *,
    bootstrap: int,
    passes_gates: bool = True,
    eligible_for_bootstrap_1000: bool = True,
) -> dict[str, object]:
    return {
        "status": "keep",
        "gate_profile": "standard",
        "command": ["python", "scripts/experiment_autopilot.py", "--bootstrap", str(bootstrap)],
        "summary": {"passes_gates": passes_gates},
        "eligible_for_bootstrap_1000": eligible_for_bootstrap_1000,
        "promotion_candidate_failures": [] if eligible_for_bootstrap_1000 else ["max_drawdown_pct"],
    }


def test_missing_lane_brief_blocks_loop() -> None:
    decision = decide_loop_action(lane_brief="missing.md")

    assert decision.action == ACTION_WRITE_LANE_BRIEF
    assert decision.allowed is False


def test_existing_brief_without_probe_allows_probe(tmp_path: Path) -> None:
    decision = decide_loop_action(lane_brief=_brief(tmp_path))

    assert decision.action == ACTION_RUN_CHEAP_PROBE
    assert decision.allowed is True


def test_weak_probe_closes_lane(tmp_path: Path) -> None:
    decision = decide_loop_action(lane_brief=_brief(tmp_path), probe_verdict="WEAK_EDGE")

    assert decision.action == ACTION_CLOSE_LANE
    assert decision.allowed is False


def test_discovery_pass_that_is_promotion_candidate_schedules_b1000(tmp_path: Path) -> None:
    decision = decide_loop_action(
        lane_brief=_brief(tmp_path),
        probe_verdict="HAS_PULSE",
        last_result=_last_result(bootstrap=100),
    )

    assert decision.action == ACTION_RUN_BOOTSTRAP_1000
    assert decision.allowed is True


def test_standard_pass_without_promotion_candidate_stops_before_b1000(tmp_path: Path) -> None:
    decision = decide_loop_action(
        lane_brief=_brief(tmp_path),
        probe_verdict="HAS_PULSE",
        last_result=_last_result(bootstrap=100, eligible_for_bootstrap_1000=False),
    )

    assert decision.action == ACTION_ITERATE_OR_CLOSE
    assert decision.allowed is False


def test_b1000_pass_requires_overlap_report(tmp_path: Path) -> None:
    decision = decide_loop_action(
        lane_brief=_brief(tmp_path),
        probe_verdict="HAS_PULSE",
        last_result=_last_result(bootstrap=1000, eligible_for_bootstrap_1000=False),
    )

    assert decision.action == ACTION_CHECK_OVERLAP
    assert decision.allowed is True


def test_high_overlap_blocks_paper_review(tmp_path: Path) -> None:
    decision = decide_loop_action(
        lane_brief=_brief(tmp_path),
        probe_verdict="HAS_PULSE",
        last_result=_last_result(bootstrap=1000, eligible_for_bootstrap_1000=False),
        overlap_report={
            "pairwise_oos": [
                {
                    "left": "live",
                    "right": "candidate",
                    "jaccard": 0.5,
                    "pct_of_left_also_in_right": 45.0,
                    "pct_of_right_also_in_left": 50.0,
                }
            ]
        },
    )

    assert decision.action == ACTION_ITERATE_OR_CLOSE
    assert decision.allowed is False


def test_low_overlap_allows_paper_review(tmp_path: Path) -> None:
    decision = decide_loop_action(
        lane_brief=_brief(tmp_path),
        probe_verdict="HAS_PULSE",
        last_result=_last_result(bootstrap=1000, eligible_for_bootstrap_1000=False),
        overlap_report={
            "pairwise_oos": [
                {
                    "left": "live",
                    "right": "candidate",
                    "jaccard": 0.05,
                    "pct_of_left_also_in_right": 5.0,
                    "pct_of_right_also_in_left": 8.0,
                }
            ]
        },
    )

    assert decision.action == ACTION_READY_FOR_PAPER_REVIEW
    assert decision.allowed is True


def test_high_overlap_handles_null_values(tmp_path: Path) -> None:
    """Null/missing metrics in overlap items must not crash; treated as 0 (low overlap here)."""
    decision = decide_loop_action(
        lane_brief=_brief(tmp_path),
        probe_verdict="HAS_PULSE",
        last_result=_last_result(bootstrap=1000, eligible_for_bootstrap_1000=False),
        overlap_report={
            "pairwise_oos": [
                {
                    "left": "live",
                    "right": "candidate",
                    "jaccard": None,
                    "pct_of_left_also_in_right": None,
                    "pct_of_right_also_in_left": 12.0,
                },
                {
                    "left": "other",
                    "right": "candidate2",
                    # all null/missing -> 0s, low overlap
                },
            ]
        },
    )

    assert decision.action == ACTION_READY_FOR_PAPER_REVIEW
    assert decision.allowed is True


def test_high_pct_null_jaccard_still_blocks(tmp_path: Path) -> None:
    """High pct metric triggers block even if jaccard is null."""
    decision = decide_loop_action(
        lane_brief=_brief(tmp_path),
        probe_verdict="HAS_PULSE",
        last_result=_last_result(bootstrap=1000, eligible_for_bootstrap_1000=False),
        overlap_report={
            "pairwise_oos": [
                {
                    "left": "live",
                    "right": "candidate",
                    "jaccard": None,
                    "pct_of_left_also_in_right": 55.0,
                    "pct_of_right_also_in_left": None,
                }
            ]
        },
    )

    assert decision.action == ACTION_ITERATE_OR_CLOSE
    assert decision.allowed is False


def test_malformed_partial_overlap_rows_are_ignored(tmp_path: Path) -> None:
    """Non-dict rows, bad types, and partial rows must not crash; only valid numeric pairs are considered."""
    decision = decide_loop_action(
        lane_brief=_brief(tmp_path),
        probe_verdict="HAS_PULSE",
        last_result=_last_result(bootstrap=1000, eligible_for_bootstrap_1000=False),
        overlap_report={
            "pairwise_oos": [
                "not a dict",
                None,
                {
                    "left": "live",
                    "right": "bad",
                    "jaccard": "not-a-number",
                    "pct_of_left_also_in_right": 99.0,
                },  # bad jaccard but high pct
                {
                    "left": "live",
                    "right": "candidate",
                    "jaccard": 0.9,
                    "pct_of_left_also_in_right": None,
                },
                {"foo": "bar"},  # missing keys -> treated as 0
            ]
        },
    )

    # The high-pct bad-jaccard row and the high-jaccard row should trigger block
    assert decision.action == ACTION_ITERATE_OR_CLOSE
    assert decision.allowed is False
    # Evidence should only contain the rows that passed the numeric threshold checks
    high = decision.evidence.get("high_overlap_pairs", [])
    assert any(p.get("right") == "bad" for p in high)
    assert any(p.get("right") == "candidate" for p in high)
