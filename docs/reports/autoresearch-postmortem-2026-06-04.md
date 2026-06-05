# Autoresearch Post-Mortem (June 2026)

**Period:** 2026-06-03 — 2026-06-04
**Objective:** Find up to 10 independent trading agents that pass standard walk-forward/bootstrap gates and together support ~8–20 trades/month for faster forward validation.
**Outcome:** One candidate validated and deployed. All other lanes closed or rejected. **Autoresearch sweeps paused** until live forward data or a materially new signal surface.

Detail ledger: [autoresearch-candidate-ledger.md](./autoresearch-candidate-ledger.md)

---

## Executive Summary

| Metric | Value |
|--------|-------|
| Total search runs | ~1,040+ |
| Standard-gate passes (new) | 0 beyond SOL |
| Deployable technical agents | **1** |
| Live technical | `agent_sol_1h_trend_pullback_overlay_live` |
| Independent live strategy | `agent_sentiment_macro` |
| Gates lowered to inflate count | **No** |

The process worked: weak candidates were filtered at bootstrap=1000; near-misses did not survive promotion; BNB/BTC 1h surfaces were closed when evidence showed no robust edge. The bottleneck is **lack of independent edge**, not tooling.

---

## What Was Deployed

**SOLUSDT 1h `trend_pullback_overlay`** — the only configuration that passed:

- Standard gate at bootstrap=100 (WFO discovery)
- Full promotion path including bootstrap=1000
- Live service: `agent_sol_1h_trend_pullback_overlay_live`

Representative metrics at promotion (bootstrap=100): ~26 WFO trades, +19% compound OOS, Sharpe ~1.7, max DD ~7.4%, P(loss) ~14%, concentration ~29%.

---

## What Failed Promotion (and Why)

### AVAX / ETH near-misses (Wave 2 → bootstrap=1000)

Tracked configs looked acceptable at bootstrap=100 (e.g. AVAX regime +6.55% OOS, 18 trades). At **bootstrap=1000** both collapsed:

| Candidate | b=100 story | b=1000 result |
|-----------|-------------|---------------|
| AVAX 1h regime-gated #0004 | Near-miss | −0.72% OOS, P(loss) 81% → **REJECT** |
| ETH 1h breakout #0004 | Near-miss | −2.35% OOS, 9 trades, P(loss) 88% → **REJECT** |

**Lesson:** bootstrap=100 is for discovery only; bootstrap=1000 is the real promotion gate. Do not paper or live-deploy from b=100 alone.

### BNB 1h — closed

| Lane | Best OOS | WFO trades | Verdict |
|------|----------|------------|---------|
| Pass1 overlay | +6.70% | 26 | REJECT (Sharpe −0.36, conc 72%) |
| Wave 5 standalone | **0%** | **0** | **CLOSED** — surface too silent |

Fifty standalone runs with best **zero trades** is not a tuning problem; the search surface does not fire on BNB 1h.

### BTC 1h — closed

| Lane | Best OOS | WFO trades | P(loss) | Verdict |
|------|----------|------------|---------|---------|
| Wave 5 standalone | **+2.83%** | 10 | 38% | NEAR_MISS — sparse, not deployable |
| Wave 6 overlay | **−2.23%** | 16 | 94% | **REJECT** — worse than standalone |

**Tradeoff confirmed:** standalone BTC had a small positive, low-DD pocket but too few trades and weak confidence; overlay added trades but destroyed return and bootstrap robustness. **No bootstrap=1000, no paper, no live** for BTC 1h.

### Other lanes (no promotion)

- Phase 1 pass1 (ETH/BNB/BTC/AVAX/SOL 4h): 0 standard passes
- Waves 2–3 (new overlay families, bridges, funding): 0 standard passes
- SOL 4h standalone / SOL 1h `mtf_breakout_standalone`: REJECT (gates or catastrophic over-trading)

---

## Standards Preserved

| Rule | Status |
|------|--------|
| Standard gate for discovery | Unchanged |
| `promotion_candidate` pre-filter before b=1000 | Added and kept |
| bootstrap=1000 required for promotion | Enforced; killed AVAX/ETH |
| No lowering gates to reach “5–10 agents” | Honored |
| Independence check (SOL) | OOS entry overlap overlay vs sentiment-macro: **0 shared bars** |

Gate reference:

- **standard:** WFO trades ≥ 20, Sharpe ≥ 0.5, OOS > 0, DD ≤ 10%, P(loss) ≤ 25%, conc ≤ 50%
- **promotion_candidate:** stricter pre-b=1000 (OOS ≥ 1%, DD ≤ 8%, P(loss) ≤ 20%, conc ≤ 40%, etc.)

---

## Live Portfolio (Current)

| Agent | Symbol | TF | Role |
|-------|--------|-----|------|
| `agent_sol_1h_trend_pullback_overlay_live` | SOLUSDT | 1h | Only deployable technical (standard + b=1000) |
| `agent_sentiment_macro` | SOLUSDT | 1h | Independent sentiment/macro futures |
| `agent_sol_sparse` / `agent_sol_panic_block_paper` | SOLUSDT | 4h | Paper research — not promotion queue |

Goal of 5–10 agents remains valid in principle, but **count is subordinate to gates and independence**. Current evidence does not support adding agents via more 1h trend-pullback or overlay parameter search.

---

## Closed Search Surfaces (Do Not Re-run Without New Hypothesis)

- BNBUSDT 1h standalone and overlay variants
- BTCUSDT 1h standalone and overlay (Wave 5–6)
- AVAX/ETH Wave 2 #0004 tracked overlays
- Repeated SOL 1h MTF standalone (over-trading risk)
- Threshold / aggregator sweeps on the same 1h technical stack

---

## What Worked in the Process

1. **Remote autoresearch loop** on production TimescaleDB with frozen overlays and reproducible artifacts.
2. **bootstrap=1000** as a noise filter for near-misses.
3. **`promotion_candidate`** to avoid wasting b=1000 runs.
4. **Entry-overlap analysis** — justified running SOL overlay + sentiment-macro together (historical OOS timing independent).
5. **Discipline** — stopping after Wave 6 instead of chasing BTC with more knobs.

---

## Recommended Next Steps

### 1. Forward validation (primary)

Keep **SOL 1h overlay live** and **sentiment-macro** running. Measure on real data, not more backtests:

- Entries per month (vs ~1–2/month design intent for overlay)
- Fills, slippage, SL/TP placement reliability
- Realized PnL and drawdown
- Loss correlation between the two live agents

Do not judge profitability from the first handful of trades.

### 2. Pause autoresearch sweeps

No new campaigns until either:

- Live SOL produces enough closed trades for a forward verdict, or
- A **new research brief** defines a materially different surface.

### 3. When research resumes — new surfaces only

Candidates must differ from “another 1h trend-pullback variant”:

- Longer-history **BTC/ETH 4h** with regime conditioning
- **Bounded high-density** families (explicit trade-count caps in design)
- **Funding / range / regime** logic (Wave 2 funding overlays were explored lightly; not exhausted, but not 1h pullback tweaks)
- Short-side or market-neutral only if safely supported by execution and risk stack

Each new lane needs: hypothesis, independence plan (entry overlap vs live agents), standard discovery, `promotion_candidate`, then bootstrap=1000.

---

## Conclusion

One robust, promoted agent exists: **SOLUSDT 1h trend_pullback overlay live**. The search infrastructure and gates behaved correctly; ~1,040 runs did not produce additional deployable independent edge on BNB/BTC/AVAX/ETH 1h surfaces tested. **Next value is forward validation and new signal design—not incremental parameter search.**

---

## Addendum — Wave 7 (2026-06-04, after this post-mortem)

This post-mortem's "new surfaces only" recommendation (longer-history BTC/ETH 4h, bounded high-density families, funding logic) was then **executed as Wave 7** — and all of it closed. ~400 additional runs (cumulative ~1,440), **0 standard passes, 0 `promotion_candidate` eligibles**:

- ETH/BTC **4h regime** overlays — too sparse (best 1–5 WFO trades). CLOSED.
- **BTC 4h `range_reversion_bounded`** — −15.89% OOS, DD 26.4%, P(loss) 94%. CLOSED.
- **BTC 4h funding-primary** — 0 trades (same silent-surface failure as BNB 1h standalone). CLOSED.
- **ETH 4h `range_reversion_bounded`** — +13.55% OOS, 24 trades, but DD 20.3% / P(loss) 56% / Sharpe 0.48. Near-miss only; not `promotion_candidate`.

Detail in the [ledger](./autoresearch-candidate-ledger.md) Wave 7 section. **Conclusion unchanged: still 1 deployable agent; the bottleneck remains independent edge, and Wave 7 confirms 4h/bounded/funding-primary did not supply it.** The next attempt needs a genuinely new surface brief, not a retune of these lanes.
