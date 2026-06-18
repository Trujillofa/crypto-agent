# Brief — Dislocation Cost-Only Isolation (Task 2)

**Status:** experiment spec — to be run by Grok; Claude reviews
**Trigger:** [cost-realism-rerun-2026-06-18.md](../reports/cost-realism-rerun-2026-06-18.md) — the SOL
dislocation lane flipped −23.8%/Sharpe −0.73 → **+4.6%/+0.15**, but the "realistic" pass changed
**cost AND the global trend filter together**, so the flip can't be attributed. This isolates it.

---

## Question

Was the dislocation flip driven by **cheaper costs**, the **trend filter coming off**, or both?
Answer it with a 2×2 factorial on the **one** lane (SOL 1h dislocation_event, basis_spread tail5 h24).

## Design — 4 cells, change one variable at a time

| Cell | Cost | Trend filter | Purpose |
|------|------|--------------|---------|
| 1 | legacy (0.4% RT) | ON | original "legacy" baseline (reproduce PR #92) |
| 2 | **realistic (0.12% RT)** | **ON** | **cost-only effect** (the missing cell) |
| 3 | legacy | OFF | filter-only effect |
| 4 | realistic | OFF | the PR #92 "realistic" cell (reproduce) |

Reuse `scripts/run_cost_realism_rerun.py` + `src/backtest/cost_overrides.py`; just allow the two
knobs to vary independently (cost profile × filter flag) instead of the bundled legacy/realistic
pair. Same frozen lane, same gate profile, point-in-time unchanged. Print resolved config per cell.

## Read-out

Report the 4-cell table (`total_return_pct`, `wfo_sharpe`, `wfo_trades`, `max_drawdown_pct`,
`profit_concentration`, verdict). Then attribute:
- **Cell 2 ≈ Cell 1** and **Cell 4 ≈ Cell 3** → the flip is the **filter**, not cost.
- **Cell 2 ≈ Cell 4** (both good) and **Cell 1 ≈ Cell 3** (both bad) → the flip is **cost**.
- Mixed → both contribute; quantify each (Δ from cost = Cell2−Cell1; Δ from filter = Cell3−Cell1).

## Why it matters

Decides whether re-opening the **fee-marginal / dislocation family** at corrected costs is justified
(if cost-driven) or whether the result was just the filter (if so, the family stays closed and we
learned the filter helps this lane too). Do **not** re-open the family until this attribution is clear.

## Guardrails
1. One variable per cell; run all 4 (no skipping the baselines).
2. Same lane/gate/period as PR #92 — this is attribution, not a new search. No parameter changes.
3. Overrides per-run only; print resolved config.

## Reviewer (Claude) checkpoints
(a) all 4 cells run, one variable each; (b) Cells 1 & 4 reproduce PR #92 within noise (sanity);
(c) attribution stated explicitly with the cost-Δ and filter-Δ quantified; (d) honest recommendation
on whether the dislocation family reopens.
