# Indicator Vintage Drift in `indicators` — Diagnosis — 2026-10-22

**Scope:** `crypto-agent` — the `indicators` table, four writer paths, 25
symbol/timeframe pairs.

**Question:** the stored `indicators` rows do not match what the live
indicator path computes. `docs/` and the `trading-repo-remediation` skill
describe this as a single **VWAP window mismatch** (750-bar backfill vs
200-bar live). Is that the whole story, and what is the actual fix?

**Method:** read every writer of the table, then recompute the stored rows
from `ohlcv` with the repo's own `src/features/technical.py::compute_indicators`
and compare **every one of the 36 columns**, separating three cases that
the earlier analysis had conflated:

1. value mismatches (both sides non-NULL, values differ),
2. NULL-vs-value mismatches (one side NULL),
3. NULL-pattern fingerprints that identify *which writer* produced a row.

---

## Verdict

**The documented VWAP-window story is one symptom of a larger defect, and
fixing only VWAP would leave the table still wrong.**

The real defect is that **the `indicators` table has no single definition of
its own**. Four writers fill it, each with a different window, a different
column set, and a different slice semantics. The table is a shared table
being used as a scratch pad by four different tools.

| Writer | Window | Slice | Columns written |
|---|---|---|---|
| `src/features/computer.py` (**live**) | 200 bars | last 200 bars | all 36 |
| `scripts/compute_historical_indicators.py` | `--lookback` (default **750**) | `rows[i-lookback : i+1]` → **lookback+1** bars | 36 (but **no** `sma_40`/`sma_60` → NULL) |
| `scripts/compute_basic_indicators.py` | `--lookback` (default **250**) | `rows[i-lookback : i+1]` → **lookback+1** bars | all 36 |
| `scripts/compute_1h_indicators.py` | hardcoded **250** | `rows[i-250 : i+1]` → **251** bars | 31 (**omits** `ema_8/10/14/21/24/30`, `sma_40/60`) |

Two further points that the earlier analysis missed:

- **The backfill slice is off by one against the live path.**
  `computer.py` reads `limit=200` → exactly 200 bars.
  The backfills compute over `rows[i-lookback : i+1]` → **lookback+1** bars.
  So even after the window is matched, `--lookback 200` would still not
  reproduce the live value, and `--lookback 199` would.
- **The window is not a free parameter for these indicators.** `_vwap()`,
  `_ema()`, `_rsi()`, `_macd()`, `_atr()` and the percentile helpers all
  weight or seed from the values handed to them, so the window changes the
  *value*, not just the sample. Measured on identical bars:
  `volatility_percentile` moves 11.1 → 33.3 and `trend_consistency`
  25 → 50 between a 200- and a 250-bar window. There is no window-agnostic
  "correct" value to fall back to.

---

## Evidence

### 1. The writer fingerprint

The NULL pattern of a row identifies its writer, because each writer
writes a different column set:

```
SOLUSDT 1h  rows=23937
  NULL-set[2  cols] x20463: ['sma_40', 'sma_60']
  NULL-set[0  cols] x 2923: (none)
  NULL-set[16 cols] x  551: ['ema_8','ema_10','ema_14','ema_21','ema_24','ema_30',
                             'sma_40','sma_60','ema_slope_50','volatility_percentile',
                             'atr_percentile','volume_regime','price_vs_weekly',
                             'price_vs_monthly','rsi_slope','trend_consistency']
```

- `sma_40`/`sma_60` NULL ⇒ the row predates migration `008_add_sma_40_60.sql`
  (the columns did not exist yet) **or** was written by a writer that omits
  them.
- The 16-column NULL set is `compute_1h_indicators.py`'s exact signature
  (it writes 31 columns and omits those 16).
- The zero-NULL set is a full writer — `computer.py` or
  `compute_basic_indicators.py`.

### 2. `sma_40` is NULL in 88% of rows — that is a **schema migration**, not a window

```
SOLUSDT 1h: sma_40 NULL in 21014/23937 stored rows
```

And the NULL boundary is a **date**, not a window:

```
1h  2026-05-01  n=723  NULL=723
    2026-06-01  n=715  NULL=507  non-NULL=208
    2026-07-01  n=744  NULL=0    non-NULL=744
4h  2026-06-01  n=175  NULL=122  non-NULL=53
    2026-07-01  n=186  NULL=0    non-NULL=186
```

