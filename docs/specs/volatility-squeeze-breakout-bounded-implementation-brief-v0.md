# Volatility Squeeze Breakout Bounded — Implementation Brief v0

**Status:** approved for engineering — **no autoresearch campaign until this brief is implemented**
**Date:** 2026-06-05
**Prerequisite:** `docs/reports/volatility-squeeze-breakout-probe-2026-06-05.md` (BTC/ETH `HAS_PULSE`, SOL `WEAK_EDGE`)
**Surface spec:** `docs/specs/volatility-squeeze-breakout-bounded-surface-v0.md`

---

## Objective

Add a **bounded** BTC/ETH 1h volatility-squeeze autoresearch lane that matches the
cheap probe’s best forward horizon (12h), then run a **narrow** discovery campaign
before any bootstrap=1000 or deploy.

**Out of scope for v0:** SOLUSDT, overlay stacks, gate changes, live agents.

---

## Evidence summary (prod probe)

| Symbol | Events | 12h mean | 24h mean | 48h mean | Concentration | Verdict |
|--------|--------|----------|----------|----------|---------------|---------|
| BTCUSDT | 176 | +0.02% | -0.06% | -0.19% | 6.4% | `HAS_PULSE` |
| ETHUSDT | 241 | +0.15% | -0.20% | -0.51% | 5.1% | `HAS_PULSE` |
| SOLUSDT | 269 | -0.03% | -0.23% | -0.27% | 5.8% | `WEAK_EDGE` |

**Design implication:** default hold must be **~12 bars (12h on 1h)**, not 30–40 bars.
Longer holds align with **negative** probe forward means — do not optimize toward
`volatility_squeeze_standalone` hold bands without rebasing on 12h.

Edge is **thin** (BTC +0.02% crude mean). Treat as a hypothesis test, not a proven edge.

---

## Hypotheses (testable in WFO)

| ID | Statement | Falsified if |
|----|-----------|--------------|
| H1 | BB-width compression → long breakout with momentum has **positive OOS** on BTC/ETH 1h when held ≤12h | 0 `standard` passes across 80 runs |
| H2 | Bounded time-stop improves risk vs unbounded `volatility_squeeze_standalone` | All passes fail DD or concentration worse than Wave-7 ETH bounded |
| H3 | BTC/ETH entries are **timing-independent** enough vs SOL overlay + sentiment-macro | Overlap check fails after any near-miss |
| H4 | SOL exclusion is correct | N/A for v0 — SOL excluded by probe + correlation |

---

## Scope

| In scope | Out of scope |
|----------|--------------|
| BTCUSDT, ETHUSDT | SOLUSDT (probe fail + live SOL stack) |
| 1h timeframe | 4h MTF, overlays with 5-strategy stacks |
| Long-only (`VolatilitySqueezeStrategy`) | Short squeeze breakdown (Option E) |
| Standalone single strategy | `volatility_squeeze_overlay` |
| Autoresearch family `volatility_squeeze_bounded` | Retuning funding-primary or RS |
| bootstrap=100 discovery | bootstrap=1000 until `promotion_candidate` |
| Paper path after gates + overlap | Live deploy without overlap pass |

---

## Strategy: reuse, do not fork

Use existing **`VolatilitySqueezeStrategy`** (`src/strategy/volatility_squeeze.py`).
Entry logic already matches the probe:

- BB width percentile &lt; `squeeze_percentile` over `squeeze_lookback`
- Close &gt; SMA(20)
- Momentum &gt; 0
- `atr_pct` ≥ `min_atr_pct`

**Do not** add `volatility_squeeze_bounded.py` unless backtest proves internal exits
fight executor `time_stop` (unlikely). Prefer config + `exit_rules` overlay.

### Default config (campaign center)

```yaml
strategy:
  strategies:
    - name: volatility_squeeze
      config:
        squeeze_lookback: 50
        squeeze_percentile: 0.20
        sma_period: 20
        momentum_period: 10
        min_atr_pct: 0.005
        max_hold_bars: 12          # probe-aligned primary hold
        atr_trail_multiplier: 3.5  # secondary; wide so 12h stop dominates
trading_execution:
  sl_atr_multiplier: 2.2
  tp_atr_multiplier: 3.2
  exit_rules:
    backtest_use_executor_exit_model: true
    time_stop_minutes: 720       # 12h — matches probe forward window
```

### Exit precedence (document in tests)

1. **Executor time stop** at 720m (`time_stop` close reason) when `exit_rules` set.
2. **Strategy** `max_hold_bars` at 12 (bar-based time stop).
3. **Strategy** ATR trailing stop after `bars_held > 2` (risk cap only).

Campaign search should keep `max_hold_bars` and `time_stop_minutes` **coherent**
(same 12h intent). Example pairs: `(12, 720)`, `(10, 600)`, `(14, 840)`.

---

## Autoresearch family: `volatility_squeeze_bounded`

### Registration

1. Add `"volatility_squeeze_bounded"` to `ALLOWED_FAMILIES` in `scripts/autoresearch_loop.py`.
2. Implement `elif family == "volatility_squeeze_bounded":` using `_standalone_strategy_overlay`
   + `exit_rules` (same pattern as `range_reversion_bounded` / `funding_normalization_standalone`).

### Search space (v0 — tight)

