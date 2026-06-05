# Basis / Premium Risk Filter — Surface v0

**Status:** **CLOSED** — Phase 2 WFO A/B rejected filter-first lane (no live deploy)
**Date:** 2026-06-05
**Prerequisite:** [`basis-premium-probe-2026-06-05.md`](../reports/basis-premium-probe-2026-06-05.md) (**HAS_PULSE**)
**Data brief:** [`basis-premium-data-ingestion-brief-v0.md`](./basis-premium-data-ingestion-brief-v0.md)
**Implementation brief:** not written; Phase 2 did not pass

---

## Role (v0)

**Default-off risk filter / router — not a strategy vote, not a standalone entry.**

The cheap probe showed that **extreme positive premium** tails (crowded perp long)
coincide with:

- positive 12h/24h forward drift on BTC/SOL, **and**
- **worse** long adverse excursion (MAE) vs baseline

That pattern supports **blocking or downweighting longs** in crowded regimes, not
opening new longs because premium is high.

| Role | v0 | Deferred |
|------|-----|----------|
| Risk filter on existing long stacks | **Yes** | — |
| Standalone basis entry strategy | **No** | Do not autoresearch primary entries |
| Short-side live behavior | **No** | Research-only after long filter validated |
| Strategy aggregator vote | **No** | Router after consensus BUY only |

---

## Why filter-first (probe read)

| Observation | Implication |
|-------------|-------------|
| Positive premium tail → forward up | Continuation exists but is **not** a clean buy signal |
| Positive premium tail → MAE worse (~25–40% vs baseline on BTC/SOL) | Crowded long = **more drawdown pain** per hold |
| Negative premium / normalization → MAE better on ETH/SOL | Possible **confidence** context only; forward edge thin |
| Large event counts (~1k+ per tail) vs session router sparse collapse | Filter may remain **viable** if rule is selective |

**Lesson from session router v1:** a filter that only wins by skipping most trades is
**REJECT** — same ≥70% trade-count floor applies here.

---

## Hypotheses (filter lane)

**HYP-F001:** Blocking consensus **BUY** when `basis_bps` or `premium_index` is in an
extreme **positive** tail reduces max DD and/or bootstrap P(loss) on the promoted SOL
1h overlay **without** collapsing WFO trade count below 70% of baseline.

**HYP-F002:** Risk improvement is driven by **better hold quality** (lower realized
adverse excursion / drawdown), not merely fewer trades.

**HYP-F003:** Negative-premium normalization may **relax** blocks (optional v0.1) only
if WFO proves DD/P(loss) improve with trade count still viable — not assumed at v0.

---

## Inputs (read-only)

Joined at bar time from production DB:

| Field | Table | Use |
|-------|-------|-----|
| `basis_bps` | `perp_basis_metrics` | Primary crowding signal (mark–index dislocation) |
| `premium_index` | `perp_basis_metrics` | Exchange-native premium; cross-check / alt threshold |
| `time`, `symbol`, `timeframe` | `perp_basis_metrics` + `ohlcv` | Alignment with overlay bars |
| `funding_rate` | `funding_rates` | Optional context at read/probe time only — **not** stored in filter row |

Exchange v0: `binance_usdm`. Symbols: **BTCUSDT, ETHUSDT, SOLUSDT** (1h).

Thresholds derived from **rolling or full-sample percentiles** on the probe window
(default candidates: top **5%** / **10%** positive tail on `basis_bps`).

---

## Initial rule candidates (research order)

### R1 — Block crowded long (primary v0)

When consensus signal is **BUY** and at bar `t`:

- `basis_bps >= positive_tail_threshold` **or**
- `premium_index >= positive_tail_threshold_premium`

→ convert BUY to **HOLD** (same semantics as session router: exits unchanged).

**Default tail:** 5% (probe-strongest); 10% as reshape only with new probe note.

### R2 — Downweight (optional, not v0 live)

Reduce position size when R1 would block but overlay confidence is high — **out of
scope** until R1 backtest A/B passes.

### R3 — Negative normalization confidence (optional v0.1)

When premium **normalizes** from extreme negative into median band, **do not** add new
entries; only consider **lifting** an R1 block if WFO shows strictly better risk with
≥70% trade retention. Probe: SOL `normalization basis_bps tail5` passed MAE gate only.

**Forbidden in v0:** enter long **because** premium is extreme negative or normalized.

---

## First targets

| Priority | Stack | Rationale |
|----------|-------|-----------|
| 1 | `agent_sol_1h_trend_pullback_overlay_live` paper twin / WFO config | Only promoted technical; probe on SOL strongest forward positive tail |
| 2 | BTC/ETH overlay candidates (future) | Only after SOL filter A/B passes |
| — | `agent_sentiment_macro` | **Do not** attach filter in v0 — independent lane |

Live overlay stays **ungated** until Phase 4 paper shadow passes.

---

## Filter placement (engine order)

Mirror session liquidity router:

```
aggregator consensus → flat SELL suppress → EMA200 → basis premium risk filter → cooldown
```

