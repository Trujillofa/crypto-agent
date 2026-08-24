# Math Models Roadmap

**Date:** 2026-08-23
**Status:** Design note only. No strategy code, no retunes, no paper/live-go from this document.

Which mathematical models to implement next in this repo, in what order, and where they plug into the existing engine. Models are **filters, sizing, and meta-labels** on top of the trusted backtest/execution path — not a second signal factory.

Related: [`RESEARCH_FRAMEWORK.md`](RESEARCH_FRAMEWORK.md), [`EXPERIMENT_AUTOPILOT.md`](EXPERIMENT_AUTOPILOT.md), [`RBI_AUTORESEARCH_LOOP.md`](RBI_AUTORESEARCH_LOOP.md).

---

## Current stack

| Piece | What is already true |
|-------|----------------------|
| Validation | WFO + bootstrap + concentration gates live in `src/backtest/experiment_autopilot.py` (`GateConfig`, `WfoWindow`). Drive with `scripts/experiment_autopilot.py` / `scripts/run_wfo.py`. |
| Primary technical stack | `TrendPullbackStrategy` plus MTF (`mtf_breakout`, `mtf_continuation`, `multi_timeframe_regime`, `regime_router`). Production agents on this stack are **paper / disarmed**; several configs emit zero fills at current aggregator thresholds. |
| Engine | `src/backtest/engine.py` evaluates at bar close and fills at **next open** (`fill_source="next_bar_open"`). Futures qty is step-truncated in `src/backtest/sizing.py`. |
| Costs | `src/backtest/cost_overrides.py` (`CostProfile`: `legacy` / `realistic` / `corrected`). Corrected book: fee `0.0004`, slip `0.0002`, `scaled_8h` funding. Rank only after the quality/cost-book PR is on `main` and this book is **frozen**. Model PRs must not edit cost overrides. |
| Funding | `FundingSettlement` via `src/features/reader.py`; v2 engine applies settlements and fingerprints them. Fees + funding move Sharpe; ignore them and the ranking is fiction. |
| Features | `src/features/technical.py` already has ATR, `atr_pct`, `volatility_percentile`, `ema_slope_50`, `trend_consistency`. **No ADX. No GARCH. No HMM.** |
| Sizing / stops today | Default is fixed `order_size_usdt`. `use_atr_sizing` is implemented on both the paper and backtest paths but disabled in every active agent config. Stops are ATR multiples (`sl_atr_multiplier` / `tp_atr_multiplier`). `RiskManager` then caps `max_position_pct`. `src/portfolio` tracks PnL; it does not size. |
| Regime today | Heuristic router on slope / vol percentile / trend consistency. That is the **baseline a new ADX or HMM flag must beat**, not a green light to add another strategy. |
| RL | `src/rl/agent.py` is a research `TradingGymEnv` (3-action long/flat, Sharpe-delta reward). Not wired into `src/main.py`. |
| Synthetic MC | `src/backtest/synthetic.py` already has a two-state Gaussian + Markov path and stress scenarios. Autopilot already bootstraps trade returns. |

A model that sizes a silent overlay (no WFO fills) cannot beat a baseline. First implementation PRs must attach to a **config that actually trades in sim** under the frozen cost book.

---

## Principles

1. **Classical first.** GARCH / EGARCH, then ADX or HMM, then LightGBM / CatBoost, then RL. Do not skip a family because a notebook used a fancier one.
2. **Overlay the trusted engine.** Next-open fills, frozen `CostProfile`, `RiskManager` caps, existing ATR exits. No parallel simulator.
3. **Train-only ranking.** Fit and choose hyperparameters on each WFO **train** window. Freeze the spec before looking at that window's test bars. Report test metrics; do not re-rank on them.
4. **Frozen costs.** Use the corrected book. Do not retune fee, slippage, or funding to make a model look good.
5. **No live-go from research paths.** Autopilot, WFO, and model scripts stop at a report. Paper compose and `trading_execution.enabled` are human gates in [`RESEARCH_FRAMEWORK.md`](RESEARCH_FRAMEWORK.md) Phase 5.
6. **One model family per implementation PR.** Beat the named baseline **after costs**. If it does not, close the family — do not add LightGBM on top of a failed GARCH.

---

## Now

### 1. GARCH / EGARCH vol → size and stops

**Job:** one-step variance forecast used for Kelly or vol-target size and for scaling ATR stops. **Not** a direction model. Do not rank on MSE of σ̂.

**Suggested modules**

- `src/features/vol_forecast.py` — fit GARCH(1,1) / EGARCH on train-window returns from `IndicatorReader.fetch_range`; emit σ̂ for the next bar.
- `src/risk/vol_sizer.py` — map σ̂ → `order_size_usdt` (vol-target or fraction-Kelly), then apply `RiskManager` caps. Do not bypass `max_position_pct` / daily loss.

**How it plugs in**

- Backtest: replace fixed notional inside `BacktestEngine` with the sizer output; scale `sl_atr_multiplier` / trailing distance by σ̂ / realized ATR, do not rewrite the exit engine.
- Autopilot: same `run_experiment_evaluation()` / `GateConfig`. Overlay is a size/stop function, not a new `BaseStrategy`. Fit per train window; freeze σ̂ path for that test window.
- First spike can be a script under `scripts/` that calls the existing evaluator. Promote into settings only after the family beats baseline.

