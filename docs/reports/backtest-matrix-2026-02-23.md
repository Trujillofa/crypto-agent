# Backtest Matrix Report

Generated: 2026-02-23

## Scope

- Implemented backtest/live parity features in `src/backtest/engine.py`:
  - Global EMA200 BUY filter parity (`apply_global_trend_filter`)
  - Optional short entries on SELL while flat (`allow_short`)
- Added coverage tests in `tests/test_backtest.py`:
  - EMA200 trend filter blocks BUY
  - Short mode opens/closes short positions
- Ran matrix over 4 symbols, 5 timeframes, 5 config profiles (100 runs total).

Symbols: BNBUSDT, ETHUSDT, BTCUSDT, SOLUSDT

Backtest window: 2026-02-05 to 2026-02-16 (UTC)

## Config Profiles

- baseline_long: min_agreement=2, buy=0.8, sell=-0.65, short=false
- strict_long: min_agreement=3, buy=1.5, sell=-1.5, short=false
- relaxed_long: min_agreement=1, buy=0.5, sell=-0.5, short=false
- baseline_short: min_agreement=2, buy=0.8, sell=-0.65, short=true
- strict_short: min_agreement=3, buy=1.5, sell=-1.5, short=true

All profiles used fees=0.1%, slippage=0.1%, SL=3%, TP=6%, EMA200 filter enabled.

## Aggregate Results by Timeframe

| Timeframe | Runs | Avg Return | Avg Win Rate | Avg Max DD | Avg Trades |
|---|---:|---:|---:|---:|---:|
| 1m | 20 | -10.44% | 18.19% | 14.95% | 36.45 |
| 5m | 20 | -0.03% | 0.00% | 0.06% | 0.15 |
| 15m | 20 | -3.35% | 22.92% | 5.06% | 6.35 |
| 1h | 20 | -0.36% | 15.33% | 2.12% | 1.30 |
| 4h | 20 | 0.00% | 0.00% | 0.00% | 0.00 |

## Aggregate Results by Profile

| Profile | Runs | Avg Return | Avg Win Rate | Avg Max DD | Avg Trades |
|---|---:|---:|---:|---:|---:|
| baseline_long | 20 | -1.56% | 13.95% | 1.80% | 3.50 |
| strict_long | 20 | -0.11% | 0.00% | 0.15% | 0.05 |
| relaxed_long | 20 | -11.05% | 7.39% | 11.33% | 32.85 |
| baseline_short | 20 | -1.55% | 23.10% | 6.49% | 6.70 |
| strict_short | 20 | +0.10% | 12.00% | 2.42% | 1.15 |

## Top Runs

| Symbol | TF | Profile | Trades | Win Rate | Return | Max DD |
|---|---|---|---:|---:|---:|---:|
| BNBUSDT | 1h | baseline_short | 2 | 100.00% | +6.23% | 3.69% |
| BTCUSDT | 1m | strict_short | 3 | 66.67% | +3.97% | 8.30% |
| BNBUSDT | 15m | baseline_short | 4 | 50.00% | +3.16% | 7.35% |
| BNBUSDT | 1m | strict_short | 5 | 60.00% | +2.45% | 8.11% |
| ETHUSDT | 1h | baseline_short | 2 | 50.00% | +2.21% | 4.53% |

## Interpretation

- 1m is not the best default in this sample; it overtrades and has the worst aggregate drawdown and return.
- 15m and 1h are materially more stable in this dataset.
- Strict thresholds reduce overtrading. Enabling short capability helps in downtrend periods.
- 4h produced no trades under tested settings in this time window.

## Recommended Next Validation Step

Candidate live-test profile (paper mode first):

- timeframe: 15m or 1h
- aggregator: min_agreement=3, buy_threshold=1.5, sell_threshold=-1.5
- allow_short: true (futures only)
- keep EMA200 BUY filter enabled

Run at least 30-60 days out-of-sample before production promotion.
