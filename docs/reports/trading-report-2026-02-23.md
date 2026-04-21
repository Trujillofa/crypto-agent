# crypto-agent Trading Report

**Generated**: 2026-02-23
**Production Branch**: `feat/tune-strategy-config`
**Server**: crypto-agent (ssh crypto-agent)

---

## 1. Live Trading Performance

### Summary (Feb 15-22, 2026)

| Metric | Value |
|--------|-------|
| Total Closed Positions | 69 |
| Winning Trades | 11 |
| Losing Trades | 56 |
| Breakeven | 2 |
| **Win Rate** | **15.9%** |
| **Total P&L** | **-$101.03** |
| Average P&L per Trade | -$1.46 |

### Per-Symbol Performance

| Symbol | Trades | Avg P&L | Status |
|--------|--------|---------|--------|
| BNBUSDT:spot | 5 | -$0.24 | Closed |
| LINKUSDT:spot | 6 | -$1.56 | Closed |
| DOTUSDT:spot | 5 | -$1.44 | Closed |
| ETHUSDT:spot | 8 | -$0.94 | Closed |
| ADAUSDT:spot | 7 | -$1.54 | Closed |
| SOLUSDT:spot | 7 | -$1.89 | Closed |
| XRPUSDT:spot | 5 | -$1.93 | Closed |
| BTCUSDT:futures | 4 | -$0.92 | Closed |
| AVAXUSDT:spot | 5 | -$2.18 | Closed |
| DOGEUSDT:spot | 5 | -$3.25 | Closed |
| ETHUSDT:futures | 8 | -$0.94 | Closed |

### Daily P&L

| Date | Trades | P&L |
|------|--------|-----|
| 2026-02-15 | 13 | -$13.80 |
| 2026-02-16 | 39 | -$53.99 |
| 2026-02-17 | 17 | -$33.24 |

### Current Open Positions

- 2 open positions (SOLUSDT:spot, BNBUSDT:spot)
- Trading mode: Test mode (no real money)

---

## 2. Backtesting Status

**Status**: No completed backtests in database

The `strategy_backtests` table is empty. Scripts exist at:
- `scripts/run_backtest.py`
- `scripts/run_full_backtest.py`

**Gap**: No validated historical performance data results.

---

## to compare against live 3. Current Production Configuration

### Trading Settings

| Parameter | Value |
|-----------|-------|
| Mode | live |
| Trading Pairs | BNBUSDT, SOLUSDT |
| Timeframe | 4h |
| Evaluation Interval | 60 seconds |
| Cooldown Candles | 10 |
| Default Trading Mode | futures |

### Strategy Configuration

| Strategy | Config |
|----------|--------|
| rsi_reversal | rsi_period=7, oversold=30, overbought=70 |
| macd_histogram | min_hist=0.0001, atr_filter=true |
| bollinger_bounce | band_dist=0.005, rsi_oversold=30, rsi_overbought=70 |
| cci_breakout | cci_buy=100, cci_sell=-100, atr_min=0.005 |
| vwap_reversion | vwap_atr_mult=1.5, rsi_oversold=40, rsi_overbought=60 |

### Signal Aggregator

| Parameter | Value |
|-----------|-------|
| Min Agreement | 2 |
| Buy Threshold | 0.8 |
| Sell Threshold | -0.65 |

### Risk Limits

| Limit | Value |
|-------|-------|
| Max Position % | 10% |
| Max Open Positions | 4 |
| Max Daily Loss % | 5% |
| Max Drawdown % | 15% |
| Max Single Loss % | 2% |
| Consecutive Losses (circuit breaker) | 3 |
| Kill Switch | Enabled (auto-reset 60min) |

### Execution

| Parameter | Value |
|-----------|-------|
| Trading Enabled | false (test mode) |
| Order Size | $100 USDT |
| Stop Loss | 3% |
| Take Profit | 6% |

---

## 4. Why No Signals (Current)

### Root Cause Analysis

1. **Global EMA200 Trend Filter** (PRIMARY)
   - Logs show: `Blocked by Global Trend Filter (Price < EMA200)` — 721 times in 24h
   - BNBUSDT: price $606 < EMA200 $696
   - SOLUSDT: price $80 < EMA200 $95
   - All BUY signals suppressed

2. **Aggregator Threshold** (SECONDARY)
   - `min_agreement: 2` requires 2+ strategies to agree
   - Current: 0 consensus signals in 24h

3. **Evaluation Cadence Mismatch**
   - 4h timeframe with 60s evaluation = 240 evaluations per candle
   - Strategies re-evaluate same candle repeatedly (log noise, not new signals)

### Code References

| Issue | File | Line |
|-------|------|------|
| EMA200 filter | `src/strategy/engine.py` | 140-158 |
| Min agreement | `src/strategy/aggregator.py` | 59 |
| Cooldown | `src/strategy/engine.py` | 180-194 |

---

## 5. Recommendations

### Immediate Actions

| Priority | Action | Expected Impact |
|----------|--------|------------------|
| 1 | Lower `min_agreement` from 2 to 1 | More signals pass through |
| 2 | Match `evaluation_interval_seconds` to timeframe (14400 for 4h) | Reduce log noise, cleaner signals |
| 3 | Review/disable EMA200 global trend filter | Allow BUY in downtrends (higher risk) |

### Short-Term Improvements

| Priority | Action | Effort |
|----------|--------|--------|
| 4 | Run backtests on current strategy config | Medium |
| 5 | Add WFO (Walk-Forward Optimization) validation | Medium |
| 6 | Build strategy lifecycle governance (candidate→validated→live) | Medium |

### Configuration Suggestions

```yaml
# Recommended changes to config/settings.yaml

strategy:
  evaluation_interval_seconds: 14400  # Match 4h timeframe
  cooldown_candles: 3  # Reduce from 10

  aggregator:
    min_agreement: 1  # Lower from 2
    buy_threshold: 0.5  # Lower from 0.8
    sell_threshold: -0.5  # Raise from -0.65
```

### Risk Considerations

- Current 15.9% win rate is below breakeven for most fee structures
- Test mode is ON — no real money at risk
- Kill switch is properly configured
- Consider disabling futures mirroring until spot strategy is profitable

---

## 6. Next Steps

1. **Apply config changes** to increase signal frequency
2. **Run backtests** to validate strategy viability historically
3. **Monitor** next 7 days of live performance in test mode
4. **Iterate** on strategy parameters based on results

---

*Report generated from production database and configuration analysis.*
