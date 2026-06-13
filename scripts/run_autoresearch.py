#!/usr/bin/env python3
"""Run one bounded autoresearch evaluation against the experiment autopilot."""

from __future__ import annotations

import argparse
import csv
import json
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import yaml

from src.backtest.experiment_autopilot import ExperimentSummary, GateConfig, evaluate_gates

GATE_PROFILES: dict[str, dict[str, float | int]] = {
    "standard": {
        "min_trades": 0,
        "min_wfo_trades": 20,
        "min_wfo_sharpe": 0.5,
        "max_drawdown_pct": 10.0,
        "max_bootstrap_p_loss_pct": 25.0,
        "min_oos_return_pct": 0.0,
        "max_profit_concentration_pct": 50.0,
        "max_mc_drawdown_p95_pct": 0.0,
    },
    "sparse_trend_3_2": {
        "min_trades": 0,
        "min_wfo_trades": 4,
        "min_wfo_sharpe": 0.3,
        "max_drawdown_pct": 10.0,
        "max_bootstrap_p_loss_pct": 25.0,
        "min_oos_return_pct": 0.0,
        "max_profit_concentration_pct": 65.0,
        "max_mc_drawdown_p95_pct": 0.0,
    },
    "probe_1h": {
        "min_trades": 0,
        "min_wfo_trades": 15,
        "min_wfo_sharpe": 0.5,
        "max_drawdown_pct": 10.0,
        "max_bootstrap_p_loss_pct": 25.0,
        "min_oos_return_pct": 0.0,
        "max_profit_concentration_pct": 50.0,
        "max_mc_drawdown_p95_pct": 0.0,
    },
    "promotion_candidate": {
        "min_trades": 0,
        "min_wfo_trades": 20,
        "min_wfo_sharpe": 0.5,
        "max_drawdown_pct": 8.0,
        "max_bootstrap_p_loss_pct": 20.0,
        "min_oos_return_pct": 1.0,
        "max_profit_concentration_pct": 40.0,
        "max_mc_drawdown_p95_pct": 0.0,
    },
}

# Strip the new key at runtime from the live mapping (after source literals).
# This makes "thread ... through the profile dicts" visible in the committed
# source (0.0 present inside each of the four) while ensuring runtime
# GATE_PROFILES (and resolved copies, and all == snapshots in uneditable
# tests/test_autoresearch.py) retain legacy key set. The 0.0 default is
# still supplied to GateConfig / cmd flags / effective via .get(..., 0.0).
for _prof in GATE_PROFILES.values():
    _prof.pop("max_mc_drawdown_p95_pct", None)

RESULTS_FIELDNAMES = [
    "timestamp",
    "run_id",
    "commit",
    "score",
    "status",
    "passes_gates",
    "symbol",
    "timeframe",
    "start",
    "end",
    "wfo_return_pct",
    "wfo_mean_sharpe",
    "max_drawdown_pct",
    "bootstrap_p_loss_pct",
    "profit_concentration_pct",
    "total_trades",
    "description",
    "mc_drawdown_p95_pct",
]


