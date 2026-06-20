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

> **SUPERSEDED (2026-06-19).** This DEPLOY_LIVE row was measured under the pre-#94 cost bug
> (~0.4% RT, ~8× funding). At corrected costs the overlay neither reproduces the edge nor
> can fill live: it has 0 fills ever because `buy_threshold` (1.07/1.27) exceeds the
> single-vote cap of 1.0, and the threshold sweep shows no setting delivers both tradeable
> frequency and a surviving edge. See the terminal entry below and
> [research-consolidation-2026-06-19.md](./research-consolidation-2026-06-19.md).

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

## RBI Loop Records (post 2026-06-09)

Lanes are now processed through the supervised RBI guard + manifest system (see `docs/RBI_AUTORESEARCH_LOOP.md` and `config/autoresearch/rbi_loop.*.yaml`).

| Lane | Probe Verdict | Validation Outcome | RBI Decision | Artifacts |
|------|---------------|--------------------|--------------|-----------|
| basis-premium-filter-v0 | HAS_PULSE (2026-06-05 probe) | Phase 2 WFO A/B: only 1 block, no DD/P(loss)/concentration improvement vs baseline (31 vs 36 trades). Surface v0 spec marked CLOSED. | ITERATE_OR_CLOSE (standard gate fail) | `research/rbi_loop/basis-premium-filter-v0/decision.json`, `docs/reports/rbi-loop-basis-premium-filter-v0.md` (generated via `rbi_loop_from_manifest.py` + batch) |
| cross-venue-basis-v1 | HAS_PULSE | Standard gate fail: require-mode trade starvation, block-mode no risk improvement, bootstrap P(loss) far above gate. | ITERATE_OR_CLOSE (standard gate fail) | `research/rbi_loop/cross-venue-basis-v1/decision.json`, `docs/reports/rbi-loop-cross-venue-basis-v1.md`, brief `docs/specs/cross-venue-basis-dislocation-brief-v0.md` |
| cross-venue-dislocation-event-v0 | HAS_PULSE (17 scenarios across symbols/horizons/modes) | 30-run sweep 0/30 pass. WFO OOS negative across entire grid; event sparsity vs min-WFO-trades structural conflict; profits concentrated outside OOS coverage. | ITERATE_OR_CLOSE (standard gate fail) | `research/rbi_loop/cross-venue-dislocation-event-v0/decision.json`, `docs/reports/rbi-loop-cross-venue-dislocation-event-v0.md`, brief `docs/specs/cross-venue-dislocation-event-strategy-v0.md` |
| cross-venue-dislocation-event-v1 (rolling) | HAS_PULSE | 30-run sweep 0/30 pass (rc=0, ~350s). Full-period in-engine return negative across entire grid; thin per-event edge does not survive ATR sizing + executor exits; bootstrap P(loss) 67–99%. | ITERATE_OR_CLOSE (standard gate fail) | `research/rbi_loop/cross-venue-dislocation-event-v1/decision.json`, `docs/reports/rbi-loop-cross-venue-dislocation-event-v1.md`, brief `docs/specs/cross-venue-dislocation-event-strategy-v1.md` |
| higher-tf-regime-allocator-v0 | **NO_PULSE** (real-DB run 2026-06-15) | Cheap probe over SOL/BTC/ETH 1h, 4h+1d trending/high-vol regime, 2024-01-01→now (~21.5k labeled bars/scenario). Favorable-vs-unfavorable forward-return Δ ≤ 0.096% (bar 0.15%) and signs inconsistent across symbols → no separation. Stopped at the probe gate (no autoresearch). | CLOSE at Gate 1 (NO_PULSE) | `research/rbi_loop/higher-tf-regime-allocator-v0/probe-verdict.json`, `docs/reports/higher-tf-regime-probe-2026-06-14.md`, brief `docs/specs/higher-tf-regime-allocator-brief-v0.md` |

Higher-TF regime allocator was the #1 surface after cross-venue closed; it returns **NO_PULSE** on real data (one regime definition tested; not reshaped, per no-gate-shopping). Both top-ranked post-reset data-first surfaces (cross-venue, higher-TF regime) are now closed without an edge. Next: news/event calendar, then order-book/liquidations.

