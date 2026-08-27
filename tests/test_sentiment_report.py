"""Sentiment report parses historical sources and treats DeepSeek answers as live."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from scripts.sentiment_report import parse_jsonl, print_report

_REPO_ROOT = Path(__file__).resolve().parent.parent
_REPORT_SCRIPT = _REPO_ROOT / "scripts" / "sentiment_report.py"


def _event(
    source: str,
    *,
    score: float = 60.0,
    provider: str = "",
    model: str = "",
    error: str = "",
    ts: str = "2026-08-15T15:03:17+00:00",
    symbol: str = "SOLUSDT",
) -> str:
    payload: dict[str, object] = {
        "symbol": symbol,
        "score": score,
        "source": source,
    }
    if provider:
        payload["provider"] = provider
    if model:
        payload["model"] = model
    if error:
        payload["error"] = error
    return json.dumps(
        {
            "type": "sentiment_score",
            "ts": ts,
            "payload": payload,
        }
    )


def test_parse_historical_sentiment_sources() -> None:
    """xai_live, xai_error_fallback, neutral_fallback, and deepseek_fallback still parse."""
    lines = [
        _event(
            "xai_live",
            score=72.0,
            provider="xai",
            model="grok-4-1-fast-reasoning",
            ts="2026-08-15T15:00:00+00:00",
        ),
        _event(
            "xai_error_fallback",
            score=30.0,
            provider="xai",
            model="none",
            error="timeout",
            ts="2026-08-15T15:01:00+00:00",
        ),
        _event(
            "neutral_fallback",
            score=50.0,
            provider="none",
            model="none",
            ts="2026-08-15T15:02:00+00:00",
        ),
        _event(
            "deepseek_fallback",
            score=62.0,
            provider="deepseek",
            model="deepseek-v4-pro",
            ts="2026-08-15T15:03:17+00:00",
        ),
    ]

    report = parse_jsonl(lines)

    assert [obs.source for obs in report.observations] == [
        "xai_live",
        "xai_error_fallback",
        "neutral_fallback",
        "deepseek_fallback",
    ]
    by_source = {obs.source: obs for obs in report.observations}
    assert by_source["xai_live"].provider == "xai"
    assert by_source["xai_live"].model == "grok-4-1-fast-reasoning"
    assert by_source["xai_error_fallback"].error == "timeout"
    assert by_source["neutral_fallback"].score == 50.0
    assert by_source["deepseek_fallback"].provider == "deepseek"
    assert by_source["deepseek_fallback"].model == "deepseek-v4-pro"


def test_deepseek_success_is_live_not_degradation(capsys) -> None:
    """Successful DeepSeek observations are answered/live, not a degraded Grok feed."""
    lines = [
        _event(
            "deepseek_fallback",
            score=float(55 + i * 2),
            provider="deepseek",
            model="deepseek-v4-pro",
            ts=f"2026-08-15T15:{i:02d}:00+00:00",
        )
        for i in range(10)
    ]

    print_report(parse_jsonl(lines))
    out = capsys.readouterr().out

    assert "Grok" not in out
    assert "Low live rate" not in out
    assert "Healthy" in out
    assert "deepseek_fallback" in out


def test_zai_success_is_live_not_degradation(capsys) -> None:
    """Successful Z.AI observations are answered/live, not a degraded Grok feed."""
    lines = [
        _event(
            "zai_live",
            score=float(55 + i * 2),
            provider="zai",
            model="glm-5.3",
            ts=f"2026-08-27T15:{i:02d}:00+00:00",
        )
        for i in range(10)
    ]

    print_report(parse_jsonl(lines))
    out = capsys.readouterr().out

    assert "Grok" not in out
    assert "Low live rate" not in out
    assert "Healthy" in out
    assert "zai_live" in out


def test_historical_xai_live_still_counts_as_live(capsys) -> None:
    """Pre-DeepSeek xai_live logs remain healthy live observations."""
    lines = [
        _event(
            "xai_live",
            score=float(50 + i * 2),
            provider="xai",
            model="grok-4-1-fast-reasoning",
            ts=f"2026-08-15T16:{i:02d}:00+00:00",
        )
        for i in range(10)
    ]

    print_report(parse_jsonl(lines))
    out = capsys.readouterr().out

    assert "Healthy" in out
    assert "Low live rate" not in out


def test_historical_and_neutral_error_sources_still_parse_as_unanswered(capsys) -> None:
    """xai_error_fallback and the provider-neutral error source both count as errors."""
    lines = [
        _event(
            "xai_error_fallback",
            score=30.0,
            provider="xai",
            model="none",
            error="timeout",
            ts="2026-08-15T17:00:00+00:00",
        ),
        _event(
            "error_fallback",
            score=30.0,
            provider="deepseek",
            model="deepseek-v4-pro",
            error="Error code: 402",
            ts="2026-08-15T17:01:00+00:00",
        ),
        _event(
            "neutral_fallback",
            score=50.0,
            provider="none",
            model="none",
            ts="2026-08-15T17:02:00+00:00",
        ),
    ]

    print_report(parse_jsonl(lines))
    out = capsys.readouterr().out

    assert "Low live rate" in out
    assert "Grok" not in out
    assert "xai_error_fallback" in out
    assert "error_fallback" in out
    assert "neutral_fallback" in out


def _isolated_report_env() -> dict[str, str]:
    env = os.environ.copy()
    env["PYTHONPATH"] = ""
    env["PYTHONNOUSERSITE"] = "1"
    return env


def _assert_standalone_report(completed: subprocess.CompletedProcess[str]) -> None:
    combined = completed.stdout + completed.stderr
    assert completed.returncode == 0, combined
    assert "SENTIMENT MONITORING REPORT" in completed.stdout
    assert "Traceback" not in combined
    assert "ModuleNotFoundError" not in combined
    assert "src.strategy" not in combined


def test_standalone_script_runs_from_tmp_dir_with_isolated_interpreter(tmp_path: Path) -> None:
    fixture = tmp_path / "events.jsonl"
    fixture.write_text(
        _event("xai_live", score=72.0, provider="xai", model="grok-4-1-fast-reasoning") + "\n",
        encoding="utf-8",
    )
    completed = subprocess.run(
        [sys.executable, "-S", str(_REPORT_SCRIPT), str(fixture)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=30,
        env=_isolated_report_env(),
        check=False,
    )
    _assert_standalone_report(completed)


def test_standalone_script_reads_stdin_dash_from_tmp_dir(tmp_path: Path) -> None:
    payload = _event(
        "deepseek_fallback",
        score=62.0,
        provider="deepseek",
        model="deepseek-v4-pro",
    )
    completed = subprocess.run(
        [sys.executable, "-S", str(_REPORT_SCRIPT), "-"],
        cwd=tmp_path,
        input=payload + "\n",
        capture_output=True,
        text=True,
        timeout=30,
        env=_isolated_report_env(),
        check=False,
    )
    _assert_standalone_report(completed)
    assert "deepseek_fallback" in completed.stdout
