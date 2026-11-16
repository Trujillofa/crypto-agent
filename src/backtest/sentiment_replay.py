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
    when no historical observation exists. Undecodable individual JSON rows
    are skipped: their symbol and timestamp cannot be trusted to establish a gap.
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
        try:
            with self._path.open(encoding="utf-8") as handle:
                for line in handle:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        event = json.loads(line)
                    except (ValueError, RecursionError):
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
                    if not symbol or not ts:
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
                        continue
                    timeline[str(symbol)].append((dt, val))
                    loaded += 1

            for symbol, items in timeline.items():
                items.sort(key=lambda x: x[0])
                self._timestamps[symbol] = [ts for ts, _ in items]

            self._timeline = timeline

            self._logger.info(
                "Loaded %d replay sentiment observations across %d symbols from %s",
                loaded,
                len(self._timeline),
                self._path,
            )
        except Exception as exc:  # noqa: BLE001
            self._timeline.clear()
            self._timestamps.clear()
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

    async def get_score(self, symbol: str, at_time: datetime | str | None = None) -> float:
        items = self._timeline.get(symbol)
        if not items:
            self._misses += 1
            return self._fallback_score
        if at_time is None:
            if items[-1][1] is None:
                self._misses += 1
                return self._fallback_score
            self._hits += 1
            return items[-1][1]

        try:
            at_time = _utc_timestamp(at_time)
        except (ValueError, TypeError, OverflowError):
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
        }
