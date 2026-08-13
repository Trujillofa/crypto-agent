# RBI Autoresearch Loop

This document defines the closed-loop operating system for pursuing profitability in
`crypto-trading-agent`.

RBI means:

1. **Research**: generate a falsifiable edge thesis and cheap probe.
2. **Backtest**: validate through WFO, bootstrap, overlap, and robustness gates.
3. **Implement**: ship only the minimum code/config needed, then paper or live-forward validate.

The loop exists to let agents do repetitive exploration while keeping promotion decisions
strict, auditable, and hard to game.

## Current Portfolio Truth

As of 2026-06-09, the evidence says:

- `agent_sol_1h_trend_pullback_overlay_live` is the only promotion-grade technical agent.
- `agent_sentiment_macro` is an independent small live experiment, not a clean backtest pass.
- `agent_sol_sparse` and `agent_sol_panic_block_paper` are paper/research only.
- New campaigns are paused unless a new primitive first shows a cheap-probe `HAS_PULSE`.

Do not start from the assumption that more agents are needed. Start from the assumption that
more bad agents increase correlated drawdown. Count is secondary to edge quality.

## Loop Architecture

```text
VISION / constraints
  -> Lane brief
  -> Cheap probe
  -> Autoresearch campaign
  -> Standard WFO/bootstrap gate
  -> Promotion-candidate gate
  -> Bootstrap=1000 revalidation
  -> Entry-overlap and portfolio-impact check
  -> Paper / live-forward validation
  -> Small live deployment or reject/close
  -> Ledger update
```

The loop is closed because every stage writes artifacts that the next stage must read.
The loop is bounded because every stage has kill criteria.

## State Files

Agents must treat these as the control plane:

| File | Purpose |
|---|---|
| `docs/reports/research-reset-2026-06-06.md` | Current stop rules, banned hypotheses, allowed next families |
| `docs/reports/autoresearch-candidate-ledger.md` | Canonical campaign and promotion ledger |
| `docs/RESEARCH_FRAMEWORK.md` | Phase model and validation philosophy |
| `docs/EXPERIMENT_AUTOPILOT.md` | Autopilot/autoresearch command reference |
| `research/results.tsv` | Local autoresearch result ledger |
| `research/last_result.json` | Latest local child run result |
| Remote `/opt/crypto-agent/research/**/results.tsv` | Production DB-backed campaign ledgers |
| Remote `/opt/crypto-agent/research/**/last_result.json` | Latest production DB-backed campaign result |

If these files disagree, prefer the most recent committed report plus the raw `results.tsv`
from the campaign being evaluated.

## Agent Roles

Use separate agent roles conceptually, even if one Codex instance performs them.

| Role | Allowed work | Not allowed |
|---|---|---|
| Supervisor | Read ledgers, choose next lane, enforce stop rules | Tune parameters directly |
| Researcher | Write lane brief, define cheap probe, inspect data coverage | Promote configs |
| Probe Runner | Run read-only probe scripts and classify `HAS_PULSE`, `WEAK_EDGE`, `NO_PULSE` | Write strategy code |
| Autoresearch Runner | Run bounded config-only sweeps | Change live configs or production services |
| Validator | Check WFO/bootstrap, concentration, overlap, portfolio risk | Accept single-window winners |
| Implementer | Add minimal code/config for a passed lane | Broaden scope or add knobs without evidence |
| Operator | Deploy paper/live, monitor drift, rollback | Increase size without milestone evidence |

## Hard Gates

### Gate 0: Lane Brief

Before code or sweeps, create or update a short brief in `docs/specs/` or `docs/reports/`:

- edge thesis
- why this primitive is different from banned lanes
- expected regime
- expected failure mode
- signal definition
- **null model the probe will beat** — name the control (shuffled/phase-randomized
  returns, shuffled-sign/permuted feature) and the `p_adj`/concentration bar up front;
  see Gate 1 "Mandatory baseline: the random-walk null". A brief with no stated null is
  incomplete.
- target symbol/timeframe
- target trade density
- independence expectation vs live agents
- validation command plan
- **`strategy.global_trend_filter_enabled` explicit choice** (`true` or `false`) —
  state why the lane wants the global EMA200 BUY filter on or off; do not inherit
  silently from `base.yaml`. The backtest audit logs the resolved filter state, buffer,
  and source at run start.

Kill immediately if the brief is just "try parameters".

### Gate 1: Cheap Probe

Run a read-only probe before any strategy class or autoresearch campaign.

Allowed examples:

```bash
uv run python scripts/probe_basis_premium.py
uv run python scripts/probe_funding_normalization.py
uv run python scripts/probe_volatility_squeeze_breakout.py
uv run python scripts/probe_range_break_continuation.py
```

