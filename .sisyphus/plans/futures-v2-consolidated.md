# Futures Trading Support — Consolidated Implementation Plan

> Supersedes: `futures-trading-support.md` (original 12-task plan)
> Applied: 5-Step Engineering Framework review + validation

## TL;DR

- **Scope**: LONG-only MVP with isolated margin on Binance USDⓈ-M perpetual futures
- **Architecture**: 2 new files + 3 extended files (not 7 new files)
- **Tasks**: 8 tasks in 3 waves (down from 12 tasks / 4 waves)
- **Signal approach**: Option B — add `trading_mode` field to Signal, no new SignalTypes
- **Safety**: Isolated margin, one-way positions, 5x default / 10x max / 20x hard cap leverage, 5% liquidation buffer
- **Test baseline**: 214 tests (all passing) — futures must not regress this

---

## What Changed (5-Step Review)

| Original Plan | After Review | Reason |
|---------------|-------------|--------|
| 7 new files | 2 new + 3 extended | Step 2: Delete redundant wrappers |
| Separate `FuturesRiskManager` | Extend `RiskManager` directly | 3 check methods, same pattern |
| Separate `FuturesPortfolioManager` | Extend `PortfolioManager` + nullable fields | Same lifecycle, extra fields |
| Separate `mark_price_feed.py` | Reuse `BinanceWebSocketIngestor` | Parameterize `base_url` |
| SHORT + CLOSE SignalTypes | LONG-only, `trading_mode` field | Step 1: Ship LONG first, validate pipeline |
| 12 tasks, 4 waves | 8 tasks, 3 waves | Step 2: Merged tests with implementation, deleted redundant tasks |
| "~40-60 hours" estimate | Large (8 tasks, 3 waves) | CLEO sizing convention |

---

## File Changes

### New Files (2)

```
src/execution/futures_client.py     # BinanceFuturesClient — fapi.binance.com endpoints
src/execution/futures_executor.py   # FuturesTradingExecutor — LONG-only order flow
```

### Extended Files (3)

```
src/risk/manager.py                 # ADD: check_liquidation_buffer(), check_max_leverage(), check_margin_usage()
src/portfolio/models.py             # ADD: position_side, leverage, liquidation_price, margin_type, funding_fees (nullable)
src/ingest/websocket.py             # MODIFY: configurable base_url for fstream.binance.com
```

### Config Changes

```
config/settings.yaml                # ADD: futures: section
config/risk.yaml                    # ADD: futures_limits: section
src/main.py                         # ADD: futures config parsing + mode selection
```

---

## Signal Design (Option B)

```python
@dataclass(frozen=True)
class Signal:
    type: SignalType          # BUY/SELL/HOLD (unchanged)
    symbol: str
    price: float
    confidence: float
    reason: str
    indicators: dict[str, float]
    trading_mode: str = "spot"  # NEW: "spot" or "futures"
```

Executor interpretation in futures mode:
- `BUY` → Open LONG position
- `SELL` → Close LONG position (reduceOnly=True)
- `HOLD` → No action

Existing strategies work unchanged. No new SignalTypes needed for MVP.

---

## Database Schema

Single `positions` table extended with nullable futures fields:

```sql
-- Futures columns added to existing positions table:
ALTER TABLE positions ADD COLUMN position_side TEXT;           -- 'LONG', NULL for spot
ALTER TABLE positions ADD COLUMN leverage INTEGER;             -- 1-20x, NULL for spot
ALTER TABLE positions ADD COLUMN margin_type TEXT;             -- 'isolated', NULL for spot
ALTER TABLE positions ADD COLUMN liquidation_price DOUBLE PRECISION;  -- NULL for spot
ALTER TABLE positions ADD COLUMN mark_price DOUBLE PRECISION;         -- NULL for spot
ALTER TABLE positions ADD COLUMN funding_fees DOUBLE PRECISION DEFAULT 0;
```

Spot positions: all futures columns are NULL. No migration needed for existing rows.

---

## Tasks

### Wave 1: Foundation (Parallel — no dependencies)

#### Task 1: Add Futures Configuration

**Files**: `config/settings.yaml`, `config/risk.yaml`, `src/main.py`
**Tests**: `tests/test_futures_config.py`

Add `futures:` section to settings:
```yaml
futures:
  enabled: false
  symbols: [BTCUSDT, ETHUSDT]
  default_leverage: 5
  max_leverage: 10
  margin_mode: isolated
  position_mode: one-way
  test_mode: true
  liquidation_buffer_pct: 5.0
```

Add `futures_limits:` to risk.yaml:
```yaml
futures_limits:
  max_leverage: 10
  hard_cap_leverage: 20
  liquidation_buffer_pct: 5.0
  max_margin_usage_pct: 50.0
  max_daily_loss_pct: 5.0
```

