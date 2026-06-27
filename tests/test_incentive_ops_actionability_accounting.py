"""Actionability default-deny + runtime caps enforcement tests.

All 17 on uncaptured fixture MUST be non-ACTIONABLE (mostly BLOCKED_NEEDS_CAPTURE).
Caps must block >1000 total, >250 per, >3 concurrent.
"""

from __future__ import annotations

from tools.incentive_ops.accounting import validate_caps
from tools.incentive_ops.actionability import check_all_actionability
from tools.incentive_ops.types import Actionability, PilotCaps


def test_all_17_non_actionable_on_starter_fixture():
    res = check_all_actionability()
    assert len(res) == 17
    for pid, r in res.items():
        assert r.status != Actionability.ACTIONABLE, f"{pid} was ACTIONABLE"
        # most should be needs capture (some may be other if we had caps etc)
        assert "BLOCKED" in str(r.status)


def test_validate_caps_blocks_total():
    caps = PilotCaps()
    ledger = [{"id": "p1", "usd": 900}]
    ck = validate_caps(ledger, {"id": "p2", "usd": 150}, caps)
    assert not ck.ok
    assert "total" in (ck.reason or "").lower()


def test_validate_caps_blocks_per_program():
    caps = PilotCaps()
    ledger = [{"id": "p1", "usd": 200}]
    ck = validate_caps(ledger, {"id": "p1", "usd": 100}, caps)
    assert not ck.ok
    assert "per-program" in (ck.reason or "").lower()


def test_validate_caps_blocks_concurrent():
    caps = PilotCaps()
    ledger = [{"id": "a", "usd": 10}, {"id": "b", "usd": 10}, {"id": "c", "usd": 10}]
    ck = validate_caps(ledger, {"id": "d", "usd": 10}, caps)
    assert not ck.ok
    assert "concurrent" in (ck.reason or "").lower()


def test_validate_caps_ok_when_under():
    caps = PilotCaps()
    ledger = [{"id": "a", "usd": 100}]
    ck = validate_caps(ledger, {"id": "b", "usd": 100}, caps)
    assert ck.ok
    assert ck.concurrent_after == 2
