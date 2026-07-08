# Sentiment/Macro Bot Improvement Plan

**Created:** 2026-03-25
**Goal:** Optimize the SentimentMeanReversion strategy using autoresearch + WFO

> Status checked on 2026-07-08. This is a dated execution plan. Treat the
> unchecked checklist below as historical unless current runtime data confirms
> the same work is still needed. More recent sentiment-macro evidence lives in
> `docs/reports/sentiment-macro-decision-gate-20260413.md`,
> `docs/reports/sentiment-macro-live-bleed-investigation-2026-06-01.md`, and
> `docs/reports/sol-sparse-dryness-diagnosis-2026-07-06.md`.

---

## Current State

### Live Performance (Paper)
| Metric | Value |
|--------|-------|
| Total P&L | +559.63 USDT |
| Win Rate | 69.7% (23W/10L) |
| Total Trades | 33 |
| Avg Trades/Week | ~1.5 |
| Symbols | BTCUSDT, ETHUSDT, SOLUSDT |
| Timeframe | 1h |

### Current Parameters
```yaml
strategies:
  - name: sentiment_mean_reversion
    config:
      rsi_oversold: 35.0
      rsi_overbought: 65.0
      bb_distance_threshold: 0.005
      sentiment_gate_threshold: 35.0
      sentiment_panic_threshold: 20.0
      sentiment_boost_threshold: 65.0

aggregator:
  min_agreement: 1
  buy_threshold: 0.6
  sell_threshold: -0.6
```

### Known Issues
1. **No backtest validation** - Strategy was deployed without WFO
2. **Sentiment scorer returns 50.0 (neutral)** - AI disabled in paper, so sentiment gating is effectively bypassed
3. **Single timeframe** - Only 1h, no multi-timeframe confirmation
4. **No per-symbol tuning** - Same params for BTC/ETH/SOL

---

## Research Questions

1. **Are current RSI thresholds optimal?** (35/65 vs 30/70 vs 40/60)
2. **Is BB distance threshold right?** (0.005 vs 0.003 vs 0.008)
3. **Should we tune aggregator thresholds?** (buy 0.6 vs 0.8 vs 1.0)
4. **Which symbols perform best?** (BTC vs ETH vs SOL on 1h)
5. **Should we add more strategies to the ensemble?**

---

## Improvement Plan

### Phase 1: Baseline WFO Validation (Day 1)

Run WFO on each symbol with current parameters to establish baseline.

```bash
# On local machine with DB access
export DB_HOST=127.0.0.1 DB_PORT=15432
source .env && export DB_PASSWORD="$POSTGRES_PASSWORD"

# BTCUSDT baseline
./scripts/run_autoresearch.sh \
  --config config/settings.sentiment_macro.yaml \
  --symbol BTCUSDT \
  --timeframe 1h \
  --train-months 3 \
  --test-months 2 \
  --gate-profile standard \
  --description "sentiment_macro_btc_baseline"

# ETHUSDT baseline
./scripts/run_autoresearch.sh \
  --config config/settings.sentiment_macro.yaml \
  --symbol ETHUSDT \
  --timeframe 1h \
  --train-months 3 \
  --test-months 2 \
  --gate-profile standard \
  --description "sentiment_macro_eth_baseline"

# SOLUSDT baseline
./scripts/run_autoresearch.sh \
  --config config/settings.sentiment_macro.yaml \
  --symbol SOLUSDT \
  --timeframe 1h \
  --train-months 3 \
  --test-months 2 \
  --gate-profile standard \
  --description "sentiment_macro_sol_baseline"
```

**Gate Profile (standard):**
- min_wfo_trades: 20
- min_wfo_sharpe: 0.5
- max_drawdown_pct: 10.0
- max_bootstrap_p_loss_pct: 25.0

---

### Phase 2: Parameter Sweep (Days 2-3)

Create overlay configs for key parameter variations:

#### 2.1 RSI Threshold Sweep

```yaml
# research/overlays/rsi_30_70.yaml
strategy:
  strategies:
    - name: sentiment_mean_reversion
      config:
        rsi_oversold: 30.0
        rsi_overbought: 70.0
```

