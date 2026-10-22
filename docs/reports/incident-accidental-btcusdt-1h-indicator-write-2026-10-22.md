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

## 6. Rollback — a pre-write backup DOES exist

**This section was rewritten.** The first version of this report claimed no
backup existed and that the pre-write values were unrecoverable. That was
wrong, and the reason it was wrong is worth recording.

The host runs a nightly `pg_dump` of the whole `marketdata` database at 03:00
(`/opt/crypto-agent/scripts/backup_db.sh`, wired into the root crontab, with
7-day retention and an offsite copy over Tailscale). The accidental write
happened at **~10:57 on 2026-10-22**. The nightly dump for that morning is
`marketdata_20261022_030001.sql.gz`, taken **~7h57m before the write**.

The first version missed it because it looked for a *pre-write backup taken
for the incident*. The correct question is "does a backup from before the
write exist", and the answer was yes all along.

The dump was immediately copied out of the retention path to
`backups/preserved/marketdata_20261022_030001.sql.gz` (the 7-day pruner
would otherwise have deleted it on 2026-10-29), and its SHA-256 recorded:

```
714742fba8b1f61d04582b2534720d0742cd54233308352a2340c0a46b6af80e
```

### 6.1 The backup really does contain the pre-write state

`indicators` is a TimescaleDB hypertable, so `pg_dump` writes its rows into
per-chunk tables (`_timescaledb_internal._hyper_2_<n>_chunk`) and the
`COPY public.indicators` block is **empty**. A naive restore-check that
looks only at the parent table finds nothing and would wrongly conclude the
backup is useless. `scripts/verify_indicator_backup.py` scans the chunk
blocks instead.

The legacy writer's fingerprint — the eight columns it left NULL
(`ema_8`, `ema_10`, `ema_14`, `ema_21`, `ema_24`, `ema_30`, `sma_40`,
`sma_60`) — is present in the dump exactly as expected for a pre-write
snapshot:

```
symbol     tf      rows  all8NULL   mixed  noneNULL
  BTCUSDT  1h     22642      19920     606      2116
  ETHUSDT  1h     22644         0     608      2116
  SOLUSDT  1h     23937         0    20463      2923
  SOLUSDT  4h      5798         0     4515       732
  AVAXUSDT 4h      4960         0       80         0
  (all other pairs)          0       0         0
```

`BTCUSDT 1h` is the **only** pair with the full legacy fingerprint, and it
is the only pair the accidental write touched. The `mixed` rows are the
transition zone where the live path had already begun writing complete rows
before the dump. Every other pair was already written by the migrated
full-column writer.

### 6.1b The backup has been restored and verified in isolation

The backup's existence and contents were verified (§6.1), but that is not
the same as a working restore path. The dump was therefore **restored into
an isolated TimescaleDB container** — separate from production, on its own
network, discarded afterwards — and the restored state was compared
value-by-value against the live database.

```
manifest keys: 20526
live rows: 23135   restored rows: 22642
rows present in both: 22642
  of which in the changed manifest: 20526
  of which NOT in the manifest (no-op rows): 2116

CHANGED rows (in manifest) that differ live vs restored: 20526/20526
NO-OP  rows (not in manifest) that differ live vs restored:     0/2116
```

The restored database differs from live on **exactly** the 20,526 rows the
accidental write changed, and matches on all 2,116 no-op rows. It also
carries 19,920 rows with the legacy eight-column NULL fingerprint, against
**0** in the live table — the positive proof that the dump predates the
write.

**The restore path is therefore proven executable and exact, not merely
present.** The historical backfill is no longer blocked on it. Full
procedure, including two non-obvious preconditions, in
`docs/reports/restore-drill-preserved-backup-2026-10-22.md`.

### 6.2 The exact affected-row manifest

`scripts/build_affected_row_manifest.py` diffs the preserved dump against
the live table, value by value, for a given pair:
```
dump rows for BTCUSDT 1h: 22642
live rows for BTCUSDT 1h: 23134

=== manifest ===
  rows in dump but not live: 0
  rows in live but not dump: 492
  rows identical:            2116
  rows CHANGED:              20526
```

- **20,526 rows changed.** The affected span is `2024-01-09 07:00:00` →
  `2026-06-01 18:00:00` — the whole history that the legacy 751-bar writer
  had produced.
- **2,116 rows are byte-identical.** These were already written by the
  migrated full-column writer before the 03:00 dump, so the accidental
  write was a no-op for them.
- **492 rows are live-only.** They were written by the live path *after*
  the dump and are not part of the incident at all.

Columns that changed, by number of affected rows:

```
  sma_40                   20526     ema_slope_50             19207
  sma_60                   20526     atr_percentile           18970
  ema_8                    19920     ema_200                  18635
  ema_10                   19920     vwap                     18635
  ema_14                   19920     ema_50                   18633
  ema_21                   19920     macd_signal              18608
  ema_24                   19920     macd_hist                18606
  ema_30                   19920     macd                     18594
                                     volatility_percentile    18356
                                     ema_26                   10956
                                     trend_consistency         2204
                                     volume_regime / price_vs_* / rsi_slope  551
```

