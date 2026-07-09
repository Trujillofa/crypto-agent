# Sentiment-Macro Decision Gate Analysis

**Date**: 2026-04-13
**Agent**: `agent_sentiment_macro` (config: `settings.sentiment_macro.yaml`)
**Analysis window**: 2026-03-19 → 2026-04-13 (25 days, post P&L sizing fix)
**Gate context**: 100-trade decision gate from project memory

> Status checked on 2026-07-08. This report is a point-in-time decision gate.
> The unchecked criteria below should be re-evaluated from current production
> trade history before marking any item done or changing the strategy.

> Rechecked on 2026-07-08 from production `positions` for
> `sentiment-macro-bot` after 2026-03-19: 86 closed positions, final realized
> PnL +316.29 USDT, peak equity +325.01 USDT, BTC worst trade -66.54 USDT, and
> minimum rolling 20-trade expectancy -5.50 USDT. No sentiment-macro trades were
> recorded in the last 7 days.

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
- [x] 80+ closed round-trips post-fix
- Equity recovery gate did not pass. Follow-up evidence checked on 2026-07-08
  showed final post-fix equity at +$316.29, below the +$325.01 peak.
- BTC outlier gate did not pass. BTC remained the worst post-fix trade at
  -$66.54.

At 13.7 trades/week, 31 more round-trips = ~2–3 weeks of operation.

### Exit criteria for KILL (reassess strategy itself)
- Peak drawdown kill gate did not trigger in the checked production sample:
  equity stayed above the +$151 threshold.
- Per-symbol PnL kill gate did not trigger in the checked production sample:
  BTC, ETH, and SOL each remained net-positive post-fix.
- [x] Rolling expectancy kill gate triggered. The checked production sample had
  a minimum rolling 20-trade expectancy of -$5.50.

### Follow-up status checked on 2026-07-08

The April HOLD decision is superseded. `config/settings.sentiment_macro.yaml`
disarms the agent to paper, and the 2026-06-19 vol-filter sweep found no
corrected-cost threshold that both trades and keeps an edge. Do not re-arm this
strategy without a new pre-registered validation plan and fresh production
evidence.

---

## Action Items

- [x] Re-run this analysis at 80 closed trades (approx 2026-04-27)
- BTC stop-loss tightening is no longer a standalone action for this strategy:
  the current recommendation is to keep sentiment-macro disarmed to paper.
- No immediate code or config change is required from this report because the
  strategy is already disarmed in `config/settings.sentiment_macro.yaml`.

## References

- Agent config: `config/settings.sentiment_macro.yaml`
- Trade source: `trades` table, `agent_id = 'sentiment-macro-bot'`
- Pre-fix sizing bug: see `MEMORY.md` → `project_pnl_sizing_fix.md`
- Decision gate memory: `MEMORY.md` → `project_sentiment_macro_gate.md`
