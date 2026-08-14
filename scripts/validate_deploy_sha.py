#!/usr/bin/env python3
"""Fail-closed check that a deploy SHA is exact lowercase hex and equals origin/main."""

from __future__ import annotations

import argparse
import re
import sys

_SHA_RE = re.compile(r"^[0-9a-f]{40}$")


def validate_deploy_sha(deploy_sha: str, origin_main_sha: str) -> None:
    """Raise ValueError unless deploy_sha is a 40-char lowercase hex SHA equal to main."""
    if not _SHA_RE.fullmatch(deploy_sha):
        raise ValueError(
            f"deploy_sha must be exactly 40 lowercase hexadecimal characters, got {deploy_sha!r}"
        )
    if not _SHA_RE.fullmatch(origin_main_sha):
        raise ValueError(
            "origin/main SHA must be exactly 40 lowercase hexadecimal characters, "
            f"got {origin_main_sha!r}"
        )
    if deploy_sha != origin_main_sha:
        raise ValueError(f"deploy_sha ({deploy_sha}) != origin/main ({origin_main_sha})")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("deploy_sha", help="Requested 40-char lowercase deploy SHA")
    parser.add_argument(
        "origin_main_sha",
        help="Current origin/main SHA (must match deploy_sha)",
    )
    args = parser.parse_args(argv)
    try:
        validate_deploy_sha(args.deploy_sha, args.origin_main_sha)
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(f"deploy_sha ok: {args.deploy_sha}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
