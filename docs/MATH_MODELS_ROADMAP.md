# Math Models — PARKED contingency / reference

**Date:** 2026-08-24 (rewritten from 2026-08-23 implementation-agenda draft)
**Status:** PARKED contingency / reference. **Creates no research lane. Authorizes no
implementation.**

This file is **not** a Now/Next schedule, not a Gate-0 brief, and not a KEEP path. It records
how GARCH / regime / meta-label / RL overlays would have to attach to the existing engine
**if** a separately scoped program is ever explicitly reopened. It does not say to implement
them.

**Program:** [research-consolidation-2026-06-23.md](reports/research-consolidation-2026-06-23.md)
(terminal; supersedes the 2026-06-19 consolidation). Public-data book **sealed**. Path 2 is
closed at Gate 0 on economics for the accessible operator profile.

Related (control plane, not a queue): [`RESEARCH_FRAMEWORK.md`](RESEARCH_FRAMEWORK.md),
[`EXPERIMENT_AUTOPILOT.md`](EXPERIMENT_AUTOPILOT.md),
[`RBI_AUTORESEARCH_LOOP.md`](RBI_AUTORESEARCH_LOOP.md).

---

## Seal (do not read this file as a reopen)

The crypto research program remains **terminal**. A public-data overlay on the same majors,
same cost book, and same execution path is not a differentiated advantage.

This document:

- creates **no** research lane;
- authorizes **no** implementation, probe, sweep, extract, paper path, or live-go;
- does **not** become active when someone wants "the next model."

**Reopening** requires explicit human authorization of a **separately scoped new program**
with a **named differentiated / non-public advantage**. Access to this note, merging this
PR, or having GARCH/HMM libraries available is not that advantage.

Gate 0/1 in [`RBI_AUTORESEARCH_LOOP.md`](RBI_AUTORESEARCH_LOOP.md) **validate an already
authorized lane**. They do **not** authorize reopening. A cheap-probe `HAS_PULSE` on a sealed
surface is still unauthorized work.

---

## Current stack (facts, not a build list)

| Piece | What is already true |
|-------|----------------------|
| Validation | WFO + bootstrap + concentration gates live in `src/backtest/experiment_autopilot.py` (`GateConfig`, `WfoWindow`). Drive with `scripts/experiment_autopilot.py` / `scripts/run_wfo.py`. |
| Primary technical stack | `TrendPullbackStrategy` plus MTF (`mtf_breakout`, `mtf_continuation`, `multi_timeframe_regime`, `regime_router`). Production agents on this stack are **paper / disarmed**; several configs emit zero fills at current aggregator thresholds. |
| Engine | `src/backtest/engine.py` evaluates at bar close and fills at **next open** (`fill_source="next_bar_open"`). Futures qty is step-truncated in `src/backtest/sizing.py`. |
| Costs | `src/backtest/cost_overrides.py` (`CostProfile`: `legacy` / `realistic` / `corrected`). Corrected book: fee `0.0004`, slip `0.0002`, `scaled_8h` funding. Model work must not edit cost overrides. |
| Funding | `FundingSettlement` via `src/features/reader.py`; v2 engine applies settlements and fingerprints them. Fees + funding move Sharpe; ignore them and the ranking is fiction. |
| Features | `src/features/technical.py` already has ATR, `atr_pct`, `volatility_percentile`, `ema_slope_50`, `trend_consistency`. **No ADX. No GARCH. No HMM.** |
| Sizing / stops today | Default is fixed `order_size_usdt`. `use_atr_sizing` is **implemented on both the paper and backtest paths but disabled in every active agent config**. Stops are ATR multiples (`sl_atr_multiplier` / `tp_atr_multiplier`). |
| Risk caps (do not over-claim) | Live/paper `RiskManager` can cap `max_position_pct`. **`BacktestEngine` does not call `RiskManager`. `BacktestConfig` has no `max_position_pct`.** The simulator does **not** already apply RiskManager caps. |
| Regime today | Heuristic router on slope / vol percentile / trend consistency. That is the **baseline a new ADX or HMM flag would have to beat**, not a green light to add another strategy. |
| RL | `src/rl/agent.py` is a research `TradingGymEnv` (3-action long/flat, Sharpe-delta reward). Not wired into `src/main.py`. |
| Synthetic MC | `src/backtest/synthetic.py` already has a two-state Gaussian + Markov path and stress scenarios. Autopilot already bootstraps trade returns. Do **not** reuse `fit_two_state_regime` for live classification. |