The six columns that changed on **every one** of the 20,526 rows are
`sma_40`, `sma_60` and the six EMAs the legacy writer omitted — the write
filled them in. The rest differ in *value* as well, because the legacy
writer used a 751-bar window and the migrated writer uses 200.

The full key list is committed at
`docs/reports/affected-rows-btcusdt-1h-2026-10-22.txt` (20,526 lines,
SHA-256 `d3c755e75e588624cb498f502160c681a55266952d760673acb34acb05db6539`),
so the affected set is exact and diffable rather than described.

### 6.3 A restore path that is actually tested

**This procedure was executed in full on 2026-10-22.** The preserved dump
was restored into an isolated TimescaleDB container, and the restored
`BTCUSDT 1h` pair was compared against the live table: it differs on
exactly the 20,526 manifest rows and matches on all 2,116 no-op rows
(§6.1b). The restore path is proven, not merely written.

The restore is not hypothetical. Restoring the affected pair is a bounded,
reversible operation:

1. Take a fresh timestamped dump of the current `indicators` table
   (the `backup_db.sh` script already does exactly this).
2. `pg_restore` the preserved dump into a **scratch database**, not into
   production.
3. `DELETE FROM indicators WHERE symbol='BTCUSDT' AND timeframe='1h'` in
   production and copy the pair back from the scratch database.
4. Re-run `scripts/verify_indicator_consistency.py` and
   `scripts/build_affected_row_manifest.py` to confirm the live table now
   matches the dump exactly.

Step 3 is reversible at every point because step 1 preserves the current
state, so the worst case is "the accidental write is undone", never "the
data is lost".

### 6.4 But a rollback is still not recommended

The pre-write state is now *recoverable*, and that changes the decision from
"impossible" to "possible but not advisable":

- The pre-write `BTCUSDT 1h` rows carry the **legacy 751-bar window** and
  the **eight NULL columns**. Restoring them would put back a data set that
  PR #204 exists specifically to eliminate, and that the live path no longer
  produces.
- The post-write rows are **exactly canonical**: 23,134 / 23,134 rows match
  a 200-bar recompute across all 36 value columns, with zero differences.
- The affected pair is the one pair in the table with no legacy residue.

Rolling back would move that pair from *correct* to *stale and incomplete*,
and would re-introduce the NULL fingerprint into the live database.

**Decision: no rollback. The backup is preserved anyway**, so the decision
is reversible if a future requirement makes the legacy state preferable.
This is a decision, not a constraint — and it is the opposite of the first
version of this report, which asserted the decision was forced.

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
| affected-row manifest | described in prose | `scripts/build_affected_row_manifest.py` — exact key list, diffable |
| backup verification | none | `scripts/verify_indicator_backup.py` — proves a dump holds the pre-write state |
| writer census | none | `scripts/classify_indicator_writers.py` — attributes every row to its writer |

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
  run. It is now *fully unblocked on the backup gate* -- a verified pre-write
  dump exists, the restore path has been **executed and proved exact** in
  an isolated database (§6.1b), and the dry-run guard is in place -- but it
  is still gated on a decision about
  whether to rewrite rows whose stored values are not reproducible from
  today's OHLCV (see §5b of the indicator-vintage diagnosis report).
  The census in `scripts/classify_indicator_writers.py` is the work list:
  124,042 rows still carry the legacy 751-bar window and 29,904 the
  251-bar window.
- The forming-candle defect was **root-caused to the minute** (see §5b of
  the indicator-vintage diagnosis report): the REST poller writes Binance's
  currently-forming kline, the indicator computer computes from it, and the
  row is never recomputed after the candle closes because the live path
  only ever writes the newest bar. It is now **fixed** (PR #208, merged as
  `622a3ac`): the indicator computer publishes the newest **closed** candle,
  so the published row is final and can no longer be frozen at a partial
  value. Verified against the live database after deployment — `SOLUSDT 4h`
  now publishes the closed `08:00` bar while the `12:00` bar is still
  forming, and the stored row matches a canonical 200-bar recompute (that
  pair was 399/400 divergent before the fix). The historical rows already
  written from forming candles stay wrong until the backfill rewrites
  them.

## 10. Reproduction

```bash
# 1. Does a given dump hold the PRE-WRITE state for this pair?
docker cp /opt/crypto-agent/scripts/verify_indicator_backup.py \
  crypto-agent-agent_sol_panic_block_paper-1:/app/scripts/
docker cp /opt/crypto-agent/backups/preserved/marketdata_20261022_030001.sql.gz \
  crypto-agent-agent_sol_panic_block_paper-1:/tmp/
docker exec crypto-agent-agent_sol_panic_block_paper-1 \
  python /app/scripts/verify_indicator_backup.py \
  /tmp/marketdata_20261022_030001.sql.gz

# 2. Exactly which rows differ between that dump and the live table?
docker exec crypto-agent-agent_sol_panic_block_paper-1 \
  python /app/scripts/build_affected_row_manifest.py \
  --dump /tmp/marketdata_20261022_030001.sql.gz \
  --symbol BTCUSDT --timeframe 1h

# 3. Which writer produced each row in the table?
docker exec crypto-agent-agent_sol_panic_block_paper-1 \
  python /app/scripts/classify_indicator_writers.py
```

The original column-by-column audit of the affected pair:

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