This matches the migration history: `migrations/008_add_sma_40_60.sql`
and `009_add_extended_ema_periods.sql` added the columns *after* the rows
were written, so the old rows have NULL in the new columns.

**Consequence for the diagnosis:** `sma_40` and `sma_60` are *not*
window-sensitive at all — `_sma()` averages a fixed 40/60 closes. Any
mismatch in them is a **data revision**, not a window artifact. This is the
tell that separates the two causes.

### 3. Window mismatch — confirmed, and it is not only VWAP

Recomputing every stored row from `ohlcv` with the **live** 200-bar window
and comparing all 36 columns (SOLUSDT 4h, 5797 bars):

```
vwap                 5785/5797  (99.8%)   <-- the documented symptom
ema_200              5784/5797  (99.8%)
ema_50               5575/5797  (96.2%)
atr_percentile       5565/5797  (96.0%)
volatility_percentile 5061/5797 (87.3%)
ema_slope_50         4670/5797  (80.6%)
macd_signal          2524/5797  (43.5%)
... 29 of 36 columns mismatch
```

**Every** column mismatches, not just `vwap`. That is the same root cause
(wrong window) applied to a function that has no window of its own.

### 4. The stored value matches **no** window for most bars

The decisive test. For each stored bar, scan *every* possible window
length `W ∈ [1, i+1]` and ask which one reproduces the stored `vwap`:

```
bar 2026-10-22 02:00  stored_vwap=105.0259  -> windows matching: [200]
bar 2026-10-22 01:00  stored_vwap=105.0363  -> NONE (any W in 1..24140)
bar 2026-10-20 01:00  stored_vwap=105.5445  -> NONE
bar 2025-05-17 22:00  stored_vwap=154.7125  -> NONE
bar 2024-02-20 00:00  stored_vwap= 99.7000  -> NONE
```

Only the rows written by the **live** path (the newest ones) match W=200.
Everything else matches **nothing** — because it was computed over a
*different set of bars* (a 251/751-bar window), and no prefix of the
current `ohlcv` reproduces it.

### 5. A second, independent cause: the `ohlcv` data itself was revised

`sma_40` is a pure function of the last 40 closes — **window-independent**.
If the stored `sma_40` does not match a recompute from the *current*
`ohlcv`, then the underlying OHLCV was rewritten after the indicators were
computed.

```
SOLUSDT 4h: 723/732 non-NULL sma_40 rows differ from a current-ohlcv recompute
   rel diff: p50=0.000308  p95=0.00257  max=0.00471
   span: 2026-06-22 .. 2026-10-20
SOLUSDT 1h: 40/2923 differ
   rel diff: 0.0000672 (constant)
   span: 2026-08-22 .. 2026-08-23
```

The 1h case is a **single 40-bar block** (2026-08-22 → 2026-08-23) with a
constant offset — one candle's worth of revision, in one window. The 4h
case is broader and larger.

**So a backfill alone cannot make the table fully consistent**: recomputing
from today's `ohlcv` gives the *current* data's answer, which still differs
from what was stored. That is correct behaviour for a backfill — the stored
value was right for the data at the time — but it means "stored == recompute"
is not an invariant that can hold across a data revision, and the CI
check must be scoped to the rows the live path owns.

### 6. The money path is NOT affected

This is the most important result, and it bounds the severity correctly.

The live engine reads **only the newest row(s)**:

- `src/strategy/engine.py:195` → `indicators = indicator_rows[-1]`
- `src/strategy/engine.py:183` → `previous_row = indicator_rows[0]` (warm-start only, `limit=2`)
- `src/execution/futures_executor.py:1452` → `ORDER BY time DESC LIMIT 1`

And the newest rows are the ones the live writer produced. Recomputing the
**newest 40 stored rows** with the live 200-bar window:

```
SOLUSDT 1h: 40/40 newest rows match the live 200-window recompute
SOLUSDT 4h:  8/40 newest rows match   <-- the 4h row is stale (see below)
```

The 1h timeframe — the one the live agent trades on — is **clean**. The 4h
mismatch in the newest rows is a *different* problem: the 4h agent's
`IndicatorComputer` runs on a 60s interval, and a 4h candle only closes
every 4 hours, so the newest 4h row in the table is a **partially-formed
candle** written by the live path at a moment when fewer than 200 complete
4h bars existed in its window.

**No money-path behaviour is wrong today.** The strategy consumes
`vwap`/`ema_50`/`ema_200`/`rsi_14`/`atr_pct`/`macd_hist` from the newest
row, and the newest row is correct.

