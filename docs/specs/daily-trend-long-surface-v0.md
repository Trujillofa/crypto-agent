# Implementation Brief — Daily Trend-Following (long-only) Surface v0 — Gate 2

**Status:** Gate 1 HAS_PULSE → **Gate 2 (config-only autoresearch)** — implementation pending
**Author role:** planned by Claude (planner/reviewer); **to be built by Grok (builder)**
**Predecessors:**
- Gate 0/1 brief: [higher-tf-trend-following-probe-v0.md](higher-tf-trend-following-probe-v0.md)
- Gate 1 verdict: [../reports/higher-tf-trend-following-probe-v0.md](../reports/higher-tf-trend-following-probe-v0.md) — **HAS_PULSE** (daily SMA50 long-only beats buy-and-hold on 3/3 symbols, risk-adjusted)
- Research rules / banned surfaces: [../reports/research-reset-2026-06-06.md](../reports/research-reset-2026-06-06.md)

---

## 0. BLOCKER to resolve before any autoresearch run

The `standard` gate profile in `scripts/run_autoresearch.py` sets **`max_drawdown_pct: 10.0`**, and
`evaluate_gates` (`src/backtest/experiment_autopilot.py:278`) fails any candidate whose
`summary.max_drawdown_pct` exceeds it. The Gate 1 probe measured **full-window** drawdowns of
**26–37%** (BTC/ETH) and **40–52%** (SOL) for the daily SMA filter. A long-only daily-trend
strategy on crypto **cannot** clear a 10% absolute DD gate — it would FAIL for a structural
reason unrelated to edge.

**Decide before running (planner recommends option A):**

- **(A — recommended) Confirm WFO per-window OOS DD, then pick the gate.** WFO OOS windows are far
  shorter than the 2.4y probe window, so per-window OOS DD may be materially smaller. Run a single
  dry WFO window first and read `summary.max_drawdown_pct`. If it is realistically in the teens,
  define a **new trend-appropriate gate profile** (e.g. `daily_trend`, `max_drawdown_pct: 25.0`,
  all other thresholds identical to `standard`) rather than loosening `standard`.
- **(B) Use `standard` unchanged** only if (A) shows OOS DD really is <10% per window. Do not assume it.

Do **not** silently lower thresholds to force a pass. Record the chosen profile + the dry-window DD
reading in the result doc. This decision is itself a reviewable artifact.

---

## 1. Scope (what to build)

A **bounded, standalone, long-only** daily trend strategy + a config-only autoresearch surface.
Nothing attached to the SOL overlay or any existing aggregator.

### 1a. Strategy: `src/strategy/daily_trend_long.py`

- New `DailyTrendLong(BaseStrategy)` modelled on `src/strategy/simple_ma.py` (closest analog).
- Rule (exactly the probe's rule — do not embellish):
  - On each **daily** bar close: if `close > SMA(sma_window)` → emit **BUY** (target = long); else emit **SELL** to flatten (long-only, so "SELL" means exit to flat, never net short).
  - `HOLD` while SMA has insufficient history.
- **Single primary parameter:** `sma_window` (int). No second optimizable knob. No RSI/ATR/vol/session add-ons — adding any is scope creep and re-opens the "MA fishing" failure mode the runbook bans.
- Required indicator key: `sma_{sma_window}` plus `close_price`. Confirm the feature computer can
  produce `sma_50` (and the neighborhood values below) on the daily timeframe; if only EMA exists,
  add the SMA indicator in `src/features/` rather than swapping to EMA (the probe used SMA — keep parity).
- Export in `src/strategy/__init__.py`. Register the name so config `strategy.strategies` can select it.

### 1b. Bounded parameter surface (NOT a search-for-best grid)

The probe already identified **SMA50** as the pulse. Gate 2 tests **local robustness around 50**, not a
fresh hunt:

- `sma_window ∈ {40, 50, 60}` — a tight neighborhood centered on the known pulse.
- **Explicitly do NOT** add 100/200 back (they FAILED Gate 1) or expand the grid. If 40/50/60 are not
  jointly robust, that is a WEAK_EDGE/close signal — **do not widen the grid to find a winner.**

### 1c. Config + overlay

- Base config: a new `config/settings.daily_trend_long.yaml` (paper mode, `test_mode: true`), daily
  timeframe (`1d`), pairs `BTCUSDT, ETHUSDT, SOLUSDT`, strategy `daily_trend_long`.
- Overlay(s): one per `sma_window` value (config-only; reuse the autoresearch overlay mechanism the
  prior lanes used — see `feat/equity-path-drawdown-mc` / `run_autoresearch.py --overlay`).

### 1d. Tests: `tests/test_daily_trend_long.py`

- Long-only invariant: never emits a net-short target.
- `close > SMA` → BUY; `close < SMA` → flat; insufficient history → HOLD.
- Switch behaviour matches the probe semantics on a small synthetic series.

---

## 2. Validation command plan (Gate 2)

```bash
# 0. Resolve the DD-gate blocker first: one dry WFO window, read max_drawdown_pct
uv run python scripts/run_wfo.py --config config/settings.daily_trend_long.yaml ...  # single window

# 1. Config-only autoresearch, one run per sma_window, under the chosen gate profile
uv run python scripts/run_autoresearch.py \
  --config config/settings.daily_trend_long.yaml \
  --overlay <overlay-for-sma_window>.yaml \
  --gate-profile <standard|daily_trend> \
  --description "daily_trend_long sma=<N> gate2"

# 2. Guard the next allowed action from artifacts
uv run python scripts/rbi_loop_guard.py \
  --lane-brief docs/specs/daily-trend-long-surface-v0.md \
  --probe-verdict <PASS|WEAK_EDGE|FAIL> --pretty
```

## 3. Pass / fail criteria

Per the chosen gate profile (standard thresholds, DD threshold resolved in §0):
`min_wfo_trades ≥ 20`, `min_wfo_sharpe ≥ 0.5`, `max_bootstrap_p_loss_pct ≤ 25`,
`min_oos_return_pct ≥ 0`, `max_profit_concentration_pct ≤ 50`, DD within the resolved gate.

- **PASS (robust):** a strict majority of the {40,50,60} surface clears the gate across symbols → advance to Gate 3/4 (robustness / walk-forward stability, then forward-validation prep).
- **WEAK_EDGE:** only `sma=50` passes and 40/60 collapse → fragile; **close the lane**, record in the candidate ledger. Do **not** widen the grid.
- **FAIL:** no surface point clears the gate → close the lane.

## 4. Guardrails (from the runbook — do not violate)

1. **No grid widening** beyond {40,50,60}. The across-window fragility (SMA100 failed all, SMA200 only ETH) is exactly what WFO must clear — not something to engineer around.
2. **No added knobs** (vol filter, ATR stop, session gate, second MA). Single primary parameter only.
3. **No DD-gate loosening to force a pass.** Resolve §0 transparently and record the reading.
4. **Long-only.** Do not add short directionality.
5. SOL is the weakest symbol and the one live agents trade — weight BTC/ETH evidence accordingly when reading results.

## 5. Reviewer (Claude) checkpoints

Claude reviews before merge: (a) the §0 DD-gate decision + dry-window reading, (b) strategy parity
with the probe rule (SMA not EMA, long-only invariant), (c) surface stayed bounded to {40,50,60},
(d) the result doc states PASS/WEAK_EDGE/FAIL honestly with the gate artifacts attached.
