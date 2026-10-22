# Incident Record — Accidental `BTCUSDT` 1h Indicator Backfill

**Date:** 2026-10-22
**Status:** disclosed, fully characterized, no live-configuration impact
**Severity:** low (data-only, no money-path impact, no service impact)
**Related:** PR #204 (`d7bfd47`), PR #205 (`33fad42`)

---

## 1. What happened

While validating PR #204 against the live database, `scripts/compute_1h_indicators.py`
was executed **without** a dry-run guard. The script upserted **23,127 rows** into the
live `indicators` table for `BTCUSDT` / `1h`.

The write was **not** an authorized backfill. It was a side effect of a validation
command that should have been read-only.

## 2. Why it happened

`compute_1h_indicators.py` had no `--dry-run` flag and no write guard, unlike
`compute_historical_indicators.py` (which had one). The command was run with the
intent of validating the script's execution path, and the absence of a guard was
not noticed before invocation.

This is the same class of gap that PR #204 closed for the other two backfills.
The fix was applied after the fact:

- `--dry-run` added to `compute_1h_indicators.py` and `compute_basic_indicators.py`
- every `writer.write_indicators(...)` call is now guarded by `if not dry_run:`
- the historical writer's `--dry-run` was verified to leave the table byte-identical
- regression tests added for all three scripts, including a source-level guard
  asserting a dry-run invocation cannot reach a write call

## 3. Scope of the write — measured, not estimated

| property | value |
|---|---|
| table | `indicators` |
| symbol / timeframe | `BTCUSDT` / `1h` |
| rows upserted | 23,127 |
| distinct `(time, symbol, timeframe)` keys | one row per key (upsert, not insert) |
| columns written | all 36 indicator columns + `computed_at` |
| schema / constraint changes | none |
| other symbols or timeframes affected | **none** |
| live agent behavior | unaffected (see §5) |

### 3.1 The legacy fingerprint was *removed*, not added

The legacy `compute_1h_indicators.py` wrote only 31 columns, leaving eight
columns NULL: `ema_8`, `ema_10`, `ema_14`, `ema_21`, `ema_24`, `ema_30`,
`sma_40`, `sma_60`. The accidental write used the migrated
`IndicatorWriter`, which writes all 36 columns.

So the write **replaced** a partial row with a complete one. The omitted-column
NULL fingerprint that identified "written by the legacy 1h writer" is **gone**
for `BTCUSDT` / `1h` — it was not extended to any other pair.

## 4. Post-write validation — every affected row compared

Every one of the 23,134 stored `BTCUSDT` / `1h` rows was compared, column by
column, against a fresh 200-bar recompute from the current `ohlcv`:

```
BTCUSDT 1h ohlcv rows: 23333
BTCUSDT 1h indicator rows: 23134  2024-01-09 07:00:00 -> 2026-10-22 10:00:00

compared 23134 rows against a 200-bar recompute
columns with differences: 0
```

**Zero differences across all 36 columns on all 23,134 rows.** The affected
pair is now *exactly* canonical under the PR #204 window definition — better
than before the write, not worse. (23,134 stored rows vs 23,127 written: the
7-row difference is the 200-bar warm-up boundary plus rows written since by
the live agent.)

### 4.1 Whole-table writer census

Every row in the `indicators` table was classified by which writer produced
it, by reproducing the stored VWAP at three candidate window lengths using
the production VWAP formula (typical price `(H+L+C)/3`, not close):

```
symbol     tf      rows    w=200    w=251    w=751    other  dominant
  BTCUSDT  1h     23134    23134        0        0        0  w=200 (23134)
  SOLUSDT  1h     23945     3939       55    19371      580  w=751 (19371)
  SOLUSDT  4h      5800        9       50     4389     1352  w=751 (4389)
  ETHUSDT  1h     22653     4018      128        0    18507  w=200 (4018)
  LINKUSDT 1h     19794        0        2    19792        0  w=751 (19792)
  ...
TOTALS  w200=39169  w251=29904  w751=124042  other=52451
```

`BTCUSDT 1h` is **23,134 / 23,134 at w=200** — the only large pair with no
legacy residue at all. The `other` bucket is concentrated in the `4h` pairs,
where the row was computed from a *forming* candle and so matches no
closed-bar window at all (see the indicator-vintage report §5b).

### 4.2 The live consistency verifier agrees

```
BTCUSDT 1h    1/1  OK
ETHUSDT 1h    1/1  OK
SOLUSDT 1h    1/1  OK
SOLUSDT 4h    1/1  OK
```

## 5. No money-path impact

