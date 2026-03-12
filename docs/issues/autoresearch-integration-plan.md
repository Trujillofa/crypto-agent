# Autoresearch Integration Plan

## What this plan is based on

I reviewed the actual contents of `/home/yderf/autoresearch`, not just the target artifact list.

What exists today in that folder:

- `program.md`: the human-maintained research prompt
- `train.py`: the only file the agent is expected to mutate
- `prepare.py`: the fixed evaluation and runtime harness
- `README.md`: workflow and keep/discard loop

What does **not** exist there today:

- `scripts/run_autoresearch.sh`
- `research/results.tsv`
- `research/run.log`
- `research/last_result.json`
- `docs/issues/autoresearch-integration-plan.md`

So for `crypto-agent`, the right move is not a literal port. The right move is to port the **pattern**:

1. one narrow mutable surface
2. one fixed evaluator
3. one machine-readable score
4. a keep/discard experiment loop
5. persistent research artifacts

## Requirements, challenged first

Before implementing anything, these requirements should be treated as mandatory:

- `autoresearch` must be local-only. It must never deploy, restart services, or touch production state.
- `autoresearch` must not edit `config/settings.yaml` directly during experiments.
- `autoresearch` must not mutate the whole repo on day one. Start with config-only search.
- `autoresearch` must optimize for robustness, not just headline return.
- `autoresearch` must evaluate against a fixed symbol, timeframe, date range, and gate set, otherwise runs are not comparable.

Requirements to reject:

- Do not copy the original "NEVER STOP" behavior as the default. In this repo, the safe default is bounded runs like `--max-runs 10`.
- Do not use raw full-period return as the main objective.
- Do not let the loop modify live trading settings, secrets, Docker files, or execution code.

## Existing pieces we should reuse

This repo already has most of the evaluator primitives:

- `scripts/run_backtest.py`: single-run backtest entrypoint
- `scripts/experiment_autopilot.py`: baseline + walk-forward + bootstrap + acceptance gates
- `scripts/run_config_search.py`: bounded parameter search
- `src/backtest/experiment_autopilot.py`: WFO, bootstrap, concentration, gate logic
- `docs/EXPERIMENT_AUTOPILOT.md`: operator-facing command docs

That means the missing piece is not "how to backtest." The missing piece is a thin **research control layer** that makes those tools behave like `autoresearch`.

## Recommended design

### 1. Use a bounded mutation surface

Phase 1 should mutate only generated strategy config overlays, not Python code.

Recommended mutable surface:

- `research/candidates/<run-id>.yaml`

These candidate files should override a frozen base config such as:

- `config/settings.autoresearch.yaml`

This keeps the loop safe, reviewable, and easy to revert. It also reuses the existing config-driven search work already present in the repo.

Phase 2 can optionally expand the mutation surface to one strategy family:

- `src/strategy/trend_pullback.py`
- matching strategy config block in the research config

But only after Phase 1 is stable.

### 2. Freeze the evaluator

The `prepare.py` equivalent for this repo should be the existing backtest validation stack, wrapped behind a single stable command.

Recommended wrapper:

- `scripts/run_autoresearch.sh`

That wrapper should call a Python runner, likely a new file such as:

- `scripts/run_autoresearch.py`

The runner should:

1. load the frozen research base config
2. apply one candidate overlay
3. run `experiment_autopilot`
4. compute a single scalar score from the result
5. write machine-readable artifacts
6. return success/failure for the loop controller

### 3. Define one scalar objective

Unlike `autoresearch`, this repo cannot optimize a single number like `val_bpb`. A trading strategy needs a gated objective.

Use this decision order:

1. candidate must pass all safety gates
2. among passing candidates, maximize `wfo_total_return_pct`
3. break ties with `wfo_mean_sharpe`
4. penalize `max_drawdown_pct`, `bootstrap_p_loss_pct`, and `profit_concentration_pct`

If no candidate passes, rank failures by a penalty score so the loop can still move toward viability.

A simple first scoring function is enough:

```text
if passes_gates:
  score = 100000 + wfo_total_return_pct * 100 + wfo_mean_sharpe * 10 - max_drawdown_pct
else:
  score = -(
    drawdown_excess * 5
    + bootstrap_excess * 3
    + concentration_excess * 2
    + max(0, min_trades_gate - total_trades)
  )
```

The exact coefficients are less important than one rule: the score must favor robust OOS behavior over in-sample return.

### 4. Standardize artifacts

Create a dedicated research workspace:

- `research/program.md`
- `research/results.tsv`
- `research/run.log`
- `research/last_result.json`
- `research/candidates/`
- `research/archive/`

Recommended `results.tsv` columns:

