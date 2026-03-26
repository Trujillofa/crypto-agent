# Sentiment/Macro Baseline WFO Results

**Generated:** 2026-03-26
**Method:** Server-side autoresearch on Hetzner
**Config:** `config/settings.sentiment_macro.yaml`
**Timeframe:** 1h
**WFO Windows:** 8 (3mo train / 2mo test)

---

## Baseline Summary

| Symbol | Win Rate | Total Return | Max DD | OOS Return | OOS Sharpe | P(Loss) | Trades | Status |
|--------|----------|--------------|--------|------------|------------|---------|--------|--------|
| **BTCUSDT** | 48.9% | -15.47% | 31.68% | -14.88% | -1.03 | 77.8% | 88 | ❌ FAIL |
| **ETHUSDT** | 36.4% | -37.81% | 44.33% | -22.69% | -1.22 | 98.2% | 88 | ❌ FAIL |
| **SOLUSDT** | 23.5% | -47.69% | 49.38% | -23.48% | -1.36 | 99.6% | 81 | ❌ FAIL |

---

## Gate Failures

| Gate | Threshold | BTCUSDT | ETHUSDT | SOLUSDT |
|------|-----------|---------|---------|---------|
| Min WFO Sharpe | ≥0.50 | -1.03 ❌ | -1.22 ❌ | -1.36 ❌ |
| Max Drawdown | ≤10% | 31.68% ❌ | 44.33% ❌ | 49.38% ❌ |
| Max P(Loss) | ≤25% | 77.8% ❌ | 98.2% ❌ | 99.6% ❌ |
| Min OOS Return | ≥0% | -14.88% ❌ | -22.69% ❌ | -23.48% ❌ |
| Max Profit Conc | ≤50% | 35.20% ✓ | 68.05% ❌ | 84.59% ❌ |

---

## Critical Finding: Live vs Backtest Discrepancy

### Live Paper Performance (Since Deploy)
| Metric | Live Value |
|--------|------------|
| Win Rate | **69.7%** |
| Total P&L | **+559 USDT** |
| Trades | 33 |
| Symbols | BTC/ETH/SOL 1h |

### Backtest Performance (Same Strategy)
| Metric | BTCUSDT | ETHUSDT | SOLUSDT |
|--------|---------|---------|---------|
| Win Rate | 48.9% | 36.4% | 23.5% |
| Return | -15.5% | -37.8% | -47.7% |

### Why the Difference?

1. **Sentiment gating is disabled in backtest**
   - The `SentimentScorer` requires an LLM (xAI/Grok) that's not available during backtest
   - It returns `50.0` (neutral) when no client is configured
   - This means every signal passes the sentiment gate - **no filtering**

2. **Parameter mismatch possible**
   - Live config may have different parameters than backtest
   - Need to compare `config/settings.sentiment_macro.yaml` with live deployment

3. **Exit rules differ**
   - Live uses paper executor with ATR-based stops
   - Backtest uses simpler exit logic

---

## Recommendations

### Immediate Actions

1. **Audit live vs backtest config**
   - Compare `settings.sentiment_macro.yaml` with running config
   - Verify aggregator thresholds match

2. **Investigate sentiment scorer**
   - Check if AI is actually enabled in live
   - If not, the "sentiment" strategy is just technical mean reversion

3. **Test with sentiment disabled**
   - Run backtest with `sentiment_gate_threshold: 0` to match live behavior
   - Compare results

### Next Experiments

The baseline strategy is **severely underperforming** in backtest. Before running parameter sweeps:

1. **Align backtest with live behavior**
   - Remove sentiment gating from backtest OR
   - Enable sentiment in live (requires API key)

2. **Test each symbol independently**
   - SOL/ETH/BTC may need different parameters

3. **Consider timeframe changes**
   - 1h may be too noisy for mean reversion
   - Test 4h or daily

---

## Raw Results (from TSV)

```
timestamp	run_id	commit	score	status	passes_gates	symbol	timeframe	start	end	wfo_return_pct	wfo_mean_sharpe	max_drawdown_pct	bootstrap_p_loss_pct	profit_concentration_pct	total_trades	description
2026-03-26T01:28:03.855842+00:00	20260326-012801-160193-f941e5	unknown	-319.903437	discard	false	BTCUSDT	1h	2024-01-09T07:00:00+00:00	2026-03-26T00:00:00+00:00	-14.88	-1.03	31.68	77.80	35.20	88	BTCUSDT_baseline
2026-03-26T01:28:28.531370+00:00	20260326-012826-532643-185898	unknown	-492.996169	discard	false	ETHUSDT	1h	2024-01-09T07:00+00:00	2026-03-26T00:00:00+00:00	-22.69	-1.22	44.33	98.20	68.05	88	ETHUSDT_baseline
2026-03-26T01:28:54.400351+00:00	20260326-012852-303703-bce538	unknown	-559.753042	discard	false	SOLUSDT	1h	2024-01-09T07:00:00+00:00	2026-03-26T00:00:00+00:00	-23.48	-1.36	49.38	99.60	84.59	81	SOLUSDT_baseline
```

---

## Files Generated

- Results: `/opt/crypto-agent/research/results.tsv` (on Hetzner)
- BTCUSDT report: `research/archive/experiment-autopilot-20260326-012801-160193-f941e5-*.md`
- ETHUSDT report: `research/archive/experiment-autopilot-20260326-012826-532643-185898-*.md`
- SOLUSDT report: `research/archive/experiment-autopilot-20260326-012852-303703-bce538-*.md`