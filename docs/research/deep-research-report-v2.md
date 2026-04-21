# Deep Research Report v2 - crypto-agent Strategy System

**Version**: 2.0
**Date**: 2026-02-16
**Status**: FACT-CHECKED vs HEAD (2026-02-16)

---

## Executive Summary

This report evaluates the original deep research recommendations against the current codebase state. Key finding: several critical gaps have been addressed since the original analysis, while others remain. The system now has exit logic and a backtest engine, but still lacks lifecycle governance and WFO/Monte Carlo validation.

---

## Fact-Check Table: Claims vs Actual Code

| # | Claim from Original Report | Status | Evidence | Notes |
|---|---------------------------|--------|----------|-------|
| 1 | "No exit logic - only closes on SELL signal" | **OUTDATED** | `src/execution/paper_executor.py:58,149,222` - trailing stop, take-profit, time-stop implemented | Exit logic added since original analysis |
| 2 | "No backtester - only paper trading" | **OUTDATED** | `src/backtest/engine.py` - full event-driven backtest with slippage, SL/TP | Tests in `tests/test_backtest*.py` |
| 3 | "No WFO or Monte Carlo" | **TRUE** | No WFO/MC modules in `src/` or `scripts/` | Gap remains |
| 4 | "No strategy lifecycle governance" | **TRUE** | No `strategy_versions` or state machine in `src/` or `migrations/` | Gap remains |
| 5 | "README says 'Spot Trading Only'" | **TRUE** | `README.md:17` - "Spot Trading Only" but `config/settings.yaml:61` has `futures.enabled: true` | Documentation drift |
| 6 | "Futures mirroring risk" | **TRUE** | `src/main.py:778,816` - spot signals mirrored to futures symbols | Unchanged - risk remains |
| 7 | "Paper executor lacks position limit enforcement" | **PARTIAL** | `src/execution/paper_executor.py:136` - checks `is_trading_allowed()` but may not call `check_position_limit()` on BUY | Needs verification |

---

## Current Architecture (Verified)

```
Binance Spot API (REST)
    ↓
OHLCV Ingestor → TimescaleDB (Docker)
    ↓
Indicator Computer → Indicators Table
    ↓
IndicatorReader → StrategyEngine (5 strategies)
    ↓
Signal Aggregator (config: min_agreement=2, buy/sell threshold=1.1/-1.1)
    ↓
Risk Manager (kill switch, circuit breakers, loss limits)
    ↓
PaperExecutor / LiveExecutor → Binance API
    ↓
Prometheus + Grafana (Docker)
```

---

## Verified Components

### ✅ Already Implemented

| Component | Location | Status |
|-----------|-----------|--------|
| Exit Logic (SLTime)/TP/ | `src/execution/paper_executor.py:58,149,222` | Working |
| Event-driven Backtest | `src/backtest/engine.py` | Working |
| Backtest Tests | `tests/test_backtest*.py` | Passing |
| Risk Manager | `src/risk/manager.py` | Working (kill switch, circuit breakers) |
| Signal Aggregator | `src/strategy/aggregator.py` | Configurable thresholds |
| Multi-strategy Engine | `src/strategy/engine.py` | 5 strategies loaded |
| Portfolio Tracking | `migrations/003_add_portfolio_tables.sql` | positions + trades tables |

### ⚠️ Partially Implemented

| Component | Status | Notes |
|-----------|--------|-------|
| Backtest config alignment | In Progress | `scripts/run_backtest.py` now matches `config/settings.yaml` |
| Paper position limits | Uncertain | May not enforce `max_open_positions` on BUY |

### ❌ Not Implemented

| Component | Gap | Impact |
|-----------|-----|--------|
| WFO Validation | No walk-forward optimization | Strategies not validated across regimes |
| Monte Carlo | No distribution testing | Can't assess fragility |
| Lifecycle Governance | No strategy state machine | No promotion gates (candidate→validated→live) |
| QuantConnect Integration | Not integrated | Would require artifact handoff layer |

---

## Strategy Configuration (Live)

From `config/settings.yaml:72-106`:

```yaml
strategy:
  evaluation_interval_seconds: 60
  cooldown_candles: 6
  default_trading_mode: spot
  strategies:
    - name: simple_ma (ema_short=12, ema_long=26)
    - name: rsi_reversal (period=14, oversold=30, overbought=70)
    - name: macd_histogram (min_hist=0.0, atr_min_pct=0.003)
    - name: bollinger_bounce (band_dist=0.003, rsi 30/70)
    - name: momentum (rsi_buy=60, rsi_sell=40)
  aggregator:
    min_agreement: 2
    buy_threshold: 1.1
    sell_threshold: -1.1
```

---

## Risk Configuration (Live)

From `config/risk.yaml`:

```yaml
position_limits:
  max_position_pct: 0.10
  max_open_positions: 5
loss_limits:
  max_daily_loss_pct: 0.05
  max_drawdown_pct: 0.15
  max_single_loss_pct: 0.02
circuit_breakers:
  consecutive_losses: 3
  api_errors: 3
kill_switch:
  enabled: true
  auto_reset_minutes: 60
```

---

## Recommendations (Prioritized)

### Phase 1: Quick Wins (This Week)

| Priority | Action | Effort | Impact |
|----------|--------|--------|--------|
| 1 | Verify paper position limit enforcement in BUY path | Low | High (prevents overtrading) |
| 2 | Align all backtest scripts with live config | Low | Medium (ensures valid testing) |
| 3 | Disable futures mirroring until strategy is profitable | Low | High (reduces burst-loss risk) |

### Phase 2: Validation Pipeline (2-4 Weeks)

| Priority | Action | Effort | Impact |
|----------|--------|--------|--------|
| 4 | Add WFO-lite to backtest engine | Medium | High (regime robustness) |
| 5 | Build lifecycle gate DB + promotion logic | Medium | High (governance) |
| 6 | Create experiment tracking table | Medium | Medium (attribution) |

### Phase 3: Enhancement (1-2 Months)

| Priority | Action | Effort | Impact |
|----------|--------|--------|--------|
| 7 | Add Monte Carlo slippage testing | Medium | Medium |
| 8 | Integrate QuantConnect for validation (optional) | High | Medium |

---

## Rollback/Recovery Plan

Before any live trading:
1. Kill switch auto-resets every 60 minutes in paper mode
2. Daily loss limit at 5% portfolio
3. Max drawdown at 15%
4. Max 5 open positions
5. Telegram alerts on all risk events

---

## Appendix: File References

| File | Purpose |
|------|---------|
| `src/main.py` | Entry point, signal routing |
| `src/strategy/engine.py` | Strategy evaluation loop |
| `src/strategy/aggregator.py` | Consensus signal logic |
| `src/execution/paper_executor.py` | Paper trading with exits |
| `src/backtest/engine.py` | Historical backtesting |
| `src/risk/manager.py` | Risk controls |
| `config/settings.yaml` | Trading config |
| `config/risk.yaml` | Risk limits |
| `migrations/003_add_portfolio_tables.sql` | DB schema |
| `tests/test_backtest*.py` | Backtest tests |

---

## Changelog v1 → v2

- Added fact-check table with file references
- Marked exit logic and backtester as "Outdated" (now implemented)
- Added verified component status
- Created prioritized recommendation phases
- Added rollback/recovery plan
- Added file reference appendix
