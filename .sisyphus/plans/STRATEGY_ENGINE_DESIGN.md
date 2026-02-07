# Strategy Engine Design Document

## TL;DR

> Wire StrategyEngine to read indicators from TimescaleDB, evaluate EMA crossover, and deliver signals to TradingExecutor — Spot API, paper mode only.
>
> **Scope**:
> - IndicatorReader reads latest indicators + close_price from DB
> - StrategyEngine fetches indicators and evaluates EMA crossover
> - TradingExecutor.on_signal() places Spot orders (quoteOrderQty for BUY, base qty for SELL)
> - StrategyEngine started in `src/main.py`
>
> **Effort**: Medium
> **Critical Path**: IndicatorReader + EngineConfig → Spot cleanup → Strategy + Executor wiring → main.py + tests

---

## Spot API Reality Check

This project uses **Binance Spot API** (`/api/v3/`), NOT Futures. The codebase has Futures artifacts that must be cleaned. Key differences that affect the strategy engine:

| Spot Reality | Implication |
|--------------|-------------|
| No positions (only balances) | BUY = acquire base asset, SELL = liquidate held base asset |
| No leverage | `max_leverage` config is meaningless |
| No short-selling | SELL signal only valid if you hold the asset |
| `quantity` = base asset amount | BUY needs `quoteOrderQty` (spend X USDT), SELL needs base asset balance |
| No `reduce_only` | Parameter is ignored |

### Futures Artifacts to Clean

**Delete** (dead code that never produces real data):
- `PositionInfo` dataclass — Spot has no positions
- `get_positions()` — always returns `[]`
- Position monitoring loop in `executor._monitor_and_update()` — loops 10 symbols getting empty lists
- `reduce_only` parameter on order methods
- `max_leverage` from `risk.yaml` and `PositionLimits`

**Simplify** (keep but fix):
- `AccountInfo` → just `total_balance` and `available_balance` (drop margin/position/unrealized fields)
- `update_account_balance()` metric → just `total` and `available` (drop `total_margin`)
- Position metrics → remove (exposure, unrealized_pnl, position_duration)

---

## Existing Code (Confirmed)

**SimpleMACrossoverStrategy** (`src/strategy/simple_ma.py`):
- Uses **EMA** columns (`ema_12`, `ema_26`), requires `close_price`
- Stateful crossover detection via `_previous_ema_short`/`_previous_ema_long` per symbol
- Crossover fires once, then state updates — **no external dedup needed**

**StrategyEngine** (`src/strategy/engine.py`):
- Scaffold with `indicators = {}` placeholder at `_evaluate_all()` line 109
- `on_signal` async callback — already forwards to caller
- **Bugs to fix**: `callable` lowercase (lines 69, 90), mutable default `[]` on `EngineConfig.strategy_configs`

**Indicator Storage** (`src/features/writer.py`):
- Table `indicators`: PK `(time, symbol, timeframe)`, columns include `ema_12`, `ema_26`
- `close_price` in `ohlcv` table — join on `(time, symbol, timeframe)`
- pg8000 + `asyncio.to_thread()` pattern, SQLite fallback

**TradingExecutor** (`src/execution/executor.py`):
- Has `place_market_order(symbol, side, quantity)` with RiskManager gating
- `_calculate_quantity()` returns raw USDT — **wrong for Spot** (needs base asset conversion)
- Missing `on_signal()` method
- When `enabled=false`, `self._client` is `None` — any method accessing client must guard

---

## Deleted Requirements

| Deleted | Why |
|---------|-----|
| **Research task** | Already done. Schema, queries, interface all documented above. |
| **Evaluation overlap guard** | Evaluation is 1 DB query (~5ms) + 1 comparison. Interval is 60,000ms. Overlap is physically impossible. Exception handler covers DB failures. |
| **Data freshness guard** | If indicators haven't updated, strategy sees same data → no crossover → HOLD. Self-correcting. |
| **Signal dedup by timestamp** | Strategy's stateful crossover already prevents repeat signals. `_previous_ema` updates after each evaluation. A second dedup layer is redundant. |
| **5 separate test files** | Consolidated to 2. Paper-mode is one assertion, not a file. |
| **Configurable MA keys in settings.yaml** | Strategy already has `ema_short_period`/`ema_long_period` config. Adding a second config layer is indirection for no benefit. |

---

## Must NOT Have (Guardrails)

- No multi-strategy support or dynamic registry
- No signal persistence or history table
- No backtesting or performance metrics
- No new risk logic (wire existing RiskManager only)
- No margin/futures/leverage concepts
- Keep `trading_execution.enabled=false` default

