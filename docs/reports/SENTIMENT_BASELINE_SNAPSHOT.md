# Sentiment Macro Agent — Baseline Snapshot

**Frozen:** 2026-03-26 ~03:15 UTC (first sentiment data)
**Server commit:** `f13cd55` (fix(infra): add persistent data volume for agent event logs)

## Deployed Configuration

| Parameter | Value |
|-----------|-------|
| Agent ID | `sentiment-macro-bot` |
| Mode | `paper` |
| Pairs | BTCUSDT, ETHUSDT, SOLUSDT |
| Timeframe | 1h |
| Strategy | `sentiment_mean_reversion` only |
| Execution | Futures paper (3x leverage, isolated, one-way) |
| Order size | 100 USDT per trade |
| SL/TP | ATR-based (SL 2.0x, TP 4.5x, trailing 1.5x/1.0x) |
| Sentiment model | `grok-4-1-fast-reasoning` via xAI |
| Sentiment cache TTL | 300s (5 min) |
| Eval interval | 3600s (1h) |
| Gate threshold | 35 (blocks buys during FUD) |
| Panic threshold | 20 (emergency sell) |
| Boost threshold | 65 (confidence bonus) |

## First Sentiment Observations

All 3 initial observations were **live from Grok** (100% live rate):

| Symbol | Score | Bucket |
|--------|-------|--------|
| BTCUSDT | 68 | Bullish |
| ETHUSDT | 75 | Bullish |
| SOLUSDT | 82 | Euphoric |

## What We're Measuring

### Primary question
> Does Grok sentiment gating improve mean reversion performance vs neutral (50.0) fallback?

### Data needed before evaluation
- **Minimum duration:** 48 hours of continuous operation
- **Minimum observations:** 100+ sentiment scores (expect ~3/hr = 144 in 48h)
- **Minimum trades:** 5+ paper trades executed by the sentiment agent
- **Grok uptime:** >80% live responses (vs fallback)

### Evaluation criteria (after enough data)
1. **Grok reliability:** Live rate %, error rate, score distribution spread
2. **Score usefulness:** Does score vary meaningfully (spread > 20)? Does it correlate with price moves?
3. **Gate effectiveness:** Are there trades blocked by sentiment < 35 that would have been losers?
4. **Win rate delta:** Paper WR with sentiment vs backtest WR without (baseline: -15% to -48% in backtest with neutral fallback)
5. **P&L direction:** Is the sentiment agent profitable, or at least less negative than neutral backtest?

### Next step: RecordedSentimentProvider
Once enough data accumulates, build `RecordedSentimentProvider` to replay recorded Grok scores in backtests — closing the live/backtest parity gap.

## All Agents Health at Snapshot Time

All 7 agents healthy, uptime 17+ minutes after last deploy.

| Agent | Status |
|-------|--------|
| agent (default) | Healthy |
| agent_2 | Healthy |
| agent_btc | Healthy |
| agent_btc_mtf | Healthy |
| agent_avax | Healthy |
| agent_sol_sparse | Healthy |
| agent_sentiment_macro | Healthy |
