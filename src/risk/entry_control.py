"""Durable entry brake, read from ``agent_control`` before every entry order.

This is the enforcement twin of the strategy lifecycle check: same database, same
idea — a decision that outlives the process — but with the opposite failure
behaviour. Three rules make it a brake instead of a dashboard.

1. **Unreadable means disarmed.** A missing table, a missing row, a NULL flag or
   an unreachable database all resolve to "no new entries". Nothing here treats
   an unknown answer as permission.
2. **Exits are never gated.** A brake that traps a losing position turns a
   risk-off event into a bigger risk. Only entries consult this module.
3. **The cache has a hard expiry.** A short TTL keeps the hot path cheap; past
   ``max_stale_s`` the cached value is discarded, so a database outage stops new
   entries within that window instead of leaving the last known answer in force
   forever.
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from src.utils.logger import get_logger

GLOBAL_AGENT_ID = "all"
DEFAULT_TTL_S = 30.0
DEFAULT_MAX_STALE_S = 120.0

FetchRows = Callable[[Sequence[str]], Awaitable[Sequence[Mapping[str, Any]]]]


@dataclass(frozen=True)
class EntryControlState:
    """The resolved brake position for one agent."""

    armed: bool
    reduce_only: bool
    reason: str
    source: str  # "db" | "cache" | "fail_closed"

    @property
    def entries_allowed(self) -> bool:
        return self.armed and not self.reduce_only

    def describe(self) -> str:
        if self.entries_allowed:
            return f"entries allowed ({self.source})"
        return f"entries blocked: {self.reason} (source={self.source})"


async def _fetch_from_pool(agent_ids: Sequence[str]) -> Sequence[Mapping[str, Any]]:
    from src.db.pool import get_pool

    async with get_pool().acquire() as conn:
        return await conn.fetch(
            "SELECT agent_id, armed, reduce_only, reason FROM agent_control "
            "WHERE agent_id = ANY($1::text[])",
            list(agent_ids),
        )


class EntryControl:
    """Reads the control row, caches it briefly, and fails closed."""

    def __init__(
        self,
        agent_id: str = GLOBAL_AGENT_ID,
        *,
        ttl_s: float = DEFAULT_TTL_S,
        max_stale_s: float = DEFAULT_MAX_STALE_S,
        fetch_rows: FetchRows | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.agent_id = agent_id
        self._ttl_s = ttl_s
        self._max_stale_s = max_stale_s
        self._fetch_rows = fetch_rows or _fetch_from_pool
        self._clock = clock
        self._logger = get_logger(self.__class__.__name__)
        self._cached: EntryControlState | None = None
        self._cached_at: float | None = None
        self._last_ok_at: float | None = None
        self.last_fault: str | None = None

    @property
    def last_success_age_s(self) -> float | None:
        if self._last_ok_at is None:
            return None
        return max(0.0, self._clock() - self._last_ok_at)

    async def state(self, *, force: bool = False) -> EntryControlState:
        now = self._clock()
        if (
            not force
            and self._cached is not None
            and self._cached_at is not None
            and now - self._cached_at < self._ttl_s
        ):
            return EntryControlState(
                armed=self._cached.armed,
                reduce_only=self._cached.reduce_only,
                reason=self._cached.reason,
                source="cache",
            )

        try:
            rows = await self._fetch_rows([self.agent_id, GLOBAL_AGENT_ID])
        except Exception as exc:  # noqa: BLE001 - a brake may not raise into the hot path
            self.last_fault = f"{type(exc).__name__}: {exc}"
            age = self.last_success_age_s
            if self._cached is not None and age is not None and age <= self._max_stale_s:
                self._logger.warning(
                    "Entry control read failed (%s); using value %.0fs old",
                    self.last_fault,
                    age,
                )
                return EntryControlState(
                    armed=self._cached.armed,
                    reduce_only=self._cached.reduce_only,
                    reason=self._cached.reason,
                    source="cache",
                )
            self._logger.error(
                "Entry control unreadable and no fresh value (%s); blocking entries",
                self.last_fault,
            )
            return EntryControlState(
                armed=False,
                reduce_only=True,
                reason=f"control unreadable: {self.last_fault}",
                source="fail_closed",
            )

        resolved = self._combine(rows)
        self._cached = resolved
        self._cached_at = now
        self._last_ok_at = now
        self.last_fault = None
        return resolved

    def _combine(self, rows: Sequence[Mapping[str, Any]]) -> EntryControlState:
        """Most restrictive wins: the global row can veto, per-agent rows refine."""
        if not rows:
            return EntryControlState(
                armed=False,
                reduce_only=True,
                reason="no control row (table present, row missing)",
                source="db",
            )
        armed = True
        reduce_only = False
        reasons: list[str] = []
        for row in rows:
            # Strict identity: NULL, 0 or a string must never read as "armed".
            if row["armed"] is not True:
                armed = False
                reasons.append(str(row.get("reason") or f"{row['agent_id']}: disarmed"))
            if row.get("reduce_only") is True:
                reduce_only = True
        return EntryControlState(
            armed=armed,
            reduce_only=reduce_only,
            reason="; ".join(dict.fromkeys(reasons)) if reasons else "armed",
            source="db",
        )

    async def entries_allowed(self) -> tuple[bool, str]:
        state = await self.state()
        return state.entries_allowed, state.describe()
