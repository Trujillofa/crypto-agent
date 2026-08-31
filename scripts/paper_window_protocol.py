"""Eligibility helpers for frozen paper evidence windows.

Read-only. Does not query production, place orders, or call providers.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from src.sentiment_sources import SENTIMENT_ANSWERED_SOURCES, SENTIMENT_NO_ANSWER_SOURCES

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_LOCK = REPO_ROOT / "research/paper_windows/deepseek-sentiment-macro-v1/lock.json"
FROZEN_PROTOCOL_ID = "deepseek-sentiment-macro-paper-window"
FROZEN_DEPLOYED_SHA = "bc6ea9e1c62b36c82d27b96f2fb2c28d99f2f316"
FROZEN_WINDOW_START = "2026-08-31T15:26:51Z"
PERMITTED_DECISIONS = (
    "CONTINUE_COLLECTING",
    "STOP_OPERATIONAL_FAILURE",
    "STOP_PERFORMANCE_FAILURE",
    "EVIDENCE_COMPLETE",
)


class LockTamper(ValueError):
    """Lock was edited after freeze in a way this helper must refuse."""


def parse_utc(value: str) -> datetime:
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def load_and_validate_lock(path: Path = DEFAULT_LOCK) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("protocol_id") != FROZEN_PROTOCOL_ID:
        raise LockTamper("protocol_id")
    if payload.get("version") != 1:
        raise LockTamper("version")
    if payload.get("deployed_sha") != FROZEN_DEPLOYED_SHA:
        raise LockTamper("deployed_sha")
    if payload.get("provider") != "deepseek":
        raise LockTamper("provider")
    if payload.get("model") != "deepseek-v4-pro":
        raise LockTamper("model")
    if payload.get("window_start") != FROZEN_WINDOW_START:
        raise LockTamper("window_start")
    if payload.get("agent_id") != "sentiment-macro-bot":
        raise LockTamper("agent_id")
    if payload.get("settings_path") != "config/settings.sentiment_macro.yaml":
        raise LockTamper("settings_path")
    if payload.get("promote") is not False:
        raise LockTamper("promote")
    if payload.get("live_go") is not False:
        raise LockTamper("live_go")
    if payload.get("prohibit_live_promotion") is not True:
        raise LockTamper("prohibit_live_promotion")
    decisions = payload.get("permitted_decisions")
    if list(decisions or []) != list(PERMITTED_DECISIONS):
        raise LockTamper("permitted_decisions")
    if payload.get("degradation", {}).get("window") != 10:
        raise LockTamper("degradation.window")
    if payload.get("degradation", {}).get("no_answer_pct") != 0.5:
        raise LockTamper("degradation.no_answer_pct")
    if payload.get("review", {}).get("min_n_observations") != 140:
        raise LockTamper("min_n_observations")
    if payload.get("review", {}).get("min_eligible_trades_for_performance_decision") != 4:
        raise LockTamper("min_eligible_trades_for_performance_decision")
    return payload


def observation_in_window(event: dict[str, Any], lock: dict[str, Any]) -> bool:
    if event.get("type") != lock["observation_event_type"]:
        return False
    agent_id = event.get("agent_id") or (event.get("payload") or {}).get("agent_id")
    if agent_id not in (None, lock["agent_id"]):
        return False
    ts = parse_utc(str(event.get("ts", "")))
    return ts >= parse_utc(lock["window_start"])


def classify_observation_source(source: str, lock: dict[str, Any]) -> str:
    if source in SENTIMENT_ANSWERED_SOURCES:
        return "answered"
    if source in SENTIMENT_NO_ANSWER_SOURCES:
        return "no_answer"
    if lock.get("unknown_source_is_no_answer"):
        return "no_answer"
    return "unknown"


def trade_eligible(position: dict[str, Any], lock: dict[str, Any]) -> bool:
    if position.get("agent_id") not in (None, lock["agent_id"]):
        return False
    if position.get("status") != lock["inclusion_trades"]["status"]:
        return False
    entry = position.get("entry_time")
    exit_time = position.get("exit_time")
    if not entry or not exit_time:
        return False
    if parse_utc(str(entry)) < parse_utc(lock["window_start"]):
        return False
    return True


def rolling_degraded(sources: list[str], lock: dict[str, Any]) -> bool:
    window = int(lock["degradation"]["window"])
    if len(sources) < window:
        return False
    recent = sources[-window:]
    no_answer = sum(
        1 for source in recent if classify_observation_source(source, lock) == "no_answer"
    )
    return (no_answer / window) >= float(lock["degradation"]["no_answer_pct"])


def decide(
    *,
    lock: dict[str, Any],
    n_observations: int,
    n_eligible_trades: int,
    now: datetime,
    invariants_ok: bool,
    realized_pnl: float | None,
    profit_factor: float | None,
    emergency_safety: bool,
    provider_or_config_changed: bool,
) -> str:
    start = parse_utc(lock["window_start"])
    operational_due = now >= start + timedelta(days=int(lock["review"]["operational_horizon_days"]))
    strategy_due = now >= start + timedelta(days=int(lock["review"]["strategy_horizon_days"]))
    min_obs = int(lock["review"]["min_n_observations"])
    min_trades = int(lock["review"]["min_eligible_trades_for_performance_decision"])

    if emergency_safety or provider_or_config_changed or not invariants_ok:
        return "STOP_OPERATIONAL_FAILURE"
    if n_eligible_trades >= min_trades and realized_pnl is not None and profit_factor is not None:
        fail = payload_performance_failed(lock, realized_pnl, profit_factor)
        if fail:
            return "STOP_PERFORMANCE_FAILURE"
        if strategy_due and n_observations >= min_obs:
            return "EVIDENCE_COMPLETE"
    if (operational_due or strategy_due) and (
        n_observations < min_obs or n_eligible_trades < min_trades
    ):
        return "CONTINUE_COLLECTING"
    if strategy_due and n_observations >= min_obs and n_eligible_trades >= min_trades:
        return "EVIDENCE_COMPLETE"
    return "CONTINUE_COLLECTING"


def payload_performance_failed(
    lock: dict[str, Any], realized_pnl: float, profit_factor: float
) -> bool:
    rules = lock["performance_failure_when_denominator_met"]
    return realized_pnl <= float(rules["realized_pnl_lte"]) or profit_factor < float(
        rules["profit_factor_lt"]
    )
