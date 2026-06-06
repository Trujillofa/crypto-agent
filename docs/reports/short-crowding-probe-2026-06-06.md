# Short Crowding Probe — 2026-06-06

**Brief:** [`short-side-parity-audit-v0.md`](../specs/short-side-parity-audit-v0.md)
**Script:** `scripts/probe_short_crowding.py`
**Data:** production TimescaleDB — `perp_basis_metrics` + `funding_rates` + `ohlcv` (read-only)
**Window:** 2024-01-01 → 2026-06-01, 1h, `binance_usdm`

---

## Probe question

> When perp crowding is extremely bullish, do short entries have positive forward
> expectancy or reduced adverse excursion?

---

## Probe gates (v0)

| Gate | Threshold |
|------|-----------|
| Events | ≥ 20 per symbol **or** ≥ 80 pooled |
| Short forward edge | net mean > 0.15% on 12h and/or 24h (after 0.08% round-trip fee) |
| Short MAE edge | ≥ 10% lower mean short MAE vs all-hours baseline |
| Concentration | max single-event share of positive forwards ≤ 50% |
| Month dominance | max single-month share ≤ 40% |
| Cross-symbol | BTC/ETH/SOL not contradictory on pooled scenarios |

Tails tested: **5%** and **10%** on `basis_bps`, `premium_index`, `funding_rate`, plus
combined premium+funding and normalization-from-positive premium.

---

## Verdict: **WEAK_EDGE** (lane closed at probe)

Exit code 1. Large event samples, but **no scenario passes all gates**.

**Do not** write a standalone short surface brief, strategy class, paper shadow,
autoresearch, or live futures short work from this probe.

---

## Headline read

| Symbol | Bars | Short forward in positive premium tail5 | Short MAE vs baseline | Interpretation |
|--------|------|----------------------------------------|----------------------|----------------|
| BTCUSDT | 20,707 | **−0.18% / −0.42%** (12h/24h) | MAE worse (−41% / −44% Δ label = short MAE lower in tail) | Crowded-long windows **continue up** — shorts lose on drift |
| ETHUSDT | 20,707 | ~flat to **+0.12%** 24h on funding/combined | MAE ~flat (+1% to −3%) | No durable edge; net forward after fees below gate |
| SOLUSDT | 20,707 | **−0.30% / −0.68%** | MAE lower in tail (−31% / −34%) | Same as BTC — continuation dominates short entries |

**Normalization-from-positive** samples are sparse (76–258 events) and mostly negative
short forward on BTC/SOL.

**Combined premium+funding** is the densest crowding slice (~700 events/symbol at tail5)
but short forward is negative on BTC/SOL and only marginally positive on ETH before
concentration/month gates fail.

---

## Why gates failed

1. **Forward edge:** Positive perp crowding coincides with **upward continuation**, not
   short profit. Mean short forward is negative on BTC/SOL across premium/funding/combined
   tails. ETH shows weak positive raw forward on some funding scenarios but net edge
   after fees does not clear 0.15% consistently across horizons.

2. **MAE-only passes insufficient:** Several scenarios show ≥10% short MAE improvement
   (less upward excursion vs baseline), but that is **not** paired with positive short
   forward — the probe requires edge OR MAE pass **plus** concentration gates.

3. **Concentration / month dominance:** Many tail5 scenarios exceed **40%** max month
   share (BTC combined 63%, ETH combined 62%, SOL combined 55%). Event concentration
   also high on positive-forward subsets.

4. **Cross-symbol contradiction:** BTC/SOL negative short forward vs ETH weak-positive
   on overlapping scenarios — pooled promotion would be ambiguous even if single gates
   cleared.

---

## Comparison to basis long-filter probe

| Probe | Crowded positive premium | Forward read | MAE read | Outcome |
|-------|-------------------------|--------------|----------|---------|
| Basis long filter (Jun 5) | Long continuation + worse long MAE | Drift up | Worse for longs | Filter-first (rejected on SOL overlay WFO) |
| **Short crowding (Jun 6)** | Short entries in same windows | **Drift up hurts shorts** | Sometimes lower short MAE | **NO_PULSE for short entry** |

The primitives are consistent: extreme bullish crowding = **continuation**, not mean
reversion. That supports **risk management** thinking, not opening shorts into the crowd.

---

## Decision

| Action | Status |
|--------|--------|
| Close short crowding entry lane | **Yes** — probe WEAK_EDGE |
| Standalone short surface brief | **No** |
| Strategy / backtest lane | **No** |
| Paper shadow / live futures short | **No** |
| Keep `perp_basis_metrics` + funding infra | **Yes** — reusable for other surfaces |

---

## Re-run command

```bash
ssh crypto-agent "cd /opt/crypto-agent && docker run --rm \
  --network crypto-agent_crypto-net \
  -v /opt/crypto-agent:/app -w /app -e PYTHONPATH=/app \
  --env-file /opt/crypto-agent/.env \
  -e POSTGRES_HOST=timescaledb -e DB_HOST=timescaledb \
  crypto-agent-agent_sentiment_macro:latest \
  python scripts/probe_short_crowding.py"
```
