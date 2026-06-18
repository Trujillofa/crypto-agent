# Brief — Closed-Family Cost-Corrected Re-Screen (the last clean tooling test)

**Status:** experiment spec — to be run by Grok; Claude reviews
**Trigger:** Engine cost/funding defaults corrected (#94) and trend filter made explicit (#96). The
dislocation isolation (#95) showed the cost bug was real but, for that lane, did **not** hide a
deployable edge. This re-screen answers the remaining question **once**: *did the tooling
(over-charged costs + the silent global trend filter) hide a deployable edge in the families most
sensitive to it?* If no → the systematic hunt is genuinely exhausted and we consolidate.
**Predecessors:** [backtest-engine-integrity-audit-2026-06-18.md](../reports/backtest-engine-integrity-audit-2026-06-18.md),
[cost-realism-rerun-2026-06-18.md](../reports/cost-realism-rerun-2026-06-18.md),
[dislocation-cost-isolation-2026-06-18.md](../reports/dislocation-cost-isolation-2026-06-18.md).

---

## Scope — only the cost/filter-suspect families (deliberately bounded)

Re-screen **only** lanes whose closures are plausibly tooling artifacts: **frequent-trading and/or
trend-filter-confounded.** That is the **mean-reversion / dip-buying family** — it trades often (most
fee-sensitive) **and** was silently blocked from its core trade by the default-on global trend filter.

**Do NOT re-run** the cost-independent closures (waste of runs, prior is ~0):
- **Cross-asset / TradFi** — had *zero forward predictive signal* (cost-independent). Stays closed.
- **Macro calendar / surprise** — no forward drift / priced in (cost-independent). Stays closed.
- **Trend family** — daily-trend already re-tested at realistic cost (#92) → still FAIL; failed on
  trade-count/concentration, not fees. Stays closed.
- **Dislocation** — already isolated (#95) → stays closed.

## Frozen lane set (defined ex-ante — no additions to find a winner)

The mean-reversion family, on the symbols/timeframes they were originally closed at (document each
from the ledger):
1. `rsi_reversal`
2. `bollinger_strategy`
3. `mean_reversion`
4. `range_reversion_bounded` (the ETH 4h lane used in the cost-realism run)

(If a lane's original config/params aren't recoverable from the ledger/git, document and skip it —
do not invent new params; this is a re-screen of *closed* configs, not a new search.)

## Design — best-case screen at corrected costs (2 cells/lane)

Costs are already corrected on `main` (fee 0.04%, slippage 0.02%, 8h funding) — just run on `main`,
no overrides. The only swept variable is the **trend filter**, because it cuts both ways
(range-reversion needed it; dip-buyers are blocked by it):

| Cell | Cost | Trend filter |
|------|------|--------------|
| A | corrected (main default) | **OFF** (let the strategy trade its own logic) |
| B | corrected (main default) | **ON** (native/base default) |

Take **best of A/B per lane** as the screen. Rationale: if a lane can't clear the gate at corrected
costs under *either* filter setting, it is genuinely dead — not a tooling artifact.

Reference (no re-run needed): cite each lane's **original closed verdict** (legacy cost) from the
ledger so the cost-corrected delta is visible.

## Pulse / gate

Use the **same gate profile** each lane was originally judged under (`standard` unless documented
otherwise). **Do not loosen thresholds** — this isolates the tooling effect, not the bar.

Same WFO setup (train/test, period) as the original closure. Print resolved cost + filter audit per
run (now available from #94/#96).

## Read-out & decision

Per lane: table of `wfo_return_pct`, `wfo_sharpe`, `wfo_trades`, `max_drawdown_pct`,
`profit_concentration`, verdict — Cell A, Cell B, and the original legacy verdict for reference.

- **Any lane's best cell PASSES the gate** → the tooling *was* hiding an edge. Flag it; run a focused
  cost×filter attribution (like #95) before re-opening, and notify for review. Do **not** auto-promote.
- **All lanes still FAIL** → the mean-reversion family is genuinely closed at correct costs. Combined
  with dislocation (#95), this **definitively answers** that the cost bug hid no deployable edge →
  recommend **stop the structural-probe program and consolidate** (sentiment-macro / SOL overlay Phase 0).

## Guardrails

1. **Frozen lane set** — no adding/swapping lanes after seeing results.
2. **Re-screen closed configs only** — no new parameter search; if params aren't recoverable, skip+document.
3. **Don't loosen gates**; same profile/WFO as the original closure.
4. **Corrected costs = main default**; do not reintroduce legacy except as the cited reference verdict.
5. Print resolved cost+filter audit per run.

## Reviewer (Claude) checkpoints

(a) only the frozen mean-reversion set re-run; cost-independent families correctly excluded;
(b) corrected costs confirmed in the per-run audit; (c) both filter cells run, best-of taken;
(d) gate profile unchanged from original; (e) honest pass/fail per lane vs original verdict, and the
**decision stated** (re-open-with-attribution vs family-closed-consolidate).