**Baseline to beat (after costs):** same symbol/TF/strategy config, fixed `order_size_usdt`, current ATR SL/TP, `corrected` costs, next-open fills, funding on. Metrics: WFO Sharpe, max DD, turnover (trade count × round-trip), not forecast likelihood.

**Kill:** no after-cost Sharpe improvement on the frozen spec vs that baseline across ≥3 train windows, or turnover rises enough to erase the edge once fees + funding are applied.

### 2. ADX or HMM regime flag → trend vs mean-reversion config

**Job:** a single regime flag that **switches configs** of strategies we already trust (`trend_pullback` / MTF vs `mean_reversion` / `sentiment_mean_reversion`). Not a new entry thesis.

Prefer **ADX first** (no EM, lives next to ATR in `src/features/technical.py` as e.g. `adx_14`). Use a 2-state HMM only if ADX does not beat the current heuristic router on the same WFO protocol.

**Suggested modules**

- ADX: `src/features/technical.py` + writer/reader column (migration only after a cheap probe shows the flag moves after-cost Sharpe).
- HMM (if needed): `src/features/regime_hmm.py`. Do **not** reuse `fit_two_state_regime` in `src/backtest/synthetic.py` — that is for synthetic paths, not live classification.

**How it plugs in**

- Engine already joins MTF regime columns (`ema_slope_50_4h`, …). Add one flag the same way.
- Router reads the flag and selects the **existing** trend or MR config. No third strategy.
- Autopilot: two-cell overlay (flag on vs current `regime_router` heuristic) at frozen costs. Rank the flag rule on train windows only.

**Baseline to beat:** current `regime_router` / `multi_timeframe_regime` heuristic on the same stack and cost book.

**Kill:** flag does not improve after-cost train-window Sharpe vs the heuristic, or it only "wins" by silencing the account (zero trades).

---

## Next

### LightGBM / CatBoost meta-label on `src/features`

**Job:** given a BUY from the primary MTF / trend-pullback signal, predict **take vs skip** (or a size bucket). The primary signal remains the only source of entries.

- Features: existing `TechnicalIndicators` (and GARCH / ADX columns if those PRs landed). No new raw-OHLCV soup.
- Loss: Sharpe / turnover-aware (penalize fees + funding), not accuracy or AUC alone.
- Fit per WFO **train** window; rank model variants on that train window only; freeze before test.
- Plug-in: filter in front of the aggregator or executor. A meta-label cannot emit a BUY the primary strategy did not emit.

**Baseline:** primary signal with no meta-label, same costs and next-open fills.

**Kill:** no after-cost train-window Sharpe lift at comparable or lower turnover, or the model only fits one WFO window.

Do not open this PR until GARCH sizing and the regime flag have each had a yes/no after-cost result.

---

## Later

Keep `src/rl` for **execution and sizing** (Almgren–Chriss-style impact, inventory around the existing next-open / LOT_SIZE path). Do not grow `TradingGymEnv` into a raw signal generator.

HMM → PPO is allowed only if the **paper** regime flag (Now #2) already helps after costs. No PPO until that paper evidence exists.

---

## Skip

| Idea | Why not here |
|------|----------------|
| Cointegration / pairs as the main bet | Spot USDT majors share BTC beta. No pairs engine; prior multi-symbol WFOs already failed that way. Fragile as a primary book. |
| Expanding GBM Monte Carlo | Autopilot already bootstraps trades; `synthetic.py` already has two-state paths. Expanding GBM **before** the frozen cost + funding book is solid in sim manufactures confidence. |
| Transformers / sequence nets | No stack, no data contract. Revisit only if LightGBM meta-label loses after a fair train-only rank. |

---

## Implementation shape

| Family | Code (when a PR is justified) | Docs | Autopilot / WFO |
|--------|-------------------------------|------|-----------------|
| GARCH / EGARCH | `src/features/vol_forecast.py`, `src/risk/vol_sizer.py` | Lane brief in `docs/specs/`; report in `docs/reports/` | Overlay on existing evaluator; `corrected` `CostProfile`; fit on train, freeze for test |
| ADX / HMM | `src/features/technical.py` and/or `src/features/regime_hmm.py` | Same | Two-cell vs current heuristic router |
| LightGBM meta-label | Script first; later a filter module under `src/features/` or `src/strategy/` | Same | Train-window rank; primary signal still required |
| RL execution | Stay in `src/rl/` | Same | Not a signal campaign |

**Success for any implementation PR**

1. One family only.
2. Named baseline, frozen corrected costs, next-open fills, funding on.
3. Train-only ranking; test metrics reported, not used to pick the winner.
4. After-cost beat on Sharpe (and no DD / concentration blow-up vs `GateConfig`).
5. No live flag, no compose change, no strategy param retune "to help the model."

**Non-goals**

- New entry strategies or aggregator vote sources.
- Retuning `trend_pullback` / MTF / sentiment-macro thresholds.
- Editing `src/backtest/cost_overrides.py` inside a model PR.
- Paper → live, or any research script that sets `trading_execution.enabled: true`.
- Reviving closed RBI lanes by wrapping them in a model.
- Automating promotion. Reports only.

Implementation PRs still follow Gate 0/1 in [`RBI_AUTORESEARCH_LOOP.md`](RBI_AUTORESEARCH_LOOP.md): cheap probe `HAS_PULSE` before merging model code into the runtime path.