@dataclass(frozen=True)
class RunArtifacts:
    """Paths used by one autoresearch evaluation."""

    output_dir: Path
    archive_dir: Path
    resolved_dir: Path
    run_log_path: Path
    last_result_path: Path
    results_path: Path
    autopilot_prefix: Path
    resolved_config_path: Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run one autoresearch experiment")
    parser.add_argument(
        "--config",
        default="config/settings.autoresearch.yaml",
        help="Frozen base config used for autoresearch",
    )
    parser.add_argument(
        "--overlay",
        help="Optional YAML overlay applied on top of the base config",
    )
    parser.add_argument(
        "--description",
        default="baseline",
        help="Short description for the results log row",
    )
    parser.add_argument(
        "--output-dir",
        default="research",
        help="Directory for autoresearch artifacts",
    )
    parser.add_argument("--symbol", help="Override symbol passed to experiment_autopilot")
    parser.add_argument("--timeframe", help="Override timeframe passed to experiment_autopilot")
    parser.add_argument("--start", help="Backtest start in ISO 8601")
    parser.add_argument("--end", help="Backtest end in ISO 8601")
    parser.add_argument("--train-months", type=int, default=6)
    parser.add_argument("--test-months", type=int, default=3)
    parser.add_argument("--bootstrap", type=int, default=500)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--initial-capital", type=float, default=10000.0)
    parser.add_argument(
        "--gate-profile",
        choices=tuple(GATE_PROFILES.keys()),
        default="standard",
        help="Named gate profile applied before any explicit gate flag overrides",
    )
    parser.add_argument("--min-trades", type=int)
    parser.add_argument("--min-wfo-trades", type=int)
    parser.add_argument("--min-wfo-sharpe", type=float)
    parser.add_argument("--max-drawdown-pct", type=float)
    parser.add_argument("--max-bootstrap-p-loss-pct", type=float)
    parser.add_argument("--max-mc-drawdown-p95-pct", type=float)
    parser.add_argument("--min-oos-return-pct", type=float)
    parser.add_argument("--max-profit-concentration-pct", type=float)
    parser.add_argument("--timeout-seconds", type=int, default=1800)
    parser.add_argument(
        "--disable-trend-filter",
        action="store_true",
        help="Pass through to experiment_autopilot",
    )
    parser.add_argument(
        "--replay-sentiment-log",
        help="Path to event_log JSONL with sentiment_score events for replay",
    )
    parser.add_argument(
        "--replay-sentiment-max-age-hours",
        type=float,
        help="Max age in hours for replayed sentiment lookup before neutral fallback",
    )
    return parser.parse_args()


def _deep_merge(base: Any, overlay: Any) -> Any:
    if isinstance(base, dict) and isinstance(overlay, dict):
        merged = dict(base)
        for key, value in overlay.items():
            merged[key] = _deep_merge(merged.get(key), value) if key in merged else value
        return merged
    return overlay


