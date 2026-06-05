# Freqtrade Snapshot Research + MTF Automation PR

Date: 2026-06-05 (research worktree)

## Scope of Research
- Full review of current repo (CLAUDE.md, AGENTS.md, src/, scripts/, research/, docs/, 60+ test files, 20+ strategies, MTF infra, autoresearch/WFO, risk, execution, futures, overseer).
- Analysis of provided Freqtrade export: `freqtrade-freqtrade-8a5edab282632443.txt` (4MB, ~110k lines; dir tree + full source + docs + templates at that commit).
- Extraction of key Freqtrade modules via terminal rg/grep/sed on the txt (strategy/interface patterns, informative_decorator, parameters, backtesting, hyperopt, exchange, protections, data provider, etc.).
- Cross-ref with existing repo research (MTF_INFRASTRUCTURE_PLAN.md, HONEST_ASSESSMENT.md, MTF_STRATEGY_GUIDE.md, deep-research-report.md, regime results, etc.).

## Key Freqtrade Concepts Reviewed (What Stood Out)
- **IStrategy + populate_* contract**: strategies are DF transformers (`populate_indicators`, `populate_entry_trend`, `populate_exit_trend`). Very backtest-friendly (vectorized), self-contained.
- **@informative(timeframe) decorator + merge_informative_pair**: declarative MTF. Strategy says what extra data it wants; framework handles download, alignment (no-lookahead), and injection into the main DF. Higher TF data automatically suffixed/available in populate.
- **Hyperoptable Parameters** (`IntParameter`, `DecimalParameter`, ... with `space="buy"` etc.): params live in strategy source with bounds/defaults. Hyperopt tunes the space; same code for live uses `.value`.
- **Protections** (config list: CooldownPeriod, MaxDrawdown, StoplossGuard, LowProfitPairs, ...): pluggable, pair-specific or global, applied by bot without strategy changes.
- **Startup candle count, process_only_new_candles, can_short**: explicit contracts for warmup/NaNs/shorting.
- **DataProvider / informative pairs in live**: strategies can request other pairs/TFs at runtime.
- **Edge, pairlists (VolumePairList, etc), custom stoploss/exit/position adjust**: rich extension points.
- Backtest engine highly optimized for the DF model + realistic sizing/slippage.
- Strong separation of concerns, excellent templates (`new-strategy`), strategy updater, plot, analysis commands.

## Comparison to Current Agent + Gaps/Opportunities
**Strengths of this repo (often better or different)**:
- Async-first, TimescaleDB feature store (central pre-computed indicators shared across strategies/agents — efficient vs per-strategy recompute).
- Mature risk (circuit breakers, guards, staged orders, reconciliation), portfolio, lifecycle (paper→live promotion), overseer (xAI), futures exec + funding, Telegram, Prometheus/Grafana.
- Heavy research tooling (autoresearch_*.py, run_wfo.py, experiment_autopilot, monte_carlo, sentiment replay, production_drift_sentinel, etc.).
- Per-agent isolation via AGENT_ID + DB columns.
- Bar-by-bar `evaluate(symbol, indicators: dict)` is simple for stateful strategies (previous_* dicts for reclaim patterns) and same code path live vs backtest.
- Already has MTF declaration (`REQUIRED_TIMEFRAMES`), no-lookahead join, regime features (migration 007), multi-strat aggregation.

**Gaps inspired by Freqtrade that were actionable**:
- MTF data collection not driven by the declaration in live path (main gap closed by this PR).
- No declarative "parameter with range" objects (params are ad-hoc `config.get("foo", default)` in __init__; sweeps are in external scripts). This makes hyperopt-like automation and self-documenting strategies harder. (Deferred — larger scope; would touch 20+ strategies + autoresearch.)
- Protections are partly hardcoded (cooldown_candles, global risk.yaml, engine guards) vs Freqtrade's config-driven list of protection modules. Could be future refactor for more flexible per-strategy/pair rules.
- Strategy self-containment vs central features: trade-off. Freqtrade model easier for community strategies / hyperopt; ours better for shared compute + DB analytics. No plan to flip.
- Backtest here is replay/row-wise (fine for 4h histories); Freqtrade vectorized on full DF. We have good slippage/ATR/exit model tests already.
- Exchange abstraction: ours Binance-specialized (good perf, custom WS + REST + futures). Freqtrade ccxt-based (multi-exchange). Not useful to adopt unless we want more venues.
- No built-in "hyperopt" command or plot/analysis CLI (we have many scripts + chub + profit_report).

