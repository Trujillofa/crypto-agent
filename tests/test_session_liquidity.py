from __future__ import annotations

from datetime import datetime

from src.strategy.session_liquidity import (
    SessionLiquidityRouterConfig,
    apply_session_liquidity_gate,
    is_entry_allowed,
    parse_session_liquidity_router,
    session_for_hour,
    utc_hour_from_time,
)
from src.strategy.signals import Signal, SignalType


def test_session_for_hour_disjoint() -> None:
    assert session_for_hour(15) == "europe"
    assert session_for_hour(16) == "americas"
    assert session_for_hour(23) == "americas"
    assert session_for_hour(0) == "asia"


def test_is_entry_allowed_americas_only() -> None:
    assert is_entry_allowed(16, ("americas",))
    assert is_entry_allowed(23, ("americas",))
    assert not is_entry_allowed(15, ("americas",))
    assert not is_entry_allowed(8, ("americas",))


def test_apply_gate_blocks_hour_15() -> None:
    router = SessionLiquidityRouterConfig(enabled=True, allowed_windows=("americas",))
    buy = Signal(SignalType.BUY, "BTCUSDT", 100.0, 0.9, "buy", {})
    bar_time = datetime(2024, 6, 1, 15, 0, 0)
    gated, blocked = apply_session_liquidity_gate(buy, bar_time, router)
    assert blocked
    assert gated.type == SignalType.HOLD
    assert "Session Liquidity Router" in gated.reason


def test_apply_gate_allows_hour_16_and_23() -> None:
    router = SessionLiquidityRouterConfig(enabled=True, allowed_windows=("americas",))
    buy = Signal(SignalType.BUY, "BTCUSDT", 100.0, 0.9, "buy", {})
    for hour in (16, 23):
        gated, blocked = apply_session_liquidity_gate(buy, datetime(2024, 6, 1, hour, 0, 0), router)
        assert not blocked
        assert gated.type == SignalType.BUY


def test_disabled_router_allows_buy() -> None:
    router = SessionLiquidityRouterConfig(enabled=False)
    buy = Signal(SignalType.BUY, "BTCUSDT", 100.0, 0.9, "buy", {})
    gated, blocked = apply_session_liquidity_gate(buy, datetime(2024, 6, 1, 3, 0, 0), router)
    assert not blocked
    assert gated.type == SignalType.BUY


def test_sell_passes_unchanged() -> None:
    router = SessionLiquidityRouterConfig(enabled=True, allowed_windows=("americas",))
    sell = Signal(SignalType.SELL, "BTCUSDT", 100.0, 0.9, "sell", {})
    gated, blocked = apply_session_liquidity_gate(sell, datetime(2024, 6, 1, 3, 0, 0), router)
    assert not blocked
    assert gated.type == SignalType.SELL


def test_parse_session_liquidity_router_defaults_off() -> None:
    cfg = parse_session_liquidity_router(None)
    assert cfg.enabled is False
    assert cfg.allowed_windows == ("americas",)


def test_utc_hour_from_iso_string() -> None:
    assert utc_hour_from_time("2024-06-01T16:00:00+00:00") == 16