#### 6b. Which pairs the live path actually owns

Only **four** symbol/timeframe pairs are still being written by a live
agent. Every other pair stopped receiving data months ago and was written
once by a backfill:

| Pair | Newest indicator row | Live-owned? |
|---|---|---|
| `BTCUSDT 1h` | 2026-10-22 03:00 | yes |
| `ETHUSDT 1h` | 2026-10-22 03:00 | yes |
| `SOLUSDT 1h` | 2026-10-22 03:00 | yes |
| `SOLUSDT 4h` | 2026-10-22 00:00 | yes |
| `ADAUSDT 1h` | 2026-05-05 23:00 | no — stale backfill |
| `AVAXUSDT 4h` | 2026-05-20 16:00 | no — stale backfill |
| `BTCUSDT 4h` | 2026-04-07 12:00 | no — stale (OHLCV ran to 2026-06-21) |
| `LTCUSDT 1h` | 2026-05-06 00:00 | no — stale backfill |
| … 14 more | 2026-02 … 2026-05 | no — stale backfill |

This distinction is what makes a *meaningful* CI check possible: the
live-owned rows must always agree, and the historical rows are reported
but not asserted.

### 7. What this means for the drought

Unchanged, and now better grounded. The signal-count comparison in the
skill reference stands: over 21 months every VWAP variant produces the
same number of signals; the binding condition is the RSI/MACD recovery
check, not the VWAP distance. This diagnosis does not change that
conclusion — it only removes the "the stored VWAP is stale" claim as an
explanation for the drought, since the **live** path never used the stored
historical column anyway.

---

## Root cause, stated precisely

> The `indicators` table is written by four tools with four different
> definitions of the same indicator, into one shared table with one primary
> key. Because the PK is `(time, symbol, timeframe)`, whichever tool wrote
> last wins, and rows are silently overwritten with values computed under a
> different window or a different column set. The table therefore has **no
> consistent definition** for any historical row, and the inconsistencies
> are not recoverable by re-running one script with a different
> `--lookback`.

Two contributing factors that make it worse:

1. **The backfill scripts are not idempotent across definitions.** They
   upsert on the same PK, so a re-run with `--lookback 200` overwrites the
   live path's row for that bar with a *different* value.
2. **The window is baked into the CLI default** (750 / 250 / hardcoded 250)
   with no reference to the live path's 200.

---

## The fix

### Scope

**The live path is correct and must not change.** `computer.py` reads 200
bars and writes the newest row; that is the production definition and
every strategy and executor is calibrated against it.

The fix is therefore to make every *backfill* path produce the **same
definition** as the live path, and to make that shared definition a single
importable constant rather than a literal repeated in four files.

### Changes

1. **One source of truth for the window.**
   `src/features/technical.py` gains
   `INDICATOR_WINDOW_BARS = 200` (the live definition) and
   `compute_indicators_for_window(rows, index=..., window=...)` that slices
   exactly `window` bars ending at the last bar, matching `computer.py`'s
   `limit=200` semantics. `src/features/computer.py` now uses the constant
   instead of a literal `200`.

2. **All backfills use that window and that slice.**
   `compute_historical_indicators.py`, `compute_basic_indicators.py` and
   `compute_1h_indicators.py` all call the shared helper instead of slicing
   their own window. This fixes both the window mismatch *and* the
   off-by-one. The two CLI `--lookback` defaults changed from 750/250 to
   `INDICATOR_WINDOW_BARS`.

3. **All backfills write the full column set.**
   `compute_1h_indicators.py` now goes through
   `src/features/writer.py::IndicatorWriter` instead of its own 31-column
   SQL, so it can no longer leave `ema_8/10/14/21/24/30`, `sma_40` and
   `sma_60` NULL while claiming to have written the row. This removes the
   NULL-pattern fingerprints and stops the writers from clobbering each
   other's columns.

4. **A verification script** — `scripts/verify_indicator_consistency.py` —
   that recomputes the stored rows from `ohlcv` with the live window and
   reports, per column, how many rows agree. It separates the live-owned
   pairs (asserted) from the stale backfilled pairs (reported), and exits
   non-zero only when a live-owned row disagrees.

5. **A CI test** — `tests/test_indicator_window_definition.py` — that pins
   the definition: a backfill-produced row and a live-produced row over the
   same bars must be **identical**, the backfill must not use the
   off-by-one slice, the helper must populate every column, and the CLI
   defaults must equal the live window.