```yaml
# research/overlays/rsi_40_60.yaml
strategy:
  strategies:
    - name: sentiment_mean_reversion
      config:
        rsi_oversold: 40.0
        rsi_overbought: 60.0
```

#### 2.2 BB Distance Sweep

```yaml
# research/overlays/bb_tight.yaml
strategy:
  strategies:
    - name: sentiment_mean_reversion
      config:
        bb_distance_threshold: 0.003
```

```yaml
# research/overlays/bb_wide.yaml
strategy:
  strategies:
    - name: sentiment_mean_reversion
      config:
        bb_distance_threshold: 0.008
```

#### 2.3 Aggregator Threshold Sweep

```yaml
# research/overlays/aggregator_strict.yaml
strategy:
  aggregator:
    min_agreement: 2
    buy_threshold: 0.8
    sell_threshold: -0.8
```

```yaml
# research/overlays/aggregator_relaxed.yaml
strategy:
  aggregator:
    min_agreement: 1
    buy_threshold: 0.5
    sell_threshold: -0.5
```

#### Run Sweep Commands

```bash
# RSI sweep
for symbol in BTCUSDT ETHUSDT SOLUSDT; do
  for rsi in rsi_30_70 rsi_40_60; do
    ./scripts/run_autoresearch.sh \
      --config config/settings.sentiment_macro.yaml \
      --overlay research/overlays/${rsi}.yaml \
      --symbol $symbol \
      --timeframe 1h \
      --train-months 3 \
      --test-months 2 \
      --description "${symbol}_${rsi}"
  done
done

# BB sweep
for symbol in BTCUSDT ETHUSDT SOLUSDT; do
  for bb in bb_tight bb_wide; do
    ./scripts/run_autoresearch.sh \
      --config config/settings.sentiment_macro.yaml \
      --overlay research/overlays/${bb}.yaml \
      --symbol $symbol \
      --timeframe 1h \
      --train-months 3 \
      --test-months 2 \
      --description "${symbol}_${bb}"
  done
done

# Aggregator sweep
for symbol in BTCUSDT ETHUSDT SOLUSDT; do
  for agg in aggregator_strict aggregator_relaxed; do
    ./scripts/run_autoresearch.sh \
      --config config/settings.sentiment_macro.yaml \
      --overlay research/overlays/${agg}.yaml \
      --symbol $symbol \
      --timeframe 1h \
      --train-months 3 \
      --test-months 2 \
      --description "${symbol}_${agg}"
  done
done
```

---

### Phase 3: Multi-Symbol Optimization (Day 4)

Run combined optimization with per-symbol configs:

```yaml
# research/overlays/per_symbol_optimized.yaml
strategy:
  per_symbol_aggregator_config:
    BTCUSDT:
      min_agreement: 2
      buy_threshold: 0.8
      sell_threshold: -0.8
    ETHUSDT:
      min_agreement: 1
      buy_threshold: 0.6
      sell_threshold: -0.6
    SOLUSDT:
      min_agreement: 1
      buy_threshold: 0.7
      sell_threshold: -0.5
```

---

### Phase 4: Strategy Ensemble Expansion (Day 5)

Test adding complementary strategies:

```yaml
# research/overlays/ensemble_expanded.yaml
strategy:
  strategies:
    - name: sentiment_mean_reversion
      config:
        rsi_oversold: 35.0
        rsi_overbought: 65.0
        bb_distance_threshold: 0.005
    - name: vwap_reversion
      config:
        vwap_atr_multiplier: 1.5
        rsi_oversold: 40
        rsi_overbought: 60
    - name: bollinger_bounce
      config:
        band_distance_threshold: 0.003
        rsi_oversold: 35
        rsi_overbought: 65
  aggregator:
    min_agreement: 2
    buy_threshold: 0.6
    sell_threshold: -0.6
```

---

### Phase 5: Results Analysis & Deployment (Day 6)

1. **Compare all results** from `research/results.tsv`
2. **Select winning configuration** based on:
   - Passes all gates
   - Highest WFO return
   - Lowest bootstrap P(loss)
   - Reasonable trade frequency
