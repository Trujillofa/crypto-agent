# Canonical 200-bar historical backfill — 2026-10-22

**Status:** executed. 28 pairs, 1,061.7 s, 0 errors.
**Prerequisite work:** PR #204 (canonical window), #205 (verifier ownership),
#206 (writer census), #207 (incident + manifest), #208 (live path publishes the
newest closed candle), #209 (sentinel minimum sample), #210 (restore drill),
#211 (residual-risk table), #212 (backfills exclude the forming candle).

## What was written and why

The `indicators` table held rows from four different writers, each with its own
definition of the same indicator. The live path computes over exactly **200
closed bars**; the historical backfills used 251 or 751 bars and a slice that
was off by one against the live path. Whichever writer ran last won.

This backfill recomputes **every** row of every pair under the single canonical
definition — `INDICATOR_WINDOW_BARS = 200`, via the shared
`compute_indicators_for_window(rows, index=i, window=200)` helper, the same
function the live path uses.

| | |
|---|---|
| Rows before | 248,775 |
| Rows after | 253,235 |
| Delta | +4,460 (4,457 predicted new + 3 that arrived live during the run) |
| Rows upserted | 249,006 (244,549 overwrites + 4,457 new) |
| Rows audited vs canonical recompute | 249,009 — **0 differing** |
| Pairs written | 28 |
| Pairs skipped (fewer than 200 closed bars) | 6 |
| Elapsed | 1,061.7 s |
| Errors | 0 |

### Scope was measured before the write, not after

The scope was computed arithmetically from `ohlcv` (rows − 199 − forming-bar)
and then confirmed against the writer's own reported count for every pair.
Every per-pair result matched the prediction exactly:

| Pair | OHLCV bars | Rows before | Rows after | Predicted |
|---|---|---|---|---|
| ADAUSDT 1h | 20,544 | 19,794 | 20,345 | 20,345 ✓ |
| AVAXUSDT 1h | 20,137 | 19,887 | 19,938 | 19,938 ✓ |
| BTCUSDT 1h | 23,334 | 23,135 | 23,136 | 23,135 (+1 live) |
| BTCUSDT 4h | 5,377 | 4,726 | 5,178 | 5,178 ✓ |
| ETHUSDT 4h | 5,273 | 4,627 | 5,074 | 5,074 ✓ |
| SOLUSDT 4h | 6,148 | 5,800 | 5,948 | 5,948 ✓ |
| XRPUSDT 1h | 20,544 | 19,794 | 20,345 | 20,345 ✓ |

(All 28 pairs matched; the table above is a representative sample.)

### Read-only proof before the write

A full dry-run sweep over every pair left the table **byte-identical**:

```
BEFORE: rows=248775 checksum=c223422719b144de38dcff79fb8c0a00
AFTER:  rows=248775 checksum=c223422719b144de38dcff79fb8c0a00
```

The checksum is a SHA-256 over `(symbol, timeframe, time, vwap, rsi_14,
ema_200, macd_hist, sma_40)` for every row in the table. This was re-verified
immediately before the real write, so the write started from a known state.

### Backup taken before the write

A fresh full-database dump was taken *after* the dry run and *before* the
write, so the pre-backfill state is a recoverable snapshot rather than a
reconstruction:

```
backups/prebackfill_marketdata_20261022_124943.sql.gz
sha256  12dd6d4c6c8f5dd1aadcb00bae7a137d7980a209b82cecfd3cd2cad93c54ee8e
```

Verified: gzip integrity OK; the dump contains **248,775** indicator data rows
across 147 TimescaleDB chunk tables. (`COPY public.indicators` is empty in a
TimescaleDB dump — the data lives in `_timescaledb_internal._hyper_2_*_chunk`
tables. This is the same property the restore drill had to work around.)

### The writer was proven to cover the table exactly

A backfill that omits a column leaves the old value in place on conflict,
which would silently preserve the legacy value instead of replacing it — the
exact failure this work exists to remove. Verified against the live schema:

```
table value columns      : 36
writer columns           : 39   (36 values + time, symbol, timeframe)
placeholders in VALUES   : 39   (must equal 39)
MISSING from writer      : none
EXTRA in writer          : none
written but NOT updated on conflict : none
RESULT: PASS - writer covers the table exactly
```

`computed_at` is intentionally not written: it has a DB default and records
the first write, so it is left to the database.

## The forming candle is excluded

The REST poller writes Binance's in-progress kline into `ohlcv` and revises
it when the candle closes. A row computed from a forming bar is a partial
value that can never reconcile, because the live path only ever rewrites the
newest bar.

PR #212 extracted the definition of "closed" into one shared helper
(`newest_closed_index` in `src/features/technical.py`) and used it in the live
path *and* all three backfill scripts, so the two cannot disagree about which
bar they own. The run log shows the rule firing:

```
Excluding the forming candle at 2026-10-22 12:00:00+00:00 (closes 2026-10-22 15:59:59.999000+00:00)
```

## What was NOT written, and why

