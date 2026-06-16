# Daily Trend-Following Breadth Probe — Report

**Verdict:** **NO_PULSE**
**Status:** **BLOCKED_ON_INGESTION**
**Date:** 2026-06-16
**Script:** `scripts/probe_daily_trend_breadth.py`
**Spec:** [daily-trend-breadth-probe-v0.md](../specs/daily-trend-breadth-probe-v0.md)
**Data:** production TimescaleDB (SSH tunnel localhost:15432), `ohlcv` 1h — read-only.

## Coverage audit (feasibility gate)

Prod `ohlcv` at 1h holds **4 USDT pairs** with ≥700d span — far below the
**15–20 liquid-pair** universe this lane requires. Other symbols (ADA, AVAX, DOGE,
DOT, LINK, XRP) exist at 1m/4h/5m but **not** at 1h with sufficient depth.

| Symbol | Bars | Span (d) | First | Quote vol (USDT) |
|--------|------|----------|-------|------------------|
| BTCUSDT | 21,399 | 891 | 2024-01-01 | 1.85e12 |
| ETHUSDT | 21,399 | 891 | 2024-01-01 | 1.24e12 |
| SOLUSDT | 21,399 | 891 | 2024-01-01 | 5.66e11 |
| BNBUSDT | 18,834 | 784 | 2024-01-01 | 2.03e11 |

**Blocked reason:** only 4 USDT pairs have ≥700d of 1h history; need ≥15.

Per spec guardrail #5: **do not run pulse metrics on a thin universe.** The probe
stopped after the coverage audit.

## Pulse metrics

**Not run** — blocked on ingestion.

| Metric | Value | Gate | Pass |
|--------|-------|------|------|
| Max single-symbol PnL share | n/a | < 50% | n/a |
| State changes / OOS window (2mo) | n/a | ≥ 20 | n/a |
| Portfolio Sharpe vs buy-hold | n/a | Sharpe ≥ BH | n/a |
| Portfolio max DD vs buy-hold | n/a | DD < BH | n/a |
| Mean pairwise signal correlation | n/a | diagnostic | n/a |

## Vol-target variant (secondary)

Not computed — coverage gate failed.

## Interpretation

The breadth hypothesis cannot be tested until prod ingestion backfills **≥15 liquid
USDT pairs** at 1h (ideally top-20 by quote volume) with ~2y of history. Running
on BTC/ETH/SOL/BNB alone would repeat the Gate 2 structural failure (7–18 WFO trades,
92–100% profit concentration) without adding independent trend legs.

This is a **data-ingestion blocker**, not a directional verdict on SMA50 breadth.
Once the universe is populated, re-run:

```bash
uv run python scripts/probe_daily_trend_breadth.py --json
```

## RBI guard

```bash
uv run python scripts/rbi_loop_guard.py \
  --lane-brief docs/specs/daily-trend-breadth-probe-v0.md \
  --probe-verdict NO_PULSE --pretty
```

Expected: `CLOSE_LANE` — cheap probe did not return HAS_PULSE.

## Next action

1. **Ingest** top ~15–20 liquid USDT 1h OHLCV pairs into prod (read-only probe
   prerequisites only — no strategy code).
2. Re-run this probe; if concentration stays ≥50% or correlation dominates,
   **close the trend family** per spec and pivot to news/event lane.
3. Do **not** reshape into symbol cherry-picking or MA-length search.

See [research-reset-2026-06-06.md](research-reset-2026-06-06.md) for banned surfaces.
