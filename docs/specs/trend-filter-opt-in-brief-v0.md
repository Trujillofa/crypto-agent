# Brief — Global Trend Filter: make it an explicit per-lane choice (Task 3)

**Status:** implementation spec — to be built by Grok; Claude reviews
**Trigger:** [backtest-engine-integrity-audit-2026-06-18.md](../reports/backtest-engine-integrity-audit-2026-06-18.md)
Finding C + the cost-realism result. The `apply_global_trend_filter` defaults **true**
(`engine.py:49`, `base.yaml:69`) and **silently converts every sub-EMA200 BUY to HOLD for all
strategies**. The experiment showed it **cuts both ways**: daily-trend wants it off; range-reversion
got far worse without it (+1.3% → −46.5%). So it is **not** a bug to remove — it is a hidden default
that must become an **explicit, conscious per-lane decision.**

---

## Goal

Stop the filter from silently mutating signals by inheritance. Keep the mechanism; make every lane
**declare** its choice, and make the behavior **visible** in logs/output. Do **not** blanket-flip the
default to off (that would hurt lanes the filter protects).

## Scope

1. **Keep** `apply_global_trend_filter` + `global_trend_filter_buffer_pct` working exactly as-is.
2. **Make the choice explicit & logged:** at backtest start, log whether the filter is active, its
   buffer, and source (config vs default), so no run is silently filtered. Include it in the resolved
   config audit dump.
3. **Research-workflow rule (doc, not code):** every research lane config under `config/` and every
   autoresearch overlay MUST set `global_trend_filter_enabled` explicitly (true/false) rather than
   inheriting. Add this to the RBI runbook / brief template and note it in the audit report.
4. **Decide the engine/base default** deliberately: recommend leaving `base.yaml` default **true**
   (it protects naive long strategies) BUT requiring lane briefs to state the choice. If you change
   any default, document the blast radius. (Planner recommendation: keep default true, enforce
   explicitness — minimal change, no silent surprise.)

## Out of scope
- Per-strategy automatic logic (e.g. mean-reversion auto-disables) — too clever; explicit config is better.
- Cost/funding (Task 1) and the isolation experiment (Task 2).

## Guardrails
1. **Behavior-preserving by default** — existing configs that rely on the current setting must run
   identically unless they opt to change. This is about *visibility + explicitness*, not flipping outcomes.
2. No silent default flips; if you propose changing `base.yaml`, call it out loudly with the affected lanes.

## Validation
- `uv run pytest` + `uv run ruff check .` green.
- A test asserting the filter state is logged/auditable and that an explicit `false` disables it while
  an explicit `true` enables it (buffer respected).

## Reviewer (Claude) checkpoints
(a) mechanism unchanged, behavior preserved for existing configs; (b) filter state now logged +
in the audit dump (no silent filtering); (c) research-workflow explicitness rule documented in the
runbook/brief template; (d) any default change (if proposed) flagged with blast radius — default kept
true unless there's a strong, stated reason.
