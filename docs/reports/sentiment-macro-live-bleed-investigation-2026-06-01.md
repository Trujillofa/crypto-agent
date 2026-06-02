# Sentiment-Macro Live Bleed Investigation

**Date:** 2026-06-01
**Service:** `agent_sentiment_macro` on Hetzner
**Agent ID:** `sentiment-macro-bot`
**Scope:** Diagnose why the live strategy is losing despite earlier profitable results.

## Executive Summary

The current losses are not caused by failed order placement, missing protective orders, or
exchange reconciliation drift. The inspected BTC losses were valid strategy entries and were
closed by exchange-side stop-loss orders close to their configured stop prices.

The strategy should not be described as currently validated by profitable backtesting. The
existing replay-backed, executor-like backtest from 2026-03-27 through 2026-04-27 was negative
for BTC, ETH, and SOL. Production performance also changed after the 2026-04-20 futures
rollback: 32 closed trades produced `-3.54 USDT`, with a `28.1%` win rate.

Recommendation: keep the intentionally small live allocation unchanged, restore strict EMA200
filtering, and treat the live agent as a controlled production experiment. Do not increase
size until a replay-backed portfolio backtest reproduces the actual runtime behavior.

## Applied Mitigations

The following risk reductions were deployed on 2026-06-01:

- Restored strict EMA200 filtering with `global_trend_filter_buffer_pct: 0.0`.
- Removed `BTCUSDT` from the live sentiment-macro scope after BTC dominated both post-rollback
  live losses and corrected replay losses.
- Preserved the small `22 USDT` order budget, `3x` leverage, one-position portfolio limit,
  and live observation mode for `ETHUSDT` and `SOLUSDT`.
- Rebuilt the production service and verified healthy startup, zero open positions, and clean
  spot and futures reconciliation for both remaining symbols.

## Production State

The Hetzner service was healthy during inspection:

- Container: `crypto-agent-agent_sentiment_macro-1`
- Status: healthy, running for 7 days
- Deployed commit: `26e75bdcc96d44299fac43e38f9814fb38a34f8d`
- Reconciliation: repeated clean spot and futures checks with zero divergences
- Open positions at inspection time: `0`

The deployed configuration is live futures trading with:

- Symbols: `BTCUSDT`, `ETHUSDT`, `SOLUSDT`
- Timeframe: `1h`
- Order budget: `22 USDT`
- Futures leverage: `3x`
- Exit model: `2.0 ATR` stop loss, `3.5 ATR` take profit
- Portfolio limit: one concurrent long across all three symbols
- Stop-loss cooldown: 240 minutes per symbol

## Inspected BTC Losses

### 2026-05-27 BTC trade

- Signal: RSI `29.1`, lower Bollinger distance `-0.0041`, live sentiment `52`
- Signal price: `75073.40`
- Filled entry: `74932.80`
- Quantity: `0.001 BTC`
- Exchange stop: `74293.7914`
- Exchange take profit: `76051.0650`
- Exit: `74280.00`
- Realized P&L: `-0.6528 USDT`

### 2026-05-31 BTC trade

- Signal: RSI `29.1`, lower Bollinger distance `-0.0015`, live sentiment `62`
- Signal price: `73602.17`
- Filled entry: `73619.40`
- Quantity: `0.001 BTC`
- Exchange stop: `73233.4000`
- Exchange take profit: `74294.9000`
- Exit: `73227.80`
- Realized P&L: `-0.3916 USDT`

Both losses were ordinary mean-reversion failures: the bot bought oversold dips and price
continued lower. Protective orders were active and executed correctly.

## Live Performance Evidence

Lifetime production data remains positive:

| Scope | Closed trades | Win rate | Realized P&L |
|---|---:|---:|---:|
| Lifetime | 96 | 47.9% | `+605.43 USDT` |
| Since 2026-04-20 futures rollback | 32 | 28.1% | `-3.54 USDT` |

Post-rollback results by symbol:

| Symbol | Closed trades | Win rate | Realized P&L | Expectancy |
|---|---:|---:|---:|---:|
| BTCUSDT | 16 | 25.0% | `-3.15 USDT` | `-0.197 USDT` |
| ETHUSDT | 9 | 33.3% | `-0.05 USDT` | `-0.005 USDT` |
| SOLUSDT | 7 | 28.6% | `-0.34 USDT` | `-0.048 USDT` |

The degradation is broad across all three symbols. BTC is the main contributor, but this is
not a BTC-only defect.

## Why Earlier Results Did Not Protect Against This

### Live edge changed after April 20

The config comments justify futures mode using an earlier window: 95 live trades, 70% win
rate, and `+727 USDT` from 2026-03-13 through 2026-04-16. That historical window is not
representative of the 32 post-rollback trades.

### Replay-backed backtests were already negative

The 2026-05-06 replay study tested real xAI observations with executor-like exits over
2026-03-27 through 2026-04-27:

| Symbol | Trades | Win rate | Return | Sharpe | Profit factor |
|---|---:|---:|---:|---:|---:|
| BTCUSDT | 12 | 33.33% | `-5.85%` | `-5.99` | `0.21` |
| ETHUSDT | 17 | 41.18% | `-7.03%` | `-5.07` | `0.38` |
| SOLUSDT | 13 | 30.77% | `-5.33%` | `-4.18` | `0.43` |

That study explicitly concluded replay coverage was too sparse to prove a live-parity edge.
The production strategy therefore continued based on historical live performance, not a
positive replay-backed out-of-sample validation.

### Portfolio behavior differs from per-symbol backtests