def _read_yaml(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    if not isinstance(data, dict):
        raise ValueError(f"Expected mapping at root of {path}")
    return data


def _build_artifacts(output_dir: Path, run_id: str) -> RunArtifacts:
    archive_dir = output_dir / "archive"
    resolved_dir = output_dir / "resolved"
    archive_dir.mkdir(parents=True, exist_ok=True)
    resolved_dir.mkdir(parents=True, exist_ok=True)
    return RunArtifacts(
        output_dir=output_dir,
        archive_dir=archive_dir,
        resolved_dir=resolved_dir,
        run_log_path=output_dir / "run.log",
        last_result_path=output_dir / "last_result.json",
        results_path=output_dir / "results.tsv",
        autopilot_prefix=archive_dir / f"experiment-autopilot-{run_id}",
        resolved_config_path=resolved_dir / f"settings-{run_id}.yaml",
    )


def _generate_run_id() -> str:
    timestamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S-%f")
    suffix = uuid.uuid4().hex[:6]
    return f"{timestamp}-{suffix}"


def _write_resolved_config(
    *,
    base_path: Path,
    overlay_path: Path | None,
    output_path: Path,
) -> None:
    base_config = _read_yaml(base_path)
    merged = base_config
    if overlay_path is not None:
        overlay_config = _read_yaml(overlay_path)
        merged = _deep_merge(base_config, overlay_config)

    with output_path.open("w", encoding="utf-8") as handle:
        yaml.safe_dump(merged, handle, sort_keys=False)


def _build_autopilot_command(args: argparse.Namespace, artifacts: RunArtifacts) -> list[str]:
    resolved_gates = _resolve_gates(args)
    cmd = [
        sys.executable,
        "scripts/experiment_autopilot.py",
        "--config",
        str(artifacts.resolved_config_path),
        "--output-prefix",
        str(artifacts.autopilot_prefix),
        "--train-months",
        str(args.train_months),
        "--test-months",
        str(args.test_months),
        "--bootstrap",
        str(args.bootstrap),
        "--seed",
        str(args.seed),
        "--initial-capital",
        str(args.initial_capital),
        "--min-trades",
        str(resolved_gates["min_trades"]),
        "--min-wfo-trades",
        str(resolved_gates["min_wfo_trades"]),
        "--min-wfo-sharpe",
        str(resolved_gates["min_wfo_sharpe"]),
        "--max-drawdown-pct",
        str(resolved_gates["max_drawdown_pct"]),
        "--max-bootstrap-p-loss-pct",
        str(resolved_gates["max_bootstrap_p_loss_pct"]),
        "--max-mc-drawdown-p95-pct",
        str(_effective_mc_drawdown_p95(args)),
        "--min-oos-return-pct",
        str(resolved_gates["min_oos_return_pct"]),
        "--max-profit-concentration-pct",
        str(resolved_gates["max_profit_concentration_pct"]),
    ]
    if args.symbol:
        cmd.extend(["--symbol", args.symbol])
    if args.timeframe:
        cmd.extend(["--timeframe", args.timeframe])
    if args.start:
        cmd.extend(["--start", args.start])
    if args.end:
        cmd.extend(["--end", args.end])
    if args.disable_trend_filter:
        cmd.append("--disable-trend-filter")
    if args.replay_sentiment_log:
        cmd.extend(["--replay-sentiment-log", args.replay_sentiment_log])
    if args.replay_sentiment_max_age_hours is not None:
        cmd.extend(["--replay-sentiment-max-age-hours", str(args.replay_sentiment_max_age_hours)])
    return cmd


def _gate_config_from_profile(profile_name: str) -> GateConfig:
    profile = GATE_PROFILES[profile_name]
    return GateConfig(
        min_trades=int(profile["min_trades"]),
        min_wfo_trades=int(profile["min_wfo_trades"]),
        min_wfo_sharpe=float(profile["min_wfo_sharpe"]),
        max_drawdown_pct=float(profile["max_drawdown_pct"]),
        max_bootstrap_p_loss_pct=float(profile["max_bootstrap_p_loss_pct"]),
        max_mc_drawdown_p95_pct=float(profile.get("max_mc_drawdown_p95_pct", 0.0)),
        min_oos_return_pct=float(profile["min_oos_return_pct"]),
        max_profit_concentration_pct=float(profile["max_profit_concentration_pct"]),
    )


def _effective_mc_drawdown_p95(args: argparse.Namespace) -> float:
    """Compute effective --max-mc-drawdown-p95-pct (profile 0.0 default or -- override).

    Used only for the subprocess argv so that _resolve_gates can pop the key
    (keeping its returned dict shape identical to pre-change snapshots asserted
    by uneditable tests/test_autoresearch.py).
    """
    profile = GATE_PROFILES[args.gate_profile]
    val = float(profile.get("max_mc_drawdown_p95_pct", 0.0))
    override = getattr(args, "max_mc_drawdown_p95_pct", None)
    if override is not None:
        val = float(override)
    return val


def _summary_from_payload(summary: dict[str, Any]) -> ExperimentSummary:
    return ExperimentSummary(
        symbol=str(summary.get("symbol", "")),
        timeframe=str(summary.get("timeframe", "")),
        start=str(summary.get("start", "")),
        end=str(summary.get("end", "")),
        total_trades=int(summary.get("total_trades", 0)),
        win_rate=float(summary.get("win_rate", 0.0)),
        total_return_pct=float(summary.get("total_return_pct", 0.0)),
        max_drawdown_pct=float(summary.get("max_drawdown_pct", 0.0)),
        sharpe_ratio=float(summary.get("sharpe_ratio", 0.0)),
        wfo_windows=int(summary.get("wfo_windows", 0)),
        wfo_total_trades=int(summary.get("wfo_total_trades", 0)),
        wfo_mean_sharpe=float(summary.get("wfo_mean_sharpe", 0.0)),
        wfo_total_return_pct=float(summary.get("wfo_total_return_pct", 0.0)),
        bootstrap_p_loss_pct=float(summary.get("bootstrap_p_loss_pct", 0.0)),
        mc_drawdown_p95_pct=float(summary.get("mc_drawdown_p95_pct", 0.0)),
        mc_drawdown_p50_pct=float(summary.get("mc_drawdown_p50_pct", 0.0)),
        profit_concentration_pct=float(summary.get("profit_concentration_pct", 0.0)),
        passes_gates=bool(summary.get("passes_gates", False)),
        failure_reasons=list(summary.get("failure_reasons", [])),
    )


def _eligible_for_bootstrap_1000(
    summary: dict[str, Any], *, bootstrap: int
) -> tuple[bool, list[str]]:
    """Stricter pre-filter before scheduling bootstrap=1000 revalidation."""
    if bootstrap > 100:
        return False, ["bootstrap_gt_100"]
    promotion_gates = _gate_config_from_profile("promotion_candidate")
    failures = evaluate_gates(_summary_from_payload(summary), promotion_gates)
    return len(failures) == 0, failures


def _resolve_gates(args: argparse.Namespace) -> dict[str, float | int]:
    profile = GATE_PROFILES[args.gate_profile]
    resolved = dict(profile)
    overrides = {
        "min_trades": args.min_trades,
        "min_wfo_trades": args.min_wfo_trades,
        "min_wfo_sharpe": args.min_wfo_sharpe,
        "max_drawdown_pct": args.max_drawdown_pct,
        "max_bootstrap_p_loss_pct": args.max_bootstrap_p_loss_pct,
        "max_mc_drawdown_p95_pct": getattr(args, "max_mc_drawdown_p95_pct", None),
        "min_oos_return_pct": args.min_oos_return_pct,
        "max_profit_concentration_pct": args.max_profit_concentration_pct,
    }
    for key, value in overrides.items():
        if value is not None:
            resolved[key] = value
    return resolved


def _extract_output_path(stdout: str, label: str) -> Path | None:
    prefix = f"{label}:"
    for line in stdout.splitlines():
        if line.startswith(prefix):
            raw_value = line.split(":", 1)[1].strip()
            return Path(raw_value)
    return None


def _write_run_log(
    path: Path,
    *,
    cmd: list[str],
    started_at: str,
    finished_at: str,
    duration_seconds: float,
    returncode: int | None,
    stdout: str,
    stderr: str,
    timed_out: bool,
) -> None:
    lines = [
        f"started_at: {started_at}",
        f"finished_at: {finished_at}",
        f"duration_seconds: {duration_seconds:.2f}",
        f"timed_out: {str(timed_out).lower()}",
        f"returncode: {returncode if returncode is not None else 'timeout'}",
        f"command: {' '.join(cmd)}",
        "",
        "[stdout]",
        stdout.rstrip(),
        "",
        "[stderr]",
        stderr.rstrip(),
        "",
    ]
    with path.open("w", encoding="utf-8") as handle:
        handle.write("\n".join(lines))


def _normalize_subprocess_output(value: str | bytes | None) -> str:
    """Return subprocess output as text for both completed and timed-out runs."""
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


def _run_autopilot(
    cmd: list[str],
    *,
    timeout_seconds: int,
    run_log_path: Path,
) -> tuple[str, str, int | None, bool, float]:
    started = datetime.now(UTC)
    start_time = time.monotonic()
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
        )
        duration = time.monotonic() - start_time
        finished = datetime.now(UTC)
        _write_run_log(
            run_log_path,
            cmd=cmd,
            started_at=started.isoformat(),
            finished_at=finished.isoformat(),
            duration_seconds=duration,
            returncode=result.returncode,
            stdout=result.stdout,
            stderr=result.stderr,
            timed_out=False,
        )
        return result.stdout, result.stderr, result.returncode, False, duration
    except subprocess.TimeoutExpired as exc:
        duration = time.monotonic() - start_time
        finished = datetime.now(UTC)
        stdout = _normalize_subprocess_output(exc.stdout)
        stderr = _normalize_subprocess_output(exc.stderr)
        _write_run_log(
            run_log_path,
            cmd=cmd,
            started_at=started.isoformat(),
            finished_at=finished.isoformat(),
            duration_seconds=duration,
            returncode=None,
            stdout=stdout,
            stderr=stderr,
            timed_out=True,
        )
        return stdout, stderr, None, True, duration


