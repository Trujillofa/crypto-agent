# Next Candidate Search Path (June 2026)

**Objective:** find 4-9 additional independent deployable agents without lowering
promotion standards.

**Current state:** one deployable technical candidate exists:
`agent_sol_1h_trend_pullback_overlay_live`. `agent_sentiment_macro` remains live
and historically independent from the SOL overlay on OOS entry timing.

The original 5-10 agent goal is still open. The search is paused because the
tested 1h pullback/overlay surfaces are exhausted, not because the goal is
abandoned.

---

## Operating Principle

Do not continue by tuning the same surface harder. The failed lanes are already
informative:

- BTC/BNB 1h standalone/overlay: closed.
- AVAX/ETH Wave-2 near-misses: failed bootstrap=1000.
- SOL 4h standalone and SOL 1h MTF breakout: rejected.
- Repeated threshold/aggregator sweeps on the same stack: exhausted.

The next campaign must introduce a materially different source of edge:

- different timeframe,
- different market regime,
- different signal primitive,
- different holding-period logic,
- or different market microstructure input.

Agent count is secondary. A second weak agent increases risk faster than it
improves validation speed.

---

## Promotion Rules

Every candidate still follows the same promotion path:

1. Discovery run at bootstrap=100.
2. `promotion_candidate=true` (`eligible_for_bootstrap_1000` in `last_result.json`)
   before any bootstrap=1000 validation.
3. Revalidation at bootstrap=1000 — **same `standard` gate profile, only
   `--bootstrap 1000`**. There is no separate `bootstrap_1000` profile; the higher
   iteration count is the only change.
4. Entry-overlap analysis versus live agents.
5. Tracked paper config, live config, risk file, compose service, and monitoring.

Gate thresholds (`standard` and `promotion_candidate`) are defined once in
[`autoresearch-candidate-ledger.md`](./autoresearch-candidate-ledger.md) and
implemented in `GATE_PROFILES` in `scripts/run_autoresearch.py`. Do not restate
the numbers here — they would drift. The pre-filter (`promotion_candidate`) is
strictly tighter than `standard` on return, drawdown, P(loss), and concentration.

Do not use `probe_1h` for promotion. Its `min_wfo_trades` is 15 (below the
`standard` floor of 20), so it can tag research near-misses only.

---

## Phase 0 — Forward Evidence Before More Tuning

Keep these live:

- `agent_sol_1h_trend_pullback_overlay_live`
- `agent_sentiment_macro`

Track weekly:

- entries/month,
- closed trades,
- realized PnL,
- fill quality and slippage,
- exchange-side SL/TP placement reliability,
- max adverse excursion per trade,
- whether both live agents lose in the same market regime.

Review thresholds:

| Milestone | Use |
|-----------|-----|
| 5 closed SOL overlay trades | sanity check execution and frequency |
| 10 closed SOL overlay trades | first weak forward-quality read |
| 20 closed SOL overlay trades | stronger forward validation read |

This phase does not block all research, but it should block scaling notional and
prevent more SOL 1h clones unless their entry overlap is low.

**Prerequisite (blocking for realized-overlap measurement):** live DB `positions`
rows are not yet tagged with `agent_id` for these services (see ledger,
"Entry overlap — SOLUSDT 1h"). Until `agent_id` is written on position rows,
"do both live agents lose in the same regime?" and the acceptance-checklist
"entry overlap vs live agents" can only be answered from WFO/paper logs, not
realized live fills. Add `agent_id` tagging to the live position write path
before relying on any live-overlap number.

---

## Phase 1 — Longer-History 4h Regime Candidates

**Why:** 1h surfaces produced too many noisy near-misses. The 4h timeframe may
produce fewer but cleaner regime-conditioned entries and lower execution noise.

Priority lanes:

| Priority | Symbol | TF | Surface |
|----------|--------|----|---------|
| 1 | ETHUSDT | 4h | regime-conditioned trend/reversal |
| 2 | BTCUSDT | 4h | regime-conditioned trend/reversal |
| 3 | SOLUSDT | 4h | only if materially different from live SOL 1h |
| 4 | AVAXUSDT | 4h | only with strict drawdown controls |

