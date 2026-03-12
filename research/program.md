# Crypto-Agent Autoresearch Program

This folder is the bounded local workspace for autonomous strategy research.

## Scope

You are optimizing `crypto-agent` with historical backtests only.

You may change:

- `config/settings.autoresearch.yaml`
- one YAML overlay under `research/candidates/`
- `research/results.tsv` via the runner

You may not change:

- `config/settings.yaml`
- production deployment files
- secrets
- Docker or infrastructure config
- strategy Python code in the first phase

## Goal

Improve out-of-sample robustness, not just headline return.

Primary objective:

1. pass all gates
2. maximize walk-forward total return
3. break ties with walk-forward mean Sharpe
4. prefer lower drawdown, lower bootstrap loss probability, and lower profit concentration

## Fixed evaluator

The evaluator is:

```bash
./scripts/run_autoresearch.sh
```

This wrapper:

- resolves a frozen research config
- runs `scripts/experiment_autopilot.py`
- writes `research/run.log`
- updates `research/results.tsv`
- writes `research/last_result.json`

## Default workflow

1. Start from `config/settings.autoresearch.yaml`.
2. Create or update one overlay in `research/candidates/`.
3. Run:

```bash
./scripts/run_autoresearch.sh \
  --overlay research/candidates/<candidate>.yaml \
  --description "<short description>"
```

For sparse high-conviction trend strategies on shorter walk-forward windows, use the dedicated gate profile:

```bash
./scripts/run_autoresearch.sh \
  --overlay research/candidates/<candidate>.yaml \
  --description "<short description>" \
  --train-months 3 \
  --test-months 2 \
  --gate-profile sparse_trend_3_2
```

4. Read `research/last_result.json`.
5. Keep the candidate only if the new score beats the previous best.

## First-pass mutation surface

Stay inside these ranges first:

- strategy enable/disable combinations
- `strategy.aggregator.min_agreement`
- `strategy.aggregator.buy_threshold`
- `strategy.aggregator.buy_threshold_uptrend`
- `strategy.aggregator.sell_threshold`
- `strategy.global_trend_filter_enabled`
- `trading_execution.sl_atr_multiplier`
- `trading_execution.tp_atr_multiplier`
- `trading_execution.trailing_activate_atr`
- `trading_execution.trailing_offset_atr`
- individual strategy thresholds already present in YAML

Avoid broad random edits. Change a small coherent set of parameters per experiment.

## Guardrails

- Do not edit more than one hypothesis at a time.
- Do not optimize on full-period return alone.
- Do not reduce trade count to near zero just to dodge losses.
- Do not switch to code mutation until config-only search plateaus.
- Do not run indefinitely by default. Use bounded sessions.

## Suggested experiment order

1. Baseline run with no overlay.
2. Aggregator threshold sweep around the current baseline.
3. Strategy subset tests:
   - `trend_pullback` only
   - `rsi_reversal + bollinger_bounce`
   - `macd_histogram + cci_breakout`
4. Exit rule refinement with ATR stop/target parameters.
5. Per-strategy threshold refinement for the best surviving stack.

## Logging discipline

Use short descriptions in `results.tsv`, for example:

- `baseline`
- `raise buy threshold to 1.0`
- `trend pullback only`
- `wider atr target 5.0`

If a run crashes or times out, inspect:

- `research/run.log`

If a run completes, inspect:

- `research/last_result.json`

## Gate profiles

- `standard`
  - default research profile
  - expects broader sample size and tighter concentration
- `sparse_trend_3_2`
  - for `3/2` walk-forward evaluation of sparse trend-following strategies
  - tuned for low-frequency but high-conviction setups
  - still preserves the same drawdown, bootstrap, and non-negative OOS return constraints

## When to stop

Stop the current bounded session if:

- no candidate improves after several coherent tries
- multiple candidates fail the same gate for the same reason
- the search space needs a new strategy thesis, not more threshold tuning

At that point, summarize what changed, what improved, and which gate is still binding.