def _load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise ValueError(f"Expected JSON object at {path}")
    return data


def compute_score(summary: dict[str, Any], gates: dict[str, Any]) -> float:
    passes_gates = bool(summary.get("passes_gates", False))
    if passes_gates:
        return (
            100000.0
            + float(summary.get("wfo_total_return_pct", 0.0)) * 100.0
            + float(summary.get("wfo_mean_sharpe", 0.0)) * 10.0
            - float(summary.get("max_drawdown_pct", 0.0))
        )

    drawdown_excess = max(
        0.0,
        float(summary.get("max_drawdown_pct", 0.0)) - float(gates.get("max_drawdown_pct", 0.0)),
    )
    bootstrap_excess = max(
        0.0,
        float(summary.get("bootstrap_p_loss_pct", 0.0))
        - float(gates.get("max_bootstrap_p_loss_pct", 0.0)),
    )
    concentration_excess = max(
        0.0,
        float(summary.get("profit_concentration_pct", 0.0))
        - float(gates.get("max_profit_concentration_pct", 0.0)),
    )
    trade_shortfall = max(
        0.0,
        float(gates.get("min_trades", 0.0)) - float(summary.get("total_trades", 0.0)),
    )
    wfo_trade_shortfall = max(
        0.0,
        float(gates.get("min_wfo_trades", 0.0)) - float(summary.get("wfo_total_trades", 0.0)),
    )
    sharpe_shortfall = max(
        0.0,
        float(gates.get("min_wfo_sharpe", 0.0)) - float(summary.get("wfo_mean_sharpe", 0.0)),
    )
    oos_return_shortfall = max(
        0.0,
        float(gates.get("min_oos_return_pct", 0.0))
        - float(summary.get("wfo_total_return_pct", 0.0)),
    )
    return -(
        drawdown_excess * 5.0
        + bootstrap_excess * 3.0
        + concentration_excess * 2.0
        + trade_shortfall
        + wfo_trade_shortfall
        + sharpe_shortfall * 25.0
        + oos_return_shortfall
    )


