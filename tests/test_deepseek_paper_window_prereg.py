"""Frozen DeepSeek paper-window protocol. Synthetic fixtures only; no production P&L."""

from __future__ import annotations

import copy
import json
import math
from datetime import UTC, datetime
from pathlib import Path

import pytest
import yaml

from scripts.paper_window_protocol import (
    DEFAULT_LOCK,
    FROZEN_DECISION_POLICY_SHA256,
    FROZEN_DEPLOYED_SHA,
    FROZEN_WINDOW_START,
    PERMITTED_DECISIONS,
    LockTamper,
    assess_observation,
    classify_observation_source,
    decide,
    decision_policy_digest,
    load_and_validate_lock,
    observation_in_window,
    profit_factor_is_valid,
    rolling_degraded,
    trade_eligible,
)

LOCK = load_and_validate_lock(DEFAULT_LOCK)
POLICY = LOCK["decision_policy"]
T0 = "2026-08-31T15:26:51Z"
PRE_T0 = "2026-08-31T14:28:11Z"
POST_T0 = "2026-08-31T16:00:00Z"
HORIZON_OP = datetime(2026, 9, 15, tzinfo=UTC)
HORIZON_STRAT = datetime(2026, 10, 1, tzinfo=UTC)


def _obs(
    ts: str,
    source: str = "deepseek_fallback",
    *,
    agent_id: str | None = "sentiment-macro-bot",
    provider: str | None = "deepseek",
    model: str | None = "deepseek-v4-pro",
) -> dict:
    payload: dict[str, object] = {"source": source}
    if provider is not None:
        payload["provider"] = provider
    if model is not None:
        payload["model"] = model
    event: dict[str, object] = {"type": "sentiment_score", "ts": ts, "payload": payload}
    if agent_id is not None:
        event["agent_id"] = agent_id
    return event


def _position(
    *,
    entry: str,
    exit_time: str | None,
    status: str = "closed",
    agent_id: str | None = "sentiment-macro-bot",
    executor: str | None = None,
) -> dict:
    row: dict[str, object] = {"status": status, "entry_time": entry, "exit_time": exit_time}
    if agent_id is not None:
        row["agent_id"] = agent_id
    if executor is not None:
        row["executor"] = executor
    return row


def _write_mutated(tmp_path: Path, mutate) -> Path:
    payload = copy.deepcopy(LOCK)
    mutate(payload)
    path = tmp_path / "lock.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _healthy_decide(**overrides):
    kwargs = {
        "lock": LOCK,
        "n_observations": 140,
        "answered_n": 139,
        "no_answer_n": 1,
        "n_eligible_trades": 0,
        "now": HORIZON_OP,
        "invariants_ok": True,
        "degraded": False,
        "emergency_safety": False,
        "provider_or_config_changed": False,
        "identity_failure": False,
        "provider_mismatch": False,
        "paper_runtime_verified": True,
        "realized_pnl": None,
        "profit_factor": None,
    }
    kwargs.update(overrides)
    return decide(**kwargs)


def test_lock_identity_is_frozen() -> None:
    assert POLICY["protocol_id"] == "deepseek-sentiment-macro-paper-window"
    assert POLICY["version"] == 1
    assert POLICY["deployed_sha"] == FROZEN_DEPLOYED_SHA
    assert POLICY["provider"] == "deepseek"
    assert POLICY["model"] == "deepseek-v4-pro"
    assert POLICY["window_start"] == FROZEN_WINDOW_START
    assert POLICY["agent_id"] == "sentiment-macro-bot"
    assert POLICY["settings_path"] == "config/settings.sentiment_macro.yaml"
    assert POLICY["promote"] is False
    assert POLICY["live_go"] is False
    assert POLICY["prohibit_live_promotion"] is True
    assert POLICY["permitted_decisions"] == list(PERMITTED_DECISIONS)
    assert POLICY["performance_denominator"]["approved"] is False
    assert LOCK["decision_policy_sha256"] == FROZEN_DECISION_POLICY_SHA256
    assert decision_policy_digest(POLICY) == FROZEN_DECISION_POLICY_SHA256


