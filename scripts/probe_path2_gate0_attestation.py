#!/usr/bin/env python3
"""Path 2 Gate 0 attestation — records named advantage without faking HAS_PULSE."""

from __future__ import annotations

import argparse
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

GATE0_QUESTION = "do I have a credible, defensible information/latency/access asymmetry?"
DEFAULT_NAMED_ADVANTAGE = "illiquid venue microstructure where the book is thin"
INFRA_ENV_VAR = "PATH2_ILLIQUID_VENUE_ACCESS_ATTESTED"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Write Path 2 Gate 0 attestation JSON (no market-data HAS_PULSE claim)."
    )
    parser.add_argument("--lane-name", required=True, help="Stable RBI lane slug.")
    parser.add_argument("--brief", required=True, help="Path to Gate 0 lane brief.")
    parser.add_argument(
        "--named-advantage",
        default=DEFAULT_NAMED_ADVANTAGE,
        help="Explicit differentiated advantage from capstone Path 2 list.",
    )
    parser.add_argument(
        "--output",
        required=True,
        help="Output path for gate0-attestation.json.",
    )
    parser.add_argument(
        "--venue",
        default="",
        help="Named illiquid venue (required with --feasibility-doc to advance Gate 0).",
    )
    parser.add_argument(
        "--feasibility-doc",
        default="",
        help="Path to feasibility evidence file (must exist to advance Gate 0).",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite an existing attestation file.",
    )
    return parser.parse_args()


def _brief_exists(path: Path) -> bool:
    return path.is_file()


def _access_env_declared() -> bool:
    value = os.environ.get(INFRA_ENV_VAR, "").strip().lower()
    return value in {"1", "true", "yes"}


def _access_declared_pending_evidence(
    *,
    access_env_declared: bool,
    venue: str,
    feasibility_doc: Path | None,
) -> bool:
    return (
        access_env_declared
        and bool(venue.strip())
        and feasibility_doc is not None
        and feasibility_doc.is_file()
    )


def build_attestation(
    *,
    lane_name: str,
    brief_path: Path,
    named_advantage: str,
    access_env_declared: bool,
    venue: str = "",
    feasibility_doc: Path | None = None,
    existing: dict[str, Any] | None = None,
) -> dict[str, Any]:
    access_pending = _access_declared_pending_evidence(
        access_env_declared=access_env_declared,
        venue=venue,
        feasibility_doc=feasibility_doc,
    )
    gate0_answer = (
        "ACCESS_DECLARED_PENDING_EVIDENCE" if access_pending else "DECLARED_NOT_YET_OPERATIONAL"
    )
    gate0_status = "OPEN_GATE1_PENDING" if access_pending else "OPEN_PENDING_INFRA"
    payload: dict[str, Any] = {
        "generated_at": datetime.now(UTC).isoformat(),
        "lane_name": lane_name,
        "path2_program": True,
        "named_advantage": named_advantage,
        "gate0_question": GATE0_QUESTION,
        "gate0_answer": gate0_answer,
        "gate0_status": gate0_status,
        "lane_brief": str(brief_path),
        "infra_env_var": INFRA_ENV_VAR,
        "infra_attested": access_env_declared,
        "venue": venue.strip() or None,
        "feasibility_doc": str(feasibility_doc) if feasibility_doc else None,
        "rbi_probe_verdict": None,
        "notes": (
            "Path 2 Gate 0 attestation only. Does not set HAS_PULSE. "
            "Gate 1 requires venue access and illiquid-surface cheap probe."
        ),
    }
    if existing is not None:
        payload["supersedes"] = existing.get("generated_at")
    return payload


def write_attestation(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def run_attestation(
    *,
    lane_name: str,
    brief: Path,
    named_advantage: str,
    output: Path,
    venue: str = "",
    feasibility_doc: Path | None = None,
    force: bool = False,
) -> dict[str, Any]:
    if not _brief_exists(brief):
        raise FileNotFoundError(f"lane brief not found: {brief}")

    existing: dict[str, Any] | None = None
    if output.is_file():
        if not force:
            existing = json.loads(output.read_text(encoding="utf-8"))
            if isinstance(existing, dict) and existing.get("lane_name") == lane_name:
                return {
                    "status": "already_attested",
                    "output": str(output),
                    "attestation": existing,
                }
        elif output.is_file():
            existing = json.loads(output.read_text(encoding="utf-8"))

    payload = build_attestation(
        lane_name=lane_name,
        brief_path=brief,
        named_advantage=named_advantage,
        access_env_declared=_access_env_declared(),
        venue=venue,
        feasibility_doc=feasibility_doc,
        existing=existing if isinstance(existing, dict) else None,
    )
    write_attestation(output, payload)
    return {
        "status": "written",
        "output": str(output),
        "attestation": payload,
    }


def main() -> None:
    args = parse_args()
    feasibility_doc = Path(args.feasibility_doc) if args.feasibility_doc else None
    result = run_attestation(
        lane_name=args.lane_name,
        brief=Path(args.brief),
        named_advantage=args.named_advantage,
        output=Path(args.output),
        venue=args.venue,
        feasibility_doc=feasibility_doc,
        force=args.force,
    )
    print(json.dumps(result, indent=2))
    if result["status"] not in {"written", "already_attested"}:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