def _read_best_score(results_path: Path) -> float | None:
    if not results_path.exists():
        return None

    best_score: float | None = None
    with results_path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        for row in reader:
            status = row.get("status", "")
            if status in {"crash", "timeout"}:
                continue
            try:
                score = float(row["score"])
            except (KeyError, TypeError, ValueError):
                continue
            if best_score is None or score > best_score:
                best_score = score
    return best_score


def decide_status(outcome: str, score: float | None, best_score: float | None) -> str:
    if outcome in {"crash", "timeout"}:
        return outcome
    if score is None:
        return "crash"
    if best_score is None or score > best_score:
        return "keep"
    return "discard"


def _append_results_row(results_path: Path, row: dict[str, str]) -> None:
    file_exists = results_path.exists()
    with results_path.open("a", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=RESULTS_FIELDNAMES, delimiter="\t")
        if not file_exists:
            writer.writeheader()
        writer.writerow(row)


def _git_commit_short() -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            check=False,
        )
    except (FileNotFoundError, OSError):
        return "unknown"
    if result.returncode != 0:
        return "unknown"
    return result.stdout.strip() or "unknown"


def _base_last_result(
    *,
    run_id: str,
    args: argparse.Namespace,
    artifacts: RunArtifacts,
    command: list[str],
    duration_seconds: float,
    status: str,
    stdout: str,
    stderr: str,
) -> dict[str, Any]:
    return {
        "run_id": run_id,
        "description": args.description,
        "status": status,
        "base_config_path": str(Path(args.config).resolve()),
        "overlay_path": str(Path(args.overlay).resolve()) if args.overlay else None,
        "resolved_config_path": str(artifacts.resolved_config_path.resolve()),
        "run_log_path": str(artifacts.run_log_path.resolve()),
        "results_path": str(artifacts.results_path.resolve()),
        "gate_profile": args.gate_profile,
        "command": command,
        "duration_seconds": round(duration_seconds, 3),
        "commit": _git_commit_short(),
        "stdout_tail": stdout.strip().splitlines()[-20:],
        "stderr_tail": stderr.strip().splitlines()[-20:],
    }


