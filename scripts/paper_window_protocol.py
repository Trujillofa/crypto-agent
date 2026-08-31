"""Eligibility helpers for frozen paper evidence windows.

Read-only. Does not query production, place orders, or call providers.
Taxonomy comes only from lock.json, never from the runtime sentiment module.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_LOCK = REPO_ROOT / "research/paper_windows/deepseek-sentiment-macro-v1/lock.json"
FROZEN_PROTOCOL_ID = "deepseek-sentiment-macro-paper-window"
FROZEN_DEPLOYED_SHA = "bc6ea9e1c62b36c82d27b96f2fb2c28d99f2f316"
FROZEN_WINDOW_START = "2026-08-31T15:26:51Z"
FROZEN_DECISION_POLICY_SHA256 = "bc64e6861d8e2b52e8e74ad23f19e2487c80f5812d18e36a119410bce5eb7589"
TOP_LEVEL_KEYS = frozenset({"decision_policy", "decision_policy_sha256", "metadata"})
METADATA_KEYS = frozenset(
    {
        "locked_at",
        "locked_before_post_t0_performance_review",
        "event_log",
        "pre_t0_cadence",
        "denominator_notes",
    }
)
DECISION_POLICY_KEYS = frozenset(
    {
        "protocol_id",
        "version",
        "deployed_sha",
        "provider",
        "model",
        "window_start",
        "agent_id",
        "service",
        "settings_path",
        "observation_event_type",
        "trade_close_event_type",
        "review",
        "operational_health",
        "degradation",
        "answered_sources",
        "no_answer_sources",
        "valid_deepseek_sources",
        "unknown_source_is_no_answer",
        "require_exact_agent_id",
        "require_deepseek_provider_model",
        "exclusions",
        "inclusion_observations",
        "inclusion_trades",
        "operational_metrics",
        "strategy_metrics",
        "safety_invariants",
        "interruptions",
        "permitted_decisions",
        "performance_denominator",
        "performance_failure_when_denominator_met",
        "promote",
        "live_go",
        "prohibit_live_promotion",
        "not_in_scope",
    }
)
PERMITTED_DECISIONS = (
    "CONTINUE_COLLECTING",
    "STOP_OPERATIONAL_FAILURE",
    "STOP_PERFORMANCE_FAILURE",
    "INSUFFICIENT_EVIDENCE",
    "OPERATIONAL_EVIDENCE_COMPLETE",
    "EVIDENCE_COMPLETE",
)
LIVE_EXECUTOR_MARKERS = frozenset({"binance", "live", "futures_live", "spot_live"})


class LockTamper(ValueError):
    """Lock was edited after freeze in a way this helper must refuse."""


@dataclass(frozen=True)
class ObservationStatus:
    in_window: bool
    source_class: str
    valid_deepseek_answer: bool
    identity_failure: bool
    provider_mismatch: bool


def parse_utc(value: str) -> datetime:
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def canonical_dumps(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def decision_policy_digest(policy: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_dumps(policy).encode("utf-8")).hexdigest()


def _require_exact_keys(payload: dict[str, Any], expected: frozenset[str], label: str) -> None:
    actual = frozenset(payload)
    missing = expected - actual
    extra = actual - expected
    if missing or extra:
        raise LockTamper(f"{label}: missing={sorted(missing)} extra={sorted(extra)}")


def _policy(lock: dict[str, Any]) -> dict[str, Any]:
    return lock["decision_policy"]


def load_and_validate_lock(path: Path = DEFAULT_LOCK) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise LockTamper("root")
    _require_exact_keys(payload, TOP_LEVEL_KEYS, "root")
    policy = payload["decision_policy"]
    metadata = payload["metadata"]
    if not isinstance(policy, dict) or not isinstance(metadata, dict):
        raise LockTamper("root_types")
    _require_exact_keys(policy, DECISION_POLICY_KEYS, "decision_policy")
    _require_exact_keys(metadata, METADATA_KEYS, "metadata")
    digest = decision_policy_digest(policy)
    if digest != FROZEN_DECISION_POLICY_SHA256:
        raise LockTamper("frozen_digest")
    if payload.get("decision_policy_sha256") != digest:
        raise LockTamper("decision_policy_sha256")
    if policy.get("protocol_id") != FROZEN_PROTOCOL_ID:
        raise LockTamper("protocol_id")
    if policy.get("version") != 1:
        raise LockTamper("version")
    if policy.get("deployed_sha") != FROZEN_DEPLOYED_SHA:
        raise LockTamper("deployed_sha")
    if policy.get("provider") != "deepseek":
        raise LockTamper("provider")
    if policy.get("model") != "deepseek-v4-pro":
        raise LockTamper("model")
    if policy.get("window_start") != FROZEN_WINDOW_START:
        raise LockTamper("window_start")
    if policy.get("promote") is not False or policy.get("live_go") is not False:
        raise LockTamper("live_promotion")
    if policy.get("prohibit_live_promotion") is not True:
        raise LockTamper("prohibit_live_promotion")
    if list(policy.get("permitted_decisions") or []) != list(PERMITTED_DECISIONS):
        raise LockTamper("permitted_decisions")
    denom = policy.get("performance_denominator") or {}
    if denom.get("approved") is not False:
        raise LockTamper("performance_denominator.approved")
    return payload


def is_finite_number(value: object) -> bool:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return False
    return math.isfinite(float(value))


def profit_factor_is_valid(profit_factor: object, *, gross_loss: object | None = None) -> bool:
    """Zero-loss PF is undefined and never a pass; inf/NaN/None are invalid."""
    if gross_loss is not None:
        if not is_finite_number(gross_loss):
            return False
        if float(gross_loss) == 0.0:
            return False
    if not is_finite_number(profit_factor):
        return False
    return float(profit_factor) > 0.0


def classify_observation_source(source: str, lock: dict[str, Any]) -> str:
    policy = _policy(lock)
    answered = set(policy["answered_sources"])
    no_answer = set(policy["no_answer_sources"])
    if source in answered:
        return "answered"
    if source in no_answer:
        return "no_answer"
    if policy.get("unknown_source_is_no_answer"):
        return "no_answer"
    return "unknown"


def assess_observation(event: dict[str, Any], lock: dict[str, Any]) -> ObservationStatus:
    policy = _policy(lock)
    identity_failure = False
    provider_mismatch = False
    valid_deepseek = False
    in_window = False
    source_class = "no_answer"
    if event.get("type") != policy["observation_event_type"]:
        return ObservationStatus(False, source_class, False, False, False)
    try:
        ts = parse_utc(str(event.get("ts", "")))
    except (TypeError, ValueError):
        return ObservationStatus(False, source_class, False, True, False)
    if ts < parse_utc(policy["window_start"]):
        return ObservationStatus(False, source_class, False, False, False)

    payload = event.get("payload") or {}
    agent_id = event.get("agent_id")
    if agent_id is None:
        agent_id = payload.get("agent_id")
    provider = payload.get("provider")
    model = payload.get("model")
    source = str(payload.get("source") or event.get("source") or "")
    source_class = classify_observation_source(source, lock)

    if not agent_id or not provider or not model:
        identity_failure = True
        return ObservationStatus(False, source_class, False, True, False)
    if agent_id != policy["agent_id"]:
        return ObservationStatus(False, source_class, False, True, False)

    in_window = True
    if provider != policy["provider"] or model != policy["model"]:
        provider_mismatch = True
    if source not in set(policy["valid_deepseek_sources"]):
        if source_class == "answered":
            provider_mismatch = True
    valid_deepseek = (
        not identity_failure
        and not provider_mismatch
        and source in set(policy["valid_deepseek_sources"])
        and provider == policy["provider"]
        and model == policy["model"]
    )
    return ObservationStatus(
        in_window, source_class, valid_deepseek, identity_failure, provider_mismatch
    )


def observation_in_window(event: dict[str, Any], lock: dict[str, Any]) -> bool:
    return assess_observation(event, lock).in_window


def trade_eligible(
    position: dict[str, Any],
    lock: dict[str, Any],
    *,
    paper_runtime_verified: bool,
) -> bool:
    """Fail closed unless PaperExecutor provenance is proven by the caller.

    The positions table has no executor column. ``paper_runtime_verified`` must
    come from overlapping ``system_startup`` / startup-diagnostics evidence that
    this agent was on PaperExecutor covering ``entry_time``. Unproven rows are
    ineligible; this helper does not query production.
    """
    policy = _policy(lock)
    agent_id = position.get("agent_id")
    if agent_id != policy["agent_id"]:
        return False
    if position.get("status") != policy["inclusion_trades"]["status"]:
        return False
    entry = position.get("entry_time")
    exit_time = position.get("exit_time")
    if not entry or not exit_time:
        return False
    try:
        if parse_utc(str(entry)) < parse_utc(policy["window_start"]):
            return False
    except (TypeError, ValueError):
        return False
    executor = str(position.get("executor") or "").strip().lower()
    if executor in LIVE_EXECUTOR_MARKERS:
        return False
    if policy["inclusion_trades"].get("paper_runtime_verified_required") and (
        paper_runtime_verified is not True
    ):
        return False
    return True


def rolling_degraded(sources: list[str], lock: dict[str, Any]) -> bool:
    policy = _policy(lock)
    window = int(policy["degradation"]["window"])
    if len(sources) < window:
        return False
    recent = sources[-window:]
    no_answer = sum(
        1 for source in recent if classify_observation_source(source, lock) == "no_answer"
    )
    return (no_answer / window) >= float(policy["degradation"]["no_answer_pct"])


def _answered_pct(answered_n: object, n_observations: object) -> float | None:
    if not is_finite_number(answered_n) or not is_finite_number(n_observations):
        return None
    n_obs = float(n_observations)
    if n_obs <= 0:
        return None
    return 100.0 * float(answered_n) / n_obs


def decide(
    *,
    lock: dict[str, Any],
    n_observations: int,
    answered_n: int | None,
    no_answer_n: int | None,
    n_eligible_trades: int,
    now: datetime,
    invariants_ok: bool,
    degraded: bool,
    emergency_safety: bool,
    provider_or_config_changed: bool,
    identity_failure: bool,
    provider_mismatch: bool,
    paper_runtime_verified: bool,
    realized_pnl: float | None,
    profit_factor: float | None,
    gross_loss: float | None = None,
    concentration_ok: bool | None = None,
) -> str:
    policy = _policy(lock)
    start = parse_utc(policy["window_start"])
    operational_due = now >= start + timedelta(
        days=int(policy["review"]["operational_horizon_days"])
    )
    strategy_due = now >= start + timedelta(days=int(policy["review"]["strategy_horizon_days"]))
    min_obs = int(policy["review"]["min_n_observations"])
    min_answered_pct = float(policy["operational_health"]["min_answered_pct"])
    denom_approved = bool(policy["performance_denominator"]["approved"])

    if (
        emergency_safety
        or provider_or_config_changed
        or provider_mismatch
        or identity_failure
        or not invariants_ok
        or paper_runtime_verified is not True
    ):
        return "STOP_OPERATIONAL_FAILURE"

    operational_counts_ok = (
        is_finite_number(n_observations)
        and is_finite_number(answered_n)
        and is_finite_number(no_answer_n)
        and int(n_observations) == int(answered_n or 0) + int(no_answer_n or 0)
        and int(n_observations) >= 0
    )
    answered_pct = _answered_pct(answered_n, n_observations) if operational_counts_ok else None

    if operational_counts_ok and int(n_observations) >= min_obs:
        if answered_pct is None or answered_pct < min_answered_pct:
            return "STOP_OPERATIONAL_FAILURE"
    if operational_due and degraded:
        return "STOP_OPERATIONAL_FAILURE"
    if operational_due and not operational_counts_ok:
        return "STOP_OPERATIONAL_FAILURE"

    operational_pass = (
        operational_due
        and operational_counts_ok
        and int(n_observations) >= min_obs
        and answered_pct is not None
        and answered_pct >= min_answered_pct
        and not degraded
    )
    if not operational_pass:
        return "CONTINUE_COLLECTING"

    if not strategy_due:
        return "OPERATIONAL_EVIDENCE_COMPLETE"
    if not denom_approved:
        return "INSUFFICIENT_EVIDENCE"

    pf_ok = profit_factor_is_valid(profit_factor, gross_loss=gross_loss)
    pnl_ok = is_finite_number(realized_pnl)
    if not pf_ok or not pnl_ok or concentration_ok is not True:
        return "INSUFFICIENT_EVIDENCE"

    rules = policy["performance_failure_when_denominator_met"]
    if float(realized_pnl) <= float(rules["realized_pnl_lte"]) or float(profit_factor) < float(
        rules["profit_factor_lt"]
    ):
        return "STOP_PERFORMANCE_FAILURE"
    return "EVIDENCE_COMPLETE"