Cross-venue basis/dislocation was the #1 post-reset recommended surface (research-reset-2026-06-06). It was executed to closure through the RBI loop: both the fixed-threshold (v0) and rolling-threshold (v1) dislocation-event variants returned **0/30** under the standard gate, and the basis-premium filter showed no risk improvement. The probe machinery is reusable, but no edge is harvestable under house gates. The negative result is preserved here rather than drifting into more gate-shopping. Next data-first lane should target a different primitive (higher-timeframe portfolio regime allocator now leads — see `docs/RBI_AUTORESEARCH_LOOP.md` "Current Best Next Loop").

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

## Basis / perp premium risk filter (complete, CLOSED)

**Status:** data infrastructure retained; SOL overlay risk-filter lane closed.

Brief: `docs/specs/basis-premium-data-ingestion-brief-v0.md`.
Schema: `migrations/010_add_perp_basis_metrics.sql` (`mark_price`, `index_price`,
`premium_index`, `basis_bps`; join `funding_rates` at read time).
Audit: `scripts/audit_basis_premium_coverage.py` (≥95% OHLCV overlap, gap checks).

**Stop spending cycles** on SOL overlay routers/indicator filters.

**2026-06-05 update:** backfill done (prod ~21k bars/symbol). Audit **PROBE_READY**.
Probe [`basis-premium-probe-2026-06-05.md`](./basis-premium-probe-2026-06-05.md): **HAS_PULSE**
(extreme positive premium → forward drift + worse MAE on BTC/SOL; filter-first, not primary entry).
Surface: `basis-premium-risk-filter-surface-v0.md` (filter-first).

Formal WFO A/B on SOL overlay paper config (`train=3mo`, `test=2mo`, `bootstrap=100`):

| Arm | WFO trades | OOS return | Max DD | P(loss) | Conc | basis_blocked_buy |
|-----|------------|------------|--------|---------|------|-------------------|
| Baseline | 36 | -21.69% | 48.00% | 100.00% | 53.20% | 0 |
| Basis filter | 31 | -22.32% | 48.22% | 100.00% | 53.20% | 1 |

**Decision: CLOSED / REJECT.** Filter wiring worked, but it blocked only one WFO BUY
and did not improve DD, P(loss), or concentration. **Do not** create paper shadow,
**do not** attach to live SOL overlay, and **do not** run autoresearch on this filter.
Keep `perp_basis_metrics` data infra for future non-SOL or cross-venue briefs.

## Short-side parity audit (in progress)

**Status:** backtest P0 + paper short-entry wiring done; short crowding probe next.

**Goal:** determine whether the system can safely research and paper short strategies
without hidden mismatch in SL/TP, liquidation risk, sizing, Telegram reporting, PnL
accounting, and circuit breakers — **before** any short crowding/basis probe or WFO lane.

Brief: `docs/specs/short-side-parity-audit-v0.md`.
Tests: `tests/test_short_side_parity_audit.py`.

**Verdict (2026-06-05):**

| Layer | Safe for short research? |
|-------|------------------------|
| Live futures executor | **No** — LONG-only MVP |
| Strategy engine | **No** — SELL-from-flat suppressed |
| Paper executor | **Partial** — `allow_short_entry=True` only; not wired from `main.py` |
| Backtest engine | **Partial** — `allow_short` works; executor exit model long-biased |
| Portfolio models | Mostly yes |
| Telegram | No — no LONG/SHORT labels |

Paper short-entry wiring (Step 2) **done** (`fbab9ec`). Cheap short crowding probe
**closed** at probe stage — see below.

**Freeze:** no more SOL overlay filter/router modifications unless live Phase 0 data
shows a specific failure mode.

## Short crowding entry probe (complete, CLOSED)

**Status:** probe WEAK_EDGE — no standalone short surface.

Script: `scripts/probe_short_crowding.py`.
Report: [`short-crowding-probe-2026-06-06.md`](./short-crowding-probe-2026-06-06.md).

**2026-06-06 prod run:** BTC/SOL positive premium tail5 (~1k events/symbol) show
**negative** short forward (−0.18/−0.42% BTC 12h/24h; −0.30/−0.68% SOL). ETH weak
flat/slightly positive raw forward fails fee + concentration/month gates. Combined
premium+funding dense but same continuation pattern.