3. **Update production config** on Hetzner
4. **Monitor for 2 weeks** before considering live promotion

---

## Validation Gates (Standard Profile)

| Gate | Threshold | Notes |
|------|-----------|-------|
| Min WFO Trades | 20 | Statistical confidence |
| Min WFO Sharpe | 0.5 | Risk-adjusted return |
| Max Drawdown | 10% | Capital protection |
| Max Bootstrap P(Loss) | 25% | Probability check |
| Min OOS Return | 0% | Must be profitable |
| Max Profit Concentration | 50% | No single-trade dependency |

---

## Execution Checklist

This checklist is retained as historical planning context. Don't check items off
from local code alone; verify current DB coverage, backtest artifacts, deployed
config, and production logs first.

### Prerequisites
- [ ] Local TimescaleDB running with indicator data
- [ ] DB has BTC/ETH/SOL 1h data from 2024 onwards
- [ ] Python venv activated

### Phase 1 (Baseline)
- [ ] Run BTCUSDT baseline WFO
- [ ] Run ETHUSDT baseline WFO
- [ ] Run SOLUSDT baseline WFO
- [ ] Document baseline metrics

### Phase 2 (Sweeps)
- [ ] Create overlay YAML files
- [ ] Run RSI threshold sweep (6 runs)
- [ ] Run BB distance sweep (6 runs)
- [ ] Run aggregator sweep (6 runs)
- [ ] Analyze sweep results

### Phase 3 (Multi-Symbol)
- [ ] Create per-symbol optimized overlay
- [ ] Run combined optimization
- [ ] Compare vs single-symbol results

### Phase 4 (Ensemble)
- [ ] Create ensemble expansion overlay
- [ ] Run with multiple strategies
- [ ] Check if min_agreement needs adjustment

### Phase 5 (Deploy)
- [ ] Select winning configuration
- [ ] Update `config/settings.sentiment_macro.yaml`
- [ ] Deploy to Hetzner: `ssh crypto-agent "cd /opt/crypto-agent && git pull && docker compose -f docker-compose.prod.yml up -d --build agent_sentiment_macro"`
- [ ] Verify deployment
- [ ] Monitor for 2 weeks

---

## Expected Outcomes

| Scenario | Expected Impact |
|----------|-----------------|
| Tighter RSI (30/70) | Fewer trades, higher quality |
| Wider RSI (40/60) | More trades, lower win rate |
| Tighter BB (0.003) | Fewer entries, better timing |
| Wider BB (0.008) | More entries, more noise |
| Strict aggregator (min=2) | Fewer signals, higher confidence |
| Relaxed aggregator (min=1, thresh=0.5) | More signals, more noise |
| Expanded ensemble | More diverse signals, better coverage |

---

## Commands Quick Reference

```bash
# Check local DB
psql -h 127.0.0.1 -p 15432 -U trading -d marketdata -c "SELECT symbol, timeframe, COUNT(*) FROM indicators GROUP BY symbol, timeframe;"

# Start local DB (if not running)
cd /home/emilio/crypto-trading-agent && docker compose up -d timescaledb

# Run single autoresearch
./scripts/run_autoresearch.sh --config config/settings.sentiment_macro.yaml --symbol BTCUSDT --timeframe 1h --train-months 3 --test-months 2 --description "test"

# View results
cat research/results.tsv | column -t -s $'\t'

# Deploy to Hetzner
ssh crypto-agent "cd /opt/crypto-agent && git pull && docker compose -f docker-compose.prod.yml up -d --build agent_sentiment_macro"

# Check logs
ssh crypto-agent "cd /opt/crypto-agent && docker compose -f docker-compose.prod.yml logs agent_sentiment_macro --tail=100 --no-log-prefix"
```

---

## Notes

- **Sentiment scorer is currently neutral (50.0)** since AI is disabled. The strategy effectively runs as pure technical mean reversion.
- **Consider enabling AI sentiment** only after validating technical parameters work.
- **1h timeframe produces ~1.5 trades/week** - good frequency for validation.
- **Live win rate (69.7%)** is strong - goal is to maintain or improve while reducing drawdown.
