# Strategy Research Framework

A formal protocol for developing, validating, and promoting trading strategies in this codebase. Follow this for every new strategy and every major revision to an existing one.

For the automated closed-loop operating model that wraps this framework with
autoresearch, stop rules, artifact handoff, and human approval points, see
[`RBI_AUTORESEARCH_LOOP.md`](RBI_AUTORESEARCH_LOOP.md).

For a PARKED contingency reference on GARCH / regime / meta-label overlays
(no implementation authorization; sealed program), see
[`MATH_MODELS_ROADMAP.md`](MATH_MODELS_ROADMAP.md).

---

## Overview

The pipeline has five phases. Each phase has a clear goal, a defined output, and explicit gate criteria before advancing.

```
Hypothesis → Exploration → Validation → Iteration → Promotion
```

Do not skip phases. Do not treat exploration results as validation. Do not promote
without meeting the gate criteria.

---

## Phase 1: Hypothesis

**Goal**: Write down the edge thesis before touching any code or data.

### Required outputs

A written statement covering:

1. **What is the edge?** — why should this strategy make money?
2. **When should it work?** — what market regime, timeframe, or condition?
3. **When should it fail?** — what conditions break the thesis?
4. **What is the entry signal?** — specific, not vague
5. **What are the exit conditions?** — both profit-take and stop-loss

### Example (acceptable)

> EMA crossover (20/50) with RSI confirmation on 4h BTCUSDT.
> Edge thesis: short-term momentum continuation after pullbacks in uptrend.
> Should work: trending markets, moderate volatility.
> Should fail: choppy/ranging markets, high-IV environments.
> Entry: EMA20 crosses above EMA50 + RSI < 60 (not overbought).
> Exit: trailing stop 2%, hard stop 3%.

### Example (not acceptable)

> "Try some indicator combinations and see what backtest looks like."

### Kill criterion

If you cannot write the hypothesis in plain language, the idea is not ready.
Do not proceed until you can.

---

## Phase 2: Exploration (Autoresearch)

**Goal**: Identify promising parameter regions. Not to find the best config.

### Tools

```bash
python scripts/autoresearch.py           # primary exploration
python scripts/autoresearch_universal.py  # multi-symbol sweep
```

### What to do

- Define a reasonable parameter grid based on the hypothesis
- Sweep broadly, then narrow to promising regions
- Look for **clusters of decent results**, not single outlier peaks
- Record the top 5–10 candidate configs for Phase 3

### What NOT to do

- Do not pick the single best result and call it done
- Do not run hundreds of configs hoping something works
- Do not optimize profit in isolation — check drawdown and trade count too

### Overfitting budget

| Parameter count | Max iterations |
|----------------|----------------|
| 1–3 params     | 200            |
| 4–5 params     | 500            |
| 6+ params      | Reduce params first |

More than 5 free parameters is a warning sign. Simplify the strategy before
sweeping further.

### Gate to Phase 3

- At least 3 configs show Sharpe > 0.5 in-sample
- Results form a cluster (not isolated spikes)
- Top configs make sense relative to the hypothesis
- Trade count ≥ 30 per config (below 30 is statistical noise)

---

## Phase 3: Validation (Backtesting)

**Goal**: Determine whether the candidates are robust or lucky.

### Tools

```bash
python scripts/run_backtest.py              # single-window simulator (not live)
python scripts/experiment_autopilot.py      # canonical WFO + gates (not live)
python scripts/run_config_search.py         # gated search; rank on train only
```

### Required tests (in order)

#### 3a. Walk-forward validation (mandatory)

WFO is the primary validation tool. Fixed-window backtests are insufficient alone.

```bash
python scripts/run_wfo.py BTCUSDT 1h 2021-01-01 2022-01-01 --config <config>
```

Acceptable result: OOS Sharpe ≥ 0.6 across ≥ 3 folds.

#### 3b. Parameter stability check

Run the top config plus its 8 nearest neighbors (±1 step each param).
At least 6 of 9 should be profitable OOS. If only the exact config works,
it is overfit — do not continue with it.

#### 3c. Out-of-sample period test