Avoid BNB first; recent BNB 1h evidence was too silent or concentration-heavy.

Design requirements:

- explicit BTC/market regime gate,
- no entry if ATR regime is extreme unless strategy is specifically volatility
  breakout,
- bounded WFO trade target: 20-60 trades,
- avoid single-window profit concentration,
- force long-only first unless short-side execution is separately reviewed.

**Gate profile:** run discovery under `standard` (the script default), which
requires ≥ 20 WFO trades. Do **not** drop to `sparse_trend_3_2` for 4h here — a
4h candidate that cannot reach 20 trains/test trades is too sparse to validate at
bootstrap=1000 and contradicts the 20-60 trade design target. If 4h proves
structurally too sparse on every lane, that is a reason to abandon the 4h surface,
not to lower the gate.

Suggested first lane (explicit profile for clarity):

```bash
FAMILIES=regime_gated_pullback_overlay,breakout_retest_overlay \
MAX_RUNS=80 \
GATE_PROFILE=standard \
./scripts/run_autoresearch_campaign_remote.sh ETHUSDT 4h w7-eth-4h-regime
```

Stop this phase if best candidates across the campaign repeatedly show any of:

- WFO trades < 20 (cannot clear the `standard` gate the run is scored against),
- Sharpe < 0.5,
- DD > 10%,
- P(loss) > 25%,
- or profit concentration > 50%.

These match the live `standard` gate — a "near-miss" that still fails one of
them is not promotable, so do not keep retuning the same lane past it.

---

## Phase 2 — Bounded High-Density Families

**Why:** The portfolio needs more than 1-2 trades/month per agent, but Wave 6
showed that simply densifying overlays can destroy robustness. Density must be a
design constraint, not a side effect of loosening thresholds.

Define new family behavior before running broad campaigns:

- target 20-80 WFO trades,
- max one entry per symbol per N bars,
- time-stop exits included in backtest and live parity,
- no repeated buys in the same volatility impulse,
- optional cooldown after stop loss,
- reject candidates whose added trades reduce Sharpe below 0.5.

Candidate family ideas:

| Family | Hypothesis |
|--------|------------|
| `range_reversion_bounded` | Mean reversion only inside low-volatility ranges |
| `trend_continuation_bounded` | Enter after trend confirmation and shallow pullback, capped by cooldown |
| `volatility_breakout_bounded` | Trade squeeze expansion only with ATR and volume confirmation |
| `regime_router_bounded` | Route between range and trend sub-strategies instead of stacking all votes |

First implementation should be one family, not four at once. Start with
`range_reversion_bounded` because it is most different from the live SOL
trend-pullback overlay.

Promotion note: high-density candidates need extra overlap analysis. A strategy
with many trades can appear independent by symbol but still lose in the same
market shock windows.

---

## Phase 3 — Funding / Carry / Crowding Surface

**Why:** Funding logic is a different primitive from candle-pattern technical
signals. Earlier funding overlays were light sweeps, not a full surface design.

**Do not re-run the prior sweep.** `funding_extreme_overlay` already ran in Wave 2
and Wave 3 on ETHUSDT and BNBUSDT (ledger "Wave 3 — Bridge + funding"): ETH/BNB
funding 1h returned 0 near-misses and REJECT; the one BNB funding lane that traded
(28 WFO trades) was Sharpe −0.16, DD 13.9%. That family is a single crowded-funding
mean-reversion vote stacked onto the existing aggregator. A new attempt must change
the surface, not the symbol — the two deltas versus that prior sweep are:

- treat funding/crowding as the **primary** entry trigger, not one overlay vote;
- condition on funding *normalization* (reversal of the extreme), not the extreme
  level alone, to avoid entering mid-cascade.

The cost-netting and cooldown requirements below were already nominal in the old
sweep; they are necessary but not the differentiator. If the new brief reduces to
"the same overlay vote on a new pair," skip it.

Research brief:

- futures only,
- long when funding/crowding is extreme but price structure confirms reversal,
- avoid entries directly into liquidation cascades,
- include funding costs in expected return if available,
- require cooldown after funding normalization.

Best target symbols:

