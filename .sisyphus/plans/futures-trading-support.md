# Futures Trading Support Implementation

## TL;DR

> **Objective**: Extend crypto-trading-agent from spot-only to support Binance USDⓈ-M perpetual futures trading with leverage, while maintaining full backward compatibility with spot trading.
>
> **Key Challenge**: Futures introduces liquidation risk, leverage amplification, and margin management - requiring significant safety infrastructure beyond spot's position limits.
>
> **Deliverables**: 
> - Futures API client (`BinanceFuturesClient`)
> - Leveraged position tracking (`FuturesPosition` model)
> - Enhanced risk manager (liquidation monitoring, margin checks)
> - LONG/SHORT signal support with `reduceOnly` order handling
> - Configuration schema for futures settings (leverage, margin mode, max exposure)
>
> **Estimated Effort**: Large (12 tasks, ~40-60 hours)
> **Parallel Execution**: YES - 4 waves
> **Critical Path**: Config → (Client + Models) → PortfolioMgr + Risk → Executor → Integration
> **Leverage Settings**: Default 5x, Max 10x, Hard Cap 20x (safety limit)

---

## Context

### Current State (Spot Only)

The agent currently implements a **spot-only trading pipeline**:

```
Signal(BUY/SELL) → Risk Check (position % limit) → Market Order → Portfolio Position
```

**Spot-Specific Assumptions:**
1. **BUY** = spend USDT to acquire base asset (BTC, ETH, etc.)
2. **SELL** = sell all held base asset for USDT
3. **Position** = simple quantity held, no leverage
4. **Risk** = position size as % of portfolio (max 10% default)
5. **PnL** = (exit_price - entry_price) * quantity
6. **Orders** = always `MARKET` type, BUY uses `quoteOrderQty`, SELL sells full balance

**Files with Spot Assumptions:**
- `src/execution/executor.py:514` - SELL logic sells full balance
- `src/execution/executor.py:505` - BUY uses `order_size_usdt` directly
- `src/portfolio/models.py:22` - `Position` has no leverage, margin, or liquidation fields
- `src/portfolio/manager.py:62` - SQLite fallback uses simple position table
- `src/risk/manager.py:183` - `check_position_limit` uses portfolio % only
- `src/strategy/signals.py:7` - `SignalType` only has BUY/SELL/HOLD

### Futures Trading Requirements

**USDⓈ-M Perpetual Futures** introduces:

| Concept | Spot Equivalent | Futures Difference |
|---------|-----------------|-------------------|
| **Position** | Own base asset | Contract with leverage (1x-125x) |
| **Side** | BUY/SELL | LONG/SHORT/BOTH (hedge mode) |
| **Margin** | Full asset value | Fraction (1/leverage) of notional |
| **PnL** | Realized on sell | Realized on close, includes funding |
| **Liquidation** | N/A | Position forcibly closed if margin < maintenance |
| **Orders** | Market/Limit | + STOP_MARKET, TAKE_PROFIT_MARKET, TRAILING_STOP |
| **Funding** | N/A | Payments every 8 hours (longs pay shorts or vice versa) |

**New Futures-Specific Models Needed:**
```python
@dataclass
class FuturesPosition:
    symbol: str
    position_side: "LONG" | "SHORT"  # Hedge mode
    position_amt: float              # Contract quantity
    entry_price: float
    leverage: int                    # 1-125x
    margin_type: "isolated" | "cross"
    isolated_margin: float           # Isolated mode only
    unrealized_pnl: float
    liquidation_price: float         # Calculated
    mark_price: float                # For liq monitoring
    funding_fees: float              # Accumulated funding
```

### Metis Review Findings

**Critical Questions Identified:**
1. Which margin mode? (isolated vs cross - isolated safer for MVP)
2. Which position mode? (one-way vs hedge - one-way simpler)
3. Max leverage cap? (recommend 5x-10x default, never >20x)
4. Order types scope? (market + stop-market + TP-market for MVP)
5. Funding handling? (log and track, include in PnL calculations)
6. Liquidation policy? (auto-reduce position before liq buffer)

**AI-Slop Patterns to Avoid:**
- ❌ "Just map BUY→LONG, SELL→SHORT" without handling position closures
- ❌ Ignoring `reduceOnly` flag (can accidentally flip position in hedge mode)
- ❌ Using `last_price` instead of `mark_price` for liquidation checks
- ❌ Hard-coding leverage without user configuration
- ❌ Treating futures PnL same as spot (misses funding, margin impact)

**Recommended MVP Scope:**
- ✅ Isolated margin only (cross mode = future enhancement)
- ✅ One-way position mode (hedge mode = future enhancement)
- ✅ Market orders + Stop-Market (TP-Market = future enhancement)
- ✅ Max 10x leverage default (configurable, hard cap at 20x)
- ✅ Liquidation buffer: prevent orders if liq price within 5% of mark
- ✅ Separate futures risk limits (daily loss, max exposure)

---

## Work Objectives

### Core Objective
Implement Binance USDⓈ-M perpetual futures trading support with isolated margin, one-way positions, and comprehensive liquidation protection, while maintaining 100% backward compatibility with existing spot trading.

### Concrete Deliverables

1. **Futures API Client** (`src/execution/futures_client.py`)
   - `BinanceFuturesClient` class with fapi.binance.com endpoints
   - Methods: `set_leverage()`, `get_position_risk()`, `place_futures_order()`
   - Funding rate retrieval and tracking

2. **Futures Models** (`src/portfolio/futures_models.py`)
   - `FuturesPosition` with leverage, margin, liquidation price fields
   - `FuturesAccountInfo` with margin ratio, available balance
   - `FundingPayment` model for tracking funding fees

3. **Enhanced Risk Manager** (`src/risk/futures_risk.py`)
   - `FuturesRiskManager` using composition with base `RiskManager` (not inheritance)
   - Liquidation buffer checks (prevent orders if within 5% of liq)
   - Leverage limit enforcement (max 10x configurable, 20x hard cap)
   - Margin usage monitoring (< 50% of available)
   - Separate futures daily loss limits

