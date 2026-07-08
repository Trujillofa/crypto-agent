# Multi-Agent Isolation Model

**Date**: 2024-03-17
**Issue**: #15

## Current State

### Already Implemented ✅

1. **Agent ID Isolation**
   - Risk state: `data/risk_state_{agent_id}.json`
   - Portfolio/positions: `agent_id` column in DB
   - Trades: `agent_id` column in DB
   - Paper executor: position keys scoped by `agent_id`

2. **Database Schema**
   ```sql
   positions: id, symbol, market, position_side, entry_time, entry_price,
              quantity, status, exit_time, exit_price, realized_pnl, agent_id
   trades: id, time, symbol, market, side, quantity, price, order_id,
           pnl, position_id, agent_id
   ```

3. **Position Identity**
   ```python
   position_key = (market, scoped_symbol)
   scoped_symbol = f"{agent_id}::{symbol}"  # if agent_id != "default"
   ```

## Remaining Gap: Timeframe Isolation

### Problem
Position identity does NOT include timeframe:
- `SOLUSDT` on 4h and `SOLUSDT` on 1h share the same position key
- If two agents trade the same symbol on different timeframes, they share positions
- This can cause unintended position interference

### Example Collision Scenario
```
Agent A: SOLUSDT 4h strategy → Opens long position
Agent B: SOLUSDT 1h strategy → Thinks position is already open → Doesn't enter
Result: Agent B's signals are effectively ignored
```

## Proposed Solutions

### Option 1: Timeframe in Agent ID (Recommended)
Include timeframe in the agent_id naming convention:
```yaml
# config/settings.yaml
agent_id: "sol_4h_trend"  # Symbol + timeframe + strategy
```

**Pros**:
- No code changes required
- Existing isolation works as-is
- Simple and explicit

**Cons**:
- Requires careful agent naming
- Doesn't enforce timeframe separation at code level

### Option 2: Add Timeframe Column
Add `timeframe` column to positions/trades tables:
```sql
ALTER TABLE positions ADD COLUMN timeframe TEXT DEFAULT '1h';
ALTER TABLE trades ADD COLUMN timeframe TEXT DEFAULT '1h';
```

Update position_key:
```python
position_key = (market, scoped_symbol, timeframe)
```

**Pros**:
- Enforces timeframe isolation at schema level
- Can query positions by timeframe

**Cons**:
- Database migration required
- Updates needed throughout codebase
- Breaking change for existing positions

### Option 3: Scoped Symbol Enhancement
Include timeframe in scoped symbol:
```python
def _scope_symbol(self, symbol: str, timeframe: str = "1h") -> str:
    base = f"{symbol}_{timeframe}"
    if not self._symbol_prefix:
        return base
    return f"{self._symbol_prefix}{base}"

# SOLUSDT on 4h with agent "trend":
# "trend::SOLUSDT_4h"
```

**Pros**:
- No DB schema changes
- Backward compatible (default timeframe)
- Clear position identity

**Cons**:
- Changes position symbol format
- May break external integrations expecting clean symbols

## Recommendation

**Adopt Option 1 (Timeframe in Agent ID) as immediate fix:**

1. Document the naming convention: `{symbol}_{timeframe}_{strategy}`
2. Update all agent configs to use descriptive agent_ids
3. Add validation to warn if same symbol appears in multiple agents

**Implement Option 3 (Scoped Symbol Enhancement) as medium-term:**

1. Add optional `timeframe` parameter to PortfolioManager
2. Update `_scope_symbol()` to include timeframe
3. Update executor to pass timeframe when opening positions

## Implementation: Option 1

### Updated Agent Configs
```yaml
# settings.sol_4h.yaml
agent_id: "sol_4h_breakout"
trading_pairs: ["SOLUSDT"]
strategy:
  timeframe: "4h"

# settings.sol_1h.yaml
agent_id: "sol_1h_trend"
trading_pairs: ["SOLUSDT"]
strategy:
  timeframe: "1h"
```

### Validation Script
```python
# scripts/validate_agent_isolation.py
# Warns if same (symbol, agent_id base) appears multiple times
```

## Verification Checklist

Status checked on 2026-07-08. Local config and tests can only prove part of this;
production DB state still needs live verification.

- [ ] All production agents use unique agent_ids. Check `docker-compose.prod.yml`
  and the running containers before marking done.
- [ ] No two agents share symbol without timeframe distinction. Check active
  configs and runtime `AGENT_ID` values before marking done.
- [ ] Position queries show expected isolation. Requires live TimescaleDB queries.
- [ ] Cross-agent contamination test passes. `scripts/validate_agent_isolation.py`
  exists; run it against the current production DB before marking done.

## Notes

The MTF infrastructure (issue #20) was tested and abandoned. The remaining
isolation concern is for single-timeframe agents that might share symbols.

If multi-timeframe trading is needed in the future, implement Option 3.
