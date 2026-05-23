"""Tests for read-only futures close audit helpers."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from scripts.audit_futures_closes import (
    DbCloseRow,
    audit_close,
    find_matching_trade,
    normalize_symbol,
)
from src.execution.futures_client import FuturesUserTrade


def _close(**overrides: object) -> DbCloseRow:
    values = {
        "position_id": 221,
        "symbol": "sentiment-macro-bot::BTCUSDT",
        "quantity": 0.001,
        "entry_price": 75_718.0,
        "exit_price": 75_208.78343328,
        "realized_pnl": -0.5695872800933145,
        "exit_time": datetime(2026, 5, 23, 7, 46, 45, tzinfo=UTC),
        "order_id": None,
    }
    values.update(overrides)
    return DbCloseRow(**values)


def _trade(**overrides: object) -> FuturesUserTrade:
    values = {
        "trade_id": "7682581464",
        "order_id": "1019304540420",
        "symbol": "BTCUSDT",
        "side": "SELL",
        "price": 74_949.1,
        "quantity": 0.001,
        "realized_pnl": -0.7689,
        "commission": 0.03747455,
        "commission_asset": "USDT",
        "time": int(datetime(2026, 5, 23, 7, 46, 40, tzinfo=UTC).timestamp() * 1000),
    }
    values.update(overrides)
    return FuturesUserTrade(**values)


def test_normalize_symbol_removes_agent_scope() -> None:
    assert normalize_symbol("sentiment-macro-bot::BTCUSDT") == "BTCUSDT"
    assert normalize_symbol("ETHUSDT") == "ETHUSDT"


def test_find_matching_trade_prefers_order_id() -> None:
    close = _close(order_id="target")
    old_trade = _trade(
        order_id="old", time=int(datetime(2026, 5, 23, 7, 46, 44, tzinfo=UTC).timestamp() * 1000)
    )
    target_trade = _trade(
        order_id="target", time=int(datetime(2026, 5, 22, 7, 46, 44, tzinfo=UTC).timestamp() * 1000)
    )

    result = find_matching_trade(close, [old_trade, target_trade], timedelta(minutes=10))

    assert result == target_trade


def test_find_matching_trade_matches_nearest_close_time() -> None:
    close = _close()
    far_trade = _trade(
        order_id="far", time=int(datetime(2026, 5, 23, 6, 0, tzinfo=UTC).timestamp() * 1000)
    )
    near_trade = _trade(
        order_id="near", time=int(datetime(2026, 5, 23, 7, 46, 40, tzinfo=UTC).timestamp() * 1000)
    )

    result = find_matching_trade(close, [far_trade, near_trade], timedelta(minutes=10))

    assert result == near_trade


def test_audit_close_reports_drift_for_historical_estimated_close() -> None:
    result = audit_close(
        _close(),
        [_trade()],
        timedelta(minutes=10),
        price_tolerance=0.01,
        pnl_tolerance=0.01,
    )

    assert result.status == "drift"
    assert result.price_diff == pytest.approx(259.68343328)
    assert result.pnl_diff == pytest.approx(0.1993127199066855)


def test_audit_close_reports_ok_when_db_matches_binance() -> None:
    result = audit_close(
        _close(exit_price=74_949.1, realized_pnl=-0.7689, order_id="1019304540420"),
        [_trade()],
        timedelta(minutes=10),
        price_tolerance=0.01,
        pnl_tolerance=0.01,
    )

    assert result.status == "ok"
    assert result.price_diff == pytest.approx(0.0)
    assert result.pnl_diff == pytest.approx(0.0)


def test_audit_close_reports_missing_match() -> None:
    result = audit_close(
        _close(),
        [_trade(symbol="ETHUSDT")],
        timedelta(minutes=10),
        price_tolerance=0.01,
        pnl_tolerance=0.01,
    )

    assert result.status == "missing_binance_match"
    assert result.binance_trade is None