4. **Futures Executor** (`src/execution/futures_executor.py`)
   - `FuturesTradingExecutor` with LONG position support
   - `reduceOnly` order handling for position closes
   - Signal routing: BUY→LONG, SELL→Close LONG (one-way mode)

5. **Futures Portfolio Manager** (`src/portfolio/futures_manager.py`)
   - `FuturesPortfolioManager` for leveraged position lifecycle
   - DB schema with leverage, margin, liquidation price, funding fees
   - Position CRUD with futures-specific fields

6. **Mark Price Feed** (`src/ingest/mark_price_feed.py`)
   - WebSocket connection to `!markPrice@arr` stream
   - Real-time mark price updates for liquidation monitoring
   - REST fallback for polling

7. **Configuration Schema** Updates
   - `config/settings.yaml`: Add `futures:` section with leverage, margin_mode
   - `config/risk.yaml`: Add futures-specific limits (max_leverage, liq_buffer_pct)

8. **Signal Enhancement** (`src/strategy/signals.py`)
   - Add `trading_mode` field to `Signal` dataclass ("spot" or "futures")
   - Keep existing BUY/SELL/HOLD SignalTypes (no new types needed)

9. **Integration** (`src/main.py`)
   - Mode selection: `mode: spot` vs `mode: futures`
   - Conditional initialization of futures components
   - Backward compatibility: spot mode works exactly as before

10. **Testing Infrastructure**
   - Mock fapi endpoints for unit testing
   - Funding rate calculation tests
   - Liquidation price calculation tests
   - Integration tests on Binance testnet
   - Backward compatibility tests (spot unchanged)

### Definition of Done

- [ ] All 214 existing tests pass (spot backward compatibility)
- [ ] New futures tests: 50+ tests covering leverage, margin, liquidation
- [ ] Configuration validation: rejects unsafe leverage (>20x) at startup
- [ ] Paper trading mode works for futures (testnet)
- [ ] Risk manager blocks orders within 5% of liquidation price
- [ ] Funding rates are tracked and included in PnL calculations
- [ ] Documentation: USAGE.md updated with futures trading guide
- [ ] No hardcoded secrets or default passwords in new code

### Must Have (Non-Negotiable)

