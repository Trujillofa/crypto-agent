"""Tests for the cross-venue basis probe skeleton.

Focus on --smoke and --verdict-output (guard-consumable output).
These must work without DB or full data (per RBI dry-run rules).
"""

import json
import subprocess
import sys
from pathlib import Path

from scripts.probe_cross_venue_basis import _cheap_smoke_test


def test_cheap_smoke_helper():
    """The internal smoke helper should return a valid NO_PULSE report."""
    report = _cheap_smoke_test()
    assert report.verdict == "NO_PULSE"
    assert "SMOKE" in report.note


def test_smoke_cli_no_data(tmp_path: Path):
    """Running with --smoke should succeed, print NO_PULSE, and optionally write verdict JSON."""
    verdict_path = tmp_path / "verdict.json"
    result = subprocess.run(
        [
            sys.executable,
            "scripts/probe_cross_venue_basis.py",
            "--smoke",
            "--verdict-output",
            str(verdict_path),
            "--venues",
            "binance_usdm,bybit",
            "--symbols",
            "BTCUSDT",
        ],
        capture_output=True,
        text=True,
        cwd=Path(__file__).parent.parent,
    )
    assert result.returncode == 0
    assert "NO_PULSE" in result.stdout
    assert "SMOKE" in result.stdout
    assert verdict_path.exists()
    data = json.loads(verdict_path.read_text())
    assert data["verdict"] == "NO_PULSE"
    assert "SMOKE" in data["note"]
    assert "venues" in data["config"]


def test_verdict_output_format(tmp_path: Path):
    """--verdict-output must produce a minimal guard-consumable structure even in smoke."""
    verdict_path = tmp_path / "out.json"
    subprocess.run(
        [
            sys.executable,
            "scripts/probe_cross_venue_basis.py",
            "--smoke",
            "--verdict-output",
            str(verdict_path),
        ],
        capture_output=True,
        cwd=Path(__file__).parent.parent,
        check=True,
    )
    data = json.loads(verdict_path.read_text())
    assert set(data.keys()) >= {"verdict", "note", "generated_at", "config"}
    assert data["verdict"] in ("NO_PULSE", "WEAK_EDGE", "HAS_PULSE")
    # Ensure the timestamp is valid ISO 8601 (no duplicate offset+Z etc.)
    from datetime import datetime

    datetime.fromisoformat(data["generated_at"])  # must parse without error