Production permits only one concurrent long. On 2026-05-31, BTC occupied that slot while
valid ETH and SOL signals were rejected. Any validation that runs each symbol independently
does not reproduce the live portfolio selector or opportunity cost.

### Replay data must be read from the production Docker volume

The host checkout file `data/event_log_sentiment-macro-bot.jsonl` contains only 270
`sentiment_score` observations and ends at `2026-04-27 00:58 UTC`. It is stale because the
production container writes to the `agent-data` Docker volume mounted at `/app/data`.

The mounted production event log is current through `2026-06-01` and contains recent
`sentiment_score` observations from BTC, ETH, and SOL. Replay research must read the mounted
volume or copy the current volume file explicitly; using the host checkout silently evaluates
stale sentiment data.

### Corrected dense replay remains negative

After exporting the mounted production log, replay coverage increased from 270 stale host-file
observations to 4703 mounted-volume observations through `2026-06-01 19:52 UTC`. The corrected
executor-like replay used strict EMA200 filtering, `2.0 ATR` stops, `3.5 ATR` take profits, and
a 24-hour sentiment max age:

| Symbol | Trades | Win rate | Return | Sharpe | Profit factor |
|---|---:|---:|---:|---:|---:|
| BTCUSDT | 8 | 37.50% | `-3.70%` | `-3.86` | `0.25` |
| ETHUSDT | 1 | 0.00% | `-0.14%` | `-0.80` | `0.00` |
| SOLUSDT | 1 | 0.00% | `-0.03%` | `-0.06` | `0.00` |

Replay lookup coverage was dense: BTC had 1553 hits and ETH/SOL had 1560 hits each, with only
34 stale lookups per symbol. Sparse replay data is no longer the explanation for poor results.
The remaining ETH and SOL samples are too small to establish an edge.

### ETH/SOL portfolio replay after BTC quarantine

A portfolio-level replay then modeled the deployed ETH/SOL symbol order, one concurrent long,
ATR exits, 240-minute stop-loss cooldown, strict EMA200 gate, live `22 USDT` order budget, and
futures-style fees:

| Scope | Trades | Win rate | Realized P&L | Profit factor | Slot-skipped buys |
|---|---:|---:|---:|---:|---:|
| ETHUSDT/SOLUSDT portfolio | 2 | 50.00% | `+0.0076 USDT` | `1.86` | 1 |

This is directionally better after BTC quarantine but remains too sparse to establish a live
edge or justify increasing risk.

### Close-only 30-day production attribution

The authoritative `trades` table shows that ETH should also be quarantined from the live
sentiment agent:

| Symbol | Closed trades | Win rate | Realized P&L | Expectancy |
|---|---:|---:|---:|---:|
| BTCUSDT | 11 | 18.2% | `-3.2757 USDT` | `-0.2978 USDT` |
| ETHUSDT | 4 | 0.0% | `-0.7133 USDT` | `-0.1783 USDT` |
| SOLUSDT | 2 | 50.0% | `+0.0390 USDT` | `+0.0195 USDT` |

BTC quarantine is deployed. ETH quarantine is the next production change. SOL is the only
remaining sentiment-macro symbol with positive recent realized expectancy, but its sample is
still too small to justify increasing risk.

ETH quarantine was deployed later on 2026-06-01. The live sentiment-macro service now routes
only `SOLUSDT` with the original `22 USDT` budget, `3x` leverage, strict EMA200 gate, and
240-minute stop-loss cooldown.

## Operational Drift Cleanup

The production audit also found enabled services missing from runtime and stale monitoring
targets. The following repairs were applied on 2026-06-01:

- Restored `agent_sol_sparse` and `agent_sol_panic_block_paper` as healthy paper services.
- Restored Grafana as a healthy internal observability service.
- Removed the disabled AVAX scrape target and added the panic-block paper target.
- Disabled this stack's duplicate nginx definition because `tailscaled` already owns host port
  `443`.
- Fixed the production drift sentinel to inspect `docker-compose.prod.yml` instead of the
  default development Compose file.

After cleanup, Prometheus reported all three agent targets healthy and the corrected drift
sentinel reported no production errors.

## Additional Observations

- Telegram displays roughly `74 USDT` BTC exposure because BTC minimum lot size truncation
  results in `0.001 BTC`; the configured `22 USDT` budget is not the final exchange notional.
- One transient Binance position-risk HTML response and timestamp resync occurred on
  2026-06-01. Neither affected the inspected trades.
- The database stores position symbols with an agent prefix such as
  `sentiment-macro-bot::BTCUSDT`. This is unusual but did not prevent the queried reporting.

## Recommendations

1. Keep the current order budget and leverage unchanged; do not scale the live allocation.
2. Keep BTC quarantined from this strategy until a replay-backed redesign passes validation.
3. Continue low-risk live observation on SOL only.
4. Re-run replay-backed walk-forward validation across the post-2026-04-20 regime.
5. Require positive out-of-sample expectancy and controlled drawdown before increasing live
   orders. Do not treat lifetime P&L as sufficient evidence.

## Evidence Sources

- Hetzner `docker compose -f docker-compose.prod.yml ps agent_sentiment_macro`
- Hetzner `agent_sentiment_macro` container logs
- Hetzner TimescaleDB `positions` rows for `agent_id='sentiment-macro-bot'`
- `config/settings.sentiment_macro.yaml`
- `docs/reports/sentiment-macro-replay-and-tuning-2026-05-06.md`
- `docs/reports/SENTIMENT_REPLAY_FINDINGS_2026-03-27.md`