```text
timestamp	run_id	commit	score	status	passes_gates	symbol	timeframe	start	end	wfo_return_pct	wfo_mean_sharpe	max_drawdown_pct	bootstrap_p_loss_pct	profit_concentration_pct	total_trades	description
```

Use statuses:

- `keep`
- `discard`
- `crash`
- `timeout`

`last_result.json` should include the full evaluated metrics plus:

- candidate path
- resolved config path
- report paths
- duration seconds
- git commit
- exit status

### 5. Make the loop explicit

The control loop should look like this:

1. run baseline with the frozen research config
2. write baseline to `results.tsv`
3. generate one candidate overlay
4. evaluate it through the fixed runner
5. append metrics to `results.tsv`
6. keep the candidate only if `score > best_score`
7. archive discard candidates
8. stop at `--max-runs`, `--max-hours`, or operator interrupt

This is the key behavioral port from `autoresearch`.

## Implementation waves

## Wave 0: Safety and reproducibility

Deliverables:

- add `config/settings.autoresearch.yaml`
- freeze one symbol, timeframe, date range, and gate profile
- create `research/` folder layout
- add `.gitignore` rules for noisy research artifacts if they should stay local

Acceptance criteria:

- one command can reproduce the same baseline evaluation inputs
- no experiment touches `config/settings.yaml`
- no experiment requires production access

## Wave 1: Runner and scoring

Deliverables:

- add `scripts/run_autoresearch.py`
- add `scripts/run_autoresearch.sh`
- add score computation and artifact writing
- add JSON output mode to the autopilot path if needed

Acceptance criteria:

- one command writes `run.log`, `last_result.json`, and `results.tsv`
- crash and timeout states are captured cleanly
- baseline run produces a valid score
- gate profiles can encode strategy-family-specific evaluation standards without changing the underlying autopilot

## Wave 2: Candidate generation

Deliverables:

- implement bounded config mutation operators
- reuse parameter families from `scripts/run_config_search.py`
- support seeded reproducibility

The first mutation set should stay small:

- aggregator thresholds
- stop-loss / take-profit ATR multipliers
- trend filter toggle
- one strategy-family parameter group at a time

Acceptance criteria:

- candidate generator never emits invalid YAML
- generated candidates stay within hard safety ranges
- at least one baseline and one mutated candidate can be evaluated end to end

## Wave 3: Autonomous keep/discard loop

Deliverables:

- add loop controller with `--max-runs`
- add branch/tag naming for research sessions
- add archive and resume support

Acceptance criteria:

- loop can run multiple experiments without manual intervention
- best candidate is preserved automatically
- discarded candidates do not overwrite the winner

## Wave 4: Optional code mutation

Only start this wave if config-only search plateaus.

Deliverables:

- restrict code edits to one strategy module
- add pre-backtest quality gates such as targeted `pytest`
- require diff summaries in artifacts

Acceptance criteria:

- loop can mutate one approved Python file and still recover from failures
- test failures are treated as rejects before backtesting

## Minimal file plan

New files:

- `docs/issues/autoresearch-integration-plan.md`
- `research/program.md`
- `research/results.tsv`
- `scripts/run_autoresearch.py`
- `scripts/run_autoresearch.sh`
- `config/settings.autoresearch.yaml`

Likely updates:

- `scripts/experiment_autopilot.py`
- `src/backtest/experiment_autopilot.py`
- `docs/EXPERIMENT_AUTOPILOT.md`
- `tests/test_experiment_autopilot.py`
- new tests for runner and scoring

## Testing plan

Add tests for:

- score calculation and tie-breaking
- gate-to-status mapping
- `results.tsv` append behavior
- candidate generation bounds
- timeout/crash handling
- end-to-end runner behavior with mocked backtest results

Do not make the test suite depend on a live database for the new control-layer tests.

## Main risks

- **Non-reproducible data**: if the indicator database changes between runs, comparisons become noisy.
- **Overfitting**: an autonomous loop will exploit weak objectives quickly.
- **Too much mutation freedom**: letting the agent edit arbitrary strategy code early will create churn, not signal.
- **Unsafe branch behavior**: keep research on a dedicated local branch and never auto-merge.
- **Metric gaming**: if the score can be improved by reducing trade count to near zero, the loop will discover that immediately.

## Recommended first milestone

Implement only this thin slice first:

1. frozen research config
2. `run_autoresearch.py` wrapper around the existing autopilot
3. `results.tsv` + `last_result.json`
4. bounded config mutations
5. `--max-runs` keep/discard loop

That gets the core `autoresearch` behavior into `crypto-agent` with minimal new surface area and without giving an agent permission to rewrite the trading system.
