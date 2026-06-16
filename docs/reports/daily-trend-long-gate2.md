# Daily Trend Long — Gate 2 Report

**Verdict:** **FAIL**
**Date:** 2026-06-16
**Branch:** `feat/daily-trend-long-impl`
**Spec:** [daily-trend-long-surface-v0.md](../specs/daily-trend-long-surface-v0.md)
**Gate 1 predecessor:** [higher-tf-trend-following-probe-v0.md](higher-tf-trend-following-probe-v0.md) — HAS_PULSE

---

## §0 — DD-gate blocker resolution

**Decision:** Option A — added a new `daily_trend` gate profile (`max_drawdown_pct: 25.0`, all other thresholds identical to `standard`). Did **not** loosen `standard`.

**Dry WFO window** (BTCUSDT, SMA50, `config/settings.daily_trend_long.yaml`, 6mo train / 3mo test, range 2024-01-01 → 2026-06-01):

| Metric | Value |
|---|---:|
| `summary.max_drawdown_pct` (baseline, full period) | **26.94%** |
| Per-window OOS max DD | 7.67%, 11.93%, 13.10%, 18.06% |

**Rationale:** Per-window OOS drawdowns are in the teens (not <10%), while baseline full-period DD matches the Gate 1 probe (~26% BTC). The `standard` 10% gate would fail for structural reasons unrelated to edge. The `daily_trend` 25% profile is trend-appropriate; baseline DD still exceeds it on BTC/SOL, so gates remain honest.

**Dry-run artifact:** `research/dry-wfo-daily-trend-sma50-20260616-114555.json`

---

## Implementation summary

| Component | Path |
|---|---|
| Strategy | `src/strategy/daily_trend_long.py` — `DailyTrendLong`, SMA not EMA, long-only |
| Base config | `config/settings.daily_trend_long.yaml` — paper, `test_mode: true`, `1d`, BTC/ETH/SOL |
| Overlays | `config/autoresearch/overlays/daily-trend-long-sma{40,50,60}.yaml` |
| SMA columns | `migrations/008_add_sma_40_60.sql` + `src/features/` (sma_40, sma_60) |
| Gate profile | `scripts/run_autoresearch.py` → `daily_trend` |
| Tests | `tests/test_daily_trend_long.py` (7 tests) |

Surface stayed bounded to `{40, 50, 60}` — no grid widening, no added knobs.

---

## Gate 2 autoresearch results (`daily_trend` profile)

**Artifacts:** `research/daily-trend-long-gate2/` (9 runs: 3 symbols × 3 SMA windows)

| Symbol | SMA | Pass | WFO Ret% | WFO Sharpe | Max DD% | WFO Trades | Bootstrap P(loss)% | Profit Conc% |
|--------|-----|------|----------|------------|---------|------------|-------------------|--------------|
| BTCUSDT | 40 | **no** | -18.08 | -1.04 | 25.51 | 16 | 25.60 | 100.00 |
| BTCUSDT | 50 | **no** | -18.32 | -0.96 | 26.94 | 13 | 21.80 | 100.00 |
| BTCUSDT | 60 | **no** | -15.20 | -0.86 | 23.69 | 10 | 14.00 | 100.00 |
| ETHUSDT | 40 | **no** | +34.91 | -0.28 | 27.51 | 11 | 13.60 | 100.00 |
| ETHUSDT | 50 | **no** | +21.64 | -0.47 | 30.07 | 7 | 12.80 | 100.00 |
| ETHUSDT | 60 | **no** | +19.36 | -1.24 | 30.15 | 10 | 18.60 | 100.00 |
| SOLUSDT | 40 | **no** | -0.88 | +0.20 | 33.53 | 18 | 42.20 | 92.78 |
| SOLUSDT | 50 | **no** | -13.43 | -0.33 | 57.88 | 13 | 58.80 | 92.65 |
| SOLUSDT | 60 | **no** | -12.58 | -1.28 | 50.50 | 12 | 78.80 | 100.00 |

**Passes:** 0 / 9 (0 / 3 surface points × 3 symbols).

### Dominant failure modes

1. **`min_wfo_trades`** — aggregate OOS trades 7–18 vs gate 20 (daily bar cadence + limited WFO windows).
2. **`min_wfo_sharpe`** — negative mean OOS Sharpe on most runs (BTC all negative; ETH negative despite positive OOS return on some windows).
3. **`max_drawdown_pct`** — baseline DD exceeds 25% on 6/9 runs (all ETH/SOL, BTC sma40/50).
4. **`max_profit_concentration_pct`** — 92–100% on all runs (single-window dominance).
5. **`max_bootstrap_p_loss_pct`** — SOL runs exceed 25% (40–79%).

---

## Verdict rationale

**FAIL** per §3: no `{40, 50, 60}` surface point clears the `daily_trend` gate on any symbol.

This is **not** WEAK_EDGE (only sma=50 passing with 40/60 collapsing). Gate 1's in-sample SMA50 pulse did **not** survive walk-forward validation:

- BTC: negative OOS return on all three windows; sma=50 matches probe baseline DD but fails WFO Sharpe/trades.
- ETH: positive compound OOS return on all SMA values, but fails trades/Sharpe/concentration/DD gates.
- SOL: weakest symbol (per spec weighting) — large DD (34–58%), high bootstrap P(loss), negative OOS return.

**Action:** Close the lane. Do not widen the grid. Record in candidate ledger.

---

## RBI loop guard

```bash
uv run python scripts/rbi_loop_guard.py \
  --lane-brief docs/specs/daily-trend-long-surface-v0.md \
  --probe-verdict NO_PULSE \
  --last-result research/daily-trend-long-gate2/last_result.json \
  --pretty
```

(`rbi_loop_guard.py` accepts `HAS_PULSE|WEAK_EDGE|NO_PULSE`; Gate 2 **FAIL** maps to `NO_PULSE`.)

**Guard output:** `CLOSE_LANE` — `"cheap probe verdict is NO_PULSE, not HAS_PULSE"`.

---

## Reviewer checkpoints (§5)

| Check | Status |
|-------|--------|
| §0 DD-gate decision + dry-window reading recorded | ✅ |
| Strategy parity with probe (SMA, long-only) | ✅ |
| Surface bounded to {40,50,60} | ✅ |
| Honest PASS/WEAK_EDGE/FAIL with artifacts | ✅ FAIL |