Probe classification:

| Label | Meaning | Next action |
|---|---|---|
| `HAS_PULSE` | Forward return and MAE/fee profile are plausibly exploitable | Write bounded strategy surface |
| `WEAK_EDGE` | Some signal exists but not enough after fees/risk | Close or redesign thesis |
| `NO_PULSE` | No useful predictive structure | Close lane |

No `HAS_PULSE`, no autoresearch.

#### Mandatory baseline: the random-walk null

A probe does not earn `HAS_PULSE` by showing a positive forward return. It earns it by
**beating the random-walk null** — the hypothesis that the series carries no exploitable
structure and any apparent edge is sampling noise. This is the formal version of the
shuffled-sign / block-bootstrap controls already run per lane (microstructure #110, mNAV
concentration cap); making it a standing gate stops a future probe from mistaking an
in-sample artifact for signal.

Every Gate-1 probe MUST report, and clear, all three:

1. **A null model the edge is measured against.** Construct a control that destroys the
   thesis-specific information while preserving the data's nuisance structure, then show
   the real edge exceeds the null distribution. Pick the control that kills *only* the
   claimed signal:
   - directional / return-prediction theses → **shuffled or phase-randomized returns**
     (preserves the marginal return distribution and volatility clustering, destroys
     temporal order);
   - signed-flow / feature theses → **shuffled-sign / permuted-feature** control
     (preserves magnitude distribution, destroys the feature→return link), per #110.
2. **Significance with multiple-testing correction.** Bootstrap the edge statistic with
   **block** bootstrap (≥ 1000 resamples) to respect autocorrelation — IID resampling
   inflates significance on serially-correlated returns. If the probe scans N horizons,
   thresholds, or buckets, correct the p-value across all N (Bonferroni/Holm). Report
   `p_adj`, not the raw best-of-N.
3. **Edge is not concentrated.** No single UTC day/hour/event may supply more than **25%**
   of the edge (the mNAV concentration cap). A pulse that lives in one bar is a single
   draw from the null, not a repeatable edge.

