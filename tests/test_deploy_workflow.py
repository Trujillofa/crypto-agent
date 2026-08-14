"""Deterministic checks that production deploy is manual and fail-closed."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from scripts.validate_deploy_sha import main, validate_deploy_sha

WORKFLOW = Path(".github/workflows/deploy.yml")
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
    assert "tailscale/github-action@" in raw
    assert "git pull --ff-only origin main" in raw
    assert "grep '^agent_'" in raw
    assert "docker compose -f docker-compose.prod.yml build $AGENTS" in raw
    assert "docker compose -f docker-compose.prod.yml up -d --remove-orphans $AGENTS" in raw
    assert "sleep 120" in raw
    assert r"grep -vE '\(healthy\)$'" in raw
    assert "appleboy/telegram-action@" in raw
    assert "inputs.deploy_sha" in raw
    assert "github.event.workflow_run" not in raw