- The live path (`src/features/computer.py`) writes **one** row per
  symbol/timeframe — the newest OHLCV bar. It overwrites whatever is stored
  for that bar on every 60 s cycle.
- The accidental write's rows are historical bars the live path never
  revisits, and the newest bar was written by the live path *after* the
  accidental write anyway.
- The live agent was verified running with **0 restarts** and no error logs
  after the write.
- The write changed no schema, no constraint, no configuration, and no code
  path that the money path depends on.

## 6. Rollback — what is and is not possible

**There is no pre-write backup.** The write was accidental, unannounced, and
performed before a backup procedure existed for it.

Consequences, stated plainly:

- The **former** stored values (the legacy 31-column rows with eight NULL
  columns) **cannot be restored** from anything on this machine. They are
  gone.
- A **recomputation** to the pre-write state is possible in the mechanical
  sense — one could write NULL into the eight columns again — but that would
  be *re-creating* a known-bad state, not restoring a backup, and would
  reintroduce the exact NULL fingerprint that PR #204 set out to eliminate.
- Therefore **no rollback was performed, and none is recommended.** The
  affected rows are now canonical; rolling back would move them from correct
  to incorrect.

This is the honest answer: the rollback question resolves to "not applicable,
because the post-write state is strictly better and the pre-write state is
unrecoverable". It is recorded here rather than glossed over.

## 7. Prevention — what changed

| control | before | after |
|---|---|---|
| `compute_historical_indicators.py --dry-run` | existed, but unusable (no DB config resolution) | exists, works, verified byte-identical output |
| `compute_basic_indicators.py --dry-run` | **absent** | added, guarded, tested |
| `compute_1h_indicators.py --dry-run` | **absent** (root cause) | added, guarded, tested |
| dry-run proof | none | table row count + checksum unchanged across a dry run |
| column coverage | 31 of 36 (legacy 1h writer) | 36 of 36 for every writer |
| database config | `DB_*` only, unusable in the container | shared resolver: `DB_*` precedence, `POSTGRES_*` fallback |
| pool usage | second pool, double close in the 1h script | shared pool, single `close_pool()` |

Regression tests live in `tests/test_indicator_window_definition.py` and
`tests/test_compute_basic_indicators.py`.

## 8. Standing rules going forward

1. **No historical write without a backup.** A timestamped backup of the
   affected rows (or the whole table) must exist *before* any backfill runs,
   and the restore path must be tested, not just written.
2. **Dry-run first, always**, and verify the table checksum is unchanged
   before committing to the write.
3. **A backfill is not a validation step.** Read-only validation means
   read-only.
4. Every write goes through a reviewed PR and green CI.

## 9. Outstanding

- The broad historical backfill across the remaining pairs has **not** been
  run. It is gated on items 1 and 2 above, and on a decision about whether
  to rewrite rows whose stored values are not reproducible from today's
  OHLCV (see §5b of the indicator-vintage diagnosis report).
- The 4h forming-candle defect is diagnosed and documented but not fixed.

## 10. Reproduction

```bash
# Affected-pair validation (read-only): every row vs a 200-bar recompute
docker exec -i crypto-agent-agent_sol_panic_block_paper-1 python - <<'PY'
import asyncio, os, sys
sys.path.insert(0, "/app")
import asyncpg
from src.features.technical import compute_indicators_for_window

async def main():
    conn = await asyncpg.connect(
        host=os.environ["POSTGRES_HOST"], port=int(os.environ["POSTGRES_PORT"]),
        database=os.environ["POSTGRES_DB"], user=os.environ["POSTGRES_USER"],
        password=os.environ["POSTGRES_PASSWORD"])
    rows = await conn.fetch("""SELECT time, open_price, high_price, low_price, close_price, volume
        FROM ohlcv WHERE symbol='BTCUSDT' AND timeframe='1h' ORDER BY time ASC""")
    stored = {r["time"]: r for r in await conn.fetch(
        "SELECT * FROM indicators WHERE symbol='BTCUSDT' AND timeframe='1h'")}
    series = [dict(r) for r in rows]
    diffs = 0
    for i in range(199, len(series)):
        st = stored.get(series[i]["time"])
        if st is None: continue
        rec = compute_indicators_for_window(series, index=i, window=200)
        for c in rec.__dataclass_fields__:
            sv, rv = st.get(c), getattr(rec, c)
            if sv is None and rv is None: continue
            if sv is None or rv is None: diffs += 1; continue
            if abs(float(sv) - float(rv)) > max(1e-6, abs(float(rv)) * 1e-6): diffs += 1
    print("column differences:", diffs)

asyncio.run(main())
PY
```

Expected: `column differences: 0`.
