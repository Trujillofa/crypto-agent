# Autoresearch Candidate Ledger

Tracks bounded autoresearch campaigns and promotion decisions.

**Goal:** find up to 10 agents, but only if each survives bootstrap=1000 **and** adds independence.
Count is secondary to gates.

| Profile | Purpose |
|---------|---------|
| `standard` | Discovery / campaign pass |
| `promotion_candidate` | Pre-filter before scheduling bootstrap=1000 (stricter) |
| bootstrap=1000 | Final promotion gate (same thresholds as `standard`) |

`standard`: WFO trades ≥ 20, mean OOS Sharpe ≥ 0.5, OOS return > 0, max DD ≤ 10%, bootstrap P(loss) ≤ 25%, concentration ≤ 50%.

`promotion_candidate` (required before b=1000): WFO trades ≥ 20, OOS return ≥ 1%, max DD ≤ 8%, bootstrap P(loss) ≤ 20% at b=100, concentration ≤ 40%, Sharpe ≥ 0.5. Recorded in `last_result.json` as `eligible_for_bootstrap_1000`.

## Deployed / Tracked Candidates

| Date | Symbol | TF | Family | Runs | Passes | Best artifact | WFO trades | WFO Sharpe | OOS return | Max DD | P(loss) | Concentration | Decision |
|------|--------|-----|--------|------|--------|---------------|------------|------------|------------|--------|---------|---------------|----------|
| 2026-06-03 | SOLUSDT | 1h | trend_pullback_overlay | 30 | 2 | `/tmp/crypto-agent-autoresearch-tracked-sol-1h-trend-pullback-overlay/archive/experiment-autopilot-20260603-205536-773725-2bdb15-20260603-155544.json` | 26 | 1.72 | 19.13% | 7.39% | 14.10% | 28.65% | DEPLOY_LIVE |

Live service: `agent_sol_1h_trend_pullback_overlay_live` (`AGENT_ID=sol-1h-trend-pullback-overlay-live`).

## Campaign Log

| Date | Symbol | TF | Families | Runs | Passes | Near-miss | Output dir | Decision |
|------|--------|-----|----------|------|--------|-----------|------------|----------|
| 2026-06-03 | ETHUSDT | 1h | trend_pullback_overlay,combined_focus | 50 | 0 | 2 | `crypto-agent:/opt/crypto-agent/research/ethusdt-1h-pass1` | NEAR_MISS |
| 2026-06-03 | ETHUSDT | 1h | near_pass_expansion,standard_gate_bridge | 50 | 0 | 1 | `crypto-agent:/opt/crypto-agent/research/ethusdt-1h-bridge` | REJECT |
| 2026-06-03 | BNBUSDT | 1h | trend_pullback_overlay,combined_focus | 50 | 0 | 0 | `crypto-agent:/opt/crypto-agent/research/bnbusdt-1h-pass1` | REJECT |
| 2026-06-03 | BTCUSDT | 1h | trend_pullback_overlay,combined_focus | 50 | 0 | 0 | `crypto-agent:/opt/crypto-agent/research/btcusdt-1h-pass1` | REJECT |
| 2026-06-03 | AVAXUSDT | 1h | trend_pullback_overlay,combined_focus | 50 | 0 | 0 | `crypto-agent:/opt/crypto-agent/research/avaxusdt-1h-pass1` | REJECT |
| 2026-06-03 | SOLUSDT | 4h | trend_pullback_overlay,combined_focus | 50 | 0 | 0 | `crypto-agent:/opt/crypto-agent/research/solusdt-4h-pass1` | REJECT |

ETH pass1 best: OOS +4.05%, WFO trades 19, Sharpe 0.31 — failed DD/bootstrap/WFO count gates.
ETH bridge best: OOS +5.09%, WFO trades 15, Sharpe 0.55 — failed WFO count / DD / bootstrap.
BNB pass1 best: OOS +6.70%, WFO trades 26, Sharpe -0.36, conc 72% — failed Sharpe/concentration.
BTC pass1 best: OOS -2.23%, WFO trades 16, Sharpe -0.37 — no viable near-miss.
AVAX pass1 best: OOS +3.09%, WFO trades 60, Sharpe 0.38, **DD 23.7%**, P(loss) 84% — over-traded, risk gates fail.
SOL 4h pass1 best: OOS +3.21%, WFO trades **6**, Sharpe 0.14 — too sparse for standard gate.

Monitor: `ssh crypto-agent 'tail -f /opt/crypto-agent/research/<lane>/campaign.log'`

## Decision Labels

- `REJECT` — failed gates, no follow-up
- `NEAR_MISS` — promising; bridge or probe_1h follow-up
- `REVALIDATE_1000` — pass at bootstrap=100; schedule bootstrap=1000
- `TRACK_CONFIG` — bootstrap=1000 pass; add tracked paper config
- `DEPLOY_LIVE` — paper validated; live compose + Prometheus target

## Independence Notes

| Agent | Symbol | TF | Notes |
|-------|--------|-----|-------|
| agent_sol_1h_trend_pullback_overlay_live | SOLUSDT | 1h | Standard-gate technical stack + trend_pullback |
| agent_sentiment_macro | SOLUSDT | 1h | Sentiment/macro — overlap risk for second SOL 1h technical |
| agent_sol_sparse | SOLUSDT | 4h | trend_pullback sparse paper |
| agent_sol_panic_block_paper | SOLUSDT | 4h | panic block paper |

