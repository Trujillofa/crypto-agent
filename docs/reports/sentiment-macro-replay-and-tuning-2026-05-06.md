# Sentiment-Macro Replay Baseline and Constrained Tuning

**Run timestamp:** 2026-05-06 UTC  
**Goal:** Validate the recommended next research path after expanded pair promotion failed: first test current live symbols with sentiment replay and executor-like exits, then run a small constrained tuning pass only if warranted.  
**Execution mode:** Research/backtest only. No live services were restarted, no runtime config was changed, and no order-placement commands were run.

## Setup

The replay/live-parity baseline used the production Hetzner indicator DB and the live sentiment-macro event log:

- Replay log: `data/event_log_sentiment-macro-bot.jsonl`
- Replay contents: 270 `sentiment_score` observations, all `xai_live`
- Coverage: `BTCUSDT`, `ETHUSDT`, `SOLUSDT`; 90 observations each
- Date range: 2026-03-27 12:32 UTC through 2026-04-27 00:58 UTC

The replay coverage summary was collected on Hetzner from `data/event_log_sentiment-macro-bot.jsonl`. The canonical autoresearch summaries were persisted on Hetzner in `research/results.tsv`, `research/archive/experiment-autopilot-20260506-005*.json`, and matching `research/resolved/settings-20260506-005*.yaml`; those research artifact directories are not tracked by git.

A temporary research config at `research/tmp_replay_baseline/settings.sentiment_macro.executor.yaml` enabled executor-like exits for backtesting:

```yaml
trading_execution:
  sl_atr_multiplier: 1.5
  tp_atr_multiplier: 3.5
  trailing_activate_atr: 1.5
  trailing_offset_atr: 1.0
  exit_rules:
    backtest_use_executor_exit_model: true
    backtest_ignore_signal_sells: false
    time_stop_minutes: 1440
```

`backtest_ignore_signal_sells` was intentionally left false. The goal was not to suppress strategy exits, but to add executor-like ATR SL/TP/trailing/time-stop behavior while still allowing the sentiment strategy's normal SELL/panic exits.

The canonical autoresearch wrapper was extended locally to pass replay options through to `scripts/experiment_autopilot.py`, because the existing wrapper could not replay sentiment even though `scripts/run_backtest.py` and `src/backtest/engine.py` already supported it.

## Current-Symbol Replay Baseline

Window tested: `2026-03-27T12:00:00+00:00` to `2026-04-27T01:00:00+00:00`.

| Symbol | Mode | Trades | Win rate | Return | Max DD | Sharpe | Profit factor | Replay coverage |
|---|---|---:|---:|---:|---:|---:|---:|---|
| `BTCUSDT` | replay | 12 | 33.33% | -5.85% | 5.92% | -5.99 | 0.21 | 124 hits / 591 misses / 590 stale |
| `ETHUSDT` | replay | 17 | 41.18% | -7.03% | 8.19% | -5.07 | 0.38 | 123 hits / 587 misses / 586 stale |
| `SOLUSDT` | replay | 13 | 30.77% | -5.33% | 5.37% | -4.18 | 0.43 | 125 hits / 589 misses / 588 stale |
| `BTCUSDT` | neutral control | 11 | 45.45% | -3.69% | 3.76% | -4.37 | 0.31 | n/a |
| `ETHUSDT` | neutral control | 14 | 35.71% | -7.71% | 8.30% | -6.21 | 0.20 | n/a |
| `SOLUSDT` | neutral control | 12 | 25.00% | -7.11% | 7.73% | -6.18 | 0.23 | n/a |

Replay did not recover the positive live/paper behavior. The replay log is real `xai_live` data, but the 24-hour max-age filter made most candle lookups stale because the observations are sparse relative to hourly candles. This means the current replay dataset is useful for plumbing validation, but not enough to prove a live-parity edge.

## Constrained Candidate Tuning

Because replay/live-parity did not validate the edge, tuning was intentionally limited to the three least-bad expansion candidates from the prior screen: `DOGEUSDT`, `BNBUSDT`, and `XRPUSDT`.

Four overlays were tested with canonical `scripts/run_autoresearch.py` and `gate-profile standard`:

- `executor_current`: current RSI/BB parameters plus executor-like exits
- `rsi30_bb010_executor`: RSI 30/70, BB distance 0.010, executor-like exits
- `rsi28_bb012_executor`: RSI 28/72, BB distance 0.012, executor-like exits
- `rsi25_bb015_executor`: RSI 25/75, BB distance 0.015, executor-like exits

All twelve runs were persisted to Hetzner `research/results.tsv` with descriptions ending in `_constrained_tune`.

| Symbol | Best overlay | Status | WFO Sharpe | WFO return | Max DD | P(loss) | Profit conc. | Trades |
|---|---|---|---:|---:|---:|---:|---:|---:|
| `DOGEUSDT` | `rsi25_bb015_executor` | discard | -1.23 | -24.16% | 52.41% | 100.00% | 63.63% | 111 |
| `BNBUSDT` | `rsi25_bb015_executor` | discard | -4.01 | -39.43% | 60.53% | 100.00% | 100.00% | 144 |
| `XRPUSDT` | `rsi25_bb015_executor` | discard | -3.04 | -34.75% | 55.92% | 100.00% | 88.93% | 120 |

The stricter RSI/wider-Bollinger variants reduced trade frequency and reduced losses versus the executor-current overlays, but they did not approach promotion quality. The best case, `DOGEUSDT rsi25_bb015_executor`, still failed every risk/quality gate except trade count.

## Decision

Do **not** promote new pairs into `agent_sentiment_macro`.

Do **not** continue tuning this exact `1h` sentiment-mean-reversion template across `DOGEUSDT`, `BNBUSDT`, or `XRPUSDT`. The constrained pass improved the shape by being more selective, but the best result remains far from the standard gate profile:

- Required WFO Sharpe: `>= 0.50`; best observed: `-1.23`
- Required WFO return: `>= 0.00%`; best observed: `-24.16%`
- Required max DD: `<= 10.00%`; best observed: `52.41%`
- Required bootstrap P(loss): `<= 25.00%`; best observed: `100.00%`
- Required profit concentration: `<= 50.00%`; best observed: `63.63%`

## Recommended Next Step

Stop pair-expansion work for the current sentiment-macro template until live/backtest parity is stronger.

The next useful research branch is a different thesis family rather than more symbol tuning:

1. Build denser historical sentiment replay if sentiment-macro remains a priority, so WFO uses real sentiment across months instead of sparse one-month samples.
2. In parallel, pivot new-pair discovery to non-sentiment templates that already showed better structure, especially the `SOLUSDT 4h` long-only moving-average/exit neighborhood and sparse trend-pullback variants.
3. Only revisit sentiment-macro expansion after a replay-backed current-symbol baseline shows positive out-of-sample behavior with controlled drawdown.