Reserve the most recent 20% of available data. Do not touch it during exploration.
Validate final candidates only on this held-out period.

Acceptable result: OOS Sharpe within 40% of IS Sharpe (e.g., IS=1.0 → OOS ≥ 0.6).

#### 3d. Multi-symbol test (if applicable)

Test on at least 2 other symbols in the same asset class.
A trend-following strategy that only works on one symbol is suspicious.

#### 3e. Monte Carlo stress test

Monte Carlo now runs inside the autoresearch pipeline (via experiment_autopilot
and run_autoresearch). A single bootstrap resampling pass (with replacement via
rng.choices on trade returns, same scheme as the prior bootstrap) produces both:

- bootstrap_p_loss_pct: probability a resampled path compounds to negative total
  return (existing gate under max_bootstrap_p_loss_pct / profile equivalents).
- The equity-path drawdown distribution: mc_drawdown_p95_pct (and p50, reported
  on ExperimentSummary), max peak-to-trough % computed by compounding each
  resampled path and tracking running peak drawdown.

mc_drawdown_p95_pct is always reported; the gate is optional and disabled by
default (GateConfig.max_mc_drawdown_p95_pct = 0.0 means do not enforce). A
threshold may be supplied later via --max-mc-drawdown-p95-pct once real
distributions have been observed on production-like runs. No hard pass/fail on
drawdown MC percentiles is applied until explicitly enabled.

### Validation scorecard

| Test | Pass criteria | Weight |
|------|--------------|--------|
| WFO OOS Sharpe | ≥ 0.6 | Critical |
| Parameter stability | ≥ 6/9 profitable | Critical |
| OOS period test | Sharpe ≥ 0.6× IS | Critical |
| Multi-symbol | ≥ 2/3 profitable | High |
| Monte Carlo P5 | pipeline bootstrap_p_loss_pct (P(loss) gate) + reported mc_drawdown_p95_pct (optional gate) | High |
| Trade count | ≥ 30 per period | Medium |
| Max drawdown | < 20% | Medium |

Failing any **Critical** test → back to Phase 2 or Phase 1.
Failing 2+ High tests → same.

### Gate to Phase 4

All 3 Critical tests pass. At most 1 High test fails. Document why.

---

## Phase 4: Iteration

**Goal**: Strengthen foundations. Not to find a better number.

### When to iterate

Iterate if each round improves one of:
- OOS robustness (wider parameter stability zone)
- Risk profile (lower drawdown, better Sharpe consistency)
- Behavioral clarity (behavior matches the thesis more precisely)
- Implementation quality (simpler code, fewer edge cases)

### When NOT to iterate

Stop iterating if:
- You are adjusting parameters to fix a specific losing period
- OOS keeps collapsing in the same way across attempts
- The strategy needs 5+ filters to be profitable
- Each "improvement" only changes one cherry-picked metric

### Iteration questions

Before each iteration round:

- Identify what specifically failed in the last validation.
- Decide whether the failure is a parameter issue or a logic issue.
- If the failure is logic-related, confirm the fix still matches the original
  hypothesis.
- After the fix, re-run the full Phase 3 scorecard.

### Kill criterion (stop iterating, abandon strategy)

Abandon the strategy if any of these are true:

1. Three consecutive Phase 3 attempts fail the same Critical test
2. The strategy requires more than 5 free parameters to pass validation
3. The fix required to pass validation contradicts the original hypothesis
4. OOS Sharpe has never exceeded 0.4 across any iteration
5. The strategy cannot be explained in plain language after iteration

Document the failure reason in `docs/reports/` before abandoning.

### Gate to Phase 5

Full Phase 3 scorecard passes. The strategy logic is stable (no changes needed
in the last iteration). Behavior matches the Phase 1 hypothesis.

---

## Phase 5: Promotion

### Step 1: Paper trading (minimum 2 weeks)

Deploy to paper mode. Monitor using `production_drift_sentinel`:

```bash
python scripts/production_drift_sentinel.py
```

Track:
- Signal frequency vs backtest expectation
- Win rate vs backtest
- Average trade duration vs backtest
- Any signals that fire but shouldn't (logic errors)

