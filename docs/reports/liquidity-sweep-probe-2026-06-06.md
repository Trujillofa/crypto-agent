# Liquidity Sweep / Failed Breakout Probe — 2026-06-06

**Verdict:** **WEAK_EDGE** — events dense, no side passes forward **and** MAE gates together
**Script:** `scripts/probe_liquidity_sweep.py`
**Spec:** [liquidity-sweep-probe-v0.md](../specs/liquidity-sweep-probe-v0.md)
**Prerequisite:** [research-reset-2026-06-06.md](./research-reset-2026-06-06.md)

---

## Run

| Field | Value |
|-------|-------|
| Host | Hetzner `crypto-agent` (prod TimescaleDB) |
| Branch | `feat/liquidity-sweep-probe` |
| Symbols | BTCUSDT, ETHUSDT, SOLUSDT |
| Timeframe | 1h |
| Window | 2024-01-01 → 2026-06-01 |
| Lookback | 24 bars |
| Fee drag | 0.08% round-trip |

---

## Event counts

| Symbol | Long (failed breakdown) | Short (failed breakout) |
|--------|-------------------------|-------------------------|
| BTCUSDT | 525 | 429 |
| ETHUSDT | 484 | 415 |
| SOLUSDT | 425 | 382 |
| **Pooled** | **1,434** | **1,226** |

Events are **not sparse**. Concentration and month dominance are fine (max month ~6%).

---

## Forward return (after fees)

### Long (failed downside breakdown)

| Symbol | 6h net | 12h net | 24h net |
|--------|--------|---------|---------|
| BTC | +0.10% | **+0.20%** | +0.11% |
| ETH | +0.04% | **+0.15%** | +0.04% |
| SOL | −0.09% | +0.09% | **+0.18%** |

BTC and ETH show marginal positive 12h net forward. SOL mixed across horizons.

### Short (failed upside breakout)

| Symbol | 6h net | 12h net | 24h net |
|--------|--------|---------|---------|
| BTC | −0.18% | −0.22% | −0.42% |
| ETH | −0.27% | −0.37% | −0.39% |
| SOL | +0.01% | −0.18% | −0.13% |

Short side is **negative** on BTC/ETH — same continuation pattern as short crowding:
failed upside breakouts are followed by further drift up, not mean-reversion down.

---

## MAE vs baseline

| Side | Pattern |
|------|---------|
| Long | Mean MAE **worse** than random-entry baseline (−6% to −27% “improvement”) |
| Short | Slight MAE improvement (+6% to +13%) but **negative forward** |

HAS_PULSE requires **both** forward edge (>0.15% net on any horizon) **and** MAE
improvement (≥10% lower than baseline). No symbol/side passes both.

---

## Gate summary

| Gate | Long | Short |
|------|------|-------|
| Events ≥ 20 | Pass (all symbols) | Pass (all symbols) |
| Forward > 0.15% net | Marginal (BTC/ETH 12h only) | Fail (BTC/ETH negative) |
| MAE ≥ 10% better | **Fail** (worse MAE) | Pass (weak) |
| Concentration ≤ 50% | Pass | Pass |
| Month ≤ 40% | Pass | Pass |
| Cross-symbol consistent | Long: mixed SOL 6h | Short: SOL slightly positive 6h vs BTC/ETH negative |

---

## Interpretation

1. **Liquidity sweeps are frequent** on 1h BTC/ETH/SOL — the event definition is not
   too narrow.
2. **Failed downside → long** shows weak positive drift on BTC/ETH but entries carry
   **higher** adverse excursion than baseline — not a controlled mean-reversion trade.
3. **Failed upside → short** repeats the crowding lesson: crypto perps continue up
   after upside sweeps; shorts lose with modest MAE help only.
4. This is **not** HAS_PULSE. It is not sparse enough to dismiss as NO_PULSE either.

---

## Decision

| Action | Verdict |
|--------|---------|
| Write standalone surface brief | **No** |
| Strategy class / backtest lane | **No** |
| SOL overlay attachment | **No** |
| Autoresearch campaign | **No** |
| Reshape event definition once | **Optional** — only if a clear structural tweak is hypothesized |
| Close lane | **Default** unless reshape is proposed |

**Lane status:** **CLOSED at probe** (WEAK_EDGE). Research reset discipline holds: no
campaigns until a different primitive shows HAS_PULSE.

---

## Commands

```bash
# Reproduce on Hetzner
ssh crypto-agent "cd /opt/crypto-agent && git checkout feat/liquidity-sweep-probe && \
  docker run --rm --network crypto-agent_crypto-net \
  -v /opt/crypto-agent:/app -w /app -e PYTHONPATH=/app \
  --env-file /opt/crypto-agent/.env \
  -e POSTGRES_HOST=timescaledb -e DB_HOST=timescaledb \
  crypto-agent-agent_sentiment_macro:latest \
  python scripts/probe_liquidity_sweep.py"
```
