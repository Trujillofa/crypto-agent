# Expanded Backtest Matrix (Top Pairs, 1h/4h)

Generated: 2026-02-23

## Scope

- Pairs: BNBUSDT, BTCUSDT, ETHUSDT
- Timeframes: 1h, 4h
- Window: 2024-02-03 04:00 UTC to latest available
- Costs: fee 0.1%, slippage 0.1%
- Risk exits: stop loss 3%, take profit 6%
- Trend filter: EMA200 BUY filter enabled

Profiles tested:

- baseline_long: min_agreement=2, buy=0.8, sell=-0.65
- strict_long: min_agreement=3, buy=1.5, sell=-1.5
- baseline_short: min_agreement=2, buy=0.8, sell=-0.65, allow_short=true
- strict_short: min_agreement=3, buy=1.5, sell=-1.5, allow_short=true

## Aggregate (24 runs)

By timeframe:

- 1h: avg return -24.05%, avg max DD 31.90%, avg trades 74.25
- 4h: avg return -13.09%, avg max DD 19.87%, avg trades 32.83

By profile:

- baseline_long: avg return -7.63%, avg max DD 14.06%, avg trades 27.83
- strict_long: avg return +1.18%, avg max DD 3.12%, avg trades 1.00
- baseline_short: avg return -61.19%, avg max DD 68.39%, avg trades 168.67
- strict_short: avg return -6.64%, avg max DD 17.98%, avg trades 16.67

## Long-Only Detail (Live-Compatible)

| Symbol | Timeframe | Profile | Trades | Win Rate | Return | Max DD | Sharpe |
|---|---|---|---:|---:|---:|---:|---:|
| BNBUSDT | 1h | baseline_long | 44 | 54.55% | -12.07% | 15.58% | -5.590 |
| BNBUSDT | 1h | strict_long | 1 | 0.00% | -3.29% | 7.18% | -2.456 |
| BNBUSDT | 4h | baseline_long | 13 | 53.85% | +1.21% | 8.83% | +1.808 |
| BNBUSDT | 4h | strict_long | 0 | 0.00% | 0.00% | 0.00% | 0.000 |
| BTCUSDT | 1h | baseline_long | 45 | 57.78% | -8.37% | 13.94% | -3.511 |
| BTCUSDT | 1h | strict_long | 1 | 0.00% | -3.29% | 4.13% | -6.824 |
| BTCUSDT | 4h | baseline_long | 14 | 35.71% | -10.57% | 18.47% | -13.069 |
| BTCUSDT | 4h | strict_long | 1 | 100.00% | +5.68% | 1.85% | +18.918 |
| ETHUSDT | 1h | baseline_long | 41 | 43.90% | -12.56% | 17.19% | -5.468 |
| ETHUSDT | 1h | strict_long | 3 | 66.67% | +8.01% | 5.54% | +5.351 |
| ETHUSDT | 4h | baseline_long | 10 | 30.00% | -3.42% | 10.32% | -3.917 |
| ETHUSDT | 4h | strict_long | 0 | 0.00% | 0.00% | 0.00% | 0.000 |

## Interpretation

- Adding more history changed the conclusion: 1h baseline long underperforms badly on all three pairs.
- The only robustly positive long-only pockets are:
  - ETHUSDT 1h with strict_long
  - BNBUSDT 4h with baseline_long
  - BTCUSDT 4h with strict_long (single trade, weak statistical confidence)
- Short-enabled profiles are unstable over this longer sample and are not promotion-ready without executor parity and stronger risk controls.

## Recommendation (Now)

- Do not run a shared long-only config on all three pairs at 1h.
- If staying with current live executor behavior (long-oriented), safest candidate is narrow-scope:
  - Pair: ETHUSDT
  - Timeframe: 1h
  - Profile: strict_long (min_agreement=3, buy=1.5, sell=-1.5)
- If you must keep BNBUSDT + SOLUSDT live, prefer 4h over 1h for drawdown control, then re-validate with a fresh OOS split before switching.
