# Daily Trend-Following Breadth Probe — Report

**Verdict:** **NO_PULSE**
**Date:** 2026-06-17
**Script:** `scripts/probe_daily_trend_breadth.py`
**Spec:** [daily-trend-breadth-probe-v0.md](../specs/daily-trend-breadth-probe-v0.md)
**Data:** production TimescaleDB (SSH tunnel `127.0.0.1:15434` → `crypto-agent-timescaledb`), read-only.

## Coverage audit (finest intraday TF per symbol)

Universe selection uses **finest available intraday TF** (prefer 1h, else 4h). Minimum
to run: **8 symbols**; prod holds **10 eligible** USDT pairs — probe ran.

| Symbol | TF | Bars | Span (d) | First | Quote vol (USDT) |
|--------|----|------|----------|-------|------------------|
| BTCUSDT | 1h | 20,725 | 882 | 2024-01-01 | 1.80e12 |
| ETHUSDT | 1h | 20,727 | 882 | 2024-01-01 | 1.21e12 |
| SOLUSDT | 1h | 21,106 | 898 | 2024-01-01 | 5.60e11 |
| XRPUSDT | 1h | 20,544 | 855 | 2024-01-01 | 3.49e11 |
| DOGEUSDT | 1h | 20,544 | 855 | 2024-01-01 | 2.74e11 |
| BNBUSDT | 1h | 20,544 | 855 | 2024-01-01 | 2.08e11 |
| ADAUSDT | 1h | 20,544 | 855 | 2024-01-01 | 9.35e10 |
| AVAXUSDT | 1h | 20,137 | 839 | 2024-01-01 | 6.67e10 |
| LINKUSDT | 1h | 20,544 | 855 | 2024-01-01 | 6.39e10 |
| LTCUSDT | 1h | 20,545 | 856 | 2024-01-01 | 5.31e10 |

Universe (fixed, liquidity-ranked): BTC, ETH, SOL, XRP, DOGE, BNB, ADA, AVAX, LINK, LTC.

## Config

- SMA window: **50** (frozen)
- Window: 2024-01-01 → 2026-06-01
- One-way fee: 0.04%
- Portfolio: equal-weight across currently-long symbols

## Pulse metrics (equal-weight portfolio)

| Metric | Value | Gate | Pass |
|--------|-------|------|------|
| Max single-symbol PnL share | **24.0%** | < 50% | **True** |
| State changes / OOS window (2mo) | **46.1** | ≥ 20 | **True** |
| Portfolio Sharpe | **0.07** | ≥ BH 0.30 | False |
| Portfolio max DD | **63.5%** | < BH 62.4% | False |
| Mean pairwise signal correlation | **0.593** | diagnostic | n/a |

## Vol-target variant (secondary)

Return −22.6% | Sharpe 0.05 | Max DD 57.7% — not a gate.

## Per-symbol standalone SMA50 (concentration inputs)

| Symbol | Return % | Role |
|--------|----------|------|
| XRPUSDT | +103.4 | largest positive leg (24% share) |
| ETHUSDT | +92.6 | |
| BNBUSDT | +84.4 | |
| BTCUSDT | +72.6 | |
| DOGEUSDT | +72.0 | |
| LINKUSDT | +3.0 | |
| SOLUSDT | +1.9 | |
| ADAUSDT | −39.1 | |
| AVAXUSDT | −51.5 | |
| LTCUSDT | −57.6 | |

## Interpretation

**Breadth fixed the Gate 2 structural failures** — concentration dropped from 92–100%
(3-symbol book) to **24%**, and implied OOS trade count rose to **46/state-changes per
2-month window** (vs 7–18 WFO trades). The diversification hypothesis partially holds on
count and PnL share.

**Risk-adjusted edge is gone at portfolio level** — equal-weight breadth portfolio
underperforms buy-and-hold on Sharpe (0.07 vs 0.30) and does not improve max drawdown
(63.5% vs 62.4%). Mean pairwise signal correlation **0.59** confirms symbols still move
together (shared beta); breadth added legs but not independent timing.

**Verdict: NO_PULSE** — close the trend family per spec; pivot to news/event lane. Do
not reshape into symbol cherry-picking or MA-length search.

## RBI guard

```bash
uv run python scripts/rbi_loop_guard.py \
  --lane-brief docs/specs/daily-trend-breadth-probe-v0.md \
  --probe-verdict NO_PULSE --pretty
```

Expected: `CLOSE_LANE`.

See [research-reset-2026-06-06.md](research-reset-2026-06-06.md) for banned surfaces.
