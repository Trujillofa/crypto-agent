# 30-Day Evidence Portfolio

**Window:** 2026-07-07 → ~2026-08-06
**Operating principle:** No track gets faith. Every track earns agent-hours through
evidence. The search bucket is an enumerated list of two probes only, then closed.
**No lane #16.**

This directory is the locked control surface for the current trading work. Each
document below fixes thresholds, rules, or gates **before** the corresponding data is
reviewed, to prevent open-ended strategy research and post-hoc rationalization.

## Tracks

| Track | Document | Rule |
|-------|----------|------|
| cTrader FX | [CTRADER_EXTERNAL_GATE.md](CTRADER_EXTERNAL_GATE.md) | External validation track only; **no cTrader code in this repository**. Challenge-ready gate + exit-path validation checklist. |
| A1 incentive farming | [A1_THRESHOLD_LOCK.md](A1_THRESHOLD_LOCK.md) | Thresholds locked before **2026-07-10**, Phase-0 closes 2026-07-11. Scale/Fix/Freeze/Kill on locked metrics. No program scales unless profitable after operator time. |
| Edge probe #1 | [NFP_PREREG.md](NFP_PREREG.md) | NFP good-news-is-good OOS. Pre-registered entry/exit/costs/thresholds. Verdict: YES or NO_PULSE. |
| Edge probe #2 | [FEE_MARGINAL_PREREG.md](FEE_MARGINAL_PREREG.md) | Fee-marginal retest post-#94/#140. **Not admitted until the exact family is named** — otherwise DELETED_NOT_NAMED, not postponed. |
| NFP forward gate | [NFP_FORWARD_GATE.md](NFP_FORWARD_GATE.md) | Forward-confirmation protocol bought by probe #1's YES. Signed 2026-07-21; measurement only, no build, no capital. First clean print 2026-08-07. |
| Day-30 fallback | [PORTFOLIO_KILL_GATE.md](PORTFOLIO_KILL_GATE.md) | If everything fails: no new public-data lane. Fallback = input acquisition or redeploy agent-hours. |
| Named-changed-input (post-portfolio) | [CVD_ABSORPTION_PREREG.md](CVD_ABSORPTION_PREREG.md) | Binance spot CVD absorption v1. Locked 2026-08-20 **before** develop fetch. Not probe #3 of the closed 30-day pair. `promote=no`. |

## Metrics, reporting, kill gates

- **A1 primary metric:** net yield per ops-hour; economic profit deducts operator time
  at $25/hour; Phase-0 ROI is a raw period return, never annualized for the decision.
- **Probe pass bar (both probes):** net expectancy > 0 after all costs, profit factor
  ≥ 1.10, acceptable drawdown, no single-outlier dependence, no post-hoc parameter
  changes.
- **Kill semantics:** each document carries its own explicit kill criterion; the
  portfolio-level kill gate composes them at day 30.

## Hard budget rules

- **No open-ended search budget.** The edge-probe list is exactly two entries and is
  closed.
- **No third probe** without a **named changed input** (new data, new access, new fee
  structure — something that materially changed since the family was last rejected),
  and even then it requires an explicit human decision, not a documentation edit.
- Editing thresholds or pass criteria after viewing data invalidates the affected
  decision (defaults: A1 → Freeze, probes → NO_PULSE).