## The Implemented Change (PR #58)
See commits on `feat/auto-mtf-data-pipeline` (tip after review: ~1340554 + df5c84c for the independent test fix) and the PR.

**Post-submission review (incorporated before merge):**
- Fixed latent trap: `_collect_required_timeframes` now iterates `required.values()` (generic, matches engine/backtest) instead of key whitelist.
- Added unit test coverage for the helper in `tests/test_main.py`.
- Also landed the pre-existing `test_autoresearch.py` family fix (unblocks CI for this and other PRs) as its own commit.

Minor: added a code comment acknowledging uniform `compute_interval=60` across TFs (harmless).

**Why this one?**
- Directly addressed repeated MTF research friction ("MTF not runnable in live", "data must be pre-populated").
- Small, focused diff (1 file, ~80 net LOC).
- High leverage: makes existing MTF strategies (mtf_template, mtf_continuation, mtf_breakout, multi_timeframe_regime, regime_router variants) actually deployable from a config that just lists the strategy name.
- Followed all CLAUDE/AGENTS rules: read before edit, one agent per file (this), 5-step framework applied in thinking, types, no dead code, ruff clean, pytest (relevant + full suite), specific `git add`, conventional commit, etc.
- Deleted nothing major (kept futures mark path, single-TF paths, etc.); simplified the "how do I get regime data?" question.

**Files touched**: only `src/main.py` (hoist resolve, new collector, per-TF loops for ingestors+computers, list-based task mgmt).

**Tests**: MTF integration/join + main/config tests green. Full run 821/822 (pre-existing autoresearch family assertion unrelated to this).

## Other Ideas from Research (Not Implemented in This PR — Future Candidates)
1. **Parameter classes** (freqtrade/strategy/parameters.py + HyperStrategyMixin): declare `foo = IntParameter(10, 50, default=30)` in strategy. Enables better autoresearch (read ranges from class), self-doc, future hyperopt runner. Would require updating many strategies + sweep scripts. Medium effort.
2. **Protections framework**: make cooldown/max-drawdown-per-pair etc config-driven pluggable objects (similar to risk guards but strategy/pair scoped). Config section like Freqtrade `"protections": [...]`.
3. **Vectorized backtest path** (optional): for very long histories, allow strategies to implement a DF-based `populate` for speed, while keeping row `evaluate` as the live contract.
4. **More informative-style helpers**: e.g. a `@requires_timeframe("4h")` decorator that also registers, or auto-suffix helpers in evaluate.
5. **Strategy validation / startup_candle_count**: formalize per-strategy "I need N candles before valid signals" and wire into engine priming + backtest (we have partial via cooldown/primed).
6. **Edge / position sizing from stats**: port ideas from Freqtrade edge module into portfolio or a new analyzer (we have profit_report + WFO already).
7. **Better hyperopt integration in autoresearch**: use the same strategy instances for opt instead of yaml overlays.

These were deprioritized per "Step 2: Delete / narrow scope" — the MTF automation was the clearest missing link between existing research artifacts and runnable system.

## Recommendations / Next Steps
- Merge PR #58 after review/CI.
- Take an existing MTF candidate (e.g. from research/regime_router_final_results or sol trend overlays), configure a paper agent with the mtf strategy, let it run with auto data collection, observe regime features populated.
- If happy with trade frequency/edge, promote via lifecycle.
- Consider Parameter classes next if we want to reduce duplication between strategy __init__ config.get's and the sweep yaml/scripts.
- Keep following the Research Framework (hypothesis → exploration → gates) even with better infra.

## Commands Run During This Work
- Extensive `grep`, `sed`, `python -c` extraction on the 109k-line Freqtrade txt.
- `list_dir`, `read_file` (limits), `grep` tool across src/docs/research.
- `uv run --isolated ... ruff ...`, pytest via .venv, manual python snippets for helpers.
- git branch, add (specific), commit, push.
- MCP GitHub tool for PR creation.

This completes the "full research then implement useful via PR" request. The change is narrow, evidence-based, and unblocks a lot of prior MTF investment.

PR: https://github.com/Trujillofa/crypto-trading-agent/pull/58
Branch: feat/auto-mtf-data-pipeline
Commit: a4bb54e