def test_lock_denominators_match_pre_t0_cadence() -> None:
    cadence = LOCK["metadata"]["pre_t0_cadence"]
    assert cadence["n_observations"] == 1970
    assert cadence["obs_per_day"] == 12.541
    assert cadence["median_gap_sec"] == 3605
    assert cadence["historical_paper_positions_excluded"] == 3
    assert cadence["no_answer_n"] == 8
    assert POLICY["review"]["operational_horizon_days"] == 14
    assert POLICY["review"]["min_n_observations"] == 140
    assert POLICY["operational_health"]["min_answered_pct"] == 99.1187234014
    assert POLICY["operational_health"]["interval"] == "clopper_pearson_one_sided_lower"
    assert POLICY["operational_health"]["pre_t0_answered_n"] == 1962
    assert POLICY["operational_health"]["pre_t0_n"] == 1970
    assert "min_eligible_trades_for_performance_decision" not in POLICY["review"]
    assert POLICY["degradation"]["window"] == 10
    assert POLICY["degradation"]["no_answer_pct"] == 0.5


def test_config_still_matches_locked_provider() -> None:
    overlay = yaml.safe_load(Path("config/settings.sentiment_macro.yaml").read_text())
    assert overlay["mode"] == "paper"
    assert overlay["ai"]["provider"] == "deepseek"
    assert overlay["ai"]["fallback_model"] == "deepseek-v4-pro"
    assert overlay["trading_execution"]["enabled"] is False
    assert overlay["trading_execution"]["test_mode"] is True
    text = Path("config/settings.sentiment_macro.yaml").read_text()
    assert "zai_base_url:" not in text
    assert "api/coding/paas/v4" not in text


def test_excludes_pre_t0_observation() -> None:
    assert observation_in_window(_obs(PRE_T0), LOCK) is False


def test_includes_post_t0_deepseek_observation() -> None:
    status = assess_observation(_obs(POST_T0), LOCK)
    assert status.in_window is True
    assert status.valid_deepseek_answer is True
    assert status.identity_failure is False
    assert status.provider_mismatch is False


def test_excludes_non_sentiment_event() -> None:
    event = {"type": "system_startup", "ts": POST_T0, "agent_id": "sentiment-macro-bot"}
    assert observation_in_window(event, LOCK) is False


def test_missing_agent_id_fails_closed() -> None:
    status = assess_observation(_obs(POST_T0, agent_id=None), LOCK)
    assert status.in_window is False
    assert status.valid_deepseek_answer is False
    assert status.identity_failure is True


def test_wrong_agent_fails_closed() -> None:
    status = assess_observation(_obs(POST_T0, agent_id="other-bot"), LOCK)
    assert status.in_window is False
    assert status.identity_failure is True
    assert status.valid_deepseek_answer is False


def test_missing_provider_or_model_fails_closed() -> None:
    missing_provider = assess_observation(_obs(POST_T0, provider=None), LOCK)
    missing_model = assess_observation(_obs(POST_T0, model=None), LOCK)
    assert missing_provider.identity_failure is True
    assert missing_model.identity_failure is True
    assert missing_provider.valid_deepseek_answer is False
    assert missing_model.valid_deepseek_answer is False


def test_zai_and_xai_are_not_valid_deepseek_answers() -> None:
    zai = assess_observation(_obs(POST_T0, "zai_live", provider="zai", model="glm-5.3"), LOCK)
    xai = assess_observation(
        _obs(POST_T0, "xai_live", provider="xai", model="grok-4-1-fast-reasoning"), LOCK
    )
    assert zai.in_window is True
    assert xai.in_window is True
    assert zai.valid_deepseek_answer is False
    assert xai.valid_deepseek_answer is False
    assert zai.provider_mismatch is True
    assert xai.provider_mismatch is True


def test_taxonomy_does_not_import_runtime_module() -> None:
    assert classify_observation_source("deepseek_fallback", LOCK) == "answered"
    assert classify_observation_source("error_fallback", LOCK) == "no_answer"
    assert classify_observation_source("mystery", LOCK) == "no_answer"
    source = Path("scripts/paper_window_protocol.py").read_text()
    assert "import src.sentiment_sources" not in source
    assert "from src.sentiment_sources" not in source


def test_excludes_trade_entered_before_t0_even_if_closed_after() -> None:
    position = _position(entry=PRE_T0, exit_time="2026-08-31T18:00:00Z")
    assert trade_eligible(position, LOCK, paper_runtime_verified=True) is False


