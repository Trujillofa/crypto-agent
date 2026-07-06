#!/usr/bin/env python3
"""CI guard: refuse changes to frozen incentive-ops artifacts during a RUNNING baseline.

Scans research/a1-incentive-farming/runs/*/manifest.yaml for a RUNNING baseline.
If one exists, recomputes the content hashes of the five frozen artifacts
(registry, handoff, spec, tooling tree, endpoint allowlist) from the working
tree and compares them to the manifest — the same semantics as the daily tick's
_verify_frozen_artifacts, so a PR that touches a frozen path without changing
its content still passes.

Exit 0: no RUNNING baseline, or all frozen hashes match.
Exit 1: at least one frozen artifact differs from its manifest hash.

Deliberately standalone (only pyyaml) and outside tools/incentive_ops/, so the
guard itself is not part of the frozen surface it protects.
"""

import hashlib
import sys
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
RUNS_ROOT = REPO_ROOT / "research/a1-incentive-farming/runs"
TOOLING_DIR = REPO_ROOT / "tools/incentive_ops"
REGISTRY_PATH = REPO_ROOT / "research/a1-incentive-farming/starter-registry-v0.yaml"
ALLOWLIST_PATH = REPO_ROOT / "config/incentive_ops/endpoint_allowlist.yaml"


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _sha256_tree(root: Path, *, suffix: str = ".py") -> str:
    """Mirror of tools.incentive_ops.baseline._sha256_tree (kept standalone)."""
    h = hashlib.sha256()
    for p in sorted(root.rglob(f"*{suffix}")):
        if "__pycache__" in p.parts:
            continue
        h.update(p.relative_to(root).as_posix().encode())
        h.update(b"\0")
        h.update(p.read_bytes())
        h.update(b"\0")
    return h.hexdigest()


def _frozen_artifact_mismatches(manifest: dict) -> list[str]:
    """Return one message per frozen artifact whose current hash differs."""
    checks = [
        ("registry", REGISTRY_PATH, manifest.get("registry_sha256")),
        ("handoff", REPO_ROOT / manifest.get("handoff_path", ""), manifest.get("handoff_sha256")),
        ("spec", REPO_ROOT / manifest.get("spec_path", ""), manifest.get("spec_sha256")),
        ("allowlist", ALLOWLIST_PATH, manifest.get("allowlist_sha256")),
    ]
    mismatches: list[str] = []
    for name, path, expected in checks:
        if not expected:
            mismatches.append(f"{name}: manifest is missing its sha256 field")
            continue
        if not path.is_file():
            mismatches.append(f"{name}: frozen file missing: {path.relative_to(REPO_ROOT)}")
            continue
        actual = _sha256_file(path)
        if actual != expected:
            mismatches.append(f"{name}: {path.relative_to(REPO_ROOT)} changed (was frozen)")

    expected_tooling = manifest.get("tooling_sha256")
    if not expected_tooling:
        mismatches.append("tooling: manifest is missing tooling_sha256")
    elif _sha256_tree(TOOLING_DIR) != expected_tooling:
        mismatches.append("tooling: tools/incentive_ops/*.py changed (was frozen)")
    return mismatches


def main() -> int:
    manifests = sorted(RUNS_ROOT.glob("*/manifest.yaml")) if RUNS_ROOT.is_dir() else []
    running = []
    for mp in manifests:
        data = yaml.safe_load(mp.read_text(encoding="utf-8"))
        if isinstance(data, dict) and data.get("status") == "RUNNING":
            running.append((mp, data))

    if not running:
        print("freeze-guard: no RUNNING baseline; nothing frozen")
        return 0

    failed = False
    for mp, manifest in running:
        mismatches = _frozen_artifact_mismatches(manifest)
        run_id = manifest.get("run_id", mp.parent.name)
        if not mismatches:
            print(f"freeze-guard: OK — frozen artifacts match RUNNING baseline {run_id}")
            continue
        failed = True
        end = manifest.get("planned_end_at_utc", "unknown")
        print(
            f"freeze-guard: FAIL — baseline {run_id} is RUNNING (freeze until {end}) "
            "and these frozen artifacts differ:",
            file=sys.stderr,
        )
        for msg in mismatches:
            print(f"  - {msg}", file=sys.stderr)
        print(
            "  Hold this change until the baseline closes, or close/abort the baseline first.",
            file=sys.stderr,
        )
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