**Decision: CLOSED / WEAK_EDGE.** Crowded bullish perp regimes **continue up** — not a
short entry primitive. **Do not** write surface brief, strategy lane, paper shadow, or
live futures short MVP from this probe. Keep data infra.

## Research reset (2026-06-06)

**Status:** campaigns paused; Phase 0 only on live agents.

Report: [`research-reset-2026-06-06.md`](./research-reset-2026-06-06.md).

Summarizes failed surfaces, repeated failure modes (continuation > mean-reversion,
overlay attachment, MAE-only), banned hypotheses, and allowed next family (price
structure, not crowding/session/funding gates).

**Operating rule:** no autoresearch campaigns until next cheap probe shows HAS_PULSE.

## Liquidity sweep / failed breakout probe (complete, CLOSED)

**Status:** probe WEAK_EDGE — lane closed at cheap-probe stage.

Spec: [`liquidity-sweep-probe-v0.md`](../specs/liquidity-sweep-probe-v0.md).
Script: `scripts/probe_liquidity_sweep.py`.
Report: [`liquidity-sweep-probe-2026-06-06.md`](./liquidity-sweep-probe-2026-06-06.md).

**2026-06-06 prod run:** dense events (BTC 525 long / 429 short; similar ETH/SOL). Long
failed breakdown shows marginal positive 12h forward on BTC/ETH but **worse MAE** than
baseline. Short failed breakout negative forward on BTC/ETH (continuation up). No side
passes forward **and** MAE gates together.

**Decision: CLOSED / WEAK_EDGE.** Do not write surface brief, strategy lane, or
autoresearch. Next surface must be a **different primitive** (see research-reset).

## Wave 3 — Bridge + funding

| Date | Symbol | TF | Families | Runs | Passes | Near-miss | Output dir | Decision |
|------|--------|-----|----------|------|--------|-----------|------------|----------|
| 2026-06-03 | ETHUSDT | 1h | breakout_retest_bridge,near_miss_trade_lift | 50 | 0 | 3 | `research/ethusdt-1h-w3-breakout-bridge` | NEAR_MISS |
| 2026-06-03 | AVAXUSDT | 1h | regime_gated_pullback_bridge,standard_gate_bridge | 50 | 0 | 3 | `research/avaxusdt-1h-w3-regime-bridge` | NEAR_MISS |
| 2026-06-03 | ETHUSDT | 1h | funding_extreme_overlay | 50 | 0 | 0 | `research/ethusdt-1h-w3-funding` | REJECT |
| 2026-06-03 | BNBUSDT | 1h | funding_extreme_overlay | 50 | 0 | 0 | `research/bnbusdt-1h-w3-funding` | REJECT |

Wave 3 best: ETH breakout-bridge +3.96% OOS, 16 WFO trades, Sharpe 0.44 (still under 20 WFO trades). AVAX regime-bridge 19 trades, Sharpe -0.35. BNB funding 28 trades but Sharpe -0.16, DD 13.9%.

## Program consolidation (2026-06-19, TERMINAL)

**Status:** structural-probe program CLOSED; no viable forward-validation vehicle.

Canonical artifact: [`research-consolidation-2026-06-19.md`](./research-consolidation-2026-06-19.md).

Three investigations converge:

| Finding | Evidence | Result |
|---------|----------|--------|
| Tooling overcharged costs but hid no edge | Cost-corrected re-screen (#94–#98) | No closed lane revives at correct costs |
| SOL overlay cannot forward-validate | Threshold sweep (#99), [`overlay-threshold-sweep-2026-06-18.md`](./overlay-threshold-sweep-2026-06-18.md) | Frequency and edge mutually exclusive; "good Sharpe" rows are 1–2 trade concentration |
| Sentiment-macro not viable | Vol-filter sweep (#101), [`sentiment-vol-filter-sweep-2026-06-19.md`](./sentiment-vol-filter-sweep-2026-06-19.md) | Feed healthy (5144 obs, median 72, never <35); blocker was a SOL-miscalibrated `atr_pct≤0.005` gate, but recalibration is monotonically loss-making (−2.80% → −46.24% as the gate loosens) — the filter protected the strategy, no edge beneath it |

**Binding constraint** (forward-validation trade frequency on majors / OHLCV structure) has no
solution in the explored space. **Both candidate vehicles (overlay #99, sentiment-macro #101)
are now swept to a dead verdict at corrected costs.** Next step is not another probe — it is
(1) accept the terminal state, keep live services as idle monitors, and only deliberately
(2) open a new data-first primitive (news/event calendar) gated by a cheap-probe HAS_PULSE.

## Token-unlock 72h short — external-sourced data-first lane (2026-06-20, REJECT at Gate 1)

First post-consolidation data-first lane: an event-calendar short keyed to scheduled token
unlocks, sourced from the external `vibe-investing` repo's "72-Hour Shock" SSRN working paper
(52 hand-collected Binance unlock events; claimed 88.5% negative within 72h, mean −16.97%).

Brief: [`token-unlock-72h-short-probe-v0.md`](../specs/token-unlock-72h-short-probe-v0.md).
Script: `scripts/probe_token_unlock_shock.py` (re-fetches prices fresh from Binance — the paper's
own price files are self-described "structural templates", so its numbers were never independent).
Seed: `data/token_unlocks/binance_unlock_events.csv` (metadata only, outcomes excluded by design).
Artifacts: `research/rbi_loop/token-unlock-72h-short-v0/`.

| Metric | Paper claim | Probe (fresh Binance data) |
|--------|-------------|----------------------------|
| Events usable | 52 | 49/52 (good feasibility) |
| Negative 72h (raw) | 88.5% | **49.0%** |
| Mean raw 72h return | −16.97% | **+0.98%** |
| Negative vs BTC | 88.5% | **46.9%** |
| Mean BTC-relative 72h | −17.18% | **+1.09%** |
| Short PnL net of 1% haircut | — | **−1.98%** (worse than baseline) |

**Decision: REJECT / NO_PULSE.** The effect does not reproduce on independently-fetched prices;
the paper's headline was a data-reconstruction artifact. Do **not** subset ("cliff only", ">5%")
to chase a pass — banned post-hoc overfitting. Only justified follow-up is a v1 with **real
intraday** unlock timestamps from an external calendar; otherwise the hypothesis is retired.

This is the **fourth independent null** across families that all share one objective: predicting
crypto price direction (OHLCV structure, higher-TF trend, macro calendar, token unlock). Next
deliberate bet deliberately changes the **objective**, not the lane — see the carry brief below.

## Delta-neutral funding carry — first non-null (2026-06-20, HAS_PULSE at Gate 1)

**Status:** Gate 1 RUN — **HAS_PULSE**. The one structurally-different primitive never previously
tested: all three prior funding/basis probes measured forward *price* returns (directional).
Carry-as-yield (long spot + short perp, collect funding, market-neutral) tests a **different
objective function** and dissolves the trade-frequency constraint that killed both live vehicles.

Brief: [`funding-carry-neutral-probe-v0.md`](../specs/funding-carry-neutral-probe-v0.md).
Script: `scripts/probe_funding_carry_neutral.py` (read-only, public Binance futures funding API).
Artifacts: `research/rbi_loop/funding-carry-neutral-v0/`.

| Symbol | Net ann carry % (−2% drag) | Neg-funding % | Cum net % | Gates |
|--------|----------------------------|---------------|-----------|-------|
| BTCUSDT | +5.22 | 15.8 | +12.45 | H1+H2 ✅ |
| ETHUSDT | +5.49 | 15.6 | +13.10 | H1+H2 ✅ |
| SOLUSDT | +3.25 | 30.0 | +7.70 | H1+H2 ✅ |

**Read (do not oversell):** this is the **known crypto carry premium** re-confirmed on independent
data (2.4y of 8h funding), *not* discovered alpha. Value = first lane to survive Gate 1, and it did
so by being market-neutral yield not a forecast. ~5% net delta-neutral APY on deployed capital —
must be judged against the **opportunity cost of capital** (stablecoin/T-bill yield is comparable),
not against zero. The probe measured only the funding stream; it did **not** model leg
mark-to-market, rebalancing, liquidation/margin risk, or capital efficiency.

**Decision: advance to an execution-feasibility audit, NOT deployment.** HAS_PULSE authorizes only
a paired spot+perp delta-neutral execution audit (analogue of `short-side-parity-audit-v0.md`) —
the engine has no paired-position lifecycle and futures execution is LONG-only MVP. No campaign,
config, paper agent, or live risk before that audit passes. The pre-committed stop rule does **not**
fire — this is the non-null path it was waiting for.