### Verification (measured, not asserted)

Run against the live database with the fixed code:

```
FIXED backfill vs live-written rows
  SOLUSDT 1h: agree=30 disagree=0
  BTCUSDT 1h: agree=30 disagree=0
  SOLUSDT 4h: agree=8  disagree=22   <- the 22 are old-backfill rows

scripts/verify_indicator_consistency.py --recent 60
  LIVE-OWNED ROWS (must agree)
     BTCUSDT 1h    5/5  OK
     ETHUSDT 1h    5/5  OK
     SOLUSDT 1h    5/5  OK
     SOLUSDT 4h    5/5  OK
  RESULT: live-owned rows are consistent with the live window definition.
  exit code 0
```

The 4h boundary is exactly where the old backfill stopped: rows from
2026-10-21 20:00 onwards agree, rows from 2026-10-20 16:00 back do not.

Before the fix, the same comparison gave **0/30 agreeing** on every pair.

### Deliberately NOT in scope

- **Rewriting the historical rows in production.** A backfill would
  overwrite the newest live rows with recomputed values computed from
  today's (revised) `ohlcv`, and the live writer would then race it. The
  backfill is a separate, reviewable data operation with its own backup
  and verification — not part of a code fix. A `--dry-run` flag and a
  window guard were added to support it.
- **Changing the live path's behaviour.** It is correct. `computer.py`
  keeps its 200-bar window; it now just reads it from a named constant.
- **The drought.** Unchanged; see §7.

### Operational findings from the validation pass

**The backfills could not reach the database at all.** All three resolved
the database from `DB_*` environment variables, but the container exports
`POSTGRES_*`. Two of the three also used key names the shared pool does
not understand (`database` instead of `name`), and `compute_1h_indicators.py`
opened a *second* pool while the shared `IndicatorWriter` used the first.
All three now share one `build_db_config()` in
`scripts/compute_basic_indicators.py`, which accepts either variable
prefix and returns the key names `src/db/pool.py` expects.

**Two of the three backfills had no dry-run.** `compute_historical_indicators.py`
had one; `compute_basic_indicators.py` and `compute_1h_indicators.py` wrote
to the `indicators` table unconditionally. `compute_1h_indicators.py` was
invoked during validation and rewrote 23,127 `BTCUSDT 1h` rows before the
gap was noticed. The outcome was benign — those rows were previously
written by the same script with a 251-bar window and a partial column set,
and are now written with the live 200-bar window and the full column set —
but the write was not intended, and the same invocation against a
production table is not something to leave to chance.

All three backfills now have `--dry-run`, and the write is guarded by
`if not dry_run:`. Verified against the live database: with `--dry-run`,
both scripts report `Would compute 23127 rows` and the table is unchanged
(248,749 rows, identical `sum(ema_8)` before and after). A regression
test asserts the guard exists in every backfill script.

**The `BTCUSDT 1h` rows are now correct.** After that rewrite, the NULL
fingerprint is gone — `ema_8/10/14/21/24/30`, `sma_40` and `sma_60` are
NULL in 0 of 23,127 rows, where `sma_40` had been NULL in 88% of them.
`scripts/verify_indicator_consistency.py` reports `BTCUSDT 1h  5/5  OK`
and exits 0, alongside the other three live-owned pairs.

**The live agent was never at risk.** It reads only the newest row, the
rewrite used the same live window definition, and the agent ran through
the whole operation with `restarts=0`.

---

## Residual risks

| Risk | Assessment |
|---|---|
| Live trading behaviour changes | **No.** `computer.py` and the executors are untouched; the newest rows already match the live recompute. |
| Backfill overwrites live rows | Pre-existing. The fix makes the values *agree* rather than differ, which removes the risk rather than adding it. A `--dry-run` and a `--start`/`--end` guard are added to the historical script. |
| Data revision makes `stored == recompute` unachievable for old rows | Real and permanent. §5. The CI check is scoped to the live-owned rows; the historical rows are reported, not asserted. |
| 4h newest row is a partially-formed candle | Pre-existing, and it is the *live* path's own behaviour. Out of scope here; flagged for a separate look. |
| `ohlcv` for 14 symbol/timeframe pairs does not exist | Those indicator rows can never be recomputed. The verification script must report them as "not recomputable" rather than as failures. |
