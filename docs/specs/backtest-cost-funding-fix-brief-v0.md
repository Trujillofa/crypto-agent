# Brief — Backtest Cost & Funding Correctness Fix (Task 1)

**Status:** implementation spec — to be built by Grok; Claude reviews
**Trigger:** [backtest-engine-integrity-audit-2026-06-18.md](../reports/backtest-engine-integrity-audit-2026-06-18.md)
Findings A (cost ~3× too high) and B (funding charged per-bar, not 8h). These are **correctness**
fixes justified independent of any strategy result.

---

## Scope (surgical — defaults + one bug, nothing else)

### Fix 1 — realistic default costs (`src/backtest/engine.py:38,45`)
- `fee_rate`: `0.001` → **`0.0004`** (Binance USDT-perp / spot taker on majors).
- `slippage_pct`: `0.001` → **`0.0002`** (a few bps on BTC/ETH/SOL).
- These are *defaults*; every config/CLI override still wins. Round-trip drops ~0.4% → ~0.12%.

### Fix 2 — funding cadence bug (`_apply_funding`, `engine.py:629-642`)
Funding is charged **every bar**; real perp funding settles **every 8h**. Fix so the effective
funding over time equals the 8h rate regardless of timeframe. Two acceptable implementations
(pick one, document it):
- charge only on bars crossing 00:00/08:00/16:00 UTC; or
- scale the per-bar charge by `timeframe_hours / 8` (reuse the approach already in
  `src/backtest/cost_overrides.py` from PR #92 — prefer reusing it, don't duplicate).

### Out of scope (note as follow-ups, do NOT build now)
- Per-symbol **empirical** costs (the cTrader `derive_sm_pair_costs.py` discipline) — larger
  enhancement, separate task.
- The trend filter (Task 3) and the Sharpe-on-flat-bars consideration (audit Finding D).

## Guardrails / risks to handle
1. **Blast radius:** changing defaults changes *all* future backtest numbers. Prior reports used
   old costs — **do not retro-edit them**; add a one-line note in the audit report that defaults
   were corrected on this date so old vs new numbers are comparable knowingly.
2. **Don't break live-agent replay:** `sentiment-macro` replay/backtest must still run. Verify the
   sentiment replay path and any test that asserts on fee/slippage/funding values still pass (update
   expected values where a test hard-codes the old defaults — and call those out in the PR).
3. **Futures funding:** verify the fix only changes cadence/scaling, not the rate, and that spot
   lanes (funding N/A) are unaffected.

## Validation
- `uv run pytest` + `uv run ruff check .` green (update + annotate any tests that pin old defaults).
- Add/extend a unit test asserting: (a) round-trip cost at new defaults ≈ 0.12%; (b) funding over a
  known multi-day 1h window equals the 8h-equivalent (≈ 1/8 of the old per-bar total).
- Print/log the resolved cost config at backtest start so runs stay auditable.

## Reviewer (Claude) checkpoints
(a) only fee/slippage defaults + funding cadence changed (no other behavior); (b) overrides still
win; (c) funding fix correct and reuses `cost_overrides` not a duplicate; (d) live-agent replay +
tests pass with old-default tests knowingly updated; (e) audit report annotated with the change date.