def test_valid_post_t0_paper_trade() -> None:
    position = _position(entry=POST_T0, exit_time="2026-08-31T18:00:00Z", executor="paper")
    assert trade_eligible(position, LOCK, paper_runtime_verified=True) is True


def test_missing_or_wrong_agent_trade_ineligible() -> None:
    missing = _position(entry=POST_T0, exit_time="2026-08-31T18:00:00Z", agent_id=None)
    wrong = _position(entry=POST_T0, exit_time="2026-08-31T18:00:00Z", agent_id="other")
    assert trade_eligible(missing, LOCK, paper_runtime_verified=True) is False
    assert trade_eligible(wrong, LOCK, paper_runtime_verified=True) is False


def test_binance_executor_marker_ineligible() -> None:
    position = _position(entry=POST_T0, exit_time="2026-08-31T18:00:00Z", executor="binance")
    assert trade_eligible(position, LOCK, paper_runtime_verified=True) is False


def test_missing_paper_provenance_ineligible() -> None:
    position = _position(entry=POST_T0, exit_time="2026-08-31T18:00:00Z")
    assert trade_eligible(position, LOCK, paper_runtime_verified=False) is False


def test_open_trade_ineligible() -> None:
    assert (
        trade_eligible(
            _position(entry=POST_T0, exit_time=None, status="open"),
            LOCK,
            paper_runtime_verified=True,
        )
        is False
    )


def test_rolling_ten_degrades_at_fifty_percent_no_answer() -> None:
    healthy = ["deepseek_fallback"] * 10
    mixed = ["deepseek_fallback"] * 5 + ["error_fallback"] * 5
    assert rolling_degraded(healthy, LOCK) is False
    assert rolling_degraded(mixed, LOCK) is True


def test_zero_loss_profit_factor_is_undefined() -> None:
    assert profit_factor_is_valid(math.inf, gross_loss=0.0) is False
    assert profit_factor_is_valid(None, gross_loss=0.0) is False
    assert profit_factor_is_valid(2.0, gross_loss=0.0) is False
    assert profit_factor_is_valid(2.0, gross_loss=10.0) is True


def test_decide_none_metrics_never_evidence_complete() -> None:
    assert _healthy_decide(now=HORIZON_STRAT, realized_pnl=None, profit_factor=None) != (
        "EVIDENCE_COMPLETE"
    )
    assert _healthy_decide(now=HORIZON_STRAT, realized_pnl=None, profit_factor=None) == (
        "INSUFFICIENT_EVIDENCE"
    )


def test_decide_nan_pnl_never_evidence_complete() -> None:
    assert _healthy_decide(now=HORIZON_STRAT, realized_pnl=math.nan, profit_factor=1.2) != (
        "EVIDENCE_COMPLETE"
    )


def test_decide_nan_pf_never_evidence_complete() -> None:
    assert _healthy_decide(now=HORIZON_STRAT, realized_pnl=1.0, profit_factor=math.nan) != (
        "EVIDENCE_COMPLETE"
    )


def test_decide_zero_loss_pf_never_evidence_complete() -> None:
    assert (
        _healthy_decide(
            now=HORIZON_STRAT,
            realized_pnl=10.0,
            profit_factor=math.inf,
            gross_loss=0.0,
            concentration_ok=True,
            n_eligible_trades=10,
        )
        != "EVIDENCE_COMPLETE"
    )


def test_decide_all_no_answer_is_operational_failure() -> None:
    assert (
        _healthy_decide(n_observations=140, answered_n=0, no_answer_n=140)
        == "STOP_OPERATIONAL_FAILURE"
    )


def test_decide_half_no_answer_is_not_operational_health() -> None:
    """70/70 is the old 50% floor and must not certify provider health."""
    assert (
        _healthy_decide(n_observations=140, answered_n=70, no_answer_n=70, degraded=False)
        == "STOP_OPERATIONAL_FAILURE"
    )


def test_decide_rejects_negative_counters() -> None:
    assert (
        _healthy_decide(n_observations=140, answered_n=141, no_answer_n=-1)
        == "STOP_OPERATIONAL_FAILURE"
    )


def test_decide_rejects_fractional_counters() -> None:
    assert (
        _healthy_decide(n_observations=140, answered_n=70.9, no_answer_n=70.1)
        == "STOP_OPERATIONAL_FAILURE"
    )


