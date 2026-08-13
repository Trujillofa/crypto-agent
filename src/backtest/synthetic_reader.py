"""In-memory indicator reader over seeded synthetic OHLCV.

Duck-types the three ``IndicatorReader`` methods the engine calls. Does not
inherit from ``IndicatorReader`` (that constructor wants DB config). Joins
reuse ``IndicatorReader._join_timeframes`` so lookahead rules stay identical.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from typing import cast

from src.features.reader import FundingSettlement, IndicatorReader
from src.features.technical import OhlcvSeries, TechnicalIndicators, compute_indicators
from src.ingest.models import Ohlcv

DEFAULT_WARMUP_BARS = 200
MIN_INDICATOR_BARS = 30


def _bar_delta(timeframe: str) -> timedelta:
    if timeframe == "4h":
        return timedelta(hours=4)
    if timeframe == "1d":
        return timedelta(days=1)
    return timedelta(hours=1)


def _parse_dt(val: str | datetime) -> datetime:
    if isinstance(val, str):
        val = datetime.fromisoformat(val)
    if val.tzinfo is None:
        return val.replace(tzinfo=UTC)
    return val


def aggregate_ohlcv(candles: Sequence[Ohlcv], target_timeframe: str) -> list[Ohlcv]:
    """Resample non-overlapping target bars. Drops a trailing incomplete group."""
    if not candles:
        return []
    source_delta = _bar_delta(candles[0].timeframe)
    target_delta = _bar_delta(target_timeframe)
    ratio_float = target_delta / source_delta
    ratio = int(ratio_float)
    if ratio < 1 or float(ratio) != ratio_float:
        raise ValueError(f"cannot aggregate {candles[0].timeframe} to {target_timeframe}")

    out: list[Ohlcv] = []
    n = len(candles)
    i = 0
    while i + ratio <= n:
        group = candles[i : i + ratio]
        last = group[-1]
        out.append(
            Ohlcv(
                symbol=last.symbol,
                timeframe=target_timeframe,
                open_time=group[0].open_time,
                close_time=last.close_time,
                open_price=group[0].open_price,
                high_price=max(candle.high_price for candle in group),
                low_price=min(candle.low_price for candle in group),
                close_price=last.close_price,
                volume=sum(candle.volume for candle in group),
            )
        )
        i += ratio
    return out


def _series(candles: Sequence[Ohlcv]) -> OhlcvSeries:
    return cast(
        OhlcvSeries,
        {
            "close": [candle.close_price for candle in candles],
            "high": [candle.high_price for candle in candles],
            "low": [candle.low_price for candle in candles],
            "volume": [candle.volume for candle in candles],
        },
    )


def _row_from(
    candle: Ohlcv,
    indicators: TechnicalIndicators,
    settlement: FundingSettlement | None = None,
) -> dict[str, object]:
    payload = asdict(indicators)
    ema_12 = payload["ema_12"]
    ema_26 = payload["ema_26"]
    row: dict[str, object] = {
        "time": candle.open_time,
        "open_price": candle.open_price,
        "close_price": candle.close_price,
        "high_price": candle.high_price,
        "low_price": candle.low_price,
        **payload,
        "ema_12": 0.0 if ema_12 is None else ema_12,
        "ema_26": 0.0 if ema_26 is None else ema_26,
    }
    if settlement is not None:
        row["funding_rate"] = settlement.funding_rate
    return row


def candles_to_indicator_rows(candles: Sequence[Ohlcv]) -> list[dict[str, object]]:
    """One indicator row per candle with at least ``MIN_INDICATOR_BARS`` of history."""
    rows: list[dict[str, object]] = []
    for i in range(len(candles)):
        if i + 1 < MIN_INDICATOR_BARS:
            continue
        indicators = compute_indicators(_series(candles[: i + 1]))
        rows.append(_row_from(candles[i], indicators))
    return rows


def _apply_funding_rates(
    rows: list[dict[str, object]],
    funding: Sequence[FundingSettlement],
) -> None:
    ordered = sorted(funding, key=lambda item: item.funding_time)
    idx = 0
    last: FundingSettlement | None = None
    for row in rows:
        bar_time = _parse_dt(row["time"])  # type: ignore[arg-type]
        while idx < len(ordered) and ordered[idx].funding_time <= bar_time:
            last = ordered[idx]
            idx += 1
        if last is not None:
            row["funding_rate"] = last.funding_rate


class SyntheticIndicatorReader:
    """Duck-typed ``IndicatorReader`` over a generated OHLCV path."""

    def __init__(
        self,
        candles: Sequence[Ohlcv],
        *,
        warmup_bars: int = DEFAULT_WARMUP_BARS,
        regime_candles: Sequence[Ohlcv] | None = None,
        funding: Sequence[FundingSettlement] | None = None,
    ) -> None:
        if warmup_bars < MIN_INDICATOR_BARS:
            raise ValueError(f"warmup_bars must be >= {MIN_INDICATOR_BARS}, got {warmup_bars}")
        if len(candles) <= warmup_bars:
            raise ValueError(
                f"need more than warmup_bars ({warmup_bars}) candles, got {len(candles)}"
            )

        self._warmup_bars = warmup_bars
        self._eval_candles = list(candles[warmup_bars:])
        self._funding = list(funding) if funding is not None else []

        computed = candles_to_indicator_rows(candles)
        skip = MIN_INDICATOR_BARS - 1
        self._rows = computed[warmup_bars - skip :]
        if self._funding:
            _apply_funding_rates(self._rows, self._funding)

        if regime_candles is None:
            regime_candles = aggregate_ohlcv(candles, "4h")
        # Full regime series: join enforces no-lookahead via close-time.
        self._regime_rows = candles_to_indicator_rows(regime_candles)

    @property
    def warmup_bars(self) -> int:
        return self._warmup_bars

    @property
    def eval_start(self) -> datetime:
        return self._eval_candles[0].open_time

    @property
    def eval_end(self) -> datetime:
        return self._eval_candles[-1].open_time

    async def fetch_range(
        self,
        symbol: str,
        timeframe: str,
        start_time: str | datetime,
        end_time: str | datetime,
    ) -> list[dict[str, object]]:
        """Return exposed rows with ``time`` in ``[start, end]`` inclusive. Oldest-first."""
        del symbol, timeframe
        start = _parse_dt(start_time)
        end = _parse_dt(end_time)
        return [
            dict(row)
            for row in self._rows
            if start <= _parse_dt(row["time"]) <= end  # type: ignore[arg-type]
        ]

    async def fetch_multi_timeframe(
        self,
        *,
        symbol: str,
        entry_timeframe: str,
        regime_timeframe: str,
        start_time: str | datetime,
        end_time: str | datetime,
    ) -> list[dict[str, object]]:
        start = _parse_dt(start_time)
        end = _parse_dt(end_time)
        entry_rows = await self.fetch_range(symbol, entry_timeframe, start, end)
        regime_start = start - timedelta(hours=24)
        regime_rows = [
            dict(row)
            for row in self._regime_rows
            if regime_start <= _parse_dt(row["time"]) <= end  # type: ignore[arg-type]
        ]
        joined = IndicatorReader._join_timeframes(entry_rows, regime_rows, regime_timeframe)
        return cast(list[dict[str, object]], joined)

    async def fetch_funding_settlements(
        self,
        symbol: str,
        start_time: str | datetime,
        end_time: str | datetime,
    ) -> list[FundingSettlement]:
        del symbol
        if not self._funding:
            return []
        start = _parse_dt(start_time)
        end = _parse_dt(end_time)
        return [item for item in self._funding if start < _parse_dt(item.funding_time) <= end]
