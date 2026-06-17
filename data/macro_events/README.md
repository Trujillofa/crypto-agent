# US Macro Event Release Timestamps (frozen ex-ante set)

Static, hand-verified release timestamps for the scheduled macro-event drift
Gate 1 probe (`scripts/probe_macro_event_drift.py`).

## Frozen event set

| Type | Count (2024-01-01 → 2026-06-01) | Release time (ET) |
|------|----------------------------------|-------------------|
| FOMC rate decision | 19 | 14:00 (last day of meeting) |
| US CPI | 28 | 08:30 |
| US NFP (Employment Situation) | 28 | 08:30 |
| **Total** | **75** | |

Event types are fixed before any return measurement. No ECB/PCE/jobless-claims.

## Sources and licensing

| Event | Primary source | Licensing |
|-------|----------------|-----------|
| FOMC | [Federal Reserve FOMC calendars](https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm) | US government work (public domain) |
| CPI, NFP | [BLS release calendars](https://www.bls.gov/schedule/news_release/) | US government work (public domain) |

2025 lapse revisions (Oct CPI/NFP canceled; delayed Sep/Nov releases) follow
[BLS revised release dates](https://www.bls.gov/bls/2025-lapse-revised-release-dates.htm).

## Timestamp precision

- All times converted from **America/New_York** to **UTC** via `zoneinfo` (DST-aware).
- CPI/NFP: **08:30 ET** → **13:30 UTC** (EST) or **12:30 UTC** (EDT).
- FOMC: **14:00 ET** → **19:00 UTC** (EST) or **18:00 UTC** (EDT).
- Precision: **minute-level** (sufficient for 1h OHLCV alignment and +6h windows).

## File

- `us_macro_releases.csv` — columns: `event_type`, `release_date_et`, `release_ts_utc`, `source`