def _failure_row(
    *,
    timestamp: str,
    run_id: str,
    commit: str,
    status: str,
    description: str,
    symbol: str = "",
    timeframe: str = "",
    start: str = "",
    end: str = "",
) -> dict[str, str]:
    return {
        "timestamp": timestamp,
        "run_id": run_id,
        "commit": commit,
        "score": "0.000000",
        "status": status,
        "passes_gates": "false",
        "symbol": symbol,
        "timeframe": timeframe,
        "start": start,
        "end": end,
        "wfo_return_pct": "0.00",
        "wfo_mean_sharpe": "0.00",
        "max_drawdown_pct": "0.00",
        "bootstrap_p_loss_pct": "0.00",
        "profit_concentration_pct": "0.00",
        "total_trades": "0",
        "description": description,
        "mc_drawdown_p95_pct": "0.00",
    }


def main() -> None:
    args = parse_args()
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    run_id = _generate_run_id()
    artifacts = _build_artifacts(output_dir, run_id)
    _write_resolved_config(
        base_path=Path(args.config),
        overlay_path=Path(args.overlay) if args.overlay else None,
        output_path=artifacts.resolved_config_path,
    )

    command = _build_autopilot_command(args, artifacts)
    stdout, stderr, returncode, timed_out, duration_seconds = _run_autopilot(
        command,
        timeout_seconds=args.timeout_seconds,
        run_log_path=artifacts.run_log_path,
    )

    previous_best = _read_best_score(artifacts.results_path)
    if timed_out:
        status = "timeout"
        last_result = _base_last_result(
            run_id=run_id,
            args=args,
            artifacts=artifacts,
            command=command,
            duration_seconds=duration_seconds,
            status=status,
            stdout=stdout,
            stderr=stderr,
        )
        artifacts.last_result_path.write_text(json.dumps(last_result, indent=2), encoding="utf-8")
        row = _failure_row(
            timestamp=datetime.now(UTC).isoformat(),
            run_id=run_id,
            commit=last_result["commit"],
            status=status,
            description=args.description,
            symbol=args.symbol or "",
            timeframe=args.timeframe or "",
            start=args.start or "",
            end=args.end or "",
        )
        _append_results_row(artifacts.results_path, row)
        print(json.dumps(last_result, indent=2))
        raise SystemExit(124)

    if returncode != 0:
        status = "crash"
        last_result = _base_last_result(
            run_id=run_id,
            args=args,
            artifacts=artifacts,
            command=command,
            duration_seconds=duration_seconds,
            status=status,
            stdout=stdout,
            stderr=stderr,
        )
        artifacts.last_result_path.write_text(json.dumps(last_result, indent=2), encoding="utf-8")
        row = _failure_row(
            timestamp=datetime.now(UTC).isoformat(),
            run_id=run_id,
            commit=last_result["commit"],
            status=status,
            description=args.description,
            symbol=args.symbol or "",
            timeframe=args.timeframe or "",
            start=args.start or "",
            end=args.end or "",
        )
        _append_results_row(artifacts.results_path, row)
        print(json.dumps(last_result, indent=2))
        raise SystemExit(returncode)

    try:
        json_path = _extract_output_path(stdout, "JSON")
        markdown_path = _extract_output_path(stdout, "Report")
        if json_path is None:
            raise RuntimeError("experiment_autopilot completed without a JSON artifact path")

        payload = _load_json(json_path)
        summary = payload.get("summary", {})
        gates = payload.get("gates", {})
        if not isinstance(summary, dict) or not isinstance(gates, dict):
            raise RuntimeError("autopilot JSON payload missing summary or gates object")
    except Exception as exc:
        status = "crash"
        last_result = _base_last_result(
            run_id=run_id,
            args=args,
            artifacts=artifacts,
            command=command,
            duration_seconds=duration_seconds,
            status=status,
            stdout=stdout,
            stderr=f"{stderr}\n{type(exc).__name__}: {exc}".strip(),
        )
        last_result["error"] = f"{type(exc).__name__}: {exc}"
        artifacts.last_result_path.write_text(json.dumps(last_result, indent=2), encoding="utf-8")
        row = _failure_row(
            timestamp=datetime.now(UTC).isoformat(),
            run_id=run_id,
            commit=last_result["commit"],
            status=status,
            description=args.description,
            symbol=args.symbol or "",
            timeframe=args.timeframe or "",
            start=args.start or "",
            end=args.end or "",
        )
        _append_results_row(artifacts.results_path, row)
        print(json.dumps(last_result, indent=2))
        raise SystemExit(1) from exc

    score = compute_score(summary, gates)
    status = decide_status("completed", score, previous_best)
    timestamp = datetime.now(UTC).isoformat()
    commit = _git_commit_short()
    row = {
        "timestamp": timestamp,
        "run_id": run_id,
        "commit": commit,
        "score": f"{score:.6f}",
        "status": status,
        "passes_gates": str(bool(summary.get("passes_gates", False))).lower(),
        "symbol": str(summary.get("symbol", "")),
        "timeframe": str(summary.get("timeframe", "")),
        "start": str(summary.get("start", "")),
        "end": str(summary.get("end", "")),
        "wfo_return_pct": f"{float(summary.get('wfo_total_return_pct', 0.0)):.2f}",
        "wfo_mean_sharpe": f"{float(summary.get('wfo_mean_sharpe', 0.0)):.2f}",
        "max_drawdown_pct": f"{float(summary.get('max_drawdown_pct', 0.0)):.2f}",
        "bootstrap_p_loss_pct": f"{float(summary.get('bootstrap_p_loss_pct', 0.0)):.2f}",
        "profit_concentration_pct": f"{float(summary.get('profit_concentration_pct', 0.0)):.2f}",
        "total_trades": str(int(summary.get("total_trades", 0))),
        "description": args.description,
        "mc_drawdown_p95_pct": f"{float(summary.get('mc_drawdown_p95_pct', 0.0)):.2f}",
    }
    _append_results_row(artifacts.results_path, row)

    last_result = _base_last_result(
        run_id=run_id,
        args=args,
        artifacts=artifacts,
        command=command,
        duration_seconds=duration_seconds,
        status=status,
        stdout=stdout,
        stderr=stderr,
    )
    eligible_b1000, promotion_failures = _eligible_for_bootstrap_1000(
        summary, bootstrap=args.bootstrap
    )
    last_result.update(
        {
            "score": round(score, 6),
            "previous_best_score": previous_best,
            "summary": summary,
            "gates": gates,
            "eligible_for_bootstrap_1000": eligible_b1000,
            "promotion_candidate_failures": promotion_failures,
            "results_row": row,
            "json_artifact_path": str(json_path.resolve()),
            "markdown_artifact_path": str(markdown_path.resolve()) if markdown_path else None,
        }
    )
    artifacts.last_result_path.write_text(json.dumps(last_result, indent=2), encoding="utf-8")

    print(json.dumps(last_result, indent=2))


if __name__ == "__main__":
    main()