Add validation in `load_settings()`: reject >20x leverage, reject cross margin.

Environment variables:
- `BINANCE_FUTURES_API_KEY` / `BINANCE_FUTURES_API_SECRET` (separate from spot keys)

**Acceptance criteria**:
- [ ] `futures.enabled` defaults to `false`
- [ ] Rejects leverage > 20x at startup
- [ ] Rejects `margin_mode: cross`
- [ ] Loads futures API keys from env vars

**Commit**: `feat(config): add futures configuration schema with safety limits`

---

#### Task 2: Create BinanceFuturesClient

**Files**: `src/execution/futures_client.py`
**Tests**: `tests/test_futures_client.py`

Implement `BinanceFuturesClient` following `BinancePrivateClient` patterns:
- Base URL: `https://fapi.binance.com` (live) or `https://demo-api.binance.com` (test mode)
- `set_leverage(symbol, leverage)` — POST `/fapi/v1/leverage`
- `get_position_risk(symbol)` — GET `/fapi/v2/positionRisk`
- `place_futures_order(symbol, side, quantity, reduce_only)` — POST `/fapi/v1/order`
- `get_funding_rate(symbol)` — GET `/fapi/v1/fundingRate`
- `get_account_info()` — GET `/fapi/v2/account`

All methods use HMAC-SHA256 signing (same as spot client).

**Acceptance criteria**:
- [ ] Connects to fapi.binance.com or demo-api.binance.com based on test_mode
- [ ] Set leverage validates 1-20x before API call
- [ ] Place order supports `reduceOnly` flag
- [ ] All endpoints tested with mocked responses

**Commit**: `feat(execution): add BinanceFuturesClient with fapi endpoints`

---

#### Task 3: Extend Position Model with Futures Fields

**Files**: `src/portfolio/models.py`, `src/portfolio/manager.py`
**Tests**: `tests/test_futures_position.py`

Add nullable futures fields to existing `Position` dataclass:
```python
position_side: str | None = None        # "LONG", None for spot
leverage: int | None = None             # 1-20x, None for spot
margin_type: str | None = None          # "isolated", None for spot
liquidation_price: float | None = None  # Calculated, None for spot
mark_price: float | None = None         # From mark price feed, None for spot
funding_fees: float = 0.0               # Accumulated funding
```

Add liquidation price calculation:
```python
def calculate_liquidation_price(entry_price, leverage, side, maintenance_margin_rate=0.004):
    """Binance isolated margin liquidation formula."""
    if side == "LONG":
        return entry_price * (1 - 1/leverage + maintenance_margin_rate)
    else:  # SHORT (future use)
        return entry_price * (1 + 1/leverage - maintenance_margin_rate)
```

Extend `PortfolioManager` to handle futures fields in open/close/update operations.

**Acceptance criteria**:
- [ ] Spot positions work unchanged (futures fields are None)
- [ ] LONG liquidation price < entry price
- [ ] Liquidation formula matches Binance docs
- [ ] DB schema supports nullable futures columns

**Commit**: `feat(portfolio): extend Position model with futures fields and liquidation calc`

---

### Wave 2: Core Logic (After Wave 1)

#### Task 4: Add Futures Risk Checks to RiskManager

**Files**: `src/risk/manager.py`, `config/risk.yaml`
**Tests**: `tests/test_futures_risk.py`
**Depends on**: Task 1 (config), Task 3 (models)

Add to `RiskConfig`:
```python
@dataclass
class FuturesLimits:
    max_leverage: int = 10
    hard_cap_leverage: int = 20
    liquidation_buffer_pct: float = 5.0
    max_margin_usage_pct: float = 50.0
    max_daily_loss_pct: float = 5.0
```

Add 3 methods to `RiskManager`:
- `check_liquidation_buffer(mark_price, liq_price, buffer_pct)` → `tuple[bool, str]`
- `check_max_leverage(requested, max_allowed)` → `tuple[bool, str]`
- `check_margin_usage(used_margin, available_margin)` → `tuple[bool, str]`

These follow the exact same pattern as existing `check_position_limit()`.

**Acceptance criteria**:
- [ ] Blocks orders when mark price within 5% of liquidation
- [ ] Rejects leverage > configured max (and hard cap 20x)
- [ ] Warns when margin usage > 50%
- [ ] Existing spot risk checks unchanged

**Commit**: `feat(risk): add futures liquidation buffer, leverage cap, and margin checks`

---

#### Task 5: Add trading_mode Field to Signal

**Files**: `src/strategy/signals.py`
**Tests**: `tests/test_signals.py` (extend existing)
**Depends on**: Task 1 (config for mode)

