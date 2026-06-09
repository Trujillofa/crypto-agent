from __future__ import annotations

from pathlib import Path


def test_rbi_loop_batch_wrapper_does_not_execute_commands() -> None:
    script = Path("scripts/run_rbi_loop_batch.sh").read_text(encoding="utf-8")

    assert "scripts/rbi_loop_batch.py" in script
    assert "--execute" not in script


def test_rbi_loop_batch_systemd_service_uses_dry_wrapper() -> None:
    service = Path("ops/systemd/crypto-agent-rbi-loop-batch.service").read_text(encoding="utf-8")

    assert "ExecStart=/bin/bash scripts/run_rbi_loop_batch.sh" in service
    assert "--execute" not in service
    assert "OnFailure=crypto-agent-telegram-failure@%n.service" in service


def test_rbi_loop_batch_timer_is_daily() -> None:
    timer = Path("ops/systemd/crypto-agent-rbi-loop-batch.timer").read_text(encoding="utf-8")

    assert "OnCalendar=*-*-* 01:15:00 UTC" in timer
    assert "Persistent=true" in timer
