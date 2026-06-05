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

**Cumulative search: ~1440+ runs** (incl. Wave 7 ~400), **1** deployable (`agent_sol_1h_trend_pullback_overlay_live`).
**Post-mortem:** [autoresearch-postmortem-2026-06-04.md](./autoresearch-postmortem-2026-06-04.md) — sweeps **paused** until live forward data or new surface brief.
**Next candidate path:** [autoresearch-next-candidate-path-2026-06-04.md](./autoresearch-next-candidate-path-2026-06-04.md).
**Plan overview / index:** [autoresearch-plan-overview.md](./autoresearch-plan-overview.md) — start here.

| Priority | Action | Status |
|----------|--------|--------|
| 1 | Forward validation: SOL overlay live + sentiment-macro | **active** |
| 2 | Phase 7: ETH/BTC 4h (complete) | **0 passes** — ETH bounded near-miss only |
| 3 | BTC/BNB 1h, AVAX/ETH W2 #0004 | **closed** |

## Wave 4 — Bootstrap 1000 + specialist-only (2026-06-04)

Tracked overlays: `config/autoresearch/overlays/`

| Lane | Type | Output dir | WFO trades | OOS | Sharpe | P(loss) | Decision |
|------|------|------------|------------|-----|--------|---------|----------|
| AVAXUSDT 1h regime (W2 #0004) | bootstrap=1000 | `research/avaxusdt-1h-validation-b1000-b1000` | 17 | −0.72% | −0.47 | 81.1% | **REJECT** (was +6.55% @ b=100) |
| ETHUSDT 1h breakout (W2 #0004) | bootstrap=1000 | `research/ethusdt-1h-validation-b1000-b1000` | 9 | −2.35% | −0.31 | 87.8% | **REJECT** |
| SOLUSDT 4h standalone (50) | sparse_trend_3_2 | `research/solusdt-4h-w4-standalone` | 16 | +7.08% | 0.08 | 57.0% | **REJECT** (DD 12%, Sharpe/P(loss) fail) |
| SOLUSDT 1h mtf_breakout (30) | standard | `research/solusdt-1h-w4-mtf-breakout` | 151 | −48.5% | −3.74 | 100% | **REJECT** (over-trades, independence risk vs live SOL) |

**Takeaway:** Do not promote AVAX/ETH from Wave-2 near-misses; bootstrap=1000 collapses edge. SOL 4h without the five-vote stack does not beat prior overlay lanes. **Still 1 deployable agent** (SOL 1h trend_pullback overlay live). Combined target 8–20 trades/month requires more lanes or lower bar only after paper-forward evidence—not gate shopping.

## Entry overlap — SOLUSDT 1h (2026-06-04)

Report: `docs/reports/entry-overlap-sol-1h.md` (WFO OOS, train=3mo test=2mo, ±1h tolerance).

| Pair | OOS entries A | OOS entries B | Shared | Jaccard | %A also in B |
|------|---------------|---------------|--------|---------|--------------|
| overlay_live vs sentiment_macro | 32 | 2 | 0 | 0% | 0% |
| overlay_live vs overlay_paper | 32 | 36 | 31 | 84% | 97% |
| sentiment_macro vs overlay_paper | 2 | 36 | 0 | 0% | 0% |

**Read:** Live SOL 1h overlay and sentiment-macro are **independent on OOS entry timing** (0 shared bars). Paper/live configs are the same signal stack (expected ~97% overlap). Live DB `positions`/`trades` now carry real per-agent `agent_id` (`bc309ae`, deployed 2026-06-04) — realized overlap is measurable from post-deploy fills onward; pre-deploy rows are bucketed as `'default'`.

## Wave 5 — BNB/BTC 1h standalone (2026-06-04, complete)

Families: `trend_pullback_standalone`, `breakout_retest_standalone`, `volatility_squeeze_standalone`. Gate: `sparse_trend_3_2`, bootstrap=100. Promotion pre-filter: `eligible_for_bootstrap_1000` in `last_result.json`.

| Symbol | Output dir | Runs | Passes | Best OOS | WFO tr | Sharpe | Max DD | P(loss) | Conc | Decision |
|--------|------------|------|--------|----------|--------|--------|--------|---------|------|----------|
| BNBUSDT | `research/bnbusdt-1h-w5b-standalone` | 50 | 0 | 0% | **0** | — | — | — | — | **CLOSED** — search surface silent |
| BTCUSDT | `research/btcusdt-1h-w5b-standalone` | 50 | 0 | +2.83% | 10 | 0.58 | 5.5% | 38% | 50.4% | **NEAR_MISS** — sparse, P(loss)/conc fail |

`w5-standalone` lanes (BNB/BTC) aborted: `run_autoresearch` import path (fixed `60702e3`); no usable archive.

**Read:** BNB 1h standalone is closed (0-trade best = wrong surface, not tuning). BTC 1h standalone found a small low-risk pocket but fails trade count / Sharpe for promotion. **Still 1 deployable agent** (SOL 1h overlay live).

## Wave 6 — BTCUSDT 1h overlay (2026-06-04, complete)

Families: `trend_pullback_overlay`, `combined_focus`, `standard_gate_bridge`. Gate: `standard`, bootstrap=100, 60 runs.

| Metric | Wave 5 standalone best | Wave 6 overlay best |
|--------|------------------------|---------------------|
| OOS return | **+2.83%** | **−2.23%** |
| WFO trades | 10 | 16 |
| Sharpe | 0.58 | −0.37 |
| Max DD | 5.5% | 10.8% |
| P(loss) | 38% | 94% |
| Concentration | 50.4% | 75.1% |
| Standard passes | 0 | **0** |

**Decision: REJECT / CLOSED.** Overlay stack **worse** than BTC standalone near-miss. Stop conditions met (Sharpe ≈ 0, P(loss) ≫ 20%, concentration high). **Do not schedule bootstrap=1000** on any Wave 6 config. BTC 1h overlay lane closed; pivot to new surfaces (longer history, bounded-density families), not more BTC 1h threshold work.

Output: `research/btcusdt-1h-w6-overlay`

## Wave 7 — New surfaces (2026-06-04, complete)

Per [autoresearch-next-candidate-path-2026-06-04.md](./autoresearch-next-candidate-path-2026-06-04.md). Gate: `standard`, bootstrap=100. **0 standard passes. 0 `promotion_candidate` eligibles.**

| Lane | Runs | Best OOS | WFO tr | Sharpe | DD | P(loss) | Decision |
|------|------|----------|--------|--------|-----|---------|----------|
| ETH 4h regime | 80 | −2.53% | 5 | −0.63 | 6.2% | 64% | **CLOSED** (too sparse) |
| BTC 4h regime | 80 | −1.30% | 1 | −0.36 | 1.3% | 100% | **CLOSED** (too sparse) |
| ETH 4h bounded | 50 | **+13.55%** | **24** | 0.48 | 20.3% | 56% | **NEAR_MISS** — DD/P(loss)/Sharpe fail |
| BTC 4h bounded | ~78 | −15.89% | 23 | −1.05 | 26.4% | 94% | **CLOSED** |
| BTC 4h funding-primary | 50 | 0% | **0** | — | — | 100% | **CLOSED** (no signals) |

**Read:** 4h regime overlays did not improve on ETH/BTC vs 1h closed lanes. Only **ETH 4h `range_reversion_bounded`** shows positive OOS with adequate trades but fails promotion pre-filter (DD 20.3%, P(loss) 56%). Optional: one b=1000 stress-test on ETH bounded best overlay — expect reject like AVAX. **No new deployable agent.**

Launcher: `scripts/run_phase7_4h_campaigns.sh`

## Wave 9 — B-SOL funding normalization (complete, CLOSED)

Tightly scoped sprint after prod probe reshape (`neg_tail_10pct` HAS_PULSE on SOL only).
**0 standard passes. 0 promotion_candidate eligibles.**

| Item | Value |
|------|-------|
| Runs | 80 |
| Best OOS | +0.82% |
| Best WFO trades | **15** (max across campaign; **0** runs ≥ 20) |
| Best Sharpe | 0.52 |
| Best DD | 7.4% |
| P(loss) / conc (best) | 53% / **100%** |
| Verdict | **CLOSED** — too sparse for standard gate; probe pulse did not transfer to WFO |

**Read:** Cheap probe counted 44 normalization **events** on funding ticks; backtest/WFO
produced at most **15** trades (28 runs had ≥10). Concentration and P(loss) fail on the
best config. **Do not** run bootstrap=1000. **Do not** deploy.

**Funding-primary on SOL:** closed.

Launcher: `scripts/run_b_sol_funding_norm_campaign.sh`

## Wave 10 — Option F volatility squeeze bounded (complete, CLOSED)

Prod probe (2026-06-05) showed BTC/ETH `HAS_PULSE` on crude 12h forward returns; SOL
`WEAK_EDGE` (excluded). Campaign: `volatility_squeeze_bounded`, paired
`time_stop_minutes = max_hold_bars * 60`, global EMA200 filter on in WFO.

**0 standard passes. 0 promotion_candidate eligibles. 80 runs total (40+40).**

| Symbol | Output dir | Runs | Passes | Best OOS | WFO tr | Sharpe | Max DD | P(loss) | Conc | Decision |
|--------|------------|------|--------|----------|--------|--------|--------|---------|------|----------|
| BTCUSDT | `research/btcusdt-1h-w10-btc-1h-vol-squeeze-bounded` | 40 | 0 | +5.82% | 33 | 0.80 | 13.3% | 64% | 59.4% | **REJECT** — DD/P(loss)/conc fail |
| ETHUSDT | `research/ethusdt-1h-w10-eth-1h-vol-squeeze-bounded` | 40 | 0 | −19.44% | 108 | −1.53 | 37.6% | 99% | 67.7% | **REJECT** |

**Read:** Probe thin edge (+0.02% / +0.15% crude 12h means) did not survive WFO with
stricter EMA200 gating and full backtest exits. BTC near-miss on return/Sharpe/trades
but fails risk gates. **Do not** run bootstrap=1000. **Do not** deploy.

**Vol squeeze bounded BTC/ETH:** closed.

Launcher: `scripts/run_option_f_vol_squeeze_campaign.sh`

## Session Liquidity Router v1 — SOL overlay americas gate (complete, CLOSED)

**2026-06-05** formal WFO A/B on prod DB (`train=3mo`, `test=2mo`, `bootstrap=100`):

| Arm | Config | WFO trades | OOS return | Max DD | P(loss) | Conc | blocked_buy |
|-----|--------|------------|------------|--------|---------|------|-------------|
| A ungated | `settings.sol_1h_trend_pullback_overlay_paper.yaml` | 36 | −21.69% | 48.00% | 100% | 53.2% | 0 |
| B gated | `settings.sol_1h_trend_pullback_overlay_paper_americas_gate.yaml` | 6 | −6.59% | 25.75% | 100% | 100% | 86 |

Artifacts: `research/solusdt-1h-session-router-wfo-ungated/`, `research/solusdt-1h-session-router-wfo-gated/`.
Phase 2 full-period backtest: 71 → 18 trades (25%). Evaluator: **REJECT** (sparse collapse 6/36 = 16.7%;
gated WFO trades &lt; 20; concentration worse).

**Decision: CLOSED.** Unconditional session pulse (americas 16–24 UTC probe on universe) did **not**
transfer to promoted SOL 1h trend-pullback overlay. Router code remains **default-off** reusable infra.
**Do not** enable on `agent_sol_1h_trend_pullback_overlay_live`. **Do not** add paper shadow service.
**Do not** retune hours (time-window overfit).

Probe report: `docs/reports/session-liquidity-router-probe-2026-06-05.md`.
Runner: `scripts/run_session_router_wfo_ab.sh`.

## Next lane — Basis / perp premium data (in progress)

**Status:** data infrastructure only (no strategy probe until audit passes).

Brief: `docs/specs/basis-premium-data-ingestion-brief-v0.md`.
Schema: `migrations/010_add_perp_basis_metrics.sql` (`mark_price`, `index_price`,
`premium_index`, `basis_bps`; join `funding_rates` at read time).
Audit: `scripts/audit_basis_premium_coverage.py` (≥95% OHLCV overlap, gap checks).

**Stop spending cycles** on SOL overlay routers/indicator filters. Next implementation:
`import_perp_basis_metrics.py` + prod backfill → audit `PROBE_READY` → cheap probe.

## Wave 3 — Bridge + funding

| Date | Symbol | TF | Families | Runs | Passes | Near-miss | Output dir | Decision |
|------|--------|-----|----------|------|--------|-----------|------------|----------|
| 2026-06-03 | ETHUSDT | 1h | breakout_retest_bridge,near_miss_trade_lift | 50 | 0 | 3 | `research/ethusdt-1h-w3-breakout-bridge` | NEAR_MISS |
| 2026-06-03 | AVAXUSDT | 1h | regime_gated_pullback_bridge,standard_gate_bridge | 50 | 0 | 3 | `research/avaxusdt-1h-w3-regime-bridge` | NEAR_MISS |
| 2026-06-03 | ETHUSDT | 1h | funding_extreme_overlay | 50 | 0 | 0 | `research/ethusdt-1h-w3-funding` | REJECT |
| 2026-06-03 | BNBUSDT | 1h | funding_extreme_overlay | 50 | 0 | 0 | `research/bnbusdt-1h-w3-funding` | REJECT |

Wave 3 best: ETH breakout-bridge +3.96% OOS, 16 WFO trades, Sharpe 0.44 (still under 20 WFO trades). AVAX regime-bridge 19 trades, Sharpe -0.35. BNB funding 28 trades but Sharpe -0.16, DD 13.9%.
