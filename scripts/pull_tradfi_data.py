#!/usr/bin/env python3
"""Pull frozen TradFi proxy series for cross-asset risk-regime probe (Step 0).

Downloads daily (full window) and 1h (Yahoo 730d cap) via yfinance, assigns
point-in-time close timestamps in UTC, and writes data/tradfi/*.csv.

Re-run only to refresh; the Gate 1 probe reads committed CSVs.
"""

from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import yfinance as yf

DATA_DIR = Path(__file__).resolve().parent.parent / "data/tradfi"
START = "2024-01-01"
END = "2026-06-02"

FROZEN_TICKERS: dict[str, str] = {
    "equity_risk": "QQQ",
    "dxy": "DX-Y.NYB",
    "us10y": "^TNX",
    "vix": "^VIX",
}

# Session close conventions for daily bars (Yahoo date-only index).
DAILY_CLOSE_LOCAL: dict[str, tuple[ZoneInfo, int, int]] = {
    "equity_risk": (ZoneInfo("America/New_York"), 16, 0),
    "dxy": (ZoneInfo("America/New_York"), 17, 0),
    "us10y": (ZoneInfo("America/Chicago"), 16, 0),
    "vix": (ZoneInfo("America/Chicago"), 16, 0),
}

CSV_FIELDS = (
    "proxy",
    "source_ticker",
    "granularity",
    "bar_open_utc",
    "close_ts_utc",
    "close",
    "is_weekend_gap_after",
)


@dataclass(frozen=True)
class PulledRow:
    proxy: str
    source_ticker: str
    granularity: str
    bar_open_utc: datetime
    close_ts_utc: datetime
    close: float
    is_weekend_gap_after: bool


def _to_utc(ts: datetime) -> datetime:
    if ts.tzinfo is None:
        return ts.replace(tzinfo=UTC)
    return ts.astimezone(UTC)


def _daily_close_ts(bar_date: datetime, proxy: str) -> datetime:
    tz, hour, minute = DAILY_CLOSE_LOCAL[proxy]
    local = datetime(bar_date.year, bar_date.month, bar_date.day, hour, minute, tzinfo=tz)
    return local.astimezone(UTC)


def _weekend_gap(prev_close: datetime, curr_close: datetime, granularity: str) -> bool:
    delta = curr_close - prev_close
    if granularity == "1d":
        return delta > timedelta(hours=36)
    return delta > timedelta(hours=72)


def _scalar_close(row) -> float:
    if "Close" in row.index:
        value = row["Close"]
    else:
        value = row.iloc[0]
    if hasattr(value, "iloc"):
        value = value.iloc[0]
    return float(value)


def _flatten_columns(frame):
    if hasattr(frame.columns, "levels") and len(frame.columns.levels) > 1:
        frame = frame.copy()
        frame.columns = frame.columns.get_level_values(0)
    return frame


def pull_daily(proxy: str, ticker: str) -> list[PulledRow]:
    frame = _flatten_columns(
        yf.download(ticker, start=START, end=END, interval="1d", progress=False, auto_adjust=True)
    )
    if frame.empty:
        return []
    rows: list[PulledRow] = []
    prev_close_ts: datetime | None = None
    for index, row in frame.iterrows():
        bar_date = (
            index.to_pydatetime()
            if hasattr(index, "to_pydatetime")
            else datetime.fromisoformat(str(index))
        )
        close_ts = _daily_close_ts(bar_date, proxy)
        bar_open = close_ts - timedelta(days=1)
        gap = False
        if prev_close_ts is not None:
            gap = _weekend_gap(prev_close_ts, close_ts, "1d")
        close = _scalar_close(row)
        rows.append(
            PulledRow(
                proxy=proxy,
                source_ticker=ticker,
                granularity="1d",
                bar_open_utc=bar_open,
                close_ts_utc=close_ts,
                close=close,
                is_weekend_gap_after=gap,
            )
        )
        prev_close_ts = close_ts
    return rows


def pull_hourly(proxy: str, ticker: str) -> list[PulledRow]:
    frame = _flatten_columns(
        yf.download(ticker, period="730d", interval="1h", progress=False, auto_adjust=True)
    )
    if frame.empty:
        return []
    rows: list[PulledRow] = []
    prev_close_ts: datetime | None = None
    for index, row in frame.iterrows():
        bar_open = _to_utc(index.to_pydatetime())
        close_ts = bar_open + timedelta(hours=1)
        gap = False
        if prev_close_ts is not None:
            gap = _weekend_gap(prev_close_ts, bar_open, "1h")
        close = _scalar_close(row)
        rows.append(
            PulledRow(
                proxy=proxy,
                source_ticker=ticker,
                granularity="1h",
                bar_open_utc=bar_open,
                close_ts_utc=close_ts,
                close=close,
                is_weekend_gap_after=gap,
            )
        )
        prev_close_ts = close_ts
    return rows


def write_csv(path: Path, rows: list[PulledRow]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS)
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    "proxy": row.proxy,
                    "source_ticker": row.source_ticker,
                    "granularity": row.granularity,
                    "bar_open_utc": row.bar_open_utc.isoformat(),
                    "close_ts_utc": row.close_ts_utc.isoformat(),
                    "close": f"{row.close:.6f}",
                    "is_weekend_gap_after": str(row.is_weekend_gap_after).lower(),
                }
            )


def main() -> int:
    parser = argparse.ArgumentParser(description="Pull frozen TradFi proxy CSVs")
    parser.add_argument("--data-dir", type=Path, default=DATA_DIR)
    args = parser.parse_args()

    summary: list[str] = []
    for proxy, ticker in FROZEN_TICKERS.items():
        daily = pull_daily(proxy, ticker)
        hourly = pull_hourly(proxy, ticker)
        if not daily:
            print(f"BLOCKED: no daily data for {proxy} ({ticker})")
            return 2
        write_csv(args.data_dir / f"{proxy}_1d.csv", daily)
        if hourly:
            write_csv(args.data_dir / f"{proxy}_1h.csv", hourly)
        summary.append(
            f"{proxy} ({ticker}): daily={len(daily)} "
            f"[{daily[0].close_ts_utc.date()}..{daily[-1].close_ts_utc.date()}], "
            f"1h={len(hourly)}"
            + (
                f" [{hourly[0].close_ts_utc.date()}..{hourly[-1].close_ts_utc.date()}]"
                if hourly
                else ""
            )
        )
        print(summary[-1])

    if not any(proxy == "equity_risk" for proxy in FROZEN_TICKERS):
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
