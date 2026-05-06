# SOL 4h AI-Overlay Backtest

**Run timestamp:** 2026-05-06 UTC  
**Goal:** Test the recommended next step after sentiment-macro pair expansion failed: keep AI as a risk/regime overlay, but apply it to stronger non-sentiment `SOLUSDT 4h` strategy families instead of the failed `1h` sentiment mean-reversion template.  
**Execution mode:** Research/backtest only. No live services were restarted, no runtime config was changed, and no order-placement commands were run.

## Method

The run used a temporary Hetzner research script at `research/tmp_ai_overlay/run_ai_overlay_backtest.py` and persisted results to:

- `research/tmp_ai_overlay/sol_4h_ai_overlay_results.json`

The temporary script compared two `SOLUSDT 4h` strategy families:

- `simple_ma`: existing EMA crossover logic from `src/strategy/simple_ma.py`
- `trend_pullback`: existing sparse trend-pullback preset from `config/settings.sol_trend_pullback_sparse.yaml`

Because long-range dense LLM replay does not exist yet for this strategy family, the run did **not** pretend to have historical AI labels. Instead, it used deterministic AI/regime-proxy gates that approximate what an AI overlay should eventually decide from sentiment/news/regime context:

| Overlay | Meaning |
|---|---|
| `none` | Baseline strategy, no overlay |
| `panic_block` | Block BUYs only during a panic/overheated proxy regime |
| `risk_on_basic` | Allow BUYs only when trend, momentum, RSI, and volatility are broadly constructive |
| `risk_on_strict` | Stricter risk-on gate with stronger trend/momentum and extension limits |

This is an overlay simulation, not a claim that these filters are already a production AI model. The purpose was to answer whether an AI-like risk/regime gate is promising enough to prototype formally.

## Results

Range: `2024-02-03T04:00:00+00:00` through `2026-05-06T00:00:00+00:00`  
WFO protocol: 8 rolling windows, 3-month train / 2-month test, long-only.

| Strategy | Overlay | Trades | Return | Max DD | Sharpe | WFO trades | WFO return | WFO mean Sharpe | Profit conc. |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| `trend_pullback` | `none` | 4 | +8.28% | 3.77% | 0.69 | 3 | +7.47% | 0.50 | 67.42% |
| `trend_pullback` | `panic_block` | 4 | +8.28% | 3.77% | 0.69 | 3 | +7.47% | 0.50 | 67.42% |
| `trend_pullback` | `risk_on_basic` | 4 | +8.28% | 3.77% | 0.69 | 3 | +7.47% | 0.50 | 67.42% |
| `trend_pullback` | `risk_on_strict` | 2 | +3.86% | 4.67% | 0.36 | 2 | +3.86% | 0.36 | 62.52% |
| `simple_ma` | `none` | 38 | -4.16% | 17.16% | -0.09 | 24 | -3.70% | -0.43 | 66.87% |
| `simple_ma` | `panic_block` | 35 | +2.75% | 12.01% | 0.16 | 22 | +0.88% | -0.17 | 66.87% |
| `simple_ma` | `risk_on_basic` | 14 | -3.69% | 13.64% | -0.19 | 11 | -1.19% | -0.10 | 66.29% |
| `simple_ma` | `risk_on_strict` | 10 | -8.98% | 16.49% | -0.68 | 9 | -3.40% | -0.05 | 56.56% |

## Interpretation

The AI-overlay direction is **more promising than sentiment-macro pair expansion**, but it is not ready for promotion.

The useful signal came from `simple_ma + panic_block`: a lightweight risk-off blocker improved the full-period result from `-4.16%` to `+2.75%`, reduced max drawdown from `17.16%` to `12.01%`, and turned WFO return from `-3.70%` to `+0.88%`. That supports the design thesis: AI should be used to block bad environments, not to manufacture entries.

However, the improvement is still below promotion quality:

- WFO mean Sharpe remained negative at `-0.17`
- Max DD remained above the 10% standard gate at `12.01%`
- Profit concentration remained high at `66.87%`

The `trend_pullback` family remains the best raw shape, but it is too sparse. The baseline and light overlays produced only `3` WFO trades, below the sparse minimum of `4`, and profit concentration remained slightly above the prior sparse ceiling. The strict overlay reduced trade count further, so stricter AI gating is not useful for this sparse template unless entry logic is expanded.

## Decision

Do **not** promote any strategy from this run.

Do **not** continue sentiment-macro pair expansion under the current `1h` mean-reversion template.

Continue AI-overlay research, but target it as a **risk-off blocker** for `SOLUSDT 4h` technical strategies:

1. First candidate: `simple_ma + panic_block` style overlay, because it improved return and drawdown while preserving enough trades.
2. Second candidate: `trend_pullback`, but only after widening entries enough to produce more WFO trades without increasing concentration.

## Recommended Next Backtest

Run a narrow `SOLUSDT 4h simple_ma` neighborhood sweep with the `panic_block` overlay held constant:

- EMA pairs near the current candidate: e.g. `8/21`, `10/24`, `12/26`, `14/30`
- Exit model variants: current fixed exits vs executor-like ATR exits
- Panic-block thresholds: keep simple and conservative; reject variants that reduce trades below ~20 WFO trades or increase concentration

Promotion criteria for the next pass:

- WFO return positive and materially above `+0.88%`
- WFO mean Sharpe above `0.0`, ideally approaching `0.5`
- Max DD at or below `10%`
- Profit concentration below `60%`
- WFO trades remain high enough to avoid sparse one-off behavior
