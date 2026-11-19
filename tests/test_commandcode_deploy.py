from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/rebuild_prod_agents.sh"


def run_script(tmp_path: Path, marker: str | None, fault: str = ""):
    root = tmp_path / "repo"
    root.mkdir()
    (root / "config").mkdir()
    (root / "config/prometheus-alerts.yml").touch()
    config = tmp_path / "marker"
    if marker is not None:
        config.write_text(marker)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "docker.log"
    docker = bin_dir / "docker"
    docker.write_text("""#!/usr/bin/env bash
printf '%s\\n' "$*" >> "$DOCKER_LOG"
case "$*" in
  *"config --services"*)
    printf 'agent_sol_panic_block_paper\\nprometheus\\nnode_exporter\\npostgres_exporter\\n'
    true ;;
  *"ps --services --status running"*) echo prometheus ;;
  *"promtool check rules"*) echo '5 rules found' ;;
  *"stat -c %i"*) stat -c %i "$DEPLOY_ROOT/config/prometheus-alerts.yml" ;;
  *"ps --format {{.Service}} {{.Status}}"*)
    case "$FAULT" in
      missing-agent) ;;
      duplicate-agent) printf 'agent_sol_panic_block_paper Up (healthy)\\nagent_sol_panic_block_paper Up (healthy)\\n' ;;
      bad-status) echo 'agent_sol_panic_block_paper Up (starting)' ;;
      no-status) echo 'agent_sol_panic_block_paper' ;;
      extra-agent) printf 'agent_sol_panic_block_paper Up (healthy)\\nghost_agent Up (healthy)\\n' ;;
      *) echo 'agent_sol_panic_block_paper Up (healthy)' ;;
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
        DOCKER_LOG=str(log),
        FAULT=fault,
        PATH=f"{bin_dir}:{env['PATH']}",
    )
    result = subprocess.run(["bash", str(SCRIPT)], env=env, capture_output=True, text=True)
    return result, log.read_text().splitlines() if log.exists() else []


@pytest.mark.parametrize("marker", [None, "enabled\n"])
def test_rebuild_warns_on_stale_marker_and_stays_base_only(
    tmp_path: Path, marker: str | None
) -> None:
    result, calls = run_script(tmp_path, marker)
    assert result.returncode == 0, result.stderr + result.stdout
    compose_calls = [call for call in calls if call.startswith("compose ")]
    assert compose_calls
    assert all("docker-compose.commandcode.yml" not in call for call in compose_calls)
    warnings = [
        line for line in result.stderr.splitlines() if line.startswith("WARNING: Command Code")
    ]
    assert len(warnings) == (1 if marker is not None else 0)
    assert any("up -d --remove-orphans" in call for call in calls)
    assert any("up -d --force-recreate prometheus" in call for call in calls)
    assert "deploy_services=agent_sol_panic_block_paper" in result.stdout


def test_commandcode_overlay_is_gone() -> None:
    """CR-4 (b) removed the overlay's only consumer; the overlay must not linger."""
    assert not (ROOT / "docker-compose.commandcode.yml").exists()
    assert "docker-compose.commandcode.yml" not in SCRIPT.read_text()


@pytest.mark.parametrize(
    "fault", ["missing-agent", "duplicate-agent", "bad-status", "no-status", "extra-agent"]
)
def test_health_gate_fails_closed_on_incomplete_status(tmp_path: Path, fault: str) -> None:
    result, calls = run_script(tmp_path, None, fault=fault)
    assert result.returncode != 0, result.stdout
    output = result.stdout + result.stderr
    assert "ERROR: agents not healthy" in output
    assert "deploy_services=" not in result.stdout
    assert not any("image prune" in call for call in calls)


DEPLOY_DOCS = ("AGENTS.md", "docs/QUICKREF.md", "docs/TRADING_EXECUTION.md")
COMMAND_PREFIXES = ("ssh ", "cd ", "docker ", "sudo ")


@pytest.mark.parametrize("name", DEPLOY_DOCS)
def test_deploy_docs_route_through_rebuild_script(name: str) -> None:
    text = (ROOT / name).read_text()
    assert "rebuild_prod_agents.sh" in text
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped.startswith(COMMAND_PREFIXES):
            continue
        assert "docker-compose.prod.yml up" not in stripped, stripped
        assert "docker-compose.prod.yml build" not in stripped, stripped