def test_decide_pre_t0_bound_rejects_138_of_140() -> None:
    assert (
        _healthy_decide(n_observations=140, answered_n=138, no_answer_n=2)
        == "STOP_OPERATIONAL_FAILURE"
    )


def test_decide_active_degradation_at_horizon_is_operational_failure() -> None:
    assert _healthy_decide(degraded=True) == "STOP_OPERATIONAL_FAILURE"


def test_decide_missing_operational_aggregates_at_horizon() -> None:
    assert _healthy_decide(answered_n=None, no_answer_n=None) == "STOP_OPERATIONAL_FAILURE"


def test_decide_valid_healthy_operational_completion() -> None:
    assert _healthy_decide() == "OPERATIONAL_EVIDENCE_COMPLETE"


def test_decide_strategy_horizon_without_approved_denominator() -> None:
    assert _healthy_decide(now=HORIZON_STRAT) == "INSUFFICIENT_EVIDENCE"


def test_decide_never_stop_performance_without_approved_denominator() -> None:
    assert (
        _healthy_decide(
            now=HORIZON_STRAT,
            n_eligible_trades=4,
            realized_pnl=-1.0,
            profit_factor=0.5,
            concentration_ok=True,
        )
        == "INSUFFICIENT_EVIDENCE"
    )
    assert (
        _healthy_decide(
            now=HORIZON_STRAT,
            n_eligible_trades=4,
            realized_pnl=-1.0,
            profit_factor=0.5,
            concentration_ok=True,
        )
        != "STOP_PERFORMANCE_FAILURE"
    )


def test_decide_continue_before_denominators() -> None:
    assert _healthy_decide(now=datetime(2026, 9, 1, tzinfo=UTC), n_observations=12) == (
        "CONTINUE_COLLECTING"
    )


def test_decide_operational_failure_on_invariant_or_emergency() -> None:
    assert _healthy_decide(invariants_ok=False) == "STOP_OPERATIONAL_FAILURE"
    assert _healthy_decide(emergency_safety=True) == "STOP_OPERATIONAL_FAILURE"
    assert _healthy_decide(provider_mismatch=True) == "STOP_OPERATIONAL_FAILURE"
    assert _healthy_decide(paper_runtime_verified=False) == "STOP_OPERATIONAL_FAILURE"


@pytest.mark.parametrize(
    "mutator",
    [
        lambda p: p["decision_policy"].__setitem__(
            "review", {**p["decision_policy"]["review"], "strategy_horizon_days": 90}
        ),
        lambda p: p["decision_policy"].__setitem__(
            "review", {**p["decision_policy"]["review"], "operational_horizon_days": 7}
        ),
        lambda p: p["decision_policy"].__setitem__(
            "performance_failure_when_denominator_met",
            {
                **p["decision_policy"]["performance_failure_when_denominator_met"],
                "profit_factor_lt": 1.5,
            },
        ),
        lambda p: p["decision_policy"].__setitem__(
            "performance_failure_when_denominator_met",
            {
                **p["decision_policy"]["performance_failure_when_denominator_met"],
                "realized_pnl_lte": -10,
            },
        ),
        lambda p: p["decision_policy"].__setitem__("answered_sources", ["deepseek_fallback"]),
        lambda p: p["decision_policy"].__setitem__("unknown_source_is_no_answer", False),
        lambda p: p["decision_policy"].__setitem__(
            "inclusion_trades",
            {**p["decision_policy"]["inclusion_trades"], "paper_runtime_verified_required": False},
        ),
        lambda p: p["decision_policy"]["exclusions"].append("new-rule"),
        lambda p: p["decision_policy"].__setitem__(
            "safety_invariants",
            {**p["decision_policy"]["safety_invariants"], "mode": "live"},
        ),
        lambda p: p["decision_policy"].__setitem__("permitted_decisions", ["CONTINUE_COLLECTING"]),
        lambda p: p["decision_policy"].__setitem__("extra_decision_field", True),
        lambda p: p["decision_policy"].__setitem__(
            "operational_health",
            {**p["decision_policy"]["operational_health"], "min_answered_pct": 50.0},
        ),
    ],
)
def test_decision_mutations_raise_lock_tamper(tmp_path: Path, mutator) -> None:
    path = _write_mutated(tmp_path, mutator)
    with pytest.raises(LockTamper):
        load_and_validate_lock(path)
