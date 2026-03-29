# Trading Mode Tagging Bug — Audit Report

**Date:** 2026-03-29
**Fixed in:** commits `5933ff5`, `4e57d8a`, `d5ed228`
**Deployed:** 2026-03-29 19:32 UTC (all 7 agents rebuilt)

## Bug Summary

Signals from individual strategies always defaulted to `trading_mode="spot"` (the Signal dataclass default) regardless of the `strategy.default_trading_mode` config setting. The aggregator saw uniform `"spot"` and returned it without consulting the configured default.

**Impact:** Agents configured with `default_trading_mode: futures` had their signals tagged as `spot`, affecting:
- Execution routing (spot vs futures path in paper executor)
- Position margin accounting (full notional vs leveraged)
- Portfolio market labels
- Historical trade records in DB

## Root Cause

1. `Signal.trading_mode` defaults to `"spot"` (`src/strategy/signals.py:35`)
2. No strategy implementation sets `trading_mode` on signals
3. `SignalAggregator._resolve_trading_mode()` returns the unanimous mode from signals — which was always `"spot"` — without checking the configured default

## Fix

1. **Engine stamps default** (`src/strategy/engine.py`): Before aggregation, signals with dataclass-default `"spot"` are re-stamped with the configured `default_trading_mode`
2. **Aggregator defense-in-depth** (`src/strategy/aggregator.py`): When all signals have `"spot"` but configured default is non-spot, returns the configured default
3. **signal_ignored logging** (`src/execution/paper_executor.py`): Moved into actual ignore branches with differentiated reasons

## Production Data Impact

### Fleet-wide closed trades by market tag

```sql
SELECT market, COUNT(*) as trades, SUM(realized_pnl)::numeric(10,2) as total_pnl
FROM positions WHERE status = 'closed'
GROUP BY market ORDER BY trades DESC;
```

| Market | Trades | P&L |
|--------|--------|-----|
| spot | 80 | +$19.74 |
| futures | 75 | +$2,264.14 |

### Sentiment-macro agent breakdown

```sql
SELECT market, COUNT(*) AS trades,
  SUM(realized_pnl)::numeric(10,2) AS total_pnl,
  COUNT(*) FILTER (WHERE realized_pnl > 0) AS wins,
  COUNT(*) FILTER (WHERE realized_pnl <= 0) AS losses
FROM positions
WHERE agent_id = 'sentiment-macro-bot' AND status = 'closed'
GROUP BY market;
```

| Market | Trades | P&L | Wins | Losses | Win Rate |
|--------|--------|-----|------|--------|----------|
| futures | 34 | +$577.75 | 22 | 12 | 65% |
| spot | 14 | -$97.72 | 3 | 11 | 21% |

The 14 spot-tagged trades cluster on **2026-03-26 to 2026-03-27**, coinciding with the EMA200 trend filter tightening. These were opened via the spot execution path, which uses full notional margin instead of leveraged margin.

### SOL trend pullback — duplicate entries

```sql
SELECT entry_time, market, position_side, realized_pnl
FROM positions WHERE agent_id = 'sol-trend-pullback-sparse'
ORDER BY entry_time;
```

Three trades on 2026-03-12 each appear as both `spot` and `futures` entries (6 rows for 3 actual trades). The 7th trade on 2026-03-24 is spot-only.

## Verification

- **Code fix deployed:** Confirmed via `grep` inside running container
- **Runtime test:** Aggregator returns `trading_mode=futures` when configured — verified in-container
- **Live log confirmation:** Pending next non-HOLD signal (all signals currently blocked by EMA200 trend filter)

## Recommendations

- Historical data is not worth correcting — the positions are closed and P&L is realized
- New trades will be correctly tagged going forward
- The duplicate SOL entries inflate trade counts in reports — account for this in analysis
