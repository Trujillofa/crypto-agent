# Relative Strength Rotation Implementation Summary

**Date:** 2026-06-04
**Status:** design-ready; implementation pending
**Surface:** `relative_strength_rotation_standalone`
**First target:** `ETHUSDT`
**First anchor:** `BTCUSDT`

---

## Executive Summary

The relative-strength rotation surface is the next candidate-search direction
after ~1,440 autoresearch runs produced only one deployable technical agent. It
is intentionally different from the exhausted surfaces: instead of tuning another
single-symbol indicator stack, it tests whether capital is rotating into a target
asset versus a market anchor, then enters on a controlled pullback.

The plan is ready as a design and implementation spec. It is **not runnable yet**.
The campaign command in the spec must wait until the cross-symbol reader path,
anchor plumbing, strategy, autoresearch family, and launcher passthrough are
implemented and tested.

---

## Related Documentation

| Document | Purpose |
|----------|---------|
| [`../specs/relative-strength-rotation-surface-v0.md`](../specs/relative-strength-rotation-surface-v0.md) | Canonical requirements and implementation plan for this surface |
| [`autoresearch-plan-overview.md`](./autoresearch-plan-overview.md) | Current portfolio/search state and document index |
| [`autoresearch-next-candidate-path-2026-06-04.md`](./autoresearch-next-candidate-path-2026-06-04.md) | Forward research plan and why this surface is next |
| [`autoresearch-postmortem-2026-06-04.md`](./autoresearch-postmortem-2026-06-04.md) | Why prior sweeps paused and what failed |
| [`autoresearch-candidate-ledger.md`](./autoresearch-candidate-ledger.md) | Campaign results, gate definitions, and promotion labels |
| [`entry-overlap-sol-1h.md`](./entry-overlap-sol-1h.md) | Independence evidence for the existing live SOL overlay vs sentiment-macro |

---

## Why This Exists

The original goal remains open: build 5-10 independent agents, each passing the
same promotion standard. The current portfolio has one deployable technical
agent:

- `agent_sol_1h_trend_pullback_overlay_live`

And one independent live sentiment/macro agent:

- `agent_sentiment_macro`

The failed sweeps were informative. BTC/BNB 1h standalone and overlay searches
did not produce promotable candidates. AVAX/ETH near-misses collapsed at
bootstrap=1000. ETH/BTC 4h regime and bounded surfaces produced no standard
passes. Repeating those lanes would mostly spend compute on already-failed
hypotheses.

Relative-strength rotation changes the research question from "is this symbol
locally oversold or trending?" to "is this symbol leading the market anchor, and
can we enter after a pullback without losing that leadership?"

---

## Implementation Scope

The implementation has six required pieces.

1. **Cross-symbol reader join**

   Add a no-lookahead join that attaches anchor data to each target decision row.
   Same-timeframe joins may use same-timestamp closed bars (`anchor_time <=
   target_time`). Different-timeframe joins must use completed anchor bars only.

   Required row fields:

   - `anchor_time`
   - `anchor_data_age_bars`
   - `anchor_close_price`
   - `anchor_return_fast`
   - `anchor_return_slow`
   - `target_return_fast`
   - `target_return_slow`
   - `rs_fast`
   - `rs_slow`
   - `rs_deterioration`

2. **Anchor plumbing**

   Thread the anchor symbol through the full research path:

   ```text
   scripts/run_autoresearch_campaign_remote.sh
      → ANCHOR_SYMBOL
      → scripts/autoresearch_loop.py --anchor-symbol
      → BacktestConfig.anchor_symbol
      → BacktestEngine
      → IndicatorReader cross-symbol join
      → strategy row fields
   ```

3. **Fail-fast behavior**

   A `relative_strength_rotation_standalone` run must fail fast if the anchor is
   missing or if anchor coverage is not available for the target WFO window. It
   must not silently run as a single-symbol campaign.

4. **Strategy implementation**

   Add and register `relative_strength_rotation`.

   The strategy must be standalone, long-only first, and require:

   - anchor non-panic / risk-on regime,
   - positive relative-strength persistence,
   - controlled pullback,
   - pullback resolution,
   - rotation-failure exit or exit rule,
   - strategy-local cooldown after its own emitted entries/exits/time stops.

5. **Autoresearch family**

   Add `relative_strength_rotation_standalone` to candidate generation. It must
   generate standalone overlays only and avoid the five-vote technical stack.

6. **Tests**

   Required tests:

   - cross-symbol join cannot use future anchor bars,
   - same-timeframe anchor joins allow same closed timestamp,
   - anchor fields exist in backtest rows,
   - missing anchor fails fast,
   - remote launcher maps `ANCHOR_SYMBOL=BTCUSDT` to `--anchor-symbol BTCUSDT`,
   - strategy holds when RS is absent/negative,
   - strategy buys only after RS persistence plus controlled pullback resolution,
   - strategy exits or blocks re-entry on rotation failure/cooldown.

---

## Out Of Scope For First Implementation

Do not add these until a baseline result exists:

- multi-anchor baskets,
- new market-data ingestion,
- short-side trading,
- SOL clone variants,
- broad BTC/BNB/AVAX campaigns,
- extra tunable parameters beyond the bounded spec list.

The first implementation should stay narrow: ETH target, BTC anchor, existing
OHLCV/indicator data, standard gate.

---

## First Campaign After Implementation

Run only after the implementation pieces above pass tests:

```bash
FAMILIES=relative_strength_rotation_standalone \
ANCHOR_SYMBOL=BTCUSDT \
MAX_RUNS=80 \
GATE_PROFILE=standard \
./scripts/run_autoresearch_campaign_remote.sh ETHUSDT 1h w9-eth-1h-relative-strength
```

If 1h overtrades or fails risk gates, review the failure mode before running:

```bash
FAMILIES=relative_strength_rotation_standalone \
ANCHOR_SYMBOL=BTCUSDT \
MAX_RUNS=80 \
GATE_PROFILE=standard \
./scripts/run_autoresearch_campaign_remote.sh ETHUSDT 4h w9-eth-4h-relative-strength
```

Do not launch BTC/BNB/SOL variants until ETH has a readable result.

---

## Promotion Rules Stay Unchanged

No candidate from this surface gets special treatment. The promotion path remains:

```text
b=100 discovery under standard gate
   → promotion_candidate / eligible_for_bootstrap_1000
   → bootstrap=1000 under the same standard gate
   → entry-overlap analysis vs live agents
   → tracked paper config
   → small live notional review
```

Do not paper or live-deploy from bootstrap=100 alone.

---

## Current Readiness

| Area | Status |
|------|--------|
| Research rationale | Ready |
| Formal spec | Ready |
| Implementation checklist | Ready |
| Campaign command | Ready after plumbing exists |
| Code implementation | Missing |
| Tests for implementation | Missing |
| Hetzner campaign | Not started |
| Deployable candidate | None yet |

The next engineering task is implementation, not more planning.
