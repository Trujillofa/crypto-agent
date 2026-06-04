# Forward Validation Snapshot — 2026-06-04

Operational read from Phase 0 of the [next-candidate-path](./autoresearch-next-candidate-path-2026-06-04.md).

## Live services (Hetzner)

| Service | Status |
|---------|--------|
| `agent_sol_1h_trend_pullback_overlay_live` | Up (healthy) |
| `agent_sentiment_macro` | Up (healthy) |

## Entry overlap (WFO OOS + live DB)

Refreshed `research/entry-overlap-sol-1h.json` on production:

| Pair | Shared entries | Jaccard |
|------|----------------|---------|
| SOL overlay live vs sentiment-macro | 0 / 32 vs 2 | 0.0 |
| SOL overlay live vs overlay paper | 31 / 32 vs 36 | 0.84 |

**Live DB (last 730d, agent-scoped):** 0 entries for both
`sol-1h-trend-pullback-overlay-live` and `sentiment-macro-bot` — agents redeployed
recently; accumulate fills before realized overlap is meaningful.

## PnL / trade count milestones

| Agent | Closed trades (DB) | Notes |
|-------|-------------------|--------|
| `sol-1h-trend-pullback-overlay-live` | 0 | No rows with this `agent_id` yet |
| `sentiment-macro-bot` | 96 (historical) | Includes pre-isolation rows |
| `default` (SOL since May) | — | Legacy bucket; do not use for forward reads |

**Milestone gates:** 5 / 10 / 20 closed SOL overlay trades — not reached under
correct `agent_id` attribution.

## Relative-strength probe (research stop)

ETH/BTC 1h default parameters: **14 events**, mean excess **−0.66%** vs BTC.
Full `relative_strength_rotation_standalone` implementation remains **paused**.

## Funding data audit (Phase 3 prerequisite)

| Symbol | Rows | Range |
|--------|------|-------|
| AVAXUSDT | 3,582 | 2023-01-01 → 2026-04-08 |
| BTCUSDT | 2,655 | 2024-01-01 → 2026-06-03 |
| ETHUSDT | 2,655 | 2024-01-01 → 2026-06-03 |
| SOLUSDT | 2,655 | 2024-01-01 → 2026-06-03 |

**Action taken (2026-06-04):** backfilled BTC/ETH/SOL via
`import_funding_rates.py` on production. Phase 3 funding-primary research can
use DB coverage; still requires a new surface brief (not the old overlay vote).

## Next operational steps

1. Weekly re-run: `scripts/run_entry_overlap_remote.sh` + PnL by agent once fills exist.
2. Backfill funding rates for BTC, ETH, SOL on production DB.
3. Do not launch RS autoresearch until probe is reshaped with positive crude edge.
4. Optional research: single bootstrap=1000 on ETH 4h `range_reversion_bounded` near-miss (formal reject record only).
