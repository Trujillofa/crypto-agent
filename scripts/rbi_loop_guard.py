#!/usr/bin/env python3
"""Decide the next allowed RBI/autoresearch loop action from artifacts."""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

ACTION_WRITE_LANE_BRIEF = "WRITE_LANE_BRIEF"
ACTION_RUN_CHEAP_PROBE = "RUN_CHEAP_PROBE"
ACTION_CLOSE_LANE = "CLOSE_LANE"
ACTION_RUN_AUTORESEARCH = "RUN_AUTORESEARCH"
ACTION_ITERATE_OR_CLOSE = "ITERATE_OR_CLOSE"
ACTION_RUN_BOOTSTRAP_1000 = "RUN_BOOTSTRAP_1000"
ACTION_CHECK_OVERLAP = "CHECK_OVERLAP"
ACTION_READY_FOR_PAPER_REVIEW = "READY_FOR_PAPER_REVIEW"

PASSING_PROBE_VERDICT = "HAS_PULSE"
HIGH_OVERLAP_JACCARD = 0.35
HIGH_OVERLAP_PCT = 40.0


@dataclass(frozen=True)
class LoopDecision:
    """Deterministic supervisor decision for one RBI lane."""

    action: str
    allowed: bool
    reasons: list[str]
    evidence: dict[str, Any]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Read RBI loop artifacts and print the next allowed action as JSON."
    )
    parser.add_argument(
        "--lane-brief",
        help="Path to lane brief/spec/report that states the edge thesis.",
    )
    parser.add_argument(
        "--probe-verdict",
        choices=("HAS_PULSE", "WEAK_EDGE", "NO_PULSE"),
        help="Cheap-probe verdict for the lane.",
    )
    parser.add_argument(
        "--last-result",
        help="Path to autoresearch last_result.json.",
    )
    parser.add_argument(
        "--overlap-report",
        help="Path to analyze_entry_overlap.py JSON output.",
    )
    parser.add_argument(
        "--pretty",
        action="store_true",
        help="Pretty-print JSON output.",
    )
    return parser.parse_args()


def _read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Expected JSON object in {path}")
    return payload


def _brief_exists(path: str | None) -> bool:
    return bool(path) and Path(path).is_file()


def _last_result_bootstrap(last_result: dict[str, Any]) -> int | None:
    command = last_result.get("command")
    if isinstance(command, list):
        for index, part in enumerate(command):
            if part == "--bootstrap" and index + 1 < len(command):
                try:
                    return int(command[index + 1])
                except (TypeError, ValueError):
                    return None
    return None


def _summary_passes_standard_gate(last_result: dict[str, Any]) -> bool:
    summary = last_result.get("summary")
    return isinstance(summary, dict) and bool(summary.get("passes_gates"))


def _promotion_eligible(last_result: dict[str, Any]) -> bool:
    return bool(last_result.get("eligible_for_bootstrap_1000"))


def _high_overlap_pairs(overlap_report: dict[str, Any]) -> list[dict[str, Any]]:
    high_pairs: list[dict[str, Any]] = []
    pairwise = overlap_report.get("pairwise_oos")
    if not isinstance(pairwise, list):
        return high_pairs
    for item in pairwise:
        if not isinstance(item, dict):
            continue
        jaccard = float(item.get("jaccard", 0.0))
        pct_left = float(item.get("pct_of_left_also_in_right", 0.0))
        pct_right = float(item.get("pct_of_right_also_in_left", 0.0))
        if jaccard >= HIGH_OVERLAP_JACCARD or max(pct_left, pct_right) >= HIGH_OVERLAP_PCT:
            high_pairs.append(item)
    return high_pairs


def decide_loop_action(
    *,
    lane_brief: str | None = None,
    probe_verdict: str | None = None,
    last_result: dict[str, Any] | None = None,
    overlap_report: dict[str, Any] | None = None,
) -> LoopDecision:
    """Return the next allowed action for a lane without running external commands."""
    evidence: dict[str, Any] = {
        "lane_brief": lane_brief,
        "probe_verdict": probe_verdict,
    }
    if not _brief_exists(lane_brief):
        return LoopDecision(
            action=ACTION_WRITE_LANE_BRIEF,
            allowed=False,
            reasons=["missing lane brief"],
            evidence=evidence,
        )

    if probe_verdict is None:
        return LoopDecision(
            action=ACTION_RUN_CHEAP_PROBE,
            allowed=True,
            reasons=["lane brief exists; cheap-probe verdict missing"],
            evidence=evidence,
        )

    if probe_verdict != PASSING_PROBE_VERDICT:
        return LoopDecision(
            action=ACTION_CLOSE_LANE,
            allowed=False,
            reasons=[f"cheap probe verdict is {probe_verdict}, not HAS_PULSE"],
            evidence=evidence,
        )

    if last_result is None:
        return LoopDecision(
            action=ACTION_RUN_AUTORESEARCH,
            allowed=True,
            reasons=["cheap probe passed; autoresearch result missing"],
            evidence=evidence,
        )

    bootstrap = _last_result_bootstrap(last_result)
    passes_standard = _summary_passes_standard_gate(last_result)
    promotion_eligible = _promotion_eligible(last_result)
    evidence.update(
        {
            "last_result_status": last_result.get("status"),
            "gate_profile": last_result.get("gate_profile"),
            "bootstrap": bootstrap,
            "passes_standard_gate": passes_standard,
            "eligible_for_bootstrap_1000": promotion_eligible,
            "promotion_candidate_failures": last_result.get("promotion_candidate_failures", []),
        }
    )

    if not passes_standard:
        return LoopDecision(
            action=ACTION_ITERATE_OR_CLOSE,
            allowed=False,
            reasons=["autoresearch result did not pass the standard gate"],
            evidence=evidence,
        )

    if bootstrap is None or bootstrap < 1000:
        if not promotion_eligible:
            return LoopDecision(
                action=ACTION_ITERATE_OR_CLOSE,
                allowed=False,
                reasons=["standard pass is not promotion-candidate eligible"],
                evidence=evidence,
            )
        return LoopDecision(
            action=ACTION_RUN_BOOTSTRAP_1000,
            allowed=True,
            reasons=["standard pass is promotion-candidate eligible at discovery bootstrap"],
            evidence=evidence,
        )

    if overlap_report is None:
        return LoopDecision(
            action=ACTION_CHECK_OVERLAP,
            allowed=True,
            reasons=["bootstrap=1000 passed; overlap report missing"],
            evidence=evidence,
        )

    high_pairs = _high_overlap_pairs(overlap_report)
    evidence["high_overlap_pairs"] = high_pairs
    if high_pairs:
        return LoopDecision(
            action=ACTION_ITERATE_OR_CLOSE,
            allowed=False,
            reasons=["overlap report contains high-overlap pairs"],
            evidence=evidence,
        )

    return LoopDecision(
        action=ACTION_READY_FOR_PAPER_REVIEW,
        allowed=True,
        reasons=["lane passed bootstrap=1000 and overlap gates"],
        evidence=evidence,
    )


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
    indent = 2 if args.pretty else None
    print(json.dumps(asdict(decision), indent=indent))
    if not decision.allowed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
