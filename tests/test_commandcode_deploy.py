from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/rebuild_prod_agents.sh"


def run_script(
    tmp_path: Path, marker: str | None, secret_mode: int | None = 0o600, fault: str = ""
):
    root = tmp_path / "repo"
    root.mkdir()
    (root / "docker-compose.commandcode.yml").touch()
    (root / "config").mkdir()
    (root / "config/prometheus-alerts.yml").touch()
    config = tmp_path / "marker"
    if marker is not None:
        config.write_text(marker)
    secret = tmp_path / "dummy.env"
    if secret_mode is not None:
        secret.write_text("COMMANDCODE_API_KEY=dummy\n")
        secret.chmod(secret_mode)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "docker.log"
    docker = bin_dir / "docker"
    docker.write_text("""#!/usr/bin/env bash
printf '%s\\n' "$*" >> "$DOCKER_LOG"
case "$*" in
  *"config --format json"*)
    if [ "$FAULT" = invalid-values ]; then echo '{"services":{"agent_sentiment_macro":{"environment":{"COMMANDCODE_ENABLED":"true","COMMANDCODE_API_KEY":"dummy","COMMANDCODE_MODEL":""}}}}'; else echo '{"services":{"agent_sentiment_macro":{"environment":{"COMMANDCODE_ENABLED":"true","COMMANDCODE_API_KEY":"dummy","COMMANDCODE_MODEL":"dummy"}}}}'; fi ;;
  *"config --services"*)
    printf 'agent_sentiment_macro\\nprometheus\\nnode_exporter\\npostgres_exporter\\n'
    if [[ "$FAULT" = extra-service && "$*" = *docker-compose.commandcode.yml* ]]; then echo unexpected; fi
    true ;;
  *"ps --services --status running"*) echo prometheus ;;
  *"promtool check rules"*) echo '5 rules found' ;;
  *"stat -c %i"*) stat -c %i "$DEPLOY_ROOT/config/prometheus-alerts.yml" ;;
  *"ps --format {{.Service}} {{.Status}}"*)
    case "$FAULT" in
      missing-agent) ;;
      duplicate-agent) printf 'agent_sentiment_macro Up (healthy)\\nagent_sentiment_macro Up (healthy)\\n' ;;
      bad-status) echo 'agent_sentiment_macro Up (starting)' ;;
      no-status) echo 'agent_sentiment_macro' ;;
      extra-agent) printf 'agent_sentiment_macro Up (healthy)\\nghost_agent Up (healthy)\\n' ;;
      *) echo 'agent_sentiment_macro Up (healthy)' ;;
    esac ;;
  *"ps --format {{.Status}} prometheus"*) echo 'Up (healthy)' ;;
esac
""")
    docker.chmod(0o755)
    sleep = bin_dir / "sleep"
    sleep.write_text("#!/bin/sh\nexit 0\n")
    sleep.chmod(0o755)
    env = os.environ.copy()
    env.update(
        DEPLOY_ROOT=str(root),
        COMMANDCODE_DEPLOY_CONFIG=str(config),
        COMMANDCODE_ENV_FILE=str(secret),
        DOCKER_LOG=str(log),
        FAULT=fault,
        PATH=f"{bin_dir}:{env['PATH']}",
    )
    result = subprocess.run(["bash", str(SCRIPT)], env=env, capture_output=True, text=True)
    return result, log.read_text().splitlines() if log.exists() else []


@pytest.mark.parametrize("marker", [None, "enabled\n"])
def test_rebuild_consistently_selects_overlay(tmp_path: Path, marker: str | None) -> None:
    result, calls = run_script(tmp_path, marker)
    assert result.returncode == 0, result.stderr + result.stdout
    compose_calls = [call for call in calls if call.startswith("compose ")]
    assert len(compose_calls) >= 6
    # Enabled preflight compares the base service list once before merging.
    selected_calls = compose_calls[1:] if marker is not None else compose_calls
    assert all(
        ("-f docker-compose.commandcode.yml" in call) == (marker is not None)
        for call in selected_calls
    )
    assert any("up -d --remove-orphans" in call for call in calls)
    assert any("up -d --force-recreate prometheus" in call for call in calls)
    assert "deploy_services=agent_sentiment_macro" in result.stdout


