# US Macro Surprise Table (CPI & NFP, frozen ex-ante)

Standardized data surprises for the macro-surprise drift Gate 1 probe
(`scripts/probe_macro_surprise_drift.py`).

## Files

| File | Purpose |
|------|---------|
| `us_macro_releases.csv` | Frozen release timestamps (from calendar probe, PR #86) |
| `us_macro_surprises.csv` | Actual, consensus, surprise, and z-score per CPI/NFP release |
| `README_SURPRISES.md` | This document |

## Metrics (frozen before edge test)

| Series | Metric | Unit |
|--------|--------|------|
| CPI | Headline CPI MoM (seasonally adjusted) | percent |
| NFP | Headline nonfarm payrolls change | thousands |

FOMC is **out of scope** for v0.

## Sources

| Field | Source | Licensing / notes |
|-------|--------|-------------------|
| **Actual (CPI MoM)** | [BLS public API](https://www.bls.gov/developers/) series `CUUR0000SA0` | US government work (public domain) |
| **Actual (NFP change)** | BLS series `CES0000000001` month-over-month level change | US government work (public domain) |
| **Consensus** | [Investing.com](https://www.investing.com/economic-calendar/) economic-calendar **Forecast** column, captured via **Wayback Machine** snapshots of event pages (`cpi-69`, `nonfarm-payrolls-227`) | Third-party calendar data; see point-in-time caveat below |

Rebuild command (requires network for BLS):

```bash
uv run python scripts/build_us_macro_surprises.py
```

## Point-in-time integrity (the crux)

Investing.com publishes a single **Forecast** column on its historical calendar table. We
**do not** have cryptographically provable pre-release snapshots. Wayback captures reduce
(but do not eliminate) the risk that a later page refresh revised the displayed forecast.

**Assessment:** best-effort median-analyst consensus as shown on a major retail calendar;
adequate for a cheap directional probe with an explicit caveat, **not** sufficient for
production surprise-trading without a paid point-in-time vendor (Trading Economics, Bloomberg,
Refinitiv).

Cross-check: Forex Factory historical cache (HuggingFace `Ehsanrs2/Forex_Factory_Calendar`)
agrees on 2024–early-2025 CPI/NFP forecasts where overlapping.

## Known gaps

| Release | Gap |
|---------|-----|
| CPI 2025-12-18 (Nov, delayed) | MoM **Forecast** not present in any captured Wayback table → row omitted |
| NFP 2025-12-16 (Nov) | Forecast present (51K); Oct same-day release not in frozen `us_macro_releases.csv` |

Coverage at build time: **55 / 56** CPI+NFP events with consensus+actual (1 missing).

## Standardized surprise

Per series, over the committed sample:

`z = (actual − consensus) / pstdev(actual − consensus)`

Computed at build time and frozen in `us_macro_surprises.csv`.

## Join key

`event_type` + `release_ts_utc` (must match `us_macro_releases.csv` exactly).
