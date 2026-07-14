# NFP Good-News-Is-Good OOS Input Contract

This document defines the immutable inputs for
`scripts/probe_nfp_good_news_oos.py`. It is not a research result and does not
authorize trading.

## Event Set

- US NFP releases from 2021-01-08 through 2023-12-08.
- Exactly one row per scheduled release in
  `nfp_good_news_oos_2021_2023.csv` when recoverable.
- A strict majority, 19 data-aligned rows, is required. Fewer rows produce
  `BLOCKED_ON_DATA`.

## Required Per-Row Evidence

- BLS release-time headline nonfarm-payroll actual, not a later revised series.
- Investing Forecast value from a Wayback snapshot of
  `https://www.investing.com/economic-calendar/nonfarm-payrolls-227`.
- Exact Wayback snapshot URL in `source_snapshot_url`.
- `surprise = actual - consensus`; `z` standardized across the committed OOS rows.

## OHLCV Input

- `BTCUSDT_1h_2021-01-01_2024-01-01.csv` was downloaded from Binance spot public
  klines on 2026-07-09 with `scripts/download_historical.py`.
- SHA-256: `b811cf83fe609abf8433dcac959e3671e8d6fd4cb73d1c9fb9f4e275a7506821`.
- The probe uses only `time` and `close_price`; it does not read production DB data.

## Collection Rules

- Select a snapshot captured after the release whose `Latest Release` date matches
  that row's scheduled release date.
- Do not substitute a later Investing table row, a revised forecast, or a current
  BLS time series for the release-time actual.
- Preserve unrecoverable releases as exclusions in the final report. Do not fill
  them with estimates or data from another calendar.

## Current State

The OHLCV input is committed. No OOS surprise CSV is committed until every included
row satisfies the evidence requirements above. The probe therefore has no verdict yet.
