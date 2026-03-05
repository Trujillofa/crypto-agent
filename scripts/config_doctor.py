#!/usr/bin/env python3
"""Static config validator for crypto-agent settings files."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.utils.config_doctor import (  # noqa: E402
    discover_config_paths,
    report_to_json,
    run_config_doctor,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Validate settings YAML files for risky or unreachable configurations"
    )
    parser.add_argument(
        "--config",
        action="append",
        default=[],
        help="Path to one settings YAML file (repeatable)",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Validate all config/settings*.yaml files",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit machine-readable JSON output",
    )
    parser.add_argument(
        "--fail-on",
        choices=("none", "warning", "error"),
        default="error",
        help="Set the minimum severity that returns non-zero exit code",
    )
    return parser.parse_args()


def should_fail(fail_on: str, error_count: int, warning_count: int) -> bool:
    if fail_on == "none":
        return False
    if fail_on == "warning":
        return (error_count + warning_count) > 0
    return error_count > 0


def main() -> int:
    args = parse_args()

    if args.config:
        paths = [Path(item) for item in args.config]
    elif args.all or not args.config:
        paths = discover_config_paths()
    else:
        paths = []

    if not paths:
        print("No config files found.")
        return 1

    reports = run_config_doctor(paths)
    error_count = sum(len(report.errors) for report in reports)
    warning_count = sum(len(report.warnings) for report in reports)
    info_count = sum(len(report.infos) for report in reports)

    if args.json:
        print(report_to_json(reports))
    else:
        for report in reports:
            print(f"\n== {report.config_path} ==")
            if not report.findings:
                print("  OK: no findings")
                continue

            for finding in report.findings:
                print(
                    f"  [{finding.severity.upper()}] {finding.code} "
                    f"({finding.path}): {finding.message}"
                )

        print("\nSummary")
        print(f"  Files: {len(reports)}")
        print(f"  Errors: {error_count}")
        print(f"  Warnings: {warning_count}")
        print(f"  Info: {info_count}")

    return 1 if should_fail(args.fail_on, error_count, warning_count) else 0


if __name__ == "__main__":
    raise SystemExit(main())
