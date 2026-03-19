#!/usr/bin/env python3
"""Generate a daily validation report for paper trading agents."""

from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import UTC, date, datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Daily paper validation report")
    parser.add_argument(
        "--day",
        help="UTC day in YYYY-MM-DD format (default: today UTC)",
    )
    parser.add_argument(
        "--output-prefix",
        default="docs/reports/paper-validation-report",
        help="Output path prefix for markdown/json artifacts",
    )
    return parser.parse_args()


def _parse_day(raw: str | None) -> date:
    if not raw:
        return datetime.now(UTC).date()
    return date.fromisoformat(raw)


def _db_config() -> dict[str, object]:
    from os import getenv

    return {
        "host": getenv("POSTGRES_HOST", "timescaledb"),
        "port": int(getenv("POSTGRES_PORT", "5432")),
        "name": getenv("POSTGRES_DB", "marketdata"),
        "user": getenv("POSTGRES_USER", "trading"),
        "password": getenv("POSTGRES_PASSWORD", ""),
    }


async def main() -> int:
    from src.utils.paper_validation_report import (
        collect_report,
        render_markdown,
        report_to_json,
    )

    args = parse_args()
    day = _parse_day(args.day)
    report = await collect_report(db_config=_db_config(), day=day)

    output_prefix = Path(args.output_prefix)
    output_prefix.parent.mkdir(parents=True, exist_ok=True)
    markdown_path = output_prefix.with_suffix(".md")
    json_path = output_prefix.with_suffix(".json")

    markdown_path.write_text(render_markdown(report), encoding="utf-8")
    json_path.write_text(report_to_json(report), encoding="utf-8")

    print(f"Markdown: {markdown_path}")
    print(f"JSON: {json_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