- BTCUSDT,
- ETHUSDT,
- SOLUSDT.

Avoid broad alt baskets until slippage and funding-data quality are verified.

Gate additions for this surface:

- positive return after funding-cost adjustment,
- no single funding event contributes > 25-30% of profit,
- no entry if exchange-side SL/TP placement is not supported.

Run this only after confirming funding data coverage in production DB.

---

## Phase 4 — Short-Side / Two-Sided Candidates

**Why:** Current live technical edge is long-biased. Additional agents may remain
correlated during broad selloffs unless a controlled short-side surface exists.

Precondition:

- futures execution supports the exact short lifecycle safely,
- risk manager blocks liquidation proximity for shorts,
- notifications clearly distinguish long and short entries,
- backtest exit model matches live futures short SL/TP behavior.

Do not launch short-side live from research alone. First create a dedicated
paper/shadow candidate and run an execution parity review.

Candidate surfaces:

- failed-breakdown retest short,
- trend-continuation short in confirmed downtrend,
- funding/crowding short when long funding is extreme.

Promotion gate remains standard + bootstrap=1000, plus manual execution review.

---

## Phase 5 — Longer-History Validation

**Why:** 2024-2026 WFO is useful but can still produce regime-specific winners.
For any candidate that looks robust, rerun with a longer available history when
the DB supports it.

Use longer history for:

- BTC 4h,
- ETH 4h,
- any funding/carry strategy,
- any candidate with only 20-25 WFO trades.

Decision rule:

- If longer history flips OOS negative or doubles P(loss), reject.
- If longer history lowers return but keeps positive OOS, acceptable DD, and
  stable concentration, keep as lower-confidence `TRACK_CONFIG`.

---

## Recommended Execution Order

| Step | Action | Expected Outcome |
|------|--------|------------------|
| 1 | Continue forward monitoring SOL overlay + sentiment-macro | real execution/frequency evidence |
| 2 | ETHUSDT 4h regime-conditioned campaign | best next independent candidate surface |
| 3 | BTCUSDT 4h regime-conditioned campaign | lower-noise BTC lane, not closed by BTC 1h result |
| 4 | Design one bounded high-density family | avoid blind parameter sweeps |
| 5 | Run bounded family on ETH/BTC/SOL 1h and 4h | seek 20-80 WFO trades without overtrading |
| 6 | Funding/crowding research brief and data audit | different signal primitive |
| 7 | Short-side feasibility review | only if execution/risk parity is proven |

Stop after each phase if no candidate reaches `promotion_candidate=true`. Do not
chain campaigns just because compute is available.

---

## Candidate Acceptance Checklist

Before adding any new tracked config:

- [ ] Standard gate passed at bootstrap=100.
- [ ] `eligible_for_bootstrap_1000=true`.
- [ ] bootstrap=1000 passed.
- [ ] Entry overlap checked versus:
  - `agent_sol_1h_trend_pullback_overlay_live`,
  - `agent_sentiment_macro`,
  - any other active promoted agent.
- [ ] Profit concentration is not single-window dominated.
- [ ] Runtime/backtest parity fields are explicit in config.
- [ ] Risk file exists for the exact `agent_id`.
- [ ] Compose service and Prometheus target are added only after promotion.
- [ ] Live notional starts small; no scaling before forward evidence.

---

## What Not To Do

- Do not rerun BTCUSDT 1h standalone/overlay without a new hypothesis.
- Do not rerun BNBUSDT 1h standalone; the surface was silent.
- Do not resurrect AVAX/ETH Wave-2 #0004; bootstrap=1000 rejected them.
- Do not deploy from bootstrap=100.
- Do not lower gates to reach 5-10 agents.
- Do not add more SOL 1h technical variants without entry-overlap proof.

---

## Success Definition

The next search cycle succeeds when it produces at least one additional candidate
that:

1. passes bootstrap=1000,
2. adds low-overlap entries versus current live agents,
3. has a documented runtime parity config,
4. starts with conservative live risk,
5. and improves expected combined portfolio trade count without increasing
   correlated drawdown.

The 5-10 agent target remains the long-term portfolio objective. The immediate
next milestone is **candidate #2**, not filling all remaining slots at once.