Acceptable result: live paper metrics within 30% of backtest equivalents.

### Step 2: Promotion gate

Before any live deployment, all of the following must be true:

| Criterion | Threshold |
|-----------|-----------|
| Paper Sharpe (annualized) | ≥ 0.6 |
| Paper win rate | Within 15% of backtest |
| Paper avg trade duration | Within 30% of backtest |
| Paper drawdown | < 15% |
| Signal rate | Within 50% of expected (not silent, not spamming) |
| Live/backtest slippage estimate | Documented and < 0.3% per trade |
| Human review | Explicit sign-off required |

### Step 3: Graduated live deployment

Start with minimum position size. Do not start with full allocation.

| Week | Max allocation |
|------|---------------|
| 1–2  | 25% of target  |
| 3–4  | 50% of target  |
| 5+   | 100% (if behavior matches) |

### Kill criterion (live)

Kill the live strategy immediately if:
- Drawdown exceeds 15% in any 30-day window
- Win rate drops below 35% over 20+ trades
- Behavior is unexplainable (fires on wrong conditions)
- Any execution bug found in live orders

---

## Decision Reference

### Quick diagnostic: which phase is the problem?

| Symptom | Likely cause | Action |
|---------|-------------|--------|
| IS good, OOS collapses | Overfit | Phase 2: reduce params, widen stability check |
| OOS inconsistent across folds | Regime sensitivity | Phase 1: tighten regime filter in hypothesis |
| Paper differs from backtest | Implementation bug or data issue | Phase 3: check signal logic and data alignment |
| Strategy needs many filters | Weak core thesis | Phase 1: rethink the edge |
| Profitable only on one symbol | Data snooping | Phase 3: multi-symbol test required |
| Behavior changed after live deploy | Market regime shift | Monitor; may require new hypothesis |

### Promotion decision matrix

| IS Sharpe | OOS Sharpe | Param stability | Decision |
|-----------|-----------|-----------------|----------|
| > 1.0 | > 0.8 | > 7/9 | Promote to paper |
| > 0.8 | > 0.6 | > 6/9 | Promote to paper (monitor closely) |
| > 0.8 | > 0.6 | < 6/9 | Iterate (stability problem) |
| Any | < 0.4 | Any | Abandon or rethink hypothesis |
| > 1.5 | < 0.3 | Any | Abandon (severely overfit) |

---

## File and Naming Conventions

| Artifact | Location | Naming |
|----------|---------|--------|
| Hypothesis notes | `docs/research/<strategy>.md` | Free-form |
| Autoresearch results | `docs/reports/<strategy>_sweep_<date>.txt` | Auto-generated |
| WFO results | `docs/reports/<strategy>_wfo_<date>.csv` | Auto-generated |
| Backtest reports | `docs/reports/<strategy>_backtest_<date>.md` | Manual summary |
| Abandonment notes | `docs/reports/<strategy>_abandoned_<date>.md` | Required on abandon |
| Paper validation runbook | `docs/PAPER_VALIDATION_REPORT.md` | Runbook for the daily paper-validation process; actual dated `.md`/`.json` artifacts are generated by `scripts/paper_validation_report.py` / `scripts/run_paper_validation_report.sh` |
| Math-model contingency (PARKED) | `docs/MATH_MODELS_ROADMAP.md` | PARKED reference for GARCH / HMM / LightGBM / RL overlays; no lane, no implementation authorization |

---

## Quick Reference Card

```
Phase 1  Hypothesis       → write the edge thesis first, always
Phase 2  Exploration      → find clusters, not peaks; ≤500 iters
Phase 3  Validation       → WFO + stability + OOS + Monte Carlo
Phase 4  Iteration        → strengthen foundations, not numbers
Phase 5  Promotion        → paper 2 weeks → graduated live

Kill criteria:
  Phase 2 → no clusters after 500 iters
  Phase 3 → 3× fails same Critical test
  Phase 4 → needs 5+ params or contradicts hypothesis
  Phase 5 → live drawdown >15% or unexplainable behavior

The finish line is robustness and trustworthiness.
Not temporary profitability.
```