Add single field to Signal dataclass:
```python
trading_mode: str = "spot"  # "spot" or "futures"
```

No other changes. Existing strategies continue to produce signals with `trading_mode="spot"` by default. The executor reads this field to decide execution path.

**Acceptance criteria**:
- [ ] Signal defaults to `trading_mode="spot"`
- [ ] All existing strategy tests pass unchanged
- [ ] Signal.__str__ includes trading_mode when not "spot"

**Commit**: `feat(strategy): add trading_mode field to Signal dataclass`

---

#### Task 6: Create FuturesTradingExecutor

**Files**: `src/execution/futures_executor.py`
**Tests**: `tests/test_futures_executor.py`
**Depends on**: Task 2 (client), Task 3 (models), Task 4 (risk), Task 5 (signals)

LONG-only executor for MVP:
- `on_signal(signal)` — routes BUY → open LONG, SELL → close LONG
- `_open_long(symbol, price)` — set leverage, place market order, register position
- `_close_long(symbol, price)` — place market order with `reduceOnly=True`, record PnL
- Risk checks before every order (liquidation buffer + leverage + margin)

**Must NOT do**:
- No SHORT support (MVP is LONG-only)
- No position flips (close then open — two separate operations)
- No hedge mode

**Acceptance criteria**:
- [ ] BUY signal opens LONG position with configured leverage
- [ ] SELL signal closes LONG with reduceOnly
- [ ] Risk checks run before every order
- [ ] Position tracked in portfolio manager with futures fields
- [ ] Telegram notification on trade execution

**Commit**: `feat(execution): add FuturesTradingExecutor with LONG-only support`

---

### Wave 3: Integration (After Wave 2)

#### Task 7: Wire Futures into Main + Mark Price Feed

**Files**: `src/main.py`, `src/ingest/websocket.py`
**Tests**: `tests/test_futures_integration.py`
**Depends on**: All Wave 1 + Wave 2 tasks

Main.py changes:
```python
if settings.futures.enabled and settings.mode == "futures":
    futures_client = BinanceFuturesClient(...)
    executor = FuturesTradingExecutor(...)
    # Start mark price WebSocket
else:
    executor = TradingExecutor(...)  # Existing spot path
```

WebSocket changes:
- Add `base_url` parameter to `BinanceWebSocketIngestor.__init__()` (currently hardcoded)
- In futures mode, start second instance: `BinanceWebSocketIngestor(base_url="wss://fstream.binance.com/ws", ...)`
- Subscribe to `!markPrice@arr` stream
- Add handler for `"markPriceUpdate"` event type (alongside existing `"kline"` handler)

**Acceptance criteria**:
- [ ] `mode: spot` initializes spot executor only (no futures code paths)
- [ ] `mode: futures` initializes futures client + executor + mark price feed
- [ ] Mark price updates flow to position.mark_price
- [ ] Startup logs show correct mode

**Commit**: `feat(main): add futures mode selection with mark price feed integration`

---

#### Task 8: Testnet Validation + Documentation

**Depends on**: Task 7

Manual testing on demo-api.binance.com:
1. Configure futures mode with demo API keys
2. Start agent, verify "Futures mode enabled" in logs
3. Verify mark price updates flowing
4. Wait for or manually trigger a BUY signal
5. Verify LONG position opened on demo account
6. Wait for SELL signal, verify position closed with PnL

Update USAGE.md with:
- Futures configuration section
- Liquidation risk warnings
- Demo trading setup instructions

**Acceptance criteria**:
- [ ] Full LONG lifecycle works on demo
- [ ] Spot mode regression: all 214 tests still pass
- [ ] USAGE.md has futures trading section

**Commit**: `docs: add futures trading guide to USAGE.md`

---

## Pre-Implementation Checklist

- [x] Test baseline: 214/214 passing
- [x] Uncommitted SHORT edits reverted
- [x] 5-Step framework applied and validated
- [ ] Futures demo API keys obtained (from demo.binance.com)
- [ ] Verify demo-api.binance.com supports fapi endpoints (Task 2 will validate this)

## Dependency Graph

```
Wave 1 (parallel):  Task 1 ──┬── Task 4 ──┐
                    Task 2 ──┤            ├── Task 6 ── Task 7 ── Task 8
                    Task 3 ──┼── Task 4 ──┘
                             └── Task 5 ──┘
```

## Safety Guardrails (Non-Negotiable)

1. Isolated margin only (cross = future enhancement)
2. One-way positions only (hedge = future enhancement)
3. LONG-only for MVP (SHORT = v2 after LONG validated)
4. Hard leverage cap: 20x enforced in code
5. Liquidation buffer: 5% of mark price
6. Spot mode unchanged: `mode: spot` is default, no futures code paths
7. Paper trading first: `test_mode: true` required before live
