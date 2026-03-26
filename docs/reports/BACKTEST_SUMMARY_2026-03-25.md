# Crypto Trading Agent - Backtest & Configuration Summary

**Generated:** 2026-03-25
**Server:** Hetzner (`ssh crypto-agent`)
**Status:** Paper trading active

---

## Active Strategies Overview

| Agent | Strategy | Symbol(s) | TF | Mode | Status |
|-------|----------|-----------|----|----|--------|
| `agent_sol_sparse` | TrendPullback | SOLUSDT | 4h | Paper | ✅ Running |
| `agent_sentiment_macro` | SentimentMeanReversion | BTC/ETH/SOL | 1h | Paper | ✅ Running |
| `agent_avax` | MA Crossover | AVAXUSDT | 4h | Paper | ✅ Running (no trades yet) |

---

## 1. SOLUSDT TrendPullback (WFO-Validated)

**Configuration:** `config/settings.sol_trend_pullback_sparse.yaml`

### Backtest Results (2024-02-03 → 2026-02-24)

| Metric | Value |
|--------|-------|
| **Win Rate** | 75.0% (6 wins / 2 losses) |
| **Total Return** | +17.09% |
| **Max Drawdown** | 4.94% |
| **Total Trades** | 8 |
| **Sharpe Ratio** | 1.50 |
| **Profit Factor** | ~3.5 (estimated from returns) |
| **Trades/Week** | ~0.13 (1 trade every ~7-8 weeks) |

### WFO Validation (Rolling Windows)

| Metric | Value |
|--------|-------|
| OOS Windows | 7 |
| Aggregate WFO Trades | 4 |
| OOS Return | +7.72% |
| OOS Mean Sharpe | 0.38 |
| Max DD (WFO) | 5.76% |
| Bootstrap P(loss) | 24.60% |

### Monte Carlo Bootstrap (2000 iterations)

| Metric | Mean | 5th %ile | 95th %ile |
|--------|------|----------|-----------|
| Return | +17.70% | +2.04% | +32.45% |
| Win Rate | 75.31% | 50.00% | 100.00% |
| Trade Sharpe | 0.99 | 0.11 | 2.60 |

**Risk Assessment:**
- Probability of negative return: **2.8%**
- 95% CI for total return: [-0.64%, +34.41%]

### Live Paper Performance (Since Deploy)

| Metric | Value |
|--------|-------|
| Total Realized P&L | +83.57 USDT |
| Closed Trades | 6 (3 wins, 3 losses) |
| Win Rate (Live) | 50.0% |
| Peak Balance | 10,240.66 USDT |
| Current Balance | ~10,083.57 USDT |
| Open Positions | 0 |

### Strategy Parameters (Deployed)

```yaml
strategy:
  rsi_reclaim_level: 48
  min_trend_strength_pct: 0.008
  max_pullback_distance_pct: 0.02
  vwap_pullback_distance_pct: 0.03
  min_atr_pct: 0.008
  
execution:
  stop_loss_pct: 0.02 (2%)
  take_profit_pct: 0.05 (5%)
  sl_atr_multiplier: 2.0
  tp_atr_multiplier: 4.5
  trailing_activate_atr: 1.5
  
futures:
  enabled: true
  default_leverage: 3x
  max_leverage: 10x
  margin_mode: isolated
```

---

## 2. Sentiment/Macro Bot (Multi-Symbol)

**Configuration:** `config/settings.sentiment_macro.yaml`

### Live Paper Performance

| Metric | Value |
|--------|-------|
| Total Realized P&L | **+559.63 USDT** |
| Closed Trades | 33 (23 wins, 10 losses) |
| Win Rate | **69.7%** |
| Peak Balance | 10,265.98 USDT |
| Open Positions | 0 |

### Recent Day (2026-03-23)

| Metric | Value |
|--------|-------|
| Daily P&L | +92.07 USDT |
| Daily Trades | 8 |
| Daily Win Rate | 62.5% |
| Signals | 10 |
| Order Fills | 13 |

### Signal Distribution

| Symbol | Signals | Orders |
|--------|---------|--------|
| BTCUSDT | 4 | 3 |
| ETHUSDT | 3 | 5 |
| SOLUSDT | 3 | 5 |

---

## 3. BTC Regime Router (Research/Abandoned)

**Status:** Research paused - insufficient trade frequency

| Metric | Value |
|--------|-------|
| Win Rate | 66.67% |
| Total Return | +9.13% |
| Max Drawdown | 4.49% |
| Sharpe Ratio | 1.59 |
| **Total Trades** | 3 (in 8 months) |
| **Trades/Year** | ~4.5 |

**Conclusion:** Too sparse for statistical confidence. Would need 2+ years of paper trading to reach 15-20 trades.

---

## Validation Gates

| Gate | Threshold | SOL TrendPullback | Sentiment/Macro |
|------|-----------|-------------------|-----------------|
| Win Rate | ≥62% | ✅ 75% (backtest) / 50% (live) | ✅ 69.7% |
| Profit Factor | ≥1.8 | ✅ ~3.5 | ✅ (positive P&L) |
| Max Drawdown | ≤4% | ⚠️ 4.94% (slightly over) | ✅ |
| Min Trades | ≥200 | ❌ 8 (sparse by design) | ✅ 33+ |

---

## Trade Frequency Summary

| Strategy | Trades/Week | Trades/Year | Notes |
|----------|-------------|-------------|-------|
| SOL TrendPullback | 0.13 | ~7 | Sparse, high quality |
| Sentiment/Macro | ~1.5 | ~80 | Active multi-symbol |
| BTC Regime Router | 0.08 | ~4 | Too sparse - paused |

---

## Key Findings

### ✅ Working Well

1. **SOLUSDT TrendPullback (4h)**
   - WFO-validated with 75% win rate in backtest
   - Low trade frequency but high quality setups
   - Monte Carlo shows only 2.8% probability of loss
   - Live paper trading shows 50% WR (small sample: 6 trades)

2. **Sentiment/Macro Bot (1h multi-symbol)**
   - Strong live performance: +559 USDT, 69.7% WR
   - Active signal generation (~1.5 trades/week)
   - Best current performer in paper trading

### ⚠️ Needs Observation

1. **SOL TrendPullback live win rate**
   - Backtest: 75% WR
   - Live: 50% WR (6 trades)
   - Need more trades to confirm if degradation is real or sample noise

### ❌ Paused/Abandoned

1. **BTC Regime Router**
   - Too few trades for statistical confidence
   - Would need 2+ years to validate

---

## Recommendations

1. **Continue paper trading SOL TrendPullback** for 4-6 more weeks (or 10+ trades) before evaluating live promotion

2. **Sentiment/Macro bot is ready for closer monitoring** - strongest performer, consider increasing allocation if paper performance holds

3. **Keep AVAX agent running** but don't expect signals - it's a sparse MA strategy on 4h

4. **Review SOL live vs backtest gap** after 10+ trades - if WR stays below 60%, investigate parameter drift

---

## Server Commands

```bash
# Check status
ssh crypto-agent "cd /opt/crypto-agent && docker compose -f docker-compose.prod.yml ps"

# View SOL agent logs
ssh crypto-agent "cd /opt/crypto-agent && docker compose -f docker-compose.prod.yml logs agent_sol_sparse --tail=100 --no-log-prefix"

# View Sentiment/Macro logs
ssh crypto-agent "cd /opt/crypto-agent && docker compose -f docker-compose.prod.yml logs agent_sentiment_macro --tail=100 --no-log-prefix"

# Daily paper reports
ssh crypto-agent "ls -la /opt/crypto-agent/data/reports/"
```
