from __future__ import annotations

import json
import math
from bisect import bisect_right
from collections import defaultdict
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

from src.sentiment_sources import SENTIMENT_ERROR_SOURCES
from src.utils.logger import get_logger


def _utc_timestamp(value: datetime | str) -> datetime:
    """Normalize supported candle/log times; legacy naive timestamps mean UTC."""
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if not isinstance(value, datetime):
        raise ValueError("timestamp must be a datetime or ISO timestamp")
    if value.tzinfo is None or value.utcoffset() is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


class ReplaySentimentScorer:
    """Replays persisted sentiment_score observations from the event log.

    Looks up the most recent score at or before the requested candle timestamp for a symbol.
    Error-source or invalid-score observations start a gap until the next usable observation;
    they never carry their synthetic score or an older score forward. Missing
    sources and non-error sources retain their historical replay behavior.
    Falls back to neutral 50.0 (or the configured fallback) during a gap or
    when no historical observation exists.

    Undecodable rows inside the log are skipped: their symbol and timestamp cannot be
    trusted to establish a gap, so dropping them changes nothing that is attributable.
    Two shapes are not attributable and are handled conservatively instead:

    * A final line without a trailing newline that does not decode is a torn append
      (``EventLog`` terminates every record with ``"\\n"``).  Its symbol, time and payload
      are unknowable, so no pre-tail score may be replayed as current: every symbol is
      treated as a gap after its newest durable observation.
    * An error-source record whose timestamp is missing or cannot be normalized cannot be
      placed in time, so the outage has no upper bound.  The affected symbol (all symbols
      when the record carries no symbol) is treated as a gap after its newest durable
      observation.

    In both cases history at or before the newest durable observation is still replayed;
    only the region the log can no longer vouch for falls back.  An older score is never
    carried forward as if it were current.
    """

    def __init__(
        self,
        event_log_path: str | Path,
        *,
        max_age_seconds: float | None = None,
        fallback_score: float = 50.0,
    ) -> None:
        self._path = Path(event_log_path)
        self._max_age_seconds = max_age_seconds
        self._fallback_score = fallback_score
        self._logger = get_logger(self.__class__.__name__)
        self._timeline: dict[str, list[tuple[datetime, float | None]]] = defaultdict(list)
        self._timestamps: dict[str, list[datetime]] = {}
        self._tainted_after: dict[str, datetime] = {}
        self._tainted_all_after: datetime | None = None
        self._hits = 0
        self._misses = 0
        self._stale_misses = 0
        self._load()

    def _load(self) -> None:
        if not self._path.exists():
            self._logger.warning("Sentiment replay log not found: %s", self._path)
            return

        loaded = 0
        timeline: dict[str, list[tuple[datetime, float | None]]] = defaultdict(list)
        unplaceable_symbols: set[str] = set()
        unplaceable_all = False
        tail_undecodable = False
        tail_terminated = True
        try:
            with self._path.open(encoding="utf-8") as handle:
                for line in handle:
                    # Both flags describe the line just read, so after the loop they
                    # describe the final line, terminated or not.
                    tail_undecodable = False
                    tail_terminated = line.endswith("\n")
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        event = json.loads(line)
                    except (ValueError, RecursionError):
                        tail_undecodable = True
                        continue
                    if not isinstance(event, dict) or event.get("type") != "sentiment_score":
                        continue
                    payload = event.get("payload")
                    if not isinstance(payload, dict):
                        continue
                    symbol = payload.get("symbol")
                    score = payload.get("score")
                    source = payload.get("source")
                    is_error = isinstance(source, str) and source in SENTIMENT_ERROR_SOURCES
                    ts = event.get("ts")
                    if not symbol:
                        # An error we cannot attribute cannot be scoped to one symbol.
                        unplaceable_all = unplaceable_all or is_error
                        continue
                    if not ts:
                        if is_error:
                            unplaceable_symbols.add(str(symbol))
                        continue
                    try:
                        dt = _utc_timestamp(ts)
                        if is_error:
                            val = None
                        else:
                            try:
                                if isinstance(score, str):
                                    # Check exact bounds before float rounding or underflow.
                                    exact = Decimal(score)
                                    val = (
                                        float(score)
                                        if exact.is_finite() and 0 <= exact <= 100
                                        else None
                                    )
                                elif isinstance(score, (int, float)) and not isinstance(
                                    score, bool
                                ):
                                    val = float(score)
                                else:
                                    val = None
                                if val is not None and (
                                    not math.isfinite(val) or not 0 <= val <= 100
                                ):
                                    val = None
                            except (ValueError, TypeError, OverflowError, InvalidOperation):
                                val = None
                    except (ValueError, TypeError, OverflowError):
                        if is_error:
                            unplaceable_symbols.add(str(symbol))
                        continue
                    timeline[str(symbol)].append((dt, val))
                    loaded += 1

            if tail_undecodable and not tail_terminated:
                # Torn append: the newest record is unknowable, so no prior score may
                # stand in for it for any symbol.
                unplaceable_all = True
                self._logger.error(
                    "Sentiment replay log %s ends with an undecodable, unterminated record "
                    "(torn append); treating every symbol as gapped after its newest durable "
                    "observation",
                    self._path,
                )

            for symbol, items in timeline.items():
                items.sort(key=lambda x: x[0])
                self._timestamps[symbol] = [ts for ts, _ in items]

            self._timeline = timeline
            self._tainted_after.clear()
            self._tainted_all_after = None
            if unplaceable_all:
                self._tainted_all_after = max(
                    (items[-1][0] for items in timeline.values()), default=None
                )
            for tainted_symbol in unplaceable_symbols:
                tainted_items = timeline.get(tainted_symbol)
                if tainted_items:
                    self._tainted_after[tainted_symbol] = tainted_items[-1][0]
            if self._tainted_after or self._tainted_all_after is not None:
                self._logger.warning(
                    "Replay sentiment log %s has an unplaceable error record or a torn tail; "
                    "gapping %s after the newest durable observation",
                    self._path,
                    "every symbol"
                    if self._tainted_all_after is not None
                    else ", ".join(sorted(self._tainted_after)),
                )

            self._logger.info(
                "Loaded %d replay sentiment observations across %d symbols from %s",
                loaded,
                len(self._timeline),
                self._path,
            )
        except Exception as exc:  # noqa: BLE001
            self._timeline.clear()
            self._timestamps.clear()
            self._tainted_after.clear()
            self._tainted_all_after = None
            self._logger.error("Failed to load replay sentiment log %s: %s", self._path, exc)

    async def get_score_for_candle(self, symbol: str, at_time: datetime | str | None) -> float:
        """Never use the latest observation if a replay candle lacks a valid clock."""
        try:
            if at_time is None:
                raise ValueError("missing candle timestamp")
            when = _utc_timestamp(at_time)
        except (ValueError, TypeError, OverflowError):
            self._misses += 1
            return self._fallback_score
        return await self.get_score(symbol, when)

    def _tainted_boundary(self, symbol: str) -> datetime | None:
        """Newest durable observation after which this symbol's state is unknowable."""
        boundaries = [
            boundary
            for boundary in (self._tainted_after.get(symbol), self._tainted_all_after)
            if boundary is not None
        ]
        return min(boundaries) if boundaries else None

    async def get_score(self, symbol: str, at_time: datetime | str | None = None) -> float:
        items = self._timeline.get(symbol)
        if not items:
            self._misses += 1
            return self._fallback_score
        if at_time is None:
            # Without a clock the request is for the newest observation, which an
            # unplaceable error or torn tail may have superseded.
            if self._tainted_boundary(symbol) is not None or items[-1][1] is None:
                self._misses += 1
                return self._fallback_score
            self._hits += 1
            return items[-1][1]

        try:
            at_time = _utc_timestamp(at_time)
        except (ValueError, TypeError, OverflowError):
            self._misses += 1
            return self._fallback_score
        boundary = self._tainted_boundary(symbol)
        if boundary is not None and at_time > boundary:
            self._misses += 1
            return self._fallback_score
        timestamps = self._timestamps.get(symbol, [])
        idx = bisect_right(timestamps, at_time) - 1
        if idx < 0:
            self._misses += 1
            return self._fallback_score

        ts, score = items[idx]
        if score is None:
            self._misses += 1
            return self._fallback_score
        if self._max_age_seconds is not None:
            age = (at_time - ts).total_seconds()
            if age > self._max_age_seconds:
                self._misses += 1
                self._stale_misses += 1
                return self._fallback_score
        self._hits += 1
        return score

    def stats(self) -> dict[str, int]:
        return {
            "hits": self._hits,
            "misses": self._misses,
            "stale_misses": self._stale_misses,
            "loaded_symbols": len(self._timeline),
            "loaded_observations": sum(len(v) for v in self._timeline.values()),
            # Symbols whose newest durable observation cannot be shown to be current.
            "tainted_symbols": (
                len(self._timeline)
                if self._tainted_all_after is not None
                else len(self._tainted_after)
            ),
        }