---

## Known Limitations

- **Symbol parsing**: `base_asset = symbol.replace("USDT", "")` works for all current pairs (BTCUSDT, ETHUSDT, etc.) but would break for hypothetical pairs like `USDCUSDT`. Acceptable for now — all 10 configured pairs use `{BASE}USDT` format. Add a proper symbol→base_asset mapping when adding non-USDT pairs.

---

## Architecture Overview

```
┌─────────────────────────────────────────────┐
│           IndicatorWriter (existing)         │
│     Writes: ema_12, ema_26, close_price    │
└───────────────────┬─────────────────────────┘
                    │ (DB)
                    ▼
┌─────────────────────────────────────────────┐
│           IndicatorReader (NEW)              │
│  fetch_latest(symbol, timeframe, limit=2)   │
│  Returns: [{ema_12, ema_26, close_price}]   │
└───────────────────┬─────────────────────────┘
                    │
                    ▼
┌─────────────────────────────────────────────┐
│           StrategyEngine (existing)          │
│  _fetch_indicators() → EMA crossover eval    │
│  Emits: Signal(Symbol, BUY/SELL/HOLD)       │
└───────────────────┬─────────────────────────┘
                    │
                    ▼
┌─────────────────────────────────────────────┐
│        TradingExecutor (modified)            │
│  on_signal() → Spot order placement         │
│  BUY: quoteOrderQty (spend USDT)            │
│  SELL: base asset balance check             │
└─────────────────────────────────────────────┘
```

---

## Signal Flow

1. **IndicatorReader** fetches latest 2 rows (ema_12, ema_26, close_price) from DB
2. **StrategyEngine** evaluates EMA crossover:
   - If ema_12 crosses above ema_26 → BUY signal
   - If ema_12 crosses below ema_26 → SELL signal
   - Otherwise → HOLD (not emitted)
3. **TradingExecutor.on_signal()** handles signals:
   - BUY → `place_market_order(symbol, "BUY", order_size_usdt)` with `quoteOrderQty`
   - SELL → Check `get_asset_balance(base_asset)`, sell held amount
   - RiskManager gates all order placements

---

## Database Schema

**indicators table** (existing):
```
PRIMARY KEY (time, symbol, timeframe)
Columns: ema_12, ema_26, [other indicators]
```

**ohlcv table** (existing):
```
PRIMARY KEY (time, symbol, timeframe)
Columns: close_price
```

**Join query**:
```sql
SELECT i.time, i.ema_12, i.ema_26, o.close_price
FROM indicators i
JOIN ohlcv o ON i.time = o.time AND i.symbol = o.symbol AND i.timeframe = o.timeframe
WHERE i.symbol = %s AND i.timeframe = %s
ORDER BY i.time DESC
LIMIT %s
```

---

## Spot Order Placement Logic

**BUY Signal**:
```python
# Spot API uses quoteOrderQty for market buys
params = {
    "symbol": symbol,
    "side": "BUY",
    "type": "MARKET",
    "quoteOrderQty": str(quantity_usdt)  # Spend X USDT
}
```

**SELL Signal**:
```python
# Get current base asset balance
base_asset = symbol.replace("USDT", "")
balance = await client.get_asset_balance(base_asset)

if balance > 0:
    params = {
        "symbol": symbol,
        "side": "SELL",
        "type": "MARKET",
        "quantity": str(balance)  # Sell held amount
    }
```

---

## Paper Mode Guarantees

When `test_mode=true`:
- `place_market_order()` returns mock order, no real API call
- `get_asset_balance()` returns mock balance (e.g., `1.0`)
- No real orders placed on Binance
- TradingExecutor can still run end-to-end with mock data

When `enabled=false`:
- `on_signal()` returns early before any client access
- `self._client` is `None`, so accessing it is impossible

---

## Key Files Modified

| File | Changes |
|------|---------|
| `src/features/reader.py` | Add `IndicatorReader` class |
| `src/strategy/engine.py` | Fix bugs, implement `_fetch_indicators()` |
| `src/execution/executor.py` | Add `on_signal()`, cleanup |
| `src/execution/binance_client.py` | Simplify for Spot, add `get_asset_balance()` |
| `src/execution/metrics.py` | Remove position metrics |
| `src/risk/manager.py` | Remove `max_leverage` |
| `src/main.py` | Wire StrategyEngine into main loop |
| `config/settings.yaml` | Add strategy config section |
| `tests/test_indicator_reader.py` | New tests |
| `tests/test_strategy_integration.py` | New integration tests |

---

*Design document generated: Feb 7, 2026*