@pytest.mark.parametrize(
    ("marker", "mode"),
    [
        ("disabled\n", 0o600),
        ("enabled\nextra\n", 0o600),
        ("", 0o600),
        ("enabled\n", None),
        ("enabled\n", 0o644),
    ],
)
def test_invalid_config_fails_before_docker(tmp_path: Path, marker: str, mode: int | None) -> None:
    result, calls = run_script(tmp_path, marker, mode)
    assert result.returncode != 0
    assert "ERROR:" in result.stderr
    assert calls == []


@pytest.mark.parametrize("fault", ["invalid-values", "extra-service"])
def test_overlay_preflight_rejects_invalid_configuration_before_build(
    tmp_path: Path, fault: str
) -> None:
    result, calls = run_script(tmp_path, "enabled\n", fault=fault)
    assert result.returncode != 0
    assert "ERROR:" in result.stderr
    assert all(" build " not in call and " up " not in call for call in calls)


def test_real_compose_merge_preserves_services_and_scopes_dummy_environment(tmp_path: Path) -> None:
    if shutil.which("docker") is None:
        pytest.skip("docker CLI not installed")
    for name in ("docker-compose.prod.yml", "docker-compose.commandcode.yml"):
        shutil.copy(ROOT / name, tmp_path / name)
    (tmp_path / ".env").write_text(
        "POSTGRES_USER=dummy\nPOSTGRES_DB=dummy\nPOSTGRES_PASSWORD=dummy\n"
    )
    secret = tmp_path / "dummy.env"
    secret.write_text(
        "COMMANDCODE_API_KEY=dummy-key\nCOMMANDCODE_ENABLED=true\nCOMMANDCODE_MODEL=dummy-model\n"
    )
    env = os.environ.copy()
    # Do not inherit host interpolation variables or credentials.
    env = {key: env[key] for key in ("PATH", "HOME") if key in env}
    env["COMMANDCODE_ENV_FILE"] = str(secret)

    def config(*files: str) -> dict:
        args = ["docker", "compose"]
        for file in files:
            args.extend(["-f", file])
        result = subprocess.run(
            args + ["config", "--format", "json"],
            cwd=tmp_path,
            env=env,
            capture_output=True,
            text=True,
            check=True,
        )
        return json.loads(result.stdout)

    base = config("docker-compose.prod.yml")
    merged = config("docker-compose.prod.yml", "docker-compose.commandcode.yml")
    assert base["services"].keys() == merged["services"].keys()
    assert {"prometheus", "postgres_exporter", "node_exporter"} <= merged["services"].keys()
    for name, service in merged["services"].items():
        if name == "agent_sentiment_macro":
            assert service["environment"]["COMMANDCODE_API_KEY"] == "dummy-key"
            assert service["environment"]["COMMANDCODE_MODEL"] == "dummy-model"
            for key in ("COMMANDCODE_API_KEY", "COMMANDCODE_ENABLED", "COMMANDCODE_MODEL"):
                service["environment"].pop(key)
        else:
            assert "COMMANDCODE_API_KEY" not in service.get("environment", {})
        assert service == base["services"][name]


@pytest.mark.parametrize(
    "fault", ["missing-agent", "duplicate-agent", "bad-status", "no-status", "extra-agent"]
)
def test_health_gate_fails_closed_on_incomplete_status(tmp_path: Path, fault: str) -> None:
    result, calls = run_script(tmp_path, None, fault=fault)
    assert result.returncode != 0, result.stdout
    output = result.stdout + result.stderr
    assert "ERROR: agents not healthy" in output
    assert "deploy_services=" not in result.stdout
    # The failed gate must not claim success through the prune/marker path.
    assert not any("image prune" in call for call in calls)


DEPLOY_DOCS = ("AGENTS.md", "docs/QUICKREF.md", "docs/TRADING_EXECUTION.md")
COMMAND_PREFIXES = ("ssh ", "cd ", "docker ", "sudo ")


@pytest.mark.parametrize("name", DEPLOY_DOCS)
def test_deploy_docs_route_through_marker_aware_script(name: str) -> None:
    text = (ROOT / name).read_text()
    assert "rebuild_prod_agents.sh" in text
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped.startswith(COMMAND_PREFIXES):
            continue
        assert "docker-compose.prod.yml up" not in stripped, stripped
        assert "docker-compose.prod.yml build" not in stripped, stripped
