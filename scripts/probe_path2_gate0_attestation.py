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
        "--force",
        action="store_true",
        help="Overwrite an existing attestation file.",
    )
    return parser.parse_args()


def _brief_exists(path: Path) -> bool:
    return path.is_file()


def _infra_attested() -> bool:
    value = os.environ.get(INFRA_ENV_VAR, "").strip().lower()
    return value in {"1", "true", "yes"}


def build_attestation(
    *,
    lane_name: str,
    brief_path: Path,
    named_advantage: str,
    infra_attested: bool,
    existing: dict[str, Any] | None = None,
) -> dict[str, Any]:
    gate0_answer = (
        "CREDIBLE_ASYMMETRY_ATTESTED" if infra_attested else "DECLARED_NOT_YET_OPERATIONAL"
    )
    gate0_status = "OPEN_GATE1_PENDING" if infra_attested else "OPEN_PENDING_INFRA"
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
        "infra_attested": infra_attested,
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
        infra_attested=_infra_attested(),
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
    result = run_attestation(
        lane_name=args.lane_name,
        brief=Path(args.brief),
        named_advantage=args.named_advantage,
        output=Path(args.output),
        force=args.force,
    )
    print(json.dumps(result, indent=2))
    if result["status"] not in {"written", "already_attested"}:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