**4,226 rows still carry the legacy NULL fingerprint.** All of them are
mathematically uncomputable, and this was proven rather than assumed:

| Cause | Rows | Pairs |
|---|---|---|
| Pair has **no** `ohlcv` at all | 3,028 | ADAUSDT 1m/5m, DOGEUSDT 1m/5m, DOTUSDT 1m/5m, XRPUSDT 1m/5m |
| Row sits inside the uncomputable prefix (fewer than 200 bars behind it) | 1,198 | 1m and 5m pairs for AVAX/BNB/BTC/ETH/LINK/SOL |

A row at position *i* of a pair's history is computable only if *i* ≥ 199.
Every remaining NULL row was checked against that boundary:

```
TOTAL  null rows 4226   unjustified 0
PASS: every remaining NULL row is inside the uncomputable prefix
      (no 200-bar window behind it). Nothing computable was left behind.
```

Before the backfill the same fingerprint covered 19,920 rows.

## Final equality audit — every stored row vs a canonical recompute

Every indicator row of every pair was recomputed from `ohlcv` under the
canonical 200-bar window and compared across **all 36 value columns**:

```
rows audited            : 249009
rows matching canonical : 249009
rows DIFFERING          : 0
columns with any difference: none
```

Per-pair results:

| Pair | Stored | Match | Result |
|---|---|---|---|
| ADAUSDT 1h | 20,345 | 20,345 | OK |
| AVAXUSDT 1h | 19,938 | 19,938 | OK |
| AVAXUSDT 4h | 5,027 | 5,027 | OK |
| BNBUSDT 1h | 20,345 | 20,345 | OK |
| BNBUSDT 4h | 4,707 | 4,707 | OK |
| BTCUSDT 1h | 23,136 | 23,136 | OK |
| BTCUSDT 4h | 5,178 | 5,178 | OK |
| BTCUSDT 5m | 2,146 | 1,976 | OK (the other 170 are in the uncomputable prefix) |
| DOGEUSDT 1h | 20,345 | 20,345 | OK |
| ETHUSDT 1h | 23,136 | 23,136 | OK |
| ETHUSDT 4h | 5,074 | 5,074 | OK |
| LINKUSDT 1h | 20,345 | 20,345 | OK |
| LINKUSDT 4h | 4,655 | 4,655 | OK |
| LTCUSDT 1h | 20,346 | 20,346 | OK |
| SOLUSDT 1h | 23,952 | 23,952 | OK |
| SOLUSDT 4h | 5,948 | 5,948 | OK |
| XRPUSDT 1h | 20,345 | 20,345 | OK |
| *6 × 1m pairs* | 29–33 each | — | NOT RECOMPUTABLE (fewer than 200 bars) |

The 6 × 1m pairs and the 4 × 5m pairs' first 170 rows are the only stored
rows the audit did not recompute, and they are the ones proven
uncomputable above.

The 5m pairs show fewer matches than stored rows for exactly the same
reason: their first 170 stored rows sit inside the uncomputable prefix.

## Post-write production state

- 9/9 containers healthy, 0 restarts.
- **0 of 28 live pairs publish a forming bar** (checked with the corrected
  condition: a pair is only defective if it published the newest `ohlcv` bar
  *and* that bar is still forming).
- The live path and the backfill now compute the same value for the same bar,
  so a live-written row and a backfilled row are interchangeable.
- The final audit recomputed **249,009** stored rows from `ohlcv` under the
  canonical 200-bar window and compared all 36 value columns:
  **249,009 match, 0 differ.**

## Residual risks

| Risk | Assessment |
|---|---|
| A row that matches a recompute *today* may not match a recompute after Binance revises history | Real and permanent. "Stored == current recompute" is not a timeless invariant; it holds against the data as it stands. |
| 4,226 rows remain legacy-shaped | Accepted. Proven uncomputable — no `ohlcv` exists for them, or no 200-bar window precedes them. They are reported, not asserted. |
| The live path now lags the newest bar by one while a candle is forming | **Intended.** The strategy engine reads the two newest rows for crossover detection, so it evaluates a genuinely closed bar. |
| A wholesale restore of the pre-backfill backup would revert every pair's rows | Known. The backup is a full-database dump. Any real rollback must be scoped to a key manifest, or accepted as a full point-in-time revert. |
| The backfill overwrote rows that were already correct | No. The writer's definition is now the canonical one for every pair, so an overwrite replaces a legacy-window value with the canonical value. This is the intended correction. |

## Evidence

- `docs/reports/indicator-vintage-drift-diagnosis-2026-10-22.md` — the diagnosis
  and the four writers.
- `docs/reports/incident-accidental-btcusdt-1h-indicator-write-2026-10-22.md` —
  the accidental write, its exact 20,526-row footprint, and the restore drill.
- `docs/reports/restore-drill-preserved-backup-2026-10-22.md` — the drill that
  proved the preserved dump restores to exactly the pre-write state.
- `docs/reports/affected-rows-btcusdt-1h-2026-10-22.txt` — the key manifest.