A model that sizes a silent overlay (no WFO fills) cannot beat a baseline. Comparable
exposure is a gate, not an afterthought — see [Winning by silence](#winning-by-silence).

---

## Conditional contracts (only if a new program is explicitly authorized)

The rest of this file is **not a queue**. If, and only if, a human reopens a separately
scoped program with a named differentiated advantage, any model-sizing / regime / meta-label
implementation MUST satisfy every contract below. Failure of any contract is a close, not a
tune.

### 1. Causal-inference contract

Fitted **parameters** freeze at each train boundary. Do **not** freeze a precomputed test
path of σ̂, regime, size, or meta-label scores.

For every test bar `t`:

- Forecast, regime, size, signal, and fill at `t` may use only observations available
  **strictly before** `t` (plus the frozen train-boundary parameters).
- **GARCH / EGARCH** may update filter **state** sequentially on realized test returns as
  they arrive. They must **not** refit parameters on test data.
- **HMM** inference on test bars must use **filtered** probabilities only. No full-sequence
  smoothing and no Viterbi state path over the test window (those use future bars).
- **Meta-label** features and labels must be causal. Where a label horizon overlaps a split
  boundary, apply purging and embargo.
- Require a **future-bar perturbation regression test**: changing bars **after** `t` must
  not alter the forecast, regime, size, signal, or fill **at or before** `t`.

Train-only ranking still applies: choose hyperparameters on each WFO **train** window;
report test metrics; do not re-rank on them.

### 2. Risk-cap integration contract

Do not claim the simulator already applies `RiskManager` caps. It does not.

Any future model-sizing implementation MUST:

- use a **shared pure cap policy** consumed by both paper/live risk and the simulator, **or**
- an **explicit frozen backtest cap configuration** (not a silent live-only cap);
- never bypass `max_position_pct` / daily loss by writing a parallel sizer that the
  simulator does not enforce;
- ship **simulator/paper parity tests** for position caps, LOT_SIZE truncation, minimum
  notional, and interaction with fixed-notional (`order_size_usdt` /
  `fixed_notional_usdt`).

### 3. Winning by silence

Trade count alone is insufficient. A sizing overlay can keep the same trades and shrink
notional toward zero, posting a flattering Sharpe on almost no exposure.

Any comparison against the frozen baseline MUST:

- require **comparable exposure / capital utilization** and a **minimum notional /
  exposure floor** (below the floor is a silent account, not a pass);
- report **gross exposure, average notional, capital utilization, turnover in notional
  terms, return, Sharpe, drawdown, and concentration** versus the frozen baseline.

Kill if the overlay only "wins" by silencing the account (zero trades **or** exposure
collapsed toward zero while trade count is retained).

### 4. Other standing constraints

- Overlay the trusted engine: next-open fills, frozen `CostProfile`, existing ATR exits.
  No parallel simulator.
- Frozen corrected costs. Do not retune fee, slippage, or funding to make a model look good.
- No live-go from research paths. Autopilot, WFO, and model scripts stop at a report.
- One model family per implementation PR. Beat the named baseline **after costs**. If it
  does not, close the family.
- Classical families before fancier ones **if** an authorized program implements overlays
  at all (GARCH/EGARCH, then ADX or HMM, then LightGBM/CatBoost, then RL execution/sizing).
  That is a technical preference under an authorized program, **not** an implementation
  schedule.

---

## Conditional implementation notes (not a Now / Next agenda)

These are plug-in constraints for an authorized program. They are **not** work to start.

### GARCH / EGARCH → size and stops (if authorized)

**Job:** one-step variance forecast used for Kelly or vol-target size and for scaling ATR
stops. **Not** a direction model. Do not rank on MSE of σ̂.

**Suggested modules (only after Gate 0/1 of an authorized program)**

- `src/features/vol_forecast.py` — fit GARCH(1,1) / EGARCH on **train-window** returns from
  `IndicatorReader.fetch_range`; freeze **parameters** at the train boundary; emit a
  causal one-step σ̂ for test bar `t` from pre-`t` observations only.
- `src/risk/vol_sizer.py` — map σ̂ → `order_size_usdt` (vol-target or fraction-Kelly), then
  apply the [risk-cap contract](#2-risk-cap-integration-contract). Do not assume
  `BacktestEngine` already calls `RiskManager`.

**How it would plug in**

- Backtest: replace fixed notional inside `BacktestEngine` with the sizer output; scale
  `sl_atr_multiplier` / trailing distance by σ̂ / realized ATR; do not rewrite the exit
  engine. ATR sizing already exists on paper and backtest paths (`use_atr_sizing`); do not
  rebuild that hook from scratch.
- Autopilot: same `run_experiment_evaluation()` / `GateConfig`. Overlay is a size/stop
  function, not a new `BaseStrategy`.
- First spike would be a script under `scripts/` that calls the existing evaluator.
  Promote into settings only after the family beats baseline under the exposure contract.

**Baseline to beat (after costs):** same symbol/TF/strategy config, fixed `order_size_usdt`,
current ATR SL/TP, `corrected` costs, next-open fills, funding on, **comparable exposure**.
Metrics: WFO Sharpe, max DD, **notional** turnover, gross exposure, average notional,
capital utilization, concentration — not forecast likelihood and not trade count alone.

**Kill:** no after-cost Sharpe improvement on the frozen spec vs that baseline across ≥3
train windows; turnover in notional terms rises enough to erase the edge once fees +
funding are applied; **or** the sizer "wins" by shrinking exposure toward zero.

### ADX or HMM regime flag (if authorized)

**Job:** a single regime flag that **switches configs** of strategies already trusted
(`trend_pullback` / MTF vs `mean_reversion` / `sentiment_mean_reversion`). Not a new entry
thesis.

Prefer **ADX first** (no EM, lives next to ATR in `src/features/technical.py` as e.g.
`adx_14`). Use a 2-state HMM only if ADX does not beat the current heuristic router on the
same WFO protocol **and** the HMM satisfies the filtered-only causal contract.

**Suggested modules**

- ADX: `src/features/technical.py` + writer/reader column (migration only after a cheap
  probe on an **authorized** lane shows the flag moves after-cost Sharpe at comparable
  exposure).
- HMM (if needed): `src/features/regime_hmm.py`. Do **not** reuse `fit_two_state_regime` in
  `src/backtest/synthetic.py`. Test-window inference: filtered probabilities only.

**Baseline to beat:** current `regime_router` / `multi_timeframe_regime` heuristic on the
same stack, cost book, and exposure.

**Kill:** flag does not improve after-cost train-window Sharpe vs the heuristic, or it only
"wins" by silencing the account (zero trades **or** collapsed exposure).

### LightGBM / CatBoost meta-label (if authorized)

**Job:** given a BUY from the primary MTF / trend-pullback signal, predict **take vs skip**
(or a size bucket). The primary signal remains the only source of entries.

- Features: existing `TechnicalIndicators` (and GARCH / ADX columns only if those already
  exist under the same authorized program). No new raw-OHLCV soup.
- Labels and features: causal, with purging/embargo across split boundaries.
- Loss: Sharpe / **notional**-turnover-aware (penalize fees + funding), not accuracy or AUC
  alone.
- Fit per WFO **train** window; rank model variants on that train window only; freeze
  parameters before test.
- Plug-in: filter in front of the aggregator or executor. A meta-label cannot emit a BUY
  the primary strategy did not emit.

**Baseline:** primary signal with no meta-label, same costs, next-open fills, comparable
exposure.

**Kill:** no after-cost train-window Sharpe lift at comparable exposure and comparable or
lower **notional** turnover, or the model only fits one WFO window.

Do not open this family unless GARCH sizing and the regime flag have each had a yes/no
after-cost result **inside the same authorized program**. That dependency is a technical
prerequisite, not a calendar.

### RL execution / sizing (if authorized)

Keep `src/rl` for **execution and sizing** (Almgren–Chriss-style impact, inventory around
the existing next-open / LOT_SIZE path). Do not grow `TradingGymEnv` into a raw signal
generator.

HMM → PPO is allowed only if a **paper** regime flag already helps after costs **inside the
same authorized program**. No PPO as a reopen vehicle.

---

## Skip (still skip, including after any future reopen)

| Idea | Why not here |
|------|----------------|
| Cointegration / pairs as the main bet | Spot USDT majors share BTC beta. No pairs engine; prior multi-symbol WFOs already failed that way. Fragile as a primary book. |
| Expanding GBM Monte Carlo | Autopilot already bootstraps trades; `synthetic.py` already has two-state paths. Expanding GBM **before** the frozen cost + funding book is solid in sim manufactures confidence. |
| Transformers / sequence nets | No stack, no data contract. Revisit only if LightGBM meta-label loses after a fair train-only rank **inside an authorized program**. |

---

## Implementation shape (conditional; not a PR queue)

| Family | Code (only if a PR is authorized) | Docs | Autopilot / WFO |
|--------|-----------------------------------|------|-----------------|
| GARCH / EGARCH | `src/features/vol_forecast.py`, `src/risk/vol_sizer.py` | New-program Gate-0 brief in `docs/specs/`; report in `docs/reports/` | Overlay on existing evaluator; `corrected` `CostProfile`; freeze **parameters** at train boundary; causal one-step on test |
| ADX / HMM | `src/features/technical.py` and/or `src/features/regime_hmm.py` | Same | Two-cell vs current heuristic router; HMM filtered-only on test |
| LightGBM meta-label | Script first; later a filter module under `src/features/` or `src/strategy/` | Same | Train-window rank; primary signal still required; purge/embargo |
| RL execution | Stay in `src/rl/` | Same | Not a signal campaign |

**Success for any future authorized implementation PR**

1. One family only, under an explicitly reopened program.
2. Named baseline, frozen corrected costs, next-open fills, funding on, comparable exposure.
3. Causal-inference contract held, including the future-bar perturbation test.
4. Risk-cap contract held, including simulator/paper parity tests.
5. Train-only ranking; test metrics reported, not used to pick the winner.
6. After-cost beat on Sharpe **and** no DD / concentration blow-up vs `GateConfig`, **and**
   not a silence/shrink win.
7. No live flag, no compose change, no strategy param retune "to help the model."

**Non-goals**

- New entry strategies or aggregator vote sources.
- Retuning `trend_pullback` / MTF / sentiment-macro thresholds.
- Editing `src/backtest/cost_overrides.py` inside a model PR.
- Paper → live, or any research script that sets `trading_execution.enabled: true`.
- Reviving closed RBI lanes by wrapping them in a model.
- Automating promotion. Reports only.
- Treating this PARKED note, or a Gate 0/1 pass, as authorization to start.

Even an authorized implementation PR still follows Gate 0/1 in
[`RBI_AUTORESEARCH_LOOP.md`](RBI_AUTORESEARCH_LOOP.md) **after** the human reopen. Gate 0/1
do not themselves reopen the sealed program.
