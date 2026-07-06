"""Tests for the CI freeze guard (scripts/check_incentive_ops_freeze.py)."""

from pathlib import Path

import pytest
import yaml

from scripts import check_incentive_ops_freeze as guard


def _make_repo(tmp_path: Path, *, status: str = "RUNNING") -> Path:
    """Build a minimal repo layout with one baseline manifest and frozen artifacts."""
    registry = tmp_path / "research/a1-incentive-farming/starter-registry-v0.yaml"
    handoff = tmp_path / "docs/specs/handoff.md"
    spec = tmp_path / "docs/specs/spec.md"
    allowlist = tmp_path / "config/incentive_ops/endpoint_allowlist.yaml"
    tooling = tmp_path / "tools/incentive_ops"
    for f, content in [
        (registry, "programs: []\n"),
        (handoff, "# handoff\n"),
        (spec, "# spec\n"),
        (allowlist, "endpoints: []\n"),
        (tooling / "baseline.py", "X = 1\n"),
    ]:
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(content, encoding="utf-8")

    manifest = {
        "run_id": "baseline-test",
        "status": status,
        "planned_end_at_utc": "2026-07-11T22:25:23Z",
        "registry_sha256": guard._sha256_file(registry),
        "handoff_path": "docs/specs/handoff.md",
        "handoff_sha256": guard._sha256_file(handoff),
        "spec_path": "docs/specs/spec.md",
        "spec_sha256": guard._sha256_file(spec),
        "allowlist_sha256": guard._sha256_file(allowlist),
        "tooling_sha256": guard._sha256_tree(tooling),
    }
    mp = tmp_path / "research/a1-incentive-farming/runs/baseline-test/manifest.yaml"
    mp.parent.mkdir(parents=True, exist_ok=True)
    mp.write_text(yaml.safe_dump(manifest), encoding="utf-8")
    return tmp_path


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = _make_repo(tmp_path)
    monkeypatch.setattr(guard, "REPO_ROOT", root)
    monkeypatch.setattr(guard, "RUNS_ROOT", root / "research/a1-incentive-farming/runs")
    monkeypatch.setattr(guard, "TOOLING_DIR", root / "tools/incentive_ops")
    monkeypatch.setattr(
        guard, "REGISTRY_PATH", root / "research/a1-incentive-farming/starter-registry-v0.yaml"
    )
    monkeypatch.setattr(
        guard, "ALLOWLIST_PATH", root / "config/incentive_ops/endpoint_allowlist.yaml"
    )
    return root


def test_passes_when_frozen_artifacts_unchanged(repo: Path):
    """A RUNNING baseline with matching hashes exits 0."""
    assert guard.main() == 0


def test_fails_when_tooling_content_changes(repo: Path):
    """Changing any tools/incentive_ops/*.py flips the tree hash and fails."""
    (repo / "tools/incentive_ops/baseline.py").write_text("X = 2\n", encoding="utf-8")
    assert guard.main() == 1


def test_fails_when_new_tooling_file_added(repo: Path):
    """Adding a new .py file (e.g. http.py in #132) changes the tree hash."""
    (repo / "tools/incentive_ops/http.py").write_text("Y = 1\n", encoding="utf-8")
    assert guard.main() == 1


def test_fails_when_allowlist_changes(repo: Path):
    """The endpoint allowlist is frozen alongside the tooling."""
    path = repo / "config/incentive_ops/endpoint_allowlist.yaml"
    path.write_text("endpoints: [new]\n", encoding="utf-8")
    assert guard.main() == 1


def test_touch_without_content_change_passes(repo: Path):
    """Content-hash semantics: rewriting identical bytes is not a violation."""
    path = repo / "tools/incentive_ops/baseline.py"
    path.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
    assert guard.main() == 0


def test_no_running_baseline_passes_with_changes(repo: Path):
    """Once the baseline is not RUNNING, the freeze lifts entirely."""
    mp = repo / "research/a1-incentive-farming/runs/baseline-test/manifest.yaml"
    manifest = yaml.safe_load(mp.read_text(encoding="utf-8"))
    manifest["status"] = "COMPLETE"
    mp.write_text(yaml.safe_dump(manifest), encoding="utf-8")
    (repo / "tools/incentive_ops/baseline.py").write_text("X = 3\n", encoding="utf-8")
    assert guard.main() == 0


def test_missing_frozen_file_fails(repo: Path):
    """Deleting a frozen artifact is a violation, not a silent pass."""
    (repo / "research/a1-incentive-farming/starter-registry-v0.yaml").unlink()
    assert guard.main() == 1


def test_no_runs_dir_passes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Repos/branches without any baseline runs are unaffected."""
    monkeypatch.setattr(guard, "RUNS_ROOT", tmp_path / "nonexistent")
    assert guard.main() == 0


def test_empty_manifest_fails(repo: Path):
    """A corrupted/empty manifest must fail loudly, not read as 'no baseline'."""
    mp = repo / "research/a1-incentive-farming/runs/baseline-test/manifest.yaml"
    mp.write_text("", encoding="utf-8")
    assert guard.main() == 1


def test_unparseable_manifest_fails(repo: Path):
    """Invalid YAML in a manifest is a hard failure, not a bypass."""
    mp = repo / "research/a1-incentive-farming/runs/baseline-test/manifest.yaml"
    mp.write_text("status: [unclosed\n  - :bad", encoding="utf-8")
    assert guard.main() == 1
