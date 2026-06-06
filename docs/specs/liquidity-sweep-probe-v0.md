# Liquidity Sweep / Failed Breakout — Cheap Probe v0

**Status:** **CLOSED** — WEAK_EDGE on 2026-06-06 prod run; see [probe report](../reports/liquidity-sweep-probe-2026-06-06.md)
**Date:** 2026-06-06
**Prerequisite:** [research-reset-2026-06-06.md](../reports/research-reset-2026-06-06.md)
**Script:** `scripts/probe_liquidity_sweep.py`

---

## Role (v0)

**Read-only feasibility probe — not a strategy, not an overlay filter.**

Question:

> After a liquidity sweep or failed breakout, does price mean-revert over 6h/12h/24h
> with controlled MAE?

This uses **price structure** (range sweep + close rejection), not funding/premium/
session labels.

| Role | v0 | Deferred |
|------|-----|----------|
| Cheap pulse test | **Yes** | — |
| Strategy class | **No** | Only if HAS_PULSE |
| SOL overlay attachment | **No** | Never as first test |
| Live / paper / autoresearch | **No** | Downstream |

---

## Event definitions (1h bars)

Parameters (defaults):

| Param | Default | Meaning |
|-------|---------|---------|
| `lookback_bars` | 24 | Prior range window (24h) |
| `range_expansion_mult` | 1.2 | Bar range ≥ 1.2× mean prior range |
| `volume_expansion_mult` | 1.2 | Bar volume ≥ 1.2× mean prior volume |
| `forward_bars_6h` | 6 | Forward horizon |
| `forward_bars_12h` | 12 | Forward horizon |
| `forward_bars_24h` | 24 | Forward horizon |

### Failed upside breakout → **short** candidate

At bar `t` (entry at `close[t]`):

1. `high[t] > max(high[t-N:t])` — sweep above prior N-bar high.
2. `close[t] < max(high[t-N:t])` — close rejects back inside range.
3. Range expansion: `(high[t]-low[t]) ≥ range_expansion_mult × mean(range[t-N:t])`.
4. Volume expansion: `volume[t] ≥ volume_expansion_mult × mean(volume[t-N:t])`.

### Failed downside breakdown → **long** candidate

1. `low[t] < min(low[t-N:t])` — sweep below prior N-bar low.
2. `close[t] > min(low[t-N:t])` — close rejects back inside range.
3. Same range/volume expansion filters.

One event per bar per side (no overlap dedup in v0).

---

## Metrics per event

| Metric | Long | Short |
|--------|------|-------|
| Forward return | `(close[t+h]-close[t])/close[t]` | `(close[t]-close[t+h])/close[t]` |
| MAE | `(close[t]-min(low))/close[t]` | `(max(high)-close[t])/close[t]` |
| MFE | `(max(high)-close[t])/close[t]` | `(close[t]-min(low))/close[t]` |

Horizons: **6h, 12h, 24h** (bar counts on 1h data).

Baseline: all-bar random-entry MAE/forward (same horizons) for improvement comparison.

Fee drag: **0.08%** round-trip deducted from mean forward for gate checks.

---

## Symbols and window

- **Symbols:** BTCUSDT, ETHUSDT, SOLUSDT
- **Timeframe:** 1h
- **Window:** 2024-01-01 → 2026-06-01 (match prior probes)
- **Data:** `ohlcv` only (volume required)

---

## Pass gates (HAS_PULSE)

Per side (long breakdown / short failed breakout), per symbol **or** pooled:

| Gate | Threshold |
|------|-----------|
| Events | ≥ 20 per symbol **or** ≥ 80 pooled |
| Forward edge | net mean > **0.15%** on 6h, 12h, or 24h (after fees) |
| MAE edge | ≥ **10%** lower mean MAE vs baseline (any horizon) |
| Event concentration | max single-event share of positive forwards ≤ **50%** |
| Month dominance | max single-month share ≤ **40%** |
| Cross-symbol | BTC/ETH/SOL not contradictory (opposing forward signs) |

**HAS_PULSE:** at least one side (long or short) passes per-symbol or pooled with **all** gates.

**WEAK_EDGE:** events exist, some marginal stats, gates fail.

**NO_PULSE / SPARSE:** no events or below event floor.

Exit code: `0` = HAS_PULSE, `1` otherwise.

---

## Out of scope (v0)

- Strategy implementation, backtest engine integration
- WFO, autoresearch, paper compose
- Multi-timeframe confirmation
- Order-book / tick liquidity data
- Parameter sweep (fixed defaults only)

---

## After probe

| Verdict | Action |
|---------|--------|
| HAS_PULSE | Write standalone surface brief; then strategy/backtest lane |
| WEAK_EDGE | Close lane or reshape event definition once |
| NO_PULSE / SPARSE | Close lane; pick different primitive |

---

## Run command (Hetzner)

```bash
ssh crypto-agent "cd /opt/crypto-agent && git fetch && git checkout feat/liquidity-sweep-probe && docker run --rm \
  --network crypto-agent_crypto-net \
  -v /opt/crypto-agent:/app -w /app -e PYTHONPATH=/app \
  --env-file /opt/crypto-agent/.env \
  -e POSTGRES_HOST=timescaledb -e DB_HOST=timescaledb \
  crypto-agent-agent_sentiment_macro:latest \
  python scripts/probe_liquidity_sweep.py"
```
