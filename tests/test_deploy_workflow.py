"""Deterministic checks that production deploy is manual and fail-closed."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest
import yaml

from scripts.validate_deploy_sha import main, validate_deploy_sha

WORKFLOW = Path(".github/workflows/deploy.yml")
ALIGN = Path("scripts/align_prod_checkout.sh")
REBUILD = Path("scripts/rebuild_prod_agents.sh")
VALID_SHA = "a" * 40
OTHER_SHA = "b" * 40


def _load_workflow() -> dict[str, object]:
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def _on(workflow: dict[str, object]) -> dict[str, object]:
    # PyYAML 1.1 treats the key `on` as boolean True.
    trigger = workflow.get("on", workflow.get(True))
    assert isinstance(trigger, dict)
    return trigger


def _raw() -> str:
    return WORKFLOW.read_text(encoding="utf-8")


def test_workflow_run_is_absent_as_deployment_trigger() -> None:
    on = _on(_load_workflow())
    assert "workflow_run" not in on
    assert "workflow_run:" not in _raw()


def test_workflow_dispatch_is_the_only_trigger() -> None:
    on = _on(_load_workflow())
    assert list(on.keys()) == ["workflow_dispatch"]


def test_deploy_sha_is_required() -> None:
    dispatch = _on(_load_workflow())["workflow_dispatch"]
    assert isinstance(dispatch, dict)
    deploy_sha = dispatch["inputs"]["deploy_sha"]
    assert deploy_sha["required"] is True
    assert deploy_sha["type"] == "string"


def test_production_environment_protection_is_referenced() -> None:
    job = _load_workflow()["jobs"]["deploy"]
    assert isinstance(job, dict)
    assert job["environment"] == "production"


def test_invalid_sha_fails_closed() -> None:
    with pytest.raises(ValueError, match="40 lowercase hexadecimal"):
        validate_deploy_sha("ABCDEF" + "0" * 34, VALID_SHA)
    with pytest.raises(ValueError, match="40 lowercase hexadecimal"):
        validate_deploy_sha("not-a-sha", VALID_SHA)
    with pytest.raises(ValueError, match="40 lowercase hexadecimal"):
        validate_deploy_sha("a" * 39, VALID_SHA)
    assert main(["NOTHEX" + "0" * 34, VALID_SHA]) == 1


def test_mismatched_sha_fails_closed() -> None:
    with pytest.raises(ValueError, match="!= origin/main"):
        validate_deploy_sha(VALID_SHA, OTHER_SHA)
    assert main([VALID_SHA, OTHER_SHA]) == 1


def test_matching_lowercase_sha_passes() -> None:
    validate_deploy_sha(VALID_SHA, VALID_SHA)
    assert main([VALID_SHA, VALID_SHA]) == 0


def test_workflow_invokes_fail_closed_sha_validator() -> None:
    raw = _raw()
    assert "python3 scripts/validate_deploy_sha.py" in raw
    assert "if: ${{ github.event.workflow_run.conclusion == 'success' }}" not in raw


def test_coordinated_rebuild_and_health_checks_remain() -> None:
    raw = _raw()
    rebuild = REBUILD.read_text(encoding="utf-8")
    align = ALIGN.read_text(encoding="utf-8")
    assert "tailscale/github-action@" in raw
    assert "cat scripts/align_prod_checkout.sh" in raw
    assert "cat scripts/rebuild_prod_agents.sh" in raw
    assert "git fetch origin main" in align
    assert "git pull --ff-only origin main" in align
    assert "grep '^agent_'" in rebuild
    assert "docker compose -f docker-compose.prod.yml build $AGENTS" in rebuild
    assert "docker compose -f docker-compose.prod.yml up -d --remove-orphans $AGENTS" in rebuild
    assert "sleep 120" in rebuild
    assert r"grep -vE '\(healthy\)$'" in rebuild
    assert "appleboy/telegram-action@" in raw
    assert "inputs.deploy_sha" in raw
    assert "github.event.workflow_run" not in raw


def test_align_compares_requested_sha_before_pull() -> None:
    align = ALIGN.read_text(encoding="utf-8")
    fetch_at = align.index("git fetch origin main")
    compare_at = align.index("origin/main ($REMOTE) != requested deploy_sha")
    pull_at = align.index("git pull --ff-only origin main")
    reverify_at = align.index("after pull, HEAD")
    assert fetch_at < compare_at < pull_at < reverify_at
    assert "aborting before pull" in align


def _git(cwd: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _bare_and_clones(tmp_path: Path) -> tuple[Path, Path, str]:
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "--bare", str(origin)], check=True, capture_output=True)
    work = tmp_path / "work"
    work.mkdir()
    _git(work, "init")
    _git(work, "config", "user.email", "deploy-test@example.com")
    _git(work, "config", "user.name", "deploy-test")
    (work / "marker").write_text("a\n", encoding="utf-8")
    _git(work, "add", "marker")
    _git(work, "commit", "-m", "a")
    _git(work, "branch", "-M", "main")
    _git(work, "remote", "add", "origin", str(origin))
    _git(work, "push", "-u", "origin", "main")
    server = tmp_path / "server"
    subprocess.run(
        ["git", "clone", "--branch", "main", str(origin), str(server)],
        check=True,
        capture_output=True,
    )
    sha_a = _git(server, "rev-parse", "HEAD")
    return work, server, sha_a


def test_align_aborts_before_pull_when_main_advanced(tmp_path: Path) -> None:
    """A stale dispatch must not move the server checkout when origin/main moved."""
    work, server, sha_a = _bare_and_clones(tmp_path)
    (work / "marker").write_text("b\n", encoding="utf-8")
    _git(work, "add", "marker")
    _git(work, "commit", "-m", "b")
    _git(work, "push", "origin", "main")

    env = {**os.environ, "REQUESTED_SHA": sha_a, "DEPLOY_ROOT": str(server)}
    proc = subprocess.run(
        ["bash", str(ALIGN)],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 1
    assert "aborting before pull" in proc.stdout + proc.stderr
    assert _git(server, "rev-parse", "HEAD") == sha_a
    assert (server / "marker").read_text(encoding="utf-8") == "a\n"


def test_align_pulls_when_requested_sha_is_current_main(tmp_path: Path) -> None:
    work, server, sha_a = _bare_and_clones(tmp_path)
    (work / "marker").write_text("b\n", encoding="utf-8")
    _git(work, "add", "marker")
    _git(work, "commit", "-m", "b")
    _git(work, "push", "origin", "main")
    sha_b = _git(work, "rev-parse", "HEAD")
    assert sha_b != sha_a

    env = {**os.environ, "REQUESTED_SHA": sha_b, "DEPLOY_ROOT": str(server)}
    proc = subprocess.run(
        ["bash", str(ALIGN)],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert _git(server, "rev-parse", "HEAD") == sha_b
    assert (server / "marker").read_text(encoding="utf-8") == "b\n"


def test_docs_do_not_bypass_deploy_contract() -> None:
    agents = Path("AGENTS.md").read_text(encoding="utf-8")
    claude = Path("CLAUDE.md").read_text(encoding="utf-8")
    deployment = Path("docs/DEPLOYMENT.md").read_text(encoding="utf-8")
    combined = agents + claude + deployment
    assert "build <service>" not in agents
    assert "build agent &&" not in claude
    assert "docker-compose up -d --build" not in combined
    assert "align_prod_checkout.sh" in agents
    assert "sleep 120" in agents
    assert "docker-compose.prod.yml" in agents
    assert 'grep "^agent_"' in agents or "grep '^agent_'" in agents