## Wave 2 — New signal families (code landed 2026-06-03)

Families in `scripts/autoresearch_loop.py`:

- `breakout_retest_overlay` — impulse breakout + retest reclaim
- `volatility_squeeze_overlay` — BB compression → expansion breakout
- `funding_extreme_overlay` — crowded funding mean-reversion vote
- `regime_gated_pullback_overlay` — trend_pullback + stricter ATR + BTC regime filter

| Date | Symbol | TF | Families | Runs | Passes | Near-miss | Output dir | Decision |
|------|--------|-----|----------|------|--------|-----------|------------|----------|
| 2026-06-03 | ETHUSDT | 1h | breakout_retest_overlay | 50 | 0 | 3 | `research/ethusdt-1h-w2a-breakout` | NEAR_MISS |
| 2026-06-03 | BNBUSDT | 1h | breakout_retest_overlay | 50 | 0 | 1 | `research/bnbusdt-1h-w2a-breakout` | REJECT |
| 2026-06-03 | BTCUSDT | 1h | breakout_retest_overlay | 50 | 0 | 0 | `research/btcusdt-1h-w2a-breakout` | REJECT |
| 2026-06-03 | SOLUSDT | 4h | volatility_squeeze_overlay | 50 | 0 | 2 | `research/solusdt-4h-w2b-squeeze` | NEAR_MISS |
| 2026-06-03 | AVAXUSDT | 1h | regime_gated_pullback_overlay | 50 | 0 | 5 | `research/avaxusdt-1h-w2b-regime` | NEAR_MISS |

Wave 2 best: AVAX regime +6.55% OOS, 18 WFO trades, P(loss) 41%; ETH breakout 19 trades but Sharpe −0.22 / P(loss) 88%.

Launch:

```bash
FAMILIES=breakout_retest_overlay MAX_RUNS=50 ./scripts/run_autoresearch_campaign_remote.sh ETHUSDT 1h w2a-breakout
```

## Priority Queue (remaining)

**Phase 1 pass1 complete (6 lanes, 300 runs): 0 new standard passes.**

**Wave 2 complete (250 runs): 0 standard passes.**
**Wave 3 complete (200 runs): 0 standard passes.** Cumulative search: **750+ runs**, **1** deployable (SOL 1h trend_pullback overlay live).

## Wave 4 — Bootstrap 1000 + specialist-only (2026-06-04)

Tracked overlays: `config/autoresearch/overlays/`

| Lane | Type | Output dir | WFO trades | OOS | Sharpe | P(loss) | Decision |
|------|------|------------|------------|-----|--------|---------|----------|
| AVAXUSDT 1h regime (W2 #0004) | bootstrap=1000 | `research/avaxusdt-1h-validation-b1000-b1000` | 17 | −0.72% | −0.47 | 81.1% | **REJECT** (was +6.55% @ b=100) |
| ETHUSDT 1h breakout (W2 #0004) | bootstrap=1000 | `research/ethusdt-1h-validation-b1000-b1000` | 9 | −2.35% | −0.31 | 87.8% | **REJECT** |
| SOLUSDT 4h standalone (50) | sparse_trend_3_2 | `research/solusdt-4h-w4-standalone` | 16 | +7.08% | 0.08 | 57.0% | **REJECT** (DD 12%, Sharpe/P(loss) fail) |
| SOLUSDT 1h mtf_breakout (30) | standard | `research/solusdt-1h-w4-mtf-breakout` | 151 | −48.5% | −3.74 | 100% | **REJECT** (over-trades, independence risk vs live SOL) |

**Takeaway:** Do not promote AVAX/ETH from Wave-2 near-misses; bootstrap=1000 collapses edge. SOL 4h without the five-vote stack does not beat prior overlay lanes. **Still 1 deployable agent** (SOL 1h trend_pullback overlay live). Combined target 8–20 trades/month requires more lanes or lower bar only after paper-forward evidence—not gate shopping.

## Wave 3 — Bridge + funding

| Date | Symbol | TF | Families | Runs | Passes | Near-miss | Output dir | Decision |
|------|--------|-----|----------|------|--------|-----------|------------|----------|
| 2026-06-03 | ETHUSDT | 1h | breakout_retest_bridge,near_miss_trade_lift | 50 | 0 | 3 | `research/ethusdt-1h-w3-breakout-bridge` | NEAR_MISS |
| 2026-06-03 | AVAXUSDT | 1h | regime_gated_pullback_bridge,standard_gate_bridge | 50 | 0 | 3 | `research/avaxusdt-1h-w3-regime-bridge` | NEAR_MISS |
| 2026-06-03 | ETHUSDT | 1h | funding_extreme_overlay | 50 | 0 | 0 | `research/ethusdt-1h-w3-funding` | REJECT |
| 2026-06-03 | BNBUSDT | 1h | funding_extreme_overlay | 50 | 0 | 0 | `research/bnbusdt-1h-w3-funding` | REJECT |

Wave 3 best: ETH breakout-bridge +3.96% OOS, 16 WFO trades, Sharpe 0.44 (still under 20 WFO trades). AVAX regime-bridge 19 trades, Sharpe -0.35. BNB funding 28 trades but Sharpe -0.16, DD 13.9%.
