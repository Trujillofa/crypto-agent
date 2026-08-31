"""Frozen DeepSeek paper-window protocol. Synthetic fixtures only; no production P&L."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
import yaml

from scripts.paper_window_protocol import (
    DEFAULT_LOCK,
    FROZEN_DEPLOYED_SHA,
    FROZEN_WINDOW_START,
    PERMITTED_DECISIONS,
    LockTamper,
    classify_observation_source,
    decide,
    load_and_validate_lock,
    observation_in_window,
    rolling_degraded,
    trade_eligible,
)

LOCK = load_and_validate_lock(DEFAULT_LOCK)
T0 = "2026-08-31T15:26:51Z"
PRE_T0 = "2026-08-31T14:28:11Z"
POST_T0 = "2026-08-31T16:00:00Z"


def _obs(ts: str, source: str = "deepseek_fallback", agent_id: str = "sentiment-macro-bot") -> dict:
    return {
        "type": "sentiment_score",
        "ts": ts,
        "agent_id": agent_id,
        "payload": {"source": source, "provider": "deepseek", "model": "deepseek-v4-pro"},
    }


def _position(
    *,
    entry: str,
    exit_time: str | None,
    status: str = "closed",
    agent_id: str = "sentiment-macro-bot",
) -> dict:
    return {
        "agent_id": agent_id,
        "status": status,
        "entry_time": entry,
        "exit_time": exit_time,
    }


def test_lock_identity_is_frozen() -> None:
    assert LOCK["protocol_id"] == "deepseek-sentiment-macro-paper-window"
    assert LOCK["version"] == 1
    assert LOCK["deployed_sha"] == FROZEN_DEPLOYED_SHA
    assert LOCK["provider"] == "deepseek"
    assert LOCK["model"] == "deepseek-v4-pro"
    assert LOCK["window_start"] == FROZEN_WINDOW_START
    assert LOCK["agent_id"] == "sentiment-macro-bot"
    assert LOCK["settings_path"] == "config/settings.sentiment_macro.yaml"
    assert LOCK["promote"] is False
    assert LOCK["live_go"] is False
    assert LOCK["prohibit_live_promotion"] is True
    assert LOCK["permitted_decisions"] == list(PERMITTED_DECISIONS)


def test_lock_denominators_match_pre_t0_cadence() -> None:
    cadence = LOCK["pre_t0_cadence"]
    assert cadence["n_observations"] == 1970
    assert cadence["obs_per_day"] == 12.541
    assert cadence["median_gap_sec"] == 3605
    assert cadence["historical_paper_positions_excluded"] == 3
    assert LOCK["review"]["operational_horizon_days"] == 14
    assert LOCK["review"]["min_n_observations"] == 140
    assert LOCK["review"]["min_eligible_trades_for_performance_decision"] == 4
    assert LOCK["degradation"]["window"] == 10
    assert LOCK["degradation"]["no_answer_pct"] == 0.5
    assert LOCK["degradation"]["blocks_new_buy"] is True


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


def test_includes_post_t0_observation() -> None:
    assert observation_in_window(_obs(POST_T0), LOCK) is True


def test_excludes_non_sentiment_event() -> None:
    event = {"type": "system_startup", "ts": POST_T0, "agent_id": "sentiment-macro-bot"}
    assert observation_in_window(event, LOCK) is False


def test_excludes_trade_entered_before_t0_even_if_closed_after() -> None:
    position = _position(entry=PRE_T0, exit_time="2026-08-31T18:00:00Z")
    assert trade_eligible(position, LOCK) is False


def test_includes_closed_trade_entered_after_t0() -> None:
    position = _position(entry=POST_T0, exit_time="2026-08-31T18:00:00Z")
    assert trade_eligible(position, LOCK) is True


def test_excludes_open_and_missing_exit() -> None:
    assert trade_eligible(_position(entry=POST_T0, exit_time=None, status="open"), LOCK) is False
    assert trade_eligible(_position(entry=POST_T0, exit_time=None, status="closed"), LOCK) is False


def test_answered_and_no_answer_taxonomy() -> None:
    assert classify_observation_source("deepseek_fallback", LOCK) == "answered"
    assert classify_observation_source("zai_live", LOCK) == "answered"
    assert classify_observation_source("error_fallback", LOCK) == "no_answer"
    assert classify_observation_source("neutral_fallback", LOCK) == "no_answer"
    assert classify_observation_source("mystery", LOCK) == "no_answer"


def test_rolling_ten_degrades_at_fifty_percent_no_answer() -> None:
    healthy = ["deepseek_fallback"] * 10
    mixed = ["deepseek_fallback"] * 5 + ["error_fallback"] * 5
    assert rolling_degraded(healthy, LOCK) is False
    assert rolling_degraded(mixed, LOCK) is True
    assert rolling_degraded(["error_fallback"] * 9, LOCK) is False


def test_decide_continue_before_denominators() -> None:
    now = datetime(2026, 9, 1, tzinfo=UTC)
    assert (
        decide(
            lock=LOCK,
            n_observations=12,
            n_eligible_trades=0,
            now=now,
            invariants_ok=True,
            realized_pnl=None,
            profit_factor=None,
            emergency_safety=False,
            provider_or_config_changed=False,
        )
        == "CONTINUE_COLLECTING"
    )


def test_decide_operational_failure_on_invariant_or_emergency() -> None:
    now = datetime(2026, 9, 1, tzinfo=UTC)
    kwargs = {
        "lock": LOCK,
        "n_observations": 200,
        "n_eligible_trades": 4,
        "now": now,
        "realized_pnl": 1.0,
        "profit_factor": 1.2,
        "provider_or_config_changed": False,
    }
    assert decide(invariants_ok=False, emergency_safety=False, **kwargs) == (
        "STOP_OPERATIONAL_FAILURE"
    )
    assert decide(invariants_ok=True, emergency_safety=True, **kwargs) == (
        "STOP_OPERATIONAL_FAILURE"
    )


def test_decide_performance_failure_only_after_min_trades() -> None:
    now = datetime(2026, 10, 1, tzinfo=UTC)
    assert (
        decide(
            lock=LOCK,
            n_observations=200,
            n_eligible_trades=4,
            now=now,
            invariants_ok=True,
            realized_pnl=-1.0,
            profit_factor=0.5,
            emergency_safety=False,
            provider_or_config_changed=False,
        )
        == "STOP_PERFORMANCE_FAILURE"
    )
    assert (
        decide(
            lock=LOCK,
            n_observations=200,
            n_eligible_trades=3,
            now=now,
            invariants_ok=True,
            realized_pnl=-1.0,
            profit_factor=0.5,
            emergency_safety=False,
            provider_or_config_changed=False,
        )
        == "CONTINUE_COLLECTING"
    )


def test_lock_refuses_live_go(tmp_path: Path) -> None:
    payload = json.loads(DEFAULT_LOCK.read_text(encoding="utf-8"))
    payload["live_go"] = True
    path = tmp_path / "lock.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(LockTamper, match="live_go"):
        load_and_validate_lock(path)


def test_lock_refuses_window_start_edit(tmp_path: Path) -> None:
    payload = json.loads(DEFAULT_LOCK.read_text(encoding="utf-8"))
    payload["window_start"] = "2026-08-31T00:00:00Z"
    path = tmp_path / "lock.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(LockTamper, match="window_start"):
        load_and_validate_lock(path)
