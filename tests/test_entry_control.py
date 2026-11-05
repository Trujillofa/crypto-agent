"""The brake's whole value is its behaviour when the data is missing or wrong."""

from __future__ import annotations

import pytest

from src.risk.entry_control import EntryControl


class Clock:
    def __init__(self) -> None:
        self.now = 1_000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def rows(*items: tuple[str, object, object, str]) -> list[dict[str, object]]:
    return [
        {"agent_id": agent, "armed": armed, "reduce_only": ro, "reason": reason}
        for agent, armed, ro, reason in items
    ]


def control(fetch, clock: Clock | None = None, **kwargs) -> EntryControl:
    return EntryControl(clock=clock or Clock(), fetch_rows=fetch, **kwargs)


async def test_an_armed_row_allows_entries() -> None:
    async def fetch(_ids):
        return rows(("all", True, False, "armed"))

    allowed, detail = await control(fetch).entries_allowed()
    assert allowed is True
    assert "allowed" in detail


async def test_a_disarmed_row_blocks_and_says_why() -> None:
    async def fetch(_ids):
        return rows(("all", False, False, "drawdown 9.1% > 8.0%"))

    allowed, detail = await control(fetch).entries_allowed()
    assert allowed is False
    assert "drawdown 9.1%" in detail


async def test_reduce_only_blocks_entries() -> None:
    async def fetch(_ids):
        return rows(("all", True, True, "reduce only"))

    allowed, _ = await control(fetch).entries_allowed()
    assert allowed is False


async def test_a_missing_row_blocks() -> None:
    async def fetch(_ids):
        return []

    state = await control(fetch).state()
    assert state.entries_allowed is False
    assert "no control row" in state.reason
    assert state.source == "db"


async def test_a_null_flag_never_reads_as_armed() -> None:
    async def fetch(_ids):
        return rows(("all", None, False, "bad row"))

    state = await control(fetch).state()
    assert state.entries_allowed is False


async def test_the_global_row_can_veto_a_per_agent_allow() -> None:
    async def fetch(_ids):
        return rows(("agent2", True, False, "agent ok"), ("all", False, False, "global stop"))

    state = await control(fetch, agent_id="agent2").state()
    assert state.entries_allowed is False
    assert state.reason == "global stop"


async def test_an_unreadable_control_with_no_history_fails_closed() -> None:
    async def fetch(_ids):
        raise RuntimeError("connection refused")

    state = await control(fetch).state()
    assert state.entries_allowed is False
    assert state.source == "fail_closed"
    assert "RuntimeError" in state.reason


async def test_a_short_outage_keeps_the_last_good_answer() -> None:
    clock = Clock()
    calls = {"n": 0}

    async def fetch(_ids):
        calls["n"] += 1
        if calls["n"] == 1:
            return rows(("all", True, False, "armed"))
        raise RuntimeError("blip")

    brake = control(fetch, clock=clock, ttl_s=30.0, max_stale_s=120.0)
    assert (await brake.state()).entries_allowed is True

    clock.advance(60.0)  # cached value expired, but the read is still recent
    state = await brake.state()
    assert state.entries_allowed is True
    assert state.source == "cache"


async def test_a_long_outage_eventually_blocks() -> None:
    clock = Clock()
    calls = {"n": 0}

    async def fetch(_ids):
        calls["n"] += 1
        if calls["n"] == 1:
            return rows(("all", True, False, "armed"))
        raise RuntimeError("gone")

    brake = control(fetch, clock=clock, ttl_s=30.0, max_stale_s=120.0)
    await brake.state()

    clock.advance(130.0)
    state = await brake.state()
    assert state.entries_allowed is False
    assert state.source == "fail_closed"


async def test_the_ttl_avoids_a_query_per_order() -> None:
    clock = Clock()
    calls = {"n": 0}

    async def fetch(_ids):
        calls["n"] += 1
        return rows(("all", True, False, "armed"))

    brake = control(fetch, clock=clock, ttl_s=30.0)
    await brake.state()
    clock.advance(10.0)
    await brake.state()
    assert calls["n"] == 1

    clock.advance(25.0)
    await brake.state()
    assert calls["n"] == 2


async def test_success_age_tracks_the_last_good_read() -> None:
    clock = Clock()

    async def fetch(_ids):
        return rows(("all", True, False, "armed"))

    brake = control(fetch, clock=clock, ttl_s=1.0)
    assert brake.last_success_age_s is None
    await brake.state()
    clock.advance(42.0)
    assert brake.last_success_age_s == pytest.approx(42.0)


async def test_both_ids_are_requested_so_a_global_row_is_never_missed() -> None:
    seen: list[list[str]] = []

    async def fetch(ids):
        seen.append(list(ids))
        return rows(("all", True, False, "armed"))

    await control(fetch, agent_id="agent2").state()
    assert seen == [["agent2", "all"]]