- **BUY → HOLD** when R1 fires
- **SELL / exits:** unchanged
- **`enabled: false` default** in all prod configs
- Backtest must increment `blocked_buy_count` (or `blocked_basis_filter_count`) when filter fires

---

## Validation order

| Phase | Work | Gate |
|-------|------|------|
| **0** | This surface brief | Done when merged |
| **1** | Cheap **backtest overlay** A/B on prod DB (full period + wiring guard) | `blocked_buy_count > 0` on filtered run |
| **2** | Formal **WFO A/B** (train=3mo, test=2mo, bootstrap=100) | Hard stops below |
| **3** | Paper shadow agent (distinct `AGENT_ID`, ≥4 weeks) | Only if Phase 2 pass |
| **4** | Autoresearch family | **Only** if Phase 2 pass + implementation brief; 40-run max SOL first |
| **5** | Live | Human approval + overlap + Phase 3 |

**No autoresearch until Phase 2 criteria are met.** No implementation brief until
Phase 1 wiring guard passes.

---

## Phase 1 — Backtest A/B (before WFO)

| Run | Config |
|-----|--------|
| A | `settings.sol_1h_trend_pullback_overlay_paper.yaml` (filter off) |
| B | `settings.sol_1h_trend_pullback_overlay_paper_basis_filter.yaml` (filter on, R1 tail5) |

Same symbol, timeframe, date range, execution model as session router Phase 2.

**Wiring guard:** B must show `blocked_buy_count > 0`. If zero → invalid run.

**Phase 1 fail → stop** before scheduling WFO.

---

## Phase 2 — WFO A/B hard stops

Reject filter lane if **any** of:

| # | Criterion |
|---|-----------|
| 1 | `blocked_buy_count == 0` on filtered run |
| 2 | Filtered `wfo_total_trades` **< 70%** of baseline |
| 3 | Filtered `wfo_total_trades` **< 20** |
| 4 | Filtered OOS return **< 50%** of baseline (when baseline OOS > 0) |
| 5 | DD / P(loss) / concentration **not clearly better** (filtered ≤ baseline on DD and P(loss); concentration within +10pp) |
| 6 | Filter fires on **too few** rows (blocked count implies <5% of baseline BUY attempts over full backtest — wiring/threshold bug) |
| 7 | **Concentration:** single month/event window contributes >50% of filtered risk improvement |

**Pass direction:** filtered run improves **risk** (DD, P(loss), concentration) with
**viable** trade count; OOS return not materially collapsed.

Planned runners (implementation PR): `scripts/run_basis_filter_wfo_ab.sh`,
`scripts/evaluate_basis_filter_wfo_ab.py` (mirror session router pair).

---

## Phase 3 — Paper shadow (only if Phase 2 pass)

- Service: `sol-1h-trend-pullback-overlay-basis-filter-paper`
- Distinct `AGENT_ID`; **no live orders**
- Run ≥4 weeks alongside ungated live overlay
- Track: `blocked_buy_count`, fills, closed trades, PnL vs live

---

## Out of scope (v0)

- Standalone `basis_primary` / `premium_entry` strategy
- Short-side entries or live shorts
- Cross-exchange basis
- Autoresearch before Phase 2 pass
- Retuning tail % without new probe doc (one reshape max in v0.1)
- Attaching filter to live `agent_sol_1h_trend_pullback_overlay_live` without Phase 3

---

## Success / stop

| Outcome | Action |
|---------|--------|
| Phase 1 wiring fail | Fix join/threshold; no WFO |
| Phase 2 REJECT | **CLOSED** in ledger; keep `perp_basis_metrics` infra; filter code default-off |
| Phase 2 PASS, Phase 3 fail | No live; optional reshape once |
| Phase 2+3 PASS | Write implementation brief; consider autoresearch overlay family |
| Filter wins only by skipping >30% trades | **REJECT** (same as session router) |

---

## Phase 2 Result — CLOSED

Formal WFO A/B ran on Hetzner against the promoted SOL 1h overlay paper config
(`train=3mo`, `test=2mo`, `bootstrap=100`):

| Arm | WFO trades | OOS return | Max DD | P(loss) | Conc | basis_blocked_buy_count |
|-----|------------|------------|--------|---------|------|-------------------------|
| Baseline | 36 | -21.69% | 48.00% | 100.00% | 53.20% | 0 |
| Basis filter | 31 | -22.32% | 48.22% | 100.00% | 53.20% | 1 |

Evaluator verdict: **REJECT**. The filter wiring worked, but it blocked only one WFO
BUY and did not improve risk: drawdown worsened slightly, P(loss) stayed 100%, and
concentration was unchanged. Do not create a paper shadow service, do not write an
implementation brief, and do not attach this filter to live SOL overlay.

---

## References

- Probe: [`basis-premium-probe-2026-06-05.md`](../reports/basis-premium-probe-2026-06-05.md)
- Probe script: `scripts/probe_basis_premium.py`
- Coverage audit: `scripts/audit_basis_premium_coverage.py`
- Session router discipline (closed): `session-liquidity-router-implementation-brief-v0.md`
- Ledger: [`autoresearch-candidate-ledger.md`](../reports/autoresearch-candidate-ledger.md)