| Parameter | Range | Notes |
|-----------|-------|-------|
| `squeeze_lookback` | 40, 50, 60 | Probe default 50 |
| `squeeze_percentile` | 0.15 – 0.28 | Wider = more events |
| `momentum_period` | 8, 10, 12 | Probe default 10 |
| `min_atr_pct` | 0.004 – 0.009 | Probe default 0.005 |
| `max_hold_bars` | 10, 12, 14, 16 | **Center 12** |
| `atr_trail_multiplier` | 2.8 – 4.0 | Wide trail; time stop primary |
| `time_stop_minutes` | 600, 720, 840 | Must track `max_hold_bars` |
| `sl_atr_multiplier` | 2.0 – 2.6 | Executor protective |
| `tp_atr_multiplier` | 2.8 – 4.0 | |
| `buy_threshold` | 0.40 – 0.70 | Standalone aggregator |

**Do not** sample `max_hold_bars` ∈ {20, 30, 40} (legacy `volatility_squeeze_standalone`).

### Unit test

Add `test_autoresearch_loop_volatility_squeeze_bounded_has_time_stop` mirroring
`test_autoresearch_loop_range_reversion_bounded_has_time_stop`:

- `time_stop_minutes` ∈ [600, 840]
- `max_hold_bars` ≤ 16
- strategy name == `volatility_squeeze`

---

## Campaign plan (after code lands)

Two **separate** lanes (do not mix symbols in one overlay sweep):

| Lane | Symbol | Runs | Output dir slug | Script |
|------|--------|------|-----------------|--------|
| F-BTC | BTCUSDT | 40 | `w10-btc-1h-vol-squeeze-bounded` | `run_option_f_vol_squeeze_campaign.sh BTCUSDT` |
| F-ETH | ETHUSDT | 40 | `w10-eth-1h-vol-squeeze-bounded` | `run_option_f_vol_squeeze_campaign.sh ETHUSDT` |

Environment:

```bash
export GATE_PROFILE=standard
export BOOTSTRAP=100
export MAX_RUNS=40
export FAMILIES=volatility_squeeze_bounded
```

Run on Hetzner via `run_autoresearch_campaign_remote.sh` (same pattern as
`scripts/run_b_sol_funding_norm_campaign.sh`).

**Stop conditions (per lane):**

- After 40 runs: if **0** `standard` passes → log `REJECT` in ledger, do not merge lanes.
- If any pass: run `promotion_candidate` filter on best artifact before scheduling b=1000.
- **Never** lower gates to get a pass count.
- **Never** add SOL to “recover” sparse BTC.

Optional combined review after both lanes: if ETH passes and BTC fails, still run
overlap on ETH-only winner — do not promote BTC by sympathy.

---

## Promotion path (unchanged)

1. Discovery @ bootstrap=100, `GATE_PROFILE=standard`.
2. Best overlay must set `promotion_candidate` / `eligible_for_bootstrap_1000`.
3. Single bootstrap=1000 revalidation on winner only.
4. **Entry overlap** vs:
   - `agent_sol_1h_trend_pullback_overlay_live`
   - `agent_sentiment_macro`
5. Tracked paper → small live (separate `AGENT_ID`, futures config review).

Reference gates: `docs/reports/autoresearch-candidate-ledger.md` only — do not duplicate numbers here.

---

## Engineering checklist

| # | Task | File(s) |
|---|------|---------|
| 1 | Add autoresearch family | `scripts/autoresearch_loop.py` |
| 2 | Family unit test | `tests/test_autoresearch.py` |
| 3 | Campaign launcher | `scripts/run_option_f_vol_squeeze_campaign.sh` |
| 4 | Ledger row template | `docs/reports/autoresearch-candidate-ledger.md` (after campaign) |
| 5 | Confirm strategy registered | `src/main.py`, `src/strategy/__init__.py` (already `volatility_squeeze`) |
| 6 | Smoke one overlay locally | `autoresearch_loop.py --family volatility_squeeze_bounded --dry-run` if supported |

**Not required for v0:** new DB columns, new indicators, compose service, prod deploy.

---

## Risk notes

| Risk | Mitigation |
|------|------------|
| Probe edge too thin → 0 WFO passes | Expected; close lane like Wave 9 funding |
| Internal trail exits before 12h | Wide `atr_trail_multiplier`; log exit reasons in backtest |
| BTC/ETH correlated drawdown | Two symbols still better than third SOL leg |
| Duplicate live squeeze exposure | No SOL; overlap script before promotion |
| Confusion with `volatility_squeeze_standalone` | Separate family name + hold caps in brief |

---

## Success / failure criteria

**Implement brief successfully when:** family generates valid overlays, tests pass, campaign script runs on Hetzner without import errors.

**Research success (candidate #2):** ≥1 lane with `standard` pass + `promotion_candidate` + overlap OK + b=1000 pass.

**Research failure (still valuable):** 0/80 combined passes → ledger `REJECT`, keep probe tooling, pick next first-principles brief (session router, etc.). Do not retune SOL or funding-primary.

---

## References

- Probe report: `docs/reports/volatility-squeeze-breakout-probe-2026-06-05.md`
- Probe script: `scripts/probe_volatility_squeeze_breakout.py`
- Existing standalone family (do not copy hold bands): `volatility_squeeze_standalone` in `autoresearch_loop.py`
- Bounded exit pattern: `range_reversion_bounded`, `funding_normalization_standalone`
- Portfolio options index: `docs/reports/candidate-search-options-2026-06-04.md`
