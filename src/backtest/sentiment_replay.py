from __future__ import annotations

import json
from bisect import bisect_right
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from src.utils.logger import get_logger


class ReplaySentimentScorer:
    """Replays persisted sentiment_score observations from the event log.

    Looks up the most recent score at or before the requested candle timestamp for a symbol.
    Falls back to neutral 50.0 when no historical observation exists.
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
        self._timeline: dict[str, list[tuple[datetime, float]]] = defaultdict(list)
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
        try:
            with self._path.open(encoding="utf-8") as handle:
                for line in handle:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        event = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if event.get("type") != "sentiment_score":
                        continue
                    payload = event.get("payload", {}) or {}
                    symbol = payload.get("symbol")
                    score = payload.get("score")
                    ts = event.get("ts")
                    if not symbol or score is None or not ts:
                        continue
                    try:
                        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                        val = float(score)
                    except (ValueError, TypeError):
                        continue
                    self._timeline[str(symbol)].append((dt, val))
                    loaded += 1

            for symbol, items in self._timeline.items():
                items.sort(key=lambda x: x[0])
                self._timestamps[symbol] = [ts for ts, _ in items]

            self._logger.info(
                "Loaded %d replay sentiment observations across %d symbols from %s",
                loaded,
                len(self._timeline),
                self._path,
            )
        except Exception as exc:  # noqa: BLE001
            self._logger.error("Failed to load replay sentiment log %s: %s", self._path, exc)

    async def get_score(self, symbol: str, at_time: datetime | None = None) -> float:
        items = self._timeline.get(symbol)
        if not items:
            self._misses += 1
            return self._fallback_score
        if at_time is None:
            self._hits += 1
            return items[-1][1]

        timestamps = self._timestamps.get(symbol, [])
        idx = bisect_right(timestamps, at_time) - 1
        if idx < 0:
            self._misses += 1
            return self._fallback_score

        ts, score = items[idx]
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