1. **Isolated margin** for MVP (cross margin too risky for initial implementation)
2. **One-way position mode** (hedge mode adds complexity, defer)
3. **Hard leverage cap** at 20x (prevent accidental 125x)
4. **Liquidation buffer** (prevent orders if within 5% of liq price)
5. **Separate risk limits** for futures (don't mix spot/futures limits)
6. **Backward compatibility** (spot trading must work unchanged)
7. **Testnet support** (futures testnet for paper trading)

### Must NOT Have (Guardrails from Metis)

1. ❌ Cross margin mode for MVP (too risky, can liquidate entire account)
2. ❌ Hedge mode for MVP (simultaneous long/short adds complexity)
3. ❌ Trailing stop orders for MVP (complex execution logic)
4. ❌ Dynamic leverage adjustment (keep leverage static per symbol)
5. ❌ No liquidation auto-recovery (manual intervention required)
6. ❌ No partial position averaging (pyramiding) without explicit rules
7. ❌ Never allow >20x leverage (even if Binance supports 125x)

---

## Verification Strategy

### Test Decision
- **Infrastructure exists**: YES (pytest with 214 tests)
- **Automated tests**: Tests-after (futures is new feature)
- **Framework**: pytest with asyncio support

### Agent-Executed QA Scenarios (MANDATORY)

**Scenario 1: Futures Client Connectivity**
```
Tool: Bash (curl via test script)
Preconditions: .env configured with BINANCE_API_KEY/SECRET
Steps:
  1. Run: python -c "from src.execution.futures_client import BinanceFuturesClient; ..."
  2. Assert: Client connects to fapi.binance.com
  3. Assert: Can retrieve funding rate for BTCUSDT
  4. Assert: Can get position risk (empty initially)
Expected: Connection successful, no API errors
Evidence: Terminal output saved to .sisyphus/evidence/futures-connect.log
```

**Scenario 2: Liquidation Price Calculation**
```
Tool: Bash (pytest)
Preconditions: FuturesPosition model implemented
Steps:
  1. Create LONG position: entry=50000, qty=0.1, leverage=10x
  2. Calculate liquidation price
  3. Assert: liq_price < entry_price (long liq below entry)
  4. Create SHORT position: entry=50000, qty=0.1, leverage=10x
  5. Assert: liq_price > entry_price (short liq above entry)
Expected: Formulas match Binance documentation
Evidence: pytest output: tests/test_futures_liquidation.py
```

**Scenario 3: Risk Manager Blocks Unsafe Orders**
```
Tool: Bash (pytest)
Preconditions: FuturesRiskManager with 5% liquidation buffer
Steps:
  1. Create position near liquidation (liq_price=45000, mark=45500)
  2. Attempt to open additional position
  3. Assert: Risk check returns (False, "Liquidation buffer breached")
  4. Assert: Order is blocked
Expected: High-risk orders rejected before API call
Evidence: pytest -v tests/test_futures_risk_manager.py
```

**Scenario 4: Backward Compatibility - Spot Unchanged**
```
Tool: Bash (pytest)
Preconditions: config/settings.yaml with mode: spot
Steps:
  1. Run full test suite: pytest tests/ -v
  2. Assert: All 214 tests pass
  3. Assert: No futures code paths executed
  4. Assert: TradingExecutor initialized (not FuturesTradingExecutor)
Expected: 100% pass rate, spot behavior identical
Evidence: pytest --tb=short tests/
```

**Scenario 5: Funding Rate Tracking**
```
Tool: Bash (python script)
Preconditions: Position open for >8 hours
Steps:
  1. Query funding rate history
  2. Calculate expected funding payment: position_size * funding_rate
  3. Assert: Funding payment recorded in position.funding_fees
  4. Assert: Total PnL includes funding impact
Expected: Funding accurately tracked and reported
Evidence: Script output and DB query results
```

---

## Execution Strategy

### Parallel Execution Waves

```
Wave 1 (Foundation - Parallel):
├── Task 1: Add futures configuration schema
├── Task 2: Create BinanceFuturesClient
└── Task 3: Create FuturesPosition model

Wave 2 (Core Logic - After Wave 1 foundations ready):
├── Task 3.5: Create FuturesPortfolioManager (DB schema)
├── Task 4: Implement FuturesRiskManager
├── Task 5: Implement FuturesTradingExecutor
└── Task 6: Add trading_mode context to Signals

Wave 3 (Integration - After Wave 2):
├── Task 7: Integrate futures components into main.py
├── Task 8: Add Mark Price feed (WebSocket/polling for liquidation monitoring)
├── Task 8.5: Add Funding rate tracking
└── Task 9: Create comprehensive test suite

Wave 4 (Validation - After Wave 3):
├── Task 10: Testnet integration testing
├── Task 11: Documentation (USAGE.md updates)
└── Task 12: Final backward compatibility verification

Critical Path: max(Task 1, Task 2, Task 3) → Task 3.5 → Task 4 → Task 5 → Task 7
Parallel Speedup: ~40% faster than sequential (Tasks 2 & 3 run parallel to Task 1)
```

### Dependency Matrix (Fixed)

| Task | Depends On | Blocks | Can Parallelize With |
|------|------------|--------|---------------------|
| 1 (Config) | None | 4, 5, 6 | None |
| 2 (Client) | None | 5, 7, 8 | 1, 3 |
| 3 (Models) | None | 4, 5 | 1, 2 |
| 3.5 (PortfolioMgr) | 1, 3 | 5, 7 | 2, 4 |
| 4 (Risk) | 1, 3 | 5, 7 | 3.5, 8 |
| 5 (Executor) | 2, 3, 3.5, 4, 6 | 7 | None |
| 6 (Signals) | 1 | 5 | 2, 3 |
| 7 (Integration) | 1, 2, 4, 5 | 10 | 8, 9 |
| 8 (Mark Price) | 2, 3 | 10, 7 | 9 |
| 9 (Tests) | 1, 2, 3, 3.5, 4 | 10 | 7, 8 |
| 10 (Testnet) | 7, 8, 9 | 11, 12 | None |
| 11 (Docs) | 10 | 12 | None |
| 12 (Final Verify) | All | None | None |

**Key Fixes**:
- Task 1 (Config) does NOT block Tasks 2 and 3. They can run in parallel.
- Task 8 (Mark Price) does NOT block Task 4 (Risk). Risk manager accepts mark_price as input parameter.
- Task 8 blocks Task 7 (Integration) and Task 10 (Testnet) only.

---

## TODOs

### Wave 1: Foundation

- [ ] 1. Add Futures Configuration Schema

  **What to do**:
  - Add `futures:` section to `config/settings.yaml`
  - Add futures risk limits to `config/risk.yaml`
  - Add validation in `src/main.py::load_settings`
  
  **Configuration to add**:
  ```yaml
  futures:
    enabled: false  # Default to spot
    symbols:
      - BTCUSDT
      - ETHUSDT
    default_leverage: 5
    max_leverage: 10  # Hard cap at 20x enforced in code
    margin_mode: isolated  # isolated only for MVP
    position_mode: one-way  # one-way only for MVP
    test_mode: true
    liquidation_buffer_pct: 5.0  # Block orders if within 5% of liq
  ```

  **API Key Management**:
  On demo.binance.com, the same API keys work for both spot and futures trading:
  - Spot API: `https://demo-api.binance.com` (existing)
  - Futures API: `https://demo.binance.com` (fapi endpoints on same domain)
  
  No separate API keys needed for demo trading. The existing `BINANCE_API_KEY` and `BINANCE_API_SECRET` work for both.
  
  For production:
  - Spot: `https://api.binance.com`
  - Futures: `https://fapi.binance.com` (different domain, may require separate API key)

  **Must NOT do**:
  - ❌ Allow cross margin mode
  - ❌ Allow >20x leverage
  - ❌ Enable futures by default (spot must remain default)

  **Recommended Agent Profile**:
  - **Category**: `unspecified-low` (config changes)
  - **Skills**: None needed

  **Parallelization**:
  - **Can Run In Parallel**: YES - Wave 1
  - **Parallel Group**: With Tasks 2, 3
  - **Blocks**: Tasks 4, 5, 6
  - **Blocked By**: None

  **References**:
  - `config/settings.yaml` - Existing config structure
  - `config/risk.yaml` - Risk config pattern
  - `src/main.py:57` - load_settings() validation pattern

  **Acceptance Criteria**:
  - [ ] `config/settings.yaml` contains `futures:` section
  - [ ] `config/risk.yaml` contains `futures_limits:` section
  - [ ] `load_settings()` validates futures config and rejects >20x leverage
  - [ ] pytest `test_settings_futures_config.py` passes

  **Agent-Executed QA**:
  ```
  Scenario: Futures config loads and validates
    Tool: Bash (pytest)
    Steps:
      1. pytest tests/test_futures_config.py -v
      2. Assert: test_futures_default_disabled passes
      3. Assert: test_futures_max_leverage_enforced passes (rejects 50x)
      4. Assert: test_futures_isolated_only passes (rejects cross)
    Expected: All futures config tests pass
    Evidence: .sisyphus/evidence/task-1-config.log
  ```

  **Commit**: YES
  - Message: `feat(config): add futures configuration schema with safety limits`
  - Files: `config/settings.yaml`, `config/risk.yaml`, `src/main.py`

- [ ] 2. Create BinanceFuturesClient

  **What to do**:
  - Create `src/execution/futures_client.py`
  - Implement `BinanceFuturesClient` class
  - Add fapi.binance.com endpoints
  - Implement: `set_leverage()`, `get_position_risk()`, `place_order()`, `get_funding_rate()`

  **API Endpoints to implement**:
  - `POST /fapi/v1/leverage` - Set leverage
  - `GET /fapi/v2/positionRisk` - Get position risk (includes liq price)
  - `POST /fapi/v1/order` - Place order
  - `GET /fapi/v1/fundingRate` - Get funding rate
  - `GET /fapi/v2/account` - Account info (margin ratio)

  **Must NOT do**:
  - ❌ Implement hedge mode endpoints (one-way only)
  - ❌ Implement cross margin switching
  - ❌ Hardcode any API keys or secrets

  **Recommended Agent Profile**:
  - **Category**: `ultrabrain` (complex API integration)
  - **Skills**: None (external API)

  **Parallelization**:
  - **Can Run In Parallel**: YES - Wave 1
  - **Parallel Group**: With Tasks 1, 3
  - **Blocks**: Task 5, 7
  - **Blocked By**: None

  **References**:
  - `src/execution/binance_client.py` - Spot client pattern to follow
  - Binance API docs: https://binance-docs.github.io/apidocs/futures/en/

  **Acceptance Criteria**:
  - [ ] Client connects to `fapi.binance.com`
  - [ ] Can set leverage (validated 1-20x)
  - [ ] Can retrieve position risk including liquidation price
  - [ ] Can place market order with `reduceOnly` flag
  - [ ] Can get current funding rate

  **Agent-Executed QA**:
  ```
  Scenario: Futures client API connectivity
    Tool: Bash (pytest with mocked responses)
    Steps:
      1. Mock fapi.binance.com responses
      2. Test set_leverage(symbol="BTCUSDT", leverage=10)
      3. Assert: Returns success
      4. Test get_position_risk(symbol="BTCUSDT")
      5. Assert: Returns liquidationPrice, markPrice, positionAmt
    Expected: All API methods work with mocked data
    Evidence: pytest tests/test_futures_client.py -v
  ```

  **Commit**: YES
  - Message: `feat(execution): add BinanceFuturesClient with fapi endpoints`

- [ ] 3. Create FuturesPosition Model

  **What to do**:
  - Create `src/portfolio/futures_models.py`
  - Implement `FuturesPosition` dataclass
  - Add liquidation price calculation
  - Add unrealized PnL calculation (including funding)

  **Model fields**:
  ```python
  @dataclass
  class FuturesPosition:
      symbol: str
      position_side: str  # "LONG" or "SHORT"
      position_amt: float  # Positive for LONG, negative for SHORT
      entry_price: float
      leverage: int
      margin_type: str = "isolated"
      isolated_margin: float = 0.0
      unrealized_pnl: float = 0.0
      liquidation_price: float = 0.0
      mark_price: float = 0.0
      funding_fees: float = 0.0
      last_funding_time: datetime | None = None
  ```

  **Must NOT do**:
  - ❌ Implement cross margin calculations (isolated only)
  - ❌ Support hedge mode (one-way only)

  **Recommended Agent Profile**:
  - **Category**: `ultrabrain` (mathematical calculations)
  - **Skills**: None

  **Parallelization**:
  - **Can Run In Parallel**: YES - Wave 1
  - **Parallel Group**: With Tasks 1, 2
  - **Blocks**: Tasks 4, 5
  - **Blocked By**: None

  **References**:
  - `src/portfolio/models.py` - Spot Position pattern
  - Binance liquidation formula: https://www.binance.com/en/support/faq/leverage-and-margin-of-crypto-trading-360043074590

  **Acceptance Criteria**:
  - [ ] Liquidation price formula matches Binance
  - [ ] Unrealized PnL calculates correctly for LONG and SHORT
  - [ ] Funding fees accumulate correctly
  - [ ] All calculations have unit tests with known inputs/outputs

  **Agent-Executed QA**:
  ```
  Scenario: Liquidation price calculation accuracy
    Tool: Bash (pytest)
    Steps:
      1. Create LONG: entry=50000, qty=0.1, leverage=10x, margin=500
      2. Calculate liq price: ~45000 (maintenance margin ~0.5%)
      3. Assert: liq_price ≈ 45000 (within 0.1% tolerance)
      4. Create SHORT: entry=50000, qty=0.1, leverage=10x
      5. Assert: liq_price ≈ 55000 (short liq above entry)
    Expected: Calculations match Binance formulas
    Evidence: pytest tests/test_futures_liquidation.py
  ```

  **Commit**: YES
  - Message: `feat(portfolio): add FuturesPosition model with liquidation calc`

- [ ] 3.5. Create FuturesPortfolioManager

  **What to do**:
  - Create `src/portfolio/futures_manager.py`
  - Implement `FuturesPortfolioManager` class
  - Extend or wrap `PortfolioManager` for futures-specific position lifecycle
  - DB schema changes for futures positions (leverage, margin, liq price, funding)
  - Handle position CRUD with futures-specific fields

  **Why This is Needed (Missing in Original Plan)**:
  - Current `PortfolioManager` assumes spot-only (one position per symbol, no leverage)
  - Futures needs to track: leverage, margin type, liquidation price, funding fees
  - Position lifecycle is different: positions are contracts, not owned assets
  - Need separate DB table or extended schema for futures positions

  **Key Methods**:
  ```python
  class FuturesPortfolioManager:
      async def open_long_position(symbol, qty, entry_price, leverage, margin)
      async def close_long_position(symbol, exit_price, order_id)
      async def update_position_margin(symbol, new_margin)  # For isolated margin
      async def update_funding_fees(symbol, funding_payment)
      async def get_position_with_liquidation_price(symbol) -> FuturesPosition
  ```

  **DB Schema Changes**:
  ```sql
  -- New table: futures_positions
  CREATE TABLE futures_positions (
      id SERIAL PRIMARY KEY,
      symbol TEXT NOT NULL,
      position_side TEXT NOT NULL,  -- 'LONG' or 'SHORT'
      position_amt DOUBLE PRECISION NOT NULL,
      entry_price DOUBLE PRECISION NOT NULL,
      leverage INTEGER NOT NULL,
      margin_type TEXT NOT NULL,    -- 'isolated' or 'cross'
      isolated_margin DOUBLE PRECISION,
      liquidation_price DOUBLE PRECISION,
      mark_price DOUBLE PRECISION,
      unrealized_pnl DOUBLE PRECISION,
      funding_fees DOUBLE PRECISION DEFAULT 0,
      status TEXT DEFAULT 'open',
      entry_time TIMESTAMPTZ NOT NULL,
      exit_time TIMESTAMPTZ,
      realized_pnl DOUBLE PRECISION
  );
  ```

  **Must NOT do**:
  - ❌ Reuse spot positions table (different fields needed)
  - ❌ Support cross margin (isolated only for MVP)
  - ❌ Support multiple positions per symbol (one-way mode)

  **Recommended Agent Profile**:
  - **Category**: `ultrabrain` (database schema + lifecycle management)
  - **Skills**: None

  **Parallelization**:
  - **Can Run In Parallel**: YES (after Tasks 1, 3)
  - **Blocks**: Task 5 (Executor needs portfolio manager)
  - **Blocked By**: Tasks 1 (config), 3 (models)

  **References**:
  - `src/portfolio/manager.py` - Spot portfolio manager pattern
  - `src/portfolio/futures_models.py` - FuturesPosition model (Task 3)

  **Acceptance Criteria**:
  - [ ] DB table created with all futures-specific fields
  - [ ] Can open LONG position with leverage, margin, entry price
  - [ ] Can close position with realized PnL calculation
  - [ ] Updates mark price and unrealized PnL
  - [ ] Tracks funding fees

  **Commit**: YES
  - Message: `feat(portfolio): add FuturesPortfolioManager with DB schema`

### Wave 2: Core Logic

- [ ] 4. Implement FuturesRiskManager

  **What to do**:
  - Create `src/risk/futures_risk.py`
  - Implement `FuturesRiskManager` using composition (wraps base RiskManager)
  - Add liquidation buffer check (block orders within 5% of liq)
  - Add leverage limit enforcement (max 10x, hard cap 20x)
  - Add margin usage check (< 50% of available)
  - Add separate futures daily loss limit

  **Key Methods** (Composition Pattern):
  ```python
  @dataclass
  class FuturesRiskManager:
      """Futures-specific risk manager using composition with base RiskManager."""
      base_manager: RiskManager  # Composition, not inheritance
      config: FuturesRiskConfig
      
      def check_liquidation_buffer(self, position: FuturesPosition, mark_price: float) -> tuple[bool, str]:
          """Block orders if within X% of liquidation price."""
          buffer_pct = self.config.liquidation_buffer_pct / 100
          liq_price = position.liquidation_price
          
          if position.position_side == "LONG":
              # For longs, mark price approaching liq from above
              if mark_price <= liq_price * (1 + buffer_pct):
                  return False, f"Within {self.config.liquidation_buffer_pct}% of liquidation"
          else:  # SHORT
              # For shorts, mark price approaching liq from below
              if mark_price >= liq_price * (1 - buffer_pct):
                  return False, f"Within {self.config.liquidation_buffer_pct}% of liquidation"
          
          return True, "OK"
      
      def check_max_leverage(self, requested_leverage: int) -> tuple[bool, str]:
          """Enforce max leverage limits (configurable 10x, hard cap 20x)."""
          if requested_leverage > 20:
              return False, "Leverage exceeds hard safety cap of 20x"
          if requested_leverage > self.config.max_leverage:
              return False, f"Leverage {requested_leverage}x exceeds max {self.config.max_leverage}x"
          return True, "OK"
      
      def check_futures_daily_loss(self, daily_pnl: float, account_value: float) -> tuple[bool, str]:
          """Check futures-specific daily loss limit (separate from spot)."""
          loss_pct = abs(min(0, daily_pnl)) / account_value
          if loss_pct > self.config.max_daily_loss_pct:
              return False, f"Daily loss {loss_pct:.2%} exceeds limit {self.config.max_daily_loss_pct:.2%}"
          return True, "OK"
  ```

  **Must NOT do**:
  - ❌ Implement cross margin liquidation logic (isolated only)
  - ❌ Allow bypassing liquidation buffer
  - ❌ Mix spot and futures risk limits

  **Recommended Agent Profile**:
  - **Category**: `ultrabrain` (complex risk logic)
  - **Skills**: None

  **Parallelization**:
  - **Can Run In Parallel**: NO (depends on Tasks 1, 3)
  - **Blocks**: Task 5, 7
  - **Blocked By**: Tasks 1, 3

  **References**:
  - `src/risk/manager.py` - Base RiskManager to extend
  - `config/risk.yaml` - Futures risk limits

  **Acceptance Criteria**:
  - [ ] Blocks orders within 5% of liquidation price
  - [ ] Rejects leverage > 20x
  - [ ] Warns if margin usage > 50%
  - [ ] Separate futures daily loss counter from spot

  **Commit**: YES
  - Message: `feat(risk): add FuturesRiskManager with liquidation protection`

- [ ] 5. Implement FuturesTradingExecutor

  **What to do**:
  - Create `src/execution/futures_executor.py`
  - Implement `FuturesTradingExecutor` class
  - Support LONG/SHORT positions
  - Handle `reduceOnly` flag for position closes
  - Map signals to futures orders (one-way mode)

  **Signal Mapping (One-Way Mode)**:
  ```
  Current SignalType.BUY → LONG position (open if no position, add if LONG exists)
  Current SignalType.SELL → Close LONG position (reduceOnly=True)
  
  For SHORT support (future), would add:
  SignalType.SHORT → SHORT position
  SignalType.SELL → Context-dependent (close LONG or add SHORT)
  ```

  **Must NOT do**:
  - ❌ Implement hedge mode position mapping
  - ❌ Allow position flips without explicit close
  - ❌ Forget reduceOnly flag on close orders

  **Recommended Agent Profile**:
  - **Category**: `ultrabrain` (complex execution logic)
  - **Skills**: None

  **Parallelization**:
  - **Can Run In Parallel**: NO (depends on Tasks 2, 3, 3.5, 4, 6)
  - **Blocks**: Task 7
  - **Blocked By**: Tasks 2 (client), 3 (models), 3.5 (portfolio mgr), 4 (risk), 6 (signals)

  **References**:
  - `src/execution/executor.py` - Spot executor pattern
  - `src/portfolio/futures_manager.py` - FuturesPortfolioManager (Task 3.5)
  - Binance futures order API: https://binance-docs.github.io/apidocs/futures/en/#new-order-trade

  **Acceptance Criteria**:
  - [ ] LONG signal opens LONG position
  - [ ] SELL signal closes LONG position with reduceOnly
  - [ ] Position tracking updates correctly
  - [ ] Risk checks run before every order

  **Commit**: YES
  - Message: `feat(execution): add FuturesTradingExecutor with LONG/SHORT support`

- [ ] 6. Add Trading Mode Context to Signals (Option B)

  **What to do**:
  - Keep existing `SignalType` enum (BUY/SELL/HOLD) unchanged
  - Add `trading_mode` field to `Signal` dataclass
  - Add signal routing logic in executor based on `trading_mode`
  - Maintain 100% backward compatibility with existing strategies

  **Why Option B (Not LONG/SHORT SignalTypes):**
  - Adding new SignalTypes would require updating ALL existing strategies
  - Option B is non-invasive: existing strategies work unchanged
  - Executor decides how to interpret BUY/SELL based on trading_mode
  - BUY in futures mode = Open LONG; SELL = Close LONG (reduceOnly)

  **Implementation**:
  ```python
  @dataclass
  class Signal:
      type: SignalType  # BUY/SELL/HOLD (unchanged)
      symbol: str
      price: float
      confidence: float
      reason: str
      indicators: dict[str, float]
      trading_mode: str = "spot"  # NEW: "spot" or "futures"
  ```

  **Signal Routing in Executor**:
  ```python
  # FuturesTradingExecutor.on_signal()
  if signal.trading_mode == "futures":
      if signal.type == SignalType.BUY:
          # Open LONG position
          await self._open_long(signal.symbol, signal.price)
      elif signal.type == SignalType.SELL:
          # Close LONG position with reduceOnly
          await self._close_long(signal.symbol, signal.price)
  else:
      # Spot mode - delegate to original logic
      await super().on_signal(signal)
  ```

  **Must NOT do**:
  - ❌ Break existing strategy implementations
  - ❌ Require strategy rewrites

  **Recommended Agent Profile**:
  - **Category**: `quick` (simple enum change)
  - **Skills**: None

  **Parallelization**:
  - **Can Run In Parallel**: YES (depends on Task 1 for mode config)
  - **Blocks**: Task 5
  - **Blocked By**: Task 1

  **References**:
  - `src/strategy/signals.py` - Current Signal implementation
  - `src/strategy/simple_ma.py` - Example strategy to keep working

  **Acceptance Criteria**:
  - [ ] Existing strategies work unchanged (BUY/SELL still function)
  - [ ] Signal has `trading_mode` field defaulting to "spot"
  - [ ] Executor routes signals correctly based on mode

  **Commit**: YES
  - Message: `feat(strategy): add trading_mode context to Signal for futures routing`

### Wave 3: Integration

- [ ] 7. Integrate Futures Components into Main

  **What to do**:
  - Modify `src/main.py` to conditionally initialize futures
  - Add mode selection logic (spot vs futures)
  - Initialize `FuturesTradingExecutor` when mode="futures"
  - Initialize `FuturesRiskManager` for futures mode
  - Keep spot initialization unchanged

  **Initialization Logic**:
  ```python
  if settings.mode == "futures":
      # Initialize futures components
      futures_client = BinanceFuturesClient(...)
      futures_risk = FuturesRiskManager(...)
      executor = FuturesTradingExecutor(...)
  else:
      # Original spot initialization
      executor = TradingExecutor(...)
  ```

  **Must NOT do**:
  - ❌ Remove or break spot initialization
  - ❌ Initialize futures components in spot mode

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high` (integration logic)
  - **Skills**: None

  **Parallelization**:
  - **Can Run In Parallel**: NO (depends on Tasks 1, 2, 4, 5)
  - **Blocks**: Task 10
  - **Blocked By**: Tasks 1, 2, 4, 5

  **References**:
  - `src/main.py` - Current initialization flow
  - `src/main.py:238` - Executor initialization

  **Acceptance Criteria**:
  - [ ] `mode: spot` initializes original components only
  - [ ] `mode: futures` initializes futures components
  - [ ] All components use correct config sections
  - [ ] Startup logging shows correct mode

  **Commit**: YES
  - Message: `feat(main): add futures mode selection and component initialization`

- [ ] 8. Add Mark Price Feed (Critical for Liquidation Monitoring)

  **What to do**:
  - Create mark price monitoring service
  - Connect to Binance futures mark price stream (WebSocket: `!markPrice@arr` or `markPrice@symbol`)
  - Poll mark price via REST (`/fapi/v1/premiumIndex`) as fallback
  - Update `FuturesPosition.mark_price` in real-time
  - Trigger risk manager liquidation buffer checks when mark price updates

  **Why This is Critical**:
  - Liquidation is based on **mark price**, not last traded price
  - Without real-time mark price, the 5% liquidation buffer cannot be enforced
  - Mark price is a time-weighted average to prevent manipulation
  - Funding rates are also calculated from mark price

  **Implementation Options**:
  1. **WebSocket (Recommended)**: `wss://fstream.binance.com/ws/!markPrice@arr` - all symbols at once
  2. **Polling**: Every 1-5 seconds call `/fapi/v1/premiumIndex` for tracked symbols

  **Data Flow**:
  ```
  WebSocket markPrice@arr → MarkPriceService → FuturesPosition.mark_price
                                             ↓
                                      FuturesRiskManager.check_liquidation_buffer()
                                             ↓
                                      Block orders if within 5% of liq
  ```

  **Must NOT do**:
  - ❌ Use last traded price for liquidation checks
  - ❌ Assume mark price updates are real-time without verification
  - ❌ Skip updating positions when mark price changes

  **Recommended Agent Profile**:
  - **Category**: `ultrabrain` (WebSocket streaming + risk integration)
  - **Skills**: None

  **Parallelization**:
  - **Can Run In Parallel**: YES (after Tasks 2, 3)
  - **Blocks**: Task 4 (Risk needs mark price), Task 10
  - **Blocked By**: Tasks 2 (client), 3 (position model needs mark_price field)

  **Acceptance Criteria**:
  - [ ] Mark price updates received via WebSocket or polling
  - [ ] All tracked futures positions have current mark_price
  - [ ] Liquidation buffer check triggers on mark price updates
  - [ ] Falls back to REST polling if WebSocket disconnects

  **Commit**: YES
  - Message: `feat(futures): add mark price feed for liquidation monitoring`

- [ ] 8.5. Add Funding Rate Tracking

  **What to do**:
  - Create funding rate monitoring loop
  - Poll funding rate every 8 hours (or use WebSocket `@markPrice` includes funding)
  - Calculate funding payment impact on positions
  - Update position funding_fees accumulator
  - Log funding payments

  **Must NOT do**:
  - ❌ Assume fixed 8-hour schedule (check API for exact times - typically 00:00, 08:00, 16:00 UTC)
  - ❌ Ignore funding in PnL calculations

  **Recommended Agent Profile**:
  - **Category**: `unspecified-low` (background task)
  - **Skills**: None

  **Parallelization**:
  - **Can Run In Parallel**: YES (after Task 8 - mark price)
  - **Blocks**: Task 10
  - **Blocked By**: Tasks 2, 3, 8

  **Acceptance Criteria**:
  - [ ] Funding rate retrieved from API
  - [ ] Funding payment calculated correctly: `position_size * funding_rate`
  - [ ] Position funding_fees updated
  - [ ] Total PnL includes funding impact

  **Commit**: YES (groups with Task 9)
  - Message: `feat(futures): add funding rate tracking and PnL impact`

- [ ] 9. Create Comprehensive Test Suite

  **What to do**:
  - Create `tests/test_futures_client.py` (mocked API tests)
  - Create `tests/test_futures_position.py` (liquidation calculations)
  - Create `tests/test_futures_risk_manager.py` (risk checks)
  - Create `tests/test_futures_executor.py` (order placement)
  - Create `tests/test_futures_integration.py` (end-to-end)
  - Create `tests/test_backward_compatibility.py` (spot unchanged)

  **Must NOT do**:
  - ❌ Skip testing liquidation scenarios
  - ❌ Skip testing funding calculations

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high` (test writing)
  - **Skills**: None

  **Parallelization**:
  - **Can Run In Parallel**: NO (depends on all above)
  - **Blocks**: Task 10
  - **Blocked By**: Tasks 1-8

  **Acceptance Criteria**:
  - [ ] 50+ new tests for futures
  - [ ] All 214 existing spot tests still pass
  - [ ] Liquidation edge cases tested
  - [ ] Funding rate impact tested
  - [ ] Risk manager blocks unsafe orders (tested)

  **Commit**: YES
  - Message: `test(futures): add comprehensive futures test suite`

### Wave 4: Validation

- [ ] 10. Testnet Integration Testing

  **What to do**:
  - Configure for Binance futures testnet
  - Run paper trading mode on testnet
  - Open test LONG positions
  - Verify liquidation price calculations match testnet
  - Close positions and verify PnL
  - Test funding rate retrieval

  **Must NOT do**:
  - ❌ Test on mainnet with real funds
  - ❌ Skip testnet validation

  **Recommended Agent Profile**:
  - **Category**: `deep` (integration testing)
  - **Skills**: None

  **Parallelization**:
  - **Can Run In Parallel**: NO
  - **Blocks**: Task 11, 12
  - **Blocked By**: Tasks 7, 8, 9

  **Acceptance Criteria**:
  - [ ] Successfully connects to futures testnet
  - [ ] Opens LONG position on testnet
  - [ ] Position risk data matches calculations
  - [ ] Closes position successfully
  - [ ] Funding rate retrieved correctly

  **Agent-Executed QA**:
  ```
  Scenario: Testnet paper trading end-to-end
    Tool: Bash (docker-compose)
    Preconditions: .env configured for testnet, mode=futures, test_mode=true
    Steps:
      1. docker-compose up --build
      2. Wait for startup logs showing "Futures mode enabled"
      3. Monitor logs for "LONG position opened"
      4. Check Grafana dashboard shows futures position
      5. Wait for SELL signal or manually trigger
      6. Verify "Position closed" in logs with PnL
    Expected: Complete paper trading cycle works on testnet
    Evidence: docker-compose logs saved, Grafana screenshots
  ```

  **Commit**: NO (manual testing only)

- [ ] 11. Update Documentation (USAGE.md)

  **What to do**:
  - Add futures trading section to `USAGE.md`
  - Document futures configuration options
  - Document risks (liquidation, leverage)
  - Add futures trading examples
  - Document differences from spot trading

  **Must NOT do**:
  - ❌ Forget to mention liquidation risk prominently
  - ❌ Skip margin mode explanation

  **Recommended Agent Profile**:
  - **Category**: `writing` (documentation)
  - **Skills**: None

  **Parallelization**:
  - **Can Run In Parallel**: YES (after Task 10)
  - **Blocks**: Task 12
  - **Blocked By**: Task 10

  **Acceptance Criteria**:
  - [ ] USAGE.md has "Futures Trading" section
  - [ ] Configuration options documented
  - [ ] Liquidation risks prominently warned
  - [ ] Example config snippets provided

  **Commit**: YES
  - Message: `docs: add futures trading guide with safety warnings`

- [ ] 12. Final Backward Compatibility Verification

  **What to do**:
  - Run full test suite: `pytest tests/ -v`
  - Verify all 214 original tests pass
  - Test spot trading mode manually
  - Verify spot-only configurations still work
  - Check no futures code paths in spot mode

  **Acceptance Criteria**:
  - [ ] 100% of original 214 tests pass
  - [ ] Spot trading works unchanged
  - [ ] No regression in spot mode performance
  - [ ] Configuration `mode: spot` is default and safe

  **Agent-Executed QA**:
  ```
  Scenario: Spot mode regression test
    Tool: Bash (pytest + docker-compose)
    Steps:
      1. Set config: mode: spot, trading_execution.enabled: true, test_mode: true
      2. Run: pytest tests/ -v --tb=short
      3. Assert: 214 tests passed, 0 failed
      4. Run: docker-compose up --build
      5. Wait 60 seconds, check logs
      6. Assert: "TradingExecutor initialized" (not FuturesTradingExecutor)
      7. Assert: No futures-related log messages
    Expected: Spot mode works exactly as before
    Evidence: pytest output, log file analysis
  ```

  **Commit**: NO (verification only)

---

## Commit Strategy

| After Task | Message | Files | Verification |
|------------|---------|-------|--------------|
| 1 | `feat(config): add futures configuration schema with safety limits` | config/settings.yaml, config/risk.yaml, src/main.py | pytest tests/test_futures_config.py |
| 2 | `feat(execution): add BinanceFuturesClient with fapi endpoints` | src/execution/futures_client.py, tests/test_futures_client.py | pytest tests/test_futures_client.py |
| 3 | `feat(portfolio): add FuturesPosition model with liquidation calc` | src/portfolio/futures_models.py, tests/test_futures_position.py | pytest tests/test_futures_position.py |
| 3.5 | `feat(portfolio): add FuturesPortfolioManager with DB schema` | src/portfolio/futures_manager.py, tests/test_futures_portfolio_manager.py | pytest tests/test_futures_portfolio_manager.py |
| 4 | `feat(risk): add FuturesRiskManager with liquidation protection` | src/risk/futures_risk.py, tests/test_futures_risk_manager.py | pytest tests/test_futures_risk_manager.py |
| 6 | `feat(strategy): add trading_mode context to Signal` | src/strategy/signals.py | pytest tests/test_signals.py |
| 5 | `feat(execution): add FuturesTradingExecutor with LONG support` | src/execution/futures_executor.py, tests/test_futures_executor.py | pytest tests/test_futures_executor.py |
| 7 | `feat(main): add futures mode selection and component initialization` | src/main.py | pytest tests/test_settings_integration.py |
| 8 | `feat(futures): add mark price feed for liquidation monitoring` | src/ingest/mark_price_feed.py, tests/test_mark_price_feed.py | pytest tests/test_mark_price_feed.py |
| 8.5 | `feat(futures): add funding rate tracking and PnL impact` | src/execution/futures_client.py (funding methods) | pytest tests/test_futures_funding.py |
| 9 | `test(futures): add comprehensive futures test suite` | tests/test_futures_*.py | pytest tests/ -v (250+ tests) |
| 11 | `docs: add futures trading guide with safety warnings` | USAGE.md | Manual review |

---

## Success Criteria

### Verification Commands
```bash
# All tests pass
pytest tests/ -v
# Expected: 264+ tests passed (214 original + 50 new futures)

# Spot backward compatibility
pytest tests/test_settings_integration.py tests/test_executor.py tests/test_portfolio_manager.py -v
# Expected: All pass

# Futures tests
pytest tests/test_futures_*.py -v
# Expected: 50+ tests pass

# Type checking (if mypy available)
mypy src/execution/futures_client.py src/portfolio/futures_models.py src/risk/futures_risk.py
```

### Final Checklist
- [ ] All 214 original tests pass (spot backward compatibility)
- [ ] 50+ new futures tests pass
- [ ] Configuration rejects >20x leverage
- [ ] Configuration rejects cross margin mode
- [ ] Risk manager blocks orders within 5% of liquidation
- [ ] Testnet paper trading verified end-to-end
- [ ] Documentation updated with futures guide
- [ ] No hardcoded secrets in new code
- [ ] No regression in spot mode performance

---

## Resolved Decisions (Review & Confirm)

The following decisions have been made based on Momus review and safety best practices. Please confirm or override:

| # | Decision | Default Value | Can Override? |
|---|----------|---------------|---------------|
| 1 | **Leverage Default** | 5x | Yes (1-10x) |
| 1 | **Leverage Max** | 10x | **No (safety cap)** |
| 1 | **Leverage Hard Cap** | 20x (code enforced) | **No** |
| 2 | **Liquidation Buffer** | 5% | Yes (3-10%) |
| 3 | **Margin Mode** | Isolated only | **No for MVP** |
| 4 | **Position Mode** | One-way only | **No for MVP** |
| 5 | **Futures Symbols** | BTCUSDT, ETHUSDT | Yes (add more) |
| 6 | **Order Types** | Market + Stop-Market | TP-Market = future |
| 7 | **Signal Mapping** | BUY→LONG, SELL→Close LONG | **No SHORT for MVP** |
| 8 | **Funding Handling** | Track, log, include in PnL | **No hedging for MVP** |
| 9 | **Risk Limits** | Separate from spot, 5% daily loss | Yes (adjust %) |
| 10 | **Mark Price Source** | WebSocket + REST fallback | Yes (polling freq) |

### Key Rationale for Defaults

- **5x default / 10x max**: Conservative for MVP. 20x is hard safety cap enforced in code (Binance allows 125x which is extremely dangerous)
- **Isolated only**: Cross margin can liquidate entire account if multiple positions go against you
- **One-way only**: Hedge mode (simultaneous long/short) adds complexity for order routing and position management
- **BTCUSDT + ETHUSDT only**: Most liquid, tightest spreads, best for testing MVP
- **No SHORT for MVP**: Simplifies signal routing and position lifecycle
- **Market + Stop-Market**: Stop-Market essential for stop-loss protection. TP-Market can be added later.
- **Mark Price WebSocket**: Required for real-time liquidation monitoring (cannot rely on polling alone)

### User Confirmation Required

Please reply with any changes to the defaults above. If no response, these defaults will be used.

## Risks and Mitigations

| Risk | Impact | Mitigation |
|------|--------|------------|
| Liquidation wipes margin | HIGH | Hard 20x max leverage, 5% liq buffer, isolated margin only |
| Cross-margin liquidates account | HIGH | Explicitly exclude cross margin from MVP |
| Funding drains margin over time | MEDIUM | Track funding in PnL, warn if funding costs excessive |
| API differences cause errors | MEDIUM | Comprehensive test suite with mocked API responses |
| Spot trading broken | HIGH | 100% backward compatibility tests, spot remains default mode |
| Complexity delays delivery | MEDIUM | Phased approach: config → client → models → risk → executor |

## Notes

- **Paper trading for futures**: Use Binance testnet (different API keys required)
- **Live trading for futures**: Requires separate API key with futures permissions enabled
- **Risk of ruin**: Futures liquidation can lose entire margin allocation. Never risk more than you can afford to lose.
- **Funding rate arbitrage**: Sophisticated traders use funding rates to hedge. MVP does not implement this - funding is tracked but not actively managed.
