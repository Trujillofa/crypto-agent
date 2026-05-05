# Backtesting Follow-up

**Date:** 2026-05-05

## Scope

This follow-up documents the May 2026 read-only research pass across local, GitHub, and Hetzner, plus the recommended next backtesting step. The work stayed in research/backtest mode only; no live trading services were restarted and no live-order commands were run.

## Environment Check

- Local repo, GitHub, and Hetzner were aligned on `main` at commit `0825e14`.
- Hetzner production services were running and healthy: `agent_avax`, `agent_sol_sparse`, `agent_sentiment_macro`, `prometheus`, and `timescaledb`.
- Reliable historical data coverage was concentrated on `BTCUSDT`, `ETHUSDT`, `SOLUSDT`, `AVAXUSDT`, and `BNBUSDT` for `1h` and `4h`. `LINKUSDT 4h` existed but was stale after 2026-03-19, and sub-hour data was too sparse for this pass.
- Local DB-backed runs were not reliable because `.env` points `POSTGRES_HOST` at the Docker service name `timescaledb`, while no local TimescaleDB listener was available on `127.0.0.1:15432`.
- Hetzner one-off research containers were the correct execution target because they had the production market/indicator data.

## Results

### SOL sparse trend-pullback refresh

The existing `SOLUSDT 4h` sparse trend-pullback thesis was refreshed through `scripts/experiment_autopilot.py` on Hetzner. It no longer passed the sparse gate on the refreshed window:

| Metric | Refresh result |
|---|---:|
| WFO trades | 3 |
| OOS mean Sharpe | 0.50 |
| Profit concentration | 67.42% |
| Gate result | FAIL |

The failure was narrow but important: trades were below the minimum of `4`, and concentration exceeded the sparse-trend ceiling of `65%`. The result keeps the historical paper candidate useful as a reference, but it should not be promoted without fresh validation.

### Tier-1 broad screening

A Tier-1 screen was run across `BTCUSDT`, `ETHUSDT`, `SOLUSDT`, `AVAXUSDT`, and `BNBUSDT` on `1h` and `4h` using `scripts/autoresearch_universal.py`.

- No `1h` strategy produced a cross-validated winner.
- The useful candidates appeared only on `4h`.
- The strongest follow-up candidates were moving-average variants on `SOLUSDT`, `AVAXUSDT`, and `BNBUSDT`, so those were moved into focused walk-forward validation.

### Focused 4h MA walk-forward validation

Focused WFO tested long-only and short-enabled variants for the 4h moving-average candidates.

| Candidate | Mode | Total trades | Mean Sharpe | Sum return | Max window DD | Result |
|---|---|---:|---:|---:|---:|---|
| `AVAXUSDT 4h MA` | long-only | 21 | -1.7425 | -11.24% | n/a | Fail |
| `AVAXUSDT 4h MA` | allow short | 60 | -1.2550 | -17.23% | n/a | Fail |
| `SOLUSDT 4h MA` | long-only | 22 | 0.4125 | 3.09% | 5.41% | Research lead |
| `SOLUSDT 4h MA` | allow short | 55 | -0.1875 | -4.23% | n/a | Fail |
| `BNBUSDT 4h MA` | long-only | 21 | -1.8425 | -6.83% | n/a | Fail |
| `BNBUSDT 4h MA` | allow short | 45 | -1.0600 | -11.86% | n/a | Fail |

No candidate passed promotion gates. Short-enabled variants degraded results across all three symbols. The only useful research lead was `SOLUSDT 4h MA` long-only: it was positive, had acceptable drawdown in the observed WFO windows, and was materially less fragile than the AVAX/BNB variants, but it still lacked enough edge to promote.

## Current Recommendation

Do not promote any new strategy from this pass. Keep the existing SOL sparse trend-pullback preset in paper/research status only, and treat the refreshed failure as a warning against production promotion.

The next recommended backtest is a narrow `SOLUSDT 4h` long-only MA neighborhood and exit-model sweep. This is better than expanding symbols immediately because the broad scan already rejected the cross-symbol MA idea, while SOL long-only retained a small positive edge with controlled drawdown.

## Next Backtest Plan

1. Freeze the current WFO protocol so comparisons stay fair: same `SOLUSDT 4h` data source, same rolling windows, same fees/slippage assumptions, and long-only routing.
2. Sweep only the parameters that plausibly improve the observed weak positive edge:
   - fast/slow MA windows around the current candidate,
   - trend-strength or slope filters,
   - ATR-based stop distance,
   - take-profit multiple,
   - time stop / maximum bars held,
   - cooldown after losing trades.
3. Reject any variant that improves total return only by increasing concentration, reducing trade count below gate, or adding shorts.
4. Promote to Monte Carlo only if WFO improves both quality and robustness: higher mean Sharpe than `0.4125`, positive sum return above `3.09%`, max window DD at or below `5.41%`, and enough trades to avoid a sparse one-off result.
5. If the neighborhood sweep fails, stop MA work for now and move to a different SOL 4h thesis family rather than widening the same weak MA template across more symbols.
