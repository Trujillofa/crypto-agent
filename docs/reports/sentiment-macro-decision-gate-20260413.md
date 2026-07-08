# Sentiment-Macro Decision Gate Analysis

**Date**: 2026-04-13
**Agent**: `agent_sentiment_macro` (config: `settings.sentiment_macro.yaml`)
**Analysis window**: 2026-03-19 → 2026-04-13 (25 days, post P&L sizing fix)
**Gate context**: 100-trade decision gate from project memory

> Status checked on 2026-07-08. This report is a point-in-time decision gate.
> The unchecked criteria below should be re-evaluated from current production
> trade history before marking any item done or changing the strategy.

---

## TL;DR

Edge is **real**. Agent is **currently -20% off peak equity**. **Do NOT scale to live yet.**
Target: 80+ closed round-trips AND equity recovery to new highs before promoting.

---

## Dataset

Only post-fix trades (after 2026-03-19, when uncapped sizing bug was resolved) are
included. Pre-fix P&L is inflated and excluded.

- 98 total trade rows (49 entries + 49 exits = 49 closed round-trips)
- 25 days active, 13.7 trades/week cadence
- Avg notional per trade: $1,003 USDT

## By-Symbol Breakdown

| Symbol   | Closed | WR  | P&L       | Avg Win | Avg Loss | Best    | Worst    |
|----------|--------|-----|-----------|---------|----------|---------|----------|
| BTCUSDT  | 24     | 50% | +$98.55   | $25.75  | -$17.54  | +$47.83 | **-$66.54** |
| ETHUSDT  | 14     | 50% | +$89.72   | $25.70  | -$12.89  | +$45.30 | -$18.33  |
| SOLUSDT  | 11     | 64% | +$73.44   | $19.51  | -$15.79  | +$37.94 | -$24.24  |
| **Total**| **49** |**53%**|**+$261.71**| $24.00 | -$15.40  |         |          |

Edge is present on **all three symbols** — not a single-market fluke. BTC shows
a fat left tail (-$66.54 worst trade is 4× its avg loss).

## Equity Curve

- **Peak equity**: +$325.01 USDT
- **Current equity**: +$261.71 USDT (**-$63, -20% off peak**)
- **Max drawdown**: -$173.87 USDT
  - = 53% of peak equity (large relative DD)
  - = 17% of average notional size (moderate absolute)

## Per-Trade Stats

- Expectancy: **+$5.34 / trade**
- Stddev: $24.40
- Per-trade Sharpe: **0.219**

## P&L Distribution (right-skewed)

| p05      | p25      | median   | p75      | p95      |
|----------|----------|----------|----------|----------|
| -$28.44  | -$13.06  | **+$3.18** | +$20.79 | +$41.97  |

Median is positive, winners bigger than losers, distribution right-skewed.
This is the expected shape for a mean-reversion-on-sentiment strategy.

---

## Decision: HOLD (do not promote to live)

### Why edge is real
1. Positive P&L on all 3 symbols independently
2. Positive per-trade expectancy (+$5.34)
3. Positive median trade (+$3.18)
4. Right-skewed distribution (winners larger than losers)
5. 53% win rate with 1.56 win/loss ratio

### Why not to scale now
1. **Currently in active drawdown** (-20% from peak) — scaling into drawdown compounds risk
2. **N=49 closed trades** is the bare minimum for edge detection; insufficient for sizing decisions
3. **BTC left tail**: -$66.54 worst trade = 6.6× stddev. Promote BTC last, or tighten SL.
4. **Per-trade Sharpe 0.219** is acceptable for mean reversion but not high-confidence

### Exit criteria for HOLD (reassess when ALL met)
- [ ] 80+ closed round-trips post-fix
- [ ] Equity recovers to new all-time high (surpasses +$325.01)
- [ ] BTC worst-trade magnitude decreases or stops being an outlier

At 13.7 trades/week, 31 more round-trips = ~2–3 weeks of operation.

### Exit criteria for KILL (reassess strategy itself)
- [ ] Equity drops below peak drawdown (-$174 below peak = equity below +$151)
- [ ] Any single symbol PnL turns net-negative post-fix
- [ ] Per-trade expectancy turns negative over a rolling 20-trade window

---

## Action Items

- [ ] Re-run this analysis at 80 closed trades (approx 2026-04-27)
- [ ] If BTC worst-trade outlier repeats, investigate tightening BTC stop-loss
- [ ] No code or config changes required right now — agent is operating correctly

## References

- Agent config: `config/settings.sentiment_macro.yaml`
- Trade source: `trades` table, `agent_id = 'sentiment-macro-bot'`
- Pre-fix sizing bug: see `MEMORY.md` → `project_pnl_sizing_fix.md`
- Decision gate memory: `MEMORY.md` → `project_sentiment_macro_gate.md`