`HAS_PULSE` requires **all** of: monotonic/structured relationship · `p_adj < 0.05` ·
beats the chosen null · concentration ≤ 25% · survives the venue-appropriate round-trip
cost. This is an **AND**, not a best-of — a probe that passes one and fails another is
`WEAK_EDGE` at most (see #110, whose verdict logic is the reference `significant = monotonic
AND p_adj < ALPHA AND beats_shuffled AND concentration_ok`).

If a probe cannot beat its own null, the result is `NO_PULSE` regardless of how positive
the raw mean looks — and that is a *true* null worth banking, not a failure to model
harder. A fancier estimator on the same data does not add information; it adds variance.
Model sophistication is never a substitute for a differentiated data surface (the banked
program's terminal lesson — `docs/reports/research-consolidation-2026-06-23.md`).

### Gate 2: Bounded Autoresearch

Autoresearch is config-only unless the lane brief explicitly requires a new strategy class.

Every autoresearch overlay under `config/autoresearch/overlays/` must set
`strategy.global_trend_filter_enabled: true` or `false` explicitly (not inherited from
`base.yaml`). The experiment autopilot audit dump records the resolved filter state at run start.

Primary tools:

```bash
uv run python scripts/autoresearch_loop.py \
  --config config/settings.autoresearch.yaml \
  --symbol SOLUSDT \
  --timeframe 1h \
  --train-months 3 \
  --test-months 2 \
  --gate-profile standard \
  --families <family> \
  --max-runs 30
```

Before launching or advancing a lane, run the supervisor guard against the artifacts
available so far:

```bash
uv run python scripts/rbi_loop_guard.py \
  --lane-brief docs/specs/<lane>.md \
  --probe-verdict HAS_PULSE \
  --last-result research/last_result.json \
  --overlap-report docs/reports/<overlap>.json \
  --pretty
```

The guard does not run backtests. It returns the next allowed action from the current
artifacts, such as `RUN_CHEAP_PROBE`, `RUN_AUTORESEARCH`, `RUN_BOOTSTRAP_1000`,
`CHECK_OVERLAP`, `READY_FOR_PAPER_REVIEW`, or `ITERATE_OR_CLOSE`.

Use the runner when an agent should persist the decision and optionally execute the next
local command:

```bash
uv run python scripts/rbi_loop_runner.py \
  --lane-name <lane> \
  --lane-brief docs/specs/<lane>.md \
  --probe-verdict HAS_PULSE \
  --last-result research/last_result.json \
  --autoresearch-command "uv run python scripts/autoresearch_loop.py --config config/settings.autoresearch.yaml --symbol SOLUSDT --timeframe 1h --train-months 3 --test-months 2 --gate-profile standard --families <family> --max-runs 30"
```

Default runner mode only writes `research/rbi_loop/<lane>/decision.json`. Add `--execute`
only for local read-only probes, config-only autoresearch, bootstrap revalidation, or
overlap checks. Do not use the runner for production deploys or live risk changes.

For repeatable lanes, prefer a manifest:

```bash
cp config/autoresearch/rbi_loop.example.yaml config/autoresearch/rbi_loop.<lane>.yaml
uv run python scripts/rbi_loop_from_manifest.py --manifest config/autoresearch/rbi_loop.<lane>.yaml
```

The manifest is the lane's local control file: it records artifact paths, timeout, and
the exact commands mapped to `RUN_CHEAP_PROBE`, `RUN_AUTORESEARCH`,
`RUN_BOOTSTRAP_1000`, and `CHECK_OVERLAP`. Add `--execute` only after reviewing the
manifest and confirming the selected action is safe.

For a multi-lane supervisor pass:

```bash
uv run python scripts/rbi_loop_batch.py \
  --glob "config/autoresearch/rbi_loop.*.yaml" \
  --summary-output research/rbi_loop/batch-summary.json \
  --markdown-output docs/reports/rbi-loop-batch-summary.md
```

Batch mode skips `rbi_loop.example.yaml` by default. It runs one guarded step per lane,
writes per-lane decision/report artifacts, and produces a batch summary. Use `--execute`
only for an explicitly reviewed manifest set; batch mode is not a production deploy tool.

For scheduled dry supervision on the production host, install:

```bash
sudo cp ops/systemd/crypto-agent-rbi-loop-batch.service /etc/systemd/system/
sudo cp ops/systemd/crypto-agent-rbi-loop-batch.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now crypto-agent-rbi-loop-batch.timer
```

The systemd job runs `scripts/run_rbi_loop_batch.sh` without `--execute`. It only writes
decision/report summaries. To change manifest discovery or output paths, use
`/etc/default/crypto-agent-rbi-loop-batch`:

```bash
MANIFEST_GLOB=config/autoresearch/rbi_loop.*.yaml
SUMMARY_OUTPUT=research/rbi_loop/batch-summary.json
MARKDOWN_OUTPUT=docs/reports/rbi-loop-batch-summary.md
```

After each runner step, render a durable report artifact:

```bash
uv run python scripts/rbi_loop_report.py \
  --decision research/rbi_loop/<lane>/decision.json \
  --output docs/reports/rbi-loop-<lane>.md
```

Use the generated report as the reviewable handoff. Fold only final accepted outcomes into
`docs/reports/autoresearch-candidate-ledger.md`; do not auto-edit the canonical ledger from
raw runner output.

For production DB-backed research:

```bash
scripts/run_autoresearch_tunnel.sh \
  --mode loop \
  --config config/settings.autoresearch.yaml \
  --symbol SOLUSDT \
  --timeframe 1h \
  --train-months 3 \
  --test-months 2 \
  --bootstrap 100 \
  --gate-profile standard \
  --families <family> \
  --max-runs 30 \
  --timeout-seconds 900 \
  --output-dir /tmp/crypto-agent-autoresearch-<lane>
```

Standard gate:

- WFO trades >= 20
- mean OOS Sharpe >= 0.5
- OOS return > 0
- max drawdown <= 10%
- bootstrap P(loss) <= 25%
- profit concentration <= 50%

Do not lower the standard gate to create more agents. `probe_1h` is only a probe profile,
not a promotion profile.

### Gate 3: Promotion Candidate

Before spending bootstrap=1000, the candidate must satisfy the stricter pre-filter:

- WFO trades >= 20
- OOS return >= 1%
- max drawdown <= 8%
- bootstrap P(loss) <= 20% at bootstrap=100
- profit concentration <= 40%
- Sharpe >= 0.5

If it misses this gate, record the near-miss and stop unless the failure suggests a new
first-principles surface.

### Gate 4: Bootstrap=1000

Run one final revalidation with bootstrap=1000. This is the real promotion filter.

Near-misses at bootstrap=100 are not deployable. Prior AVAX/ETH near-misses collapsed at
bootstrap=1000; treat that as the default expectation.

### Gate 4b: Synthetic-path stress

Gate 4b is a **diagnostic**, pending readiness. It is NOT a promotion gate.
When disabled (threshold 0.0, not on CLI), synthetic eval is not run and the
report records `not_run` (not 0.00%). Optional `--synthetic-diagnostic` with
an explicit frozen `--synthetic-fit-start`/`--synthetic-fit-end` records
path/fit/runtime evidence; it does not affect promotion or `passes_gates`.
The threshold stays 0.0.

### Gate 5: Independence and Portfolio Impact

Before paper/live promotion:

```bash
uv run python scripts/analyze_entry_overlap.py --help
```

Compare candidate entries against:

- `agent_sol_1h_trend_pullback_overlay_live`
- `agent_sentiment_macro`
- any currently enabled paper candidate on the same symbol/timeframe

Reject or keep paper-only if overlap is high and the candidate does not add clear risk
diversification.

### Gate 6: Paper / Forward Validation

Paper or minimum-size live-forward validation must track:

- expected vs actual entries/month
- fills and slippage
- SL/TP placement reliability
- trade duration
- realized PnL
- drawdown
- overlap with existing agents
- unexplained missed or extra signals

Milestones for a new live agent:

- 5 closed trades: sanity check only
- 10 closed trades: behavior review
- 20 closed trades: first serious forward verdict

Do not scale before 20 closed trades unless there is an explicit human exception.

## Stop Rules

Stop the lane and write a ledger update if any of these happen:

- No cheap-probe `HAS_PULSE`.
- Three consecutive WFO attempts fail the same critical gate.
- The candidate needs more than five free parameters.
- The fix contradicts the original thesis.
- Bootstrap=1000 P(loss) exceeds 25%.
- Profit concentration exceeds 50%.
- WFO trades stay below 20 after a trade-density-specific attempt.
- Candidate overlaps heavily with a live agent without improving portfolio risk.
- The lane belongs to a banned family in `research-reset-2026-06-06.md`.

When a lane closes, write the reason in `docs/reports/autoresearch-candidate-ledger.md`
or a dedicated report under `docs/reports/`.

## Human Approval Points

Agents may run read-only probes, local tests, and config-only sweeps within the configured
budget. Human approval is required before:

- live config changes
- production service rebuilds or restarts
- enabling a paper service in `docker-compose.prod.yml`
- changing risk limits
- increasing notional size
- adding a new data provider with credentials
- allowing short entries beyond research/paper mode

## Implementation Rules

Implement only after validation proves the lane deserves code.

When implementation is needed:

1. Add the smallest strategy/data/risk module that expresses the validated thesis.
2. Add focused tests for signal behavior, config parsing, and execution routing.
3. Run:

```bash
uv run ruff check .
uv run ruff format --check .
uv run pytest -v
```

4. Re-run the exact validation that justified the change.
5. Update the ledger with artifact paths and the final decision.

## Current Best Next Loop

Given the 2026-06 research reset, the next profitable-agent loop should not be another
SOL 1h threshold/filter campaign or another single-symbol 1h OHLCV structure probe.
The liquidity-sweep and range-break families are closed unless new evidence appears.

Cross-venue basis / dislocation (the prior #1) has now been **executed to closure** through
the RBI loop: basis-premium-filter-v0, cross-venue-basis-v1, and both the fixed (v0) and
rolling (v1) dislocation-event variants all returned `ITERATE_OR_CLOSE` (dislocation 0/30
under the standard gate). Probe machinery is reusable; no edge survives house gates. See
`docs/reports/autoresearch-candidate-ledger.md` "RBI Loop Records" for per-lane evidence.

Higher-timeframe portfolio regime allocator (the next #1) has also been probed to
closure: the real-DB cheap probe (SOL/BTC/ETH 1h, 4h+1d trending/high-vol regime,
~21.5k bars/scenario) returned `NO_PULSE` — favorable-vs-unfavorable forward-return
Δ ≤ 0.096% (bar 0.15%), signs inconsistent across symbols. Closed at the probe gate.
See `docs/reports/higher-tf-regime-probe-2026-06-14.md`.

The next lane should be data-first:

1. News/event calendar risk filter.
2. Order book or liquidation data.
3. (closed) Higher-TF portfolio regime allocator — NO_PULSE, see ledger.
4. (closed) Cross-venue basis / dislocation — see ledger; reopen only with new data.

For the next run:

```text
Write brief -> add/read data coverage probe -> classify pulse -> only then decide
whether autoresearch is justified.
```

Until then, the active operating loop is Phase 0 forward validation of:

- `agent_sol_1h_trend_pullback_overlay_live`
- `agent_sentiment_macro`

Weekly command:

```bash
uv run python scripts/paper_validation_report.py --help
uv run python scripts/production_drift_sentinel.py
scripts/run_phase0_weekly.sh
```

## Completion Definition

The RBI loop is working only if every campaign leaves behind:

- a lane brief or report
- probe result
- exact commands
- raw artifact paths
- gate decision
- ledger update
- explicit next action: promote, paper, iterate, or close

Anything else is manual prompting with extra steps, not a closed loop.
