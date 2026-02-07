# Strategy Engine Implementation Plan

## TL;DR

> Wire StrategyEngine to read indicators from TimescaleDB, evaluate EMA crossover, and deliver signals to TradingExecutor — Spot API, paper mode only.
>
> **Deliverables**:
> - IndicatorReader reads latest indicators + close_price from DB
> - StrategyEngine fetches indicators and evaluates EMA crossover
> - TradingExecutor.on_signal() places Spot orders (quoteOrderQty for BUY, base qty for SELL)
> - StrategyEngine started in `src/main.py`
> - TDD tests for reader, strategy, signal flow, paper-mode
>
> **Effort**: Medium (4 tasks, strictly sequential)
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

### Futures Artifacts to Clean (Task 2)

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

**Docs to update**: `README.md`, `TRADING_EXECUTION.md`, `config/risk.yaml`

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

**Known test breakage from Futures cleanup** (must fix in Task 2):
- `tests/test_risk_manager.py:28,35,39` — tests `max_leverage` on `PositionLimits`
- `src/execution/__init__.py:6` — exports `PositionInfo`

---

## Deleted Requirements (Step 2 — things that shouldn't exist)

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

## Execution (4 Tasks, Sequential)

### Task 1: IndicatorReader + EngineConfig fixes (additive, no breakage)

**What to do**:

**1a. Fix EngineConfig bugs** in `src/strategy/engine.py`:
- `callable[[Signal], Any]` → `Callable[[Signal], Any]` (import from `collections.abc`)
- `strategy_configs: list[...] = []` → `field(default_factory=list)`
- Add `database: Mapping[str, object] = field(default_factory=dict)` field
- Add `timeframe: str = "1m"` field

**1b. Implement IndicatorReader** (`src/features/reader.py`):
- Same pg8000 + `asyncio.to_thread()` + SQLite fallback pattern as `IndicatorWriter`
- Single method:
  ```python
  async def fetch_latest(self, symbol: str, timeframe: str, limit: int = 2) -> list[dict[str, float]]:
  ```
- SQL (pg8000):
  ```sql
  SELECT i.time, i.ema_12, i.ema_26, o.close_price
  FROM indicators i
  JOIN ohlcv o ON i.time = o.time AND i.symbol = o.symbol AND i.timeframe = o.timeframe
  WHERE i.symbol = %s AND i.timeframe = %s
  ORDER BY i.time DESC
  LIMIT %s
  ```
- Returns list oldest-first. Empty list if no data (no crash).
- Async context manager for connection lifecycle.

**1c. Update `src/features/__init__.py`** — add `IndicatorReader` export.

**Tests** (`tests/test_indicator_reader.py`):
- `test_fetch_latest_two_rows` — returns 2 dicts with ema_12, ema_26, close_price
- `test_fetch_empty_table` — returns empty list
- `test_fetch_single_row` — returns 1 row (caller handles insufficient data)
- `test_db_uses_asyncio_to_thread` — verify non-blocking

**Acceptance Criteria**:
- [ ] EngineConfig bugs fixed
- [ ] IndicatorReader implemented with tests passing
- [ ] `src/features/__init__.py` updated
- [ ] `pytest tests/test_indicator_reader.py -v` → PASS
- [ ] `pytest` (full existing suite) → PASS (no breakage — this is purely additive)

**Commit**: `feat(features): add IndicatorReader for strategy engine`

---

### Task 2: Clean Futures artifacts for Spot (destructive refactoring)

This task is isolated from Task 1 so that if tests break during cleanup, the IndicatorReader work is already safely committed.

**What to do**:

**2a. Clean `binance_client.py`**:
- Delete `PositionInfo` dataclass
- Delete `get_positions()` method
- Simplify `AccountInfo` to:
  ```python
  @dataclass(frozen=True)
  class AccountInfo:
      total_balance: float
      available_balance: float
  ```
- Update `get_account_info()` to return simplified `AccountInfo`
- Remove `reduce_only` param from `place_market_order()` and `place_limit_order()`
- Fix `place_market_order()` BUY to use `quoteOrderQty` (Spot API):
  ```python
  if side == "BUY":
      params = {"symbol": symbol, "side": "BUY", "type": "MARKET", "quoteOrderQty": str(quantity)}
  else:
      params = {"symbol": symbol, "side": "SELL", "type": "MARKET", "quantity": str(quantity)}
  ```
- Test mode mock for BUY must also reflect `quoteOrderQty`
- Add `get_asset_balance(asset: str) -> float` method:
  ```python
  async def get_asset_balance(self, asset: str) -> float:
      if self._test_mode:
          self._logger.info("TEST MODE: get_asset_balance(%s) returning mock 1.0", asset)
          return 1.0  # Mock balance for paper trading
      data = await self._request("GET", "/api/v3/account", signed=True)
      for balance in data.get("balances", []):
          if balance.get("asset") == asset:
              return float(balance.get("free", 0))
      return 0.0
  ```
  **Critical**: `get_asset_balance` MUST support `test_mode` — return mock balance (e.g., `1.0`) when in paper trading. Otherwise `on_signal()` for SELL makes real API calls even in paper mode.

**2b. Clean `executor.py`**:
- Delete position monitoring section in `_monitor_and_update()` (lines 107-126 — the `for symbol in self._config.symbols: get_positions(...)` loop)
- Update `_monitor_and_update()` account balance call to match simplified `AccountInfo`
- Remove `_calculate_quantity()` method (superseded by `on_signal()` in Task 3)

**2c. Clean `execution/metrics.py`**:
- Remove `position_exposure` gauge
- Remove `unrealized_pnl` gauge
- Remove `position_duration_seconds` histogram
- Remove `update_positions()` method from `ExecutionMetrics`
- Update `update_account_balance()` → only `total` and `available` (drop `total_margin`)

**2d. Clean `execution/__init__.py`**:
- Remove `PositionInfo` from imports and `__all__`

**2e. Clean `risk/manager.py`**:
- Remove `max_leverage` from `PositionLimits` dataclass

**2f. Clean `config/risk.yaml`**:
- Remove `max_leverage: 3` line

**2g. Fix broken tests**:
- `tests/test_risk_manager.py:28` — remove `assert limits.max_leverage == 3`
- `tests/test_risk_manager.py:35` — remove `max_leverage=5` from custom config
- `tests/test_risk_manager.py:39` — remove `assert limits.max_leverage == 5`
- Any other tests referencing deleted fields/methods

**Acceptance Criteria**:
- [ ] `PositionInfo` and `get_positions()` deleted
- [ ] `AccountInfo` simplified to 2 fields
- [ ] `place_market_order` BUY uses `quoteOrderQty`
- [ ] `get_asset_balance()` added with `test_mode` mock support
- [ ] `reduce_only` removed from order methods
- [ ] Position monitoring removed from executor
- [ ] `max_leverage` removed everywhere
- [ ] Position metrics removed
- [ ] Broken tests fixed
- [ ] `pytest` → PASS (full suite, zero failures)

**Commit**: `refactor(execution): clean Futures artifacts for Spot API`

---

### Task 3: Wire StrategyEngine + TradingExecutor signal handler

**What to do**:

**3a. Implement `StrategyEngine._fetch_indicators()`** in `src/strategy/engine.py`:
- Accept `IndicatorReader` as constructor dependency (passed in, not created)
- Implementation:
  ```python
  async def _fetch_indicators(self, symbol: str) -> dict[str, float] | None:
      rows = await self._reader.fetch_latest(symbol, self._config.timeframe, limit=2)
      if len(rows) < 2:
          self._logger.info("Warming up %s: need 2 indicator rows, have %d", symbol, len(rows))
          return None
      return rows[-1]  # Latest row — strategy handles crossover via internal state
  ```
- Update `_evaluate_all()`:
  - Replace `indicators = {}` with `await self._fetch_indicators(symbol)`
  - Skip symbol if None
  - Only call `on_signal` for BUY/SELL (not HOLD) — filter at the source

**3b. Add `TradingExecutor.on_signal()`** in `src/execution/executor.py`:
- Spot-aware signal → order mapping:
  ```python
  async def on_signal(self, signal: Signal) -> None:
      if signal.type == SignalType.HOLD:
          return
      if not self._config.enabled:
          self._logger.info("Signal ignored (executor disabled): %s", signal)
          return
      try:
          if signal.type == SignalType.BUY:
              # Spot BUY: spend order_size_usdt via quoteOrderQty
              await self.place_market_order(signal.symbol, "BUY", self._config.order_size_usdt)
          elif signal.type == SignalType.SELL:
              # Spot SELL: sell all held base asset
              base_asset = signal.symbol.replace("USDT", "")
              balance = await self._client.get_asset_balance(base_asset)
              if balance > 0:
                  await self.place_market_order(signal.symbol, "SELL", balance)
              else:
                  self._logger.info("SELL signal but no %s balance", base_asset)
      except RuntimeError as exc:
          self._logger.warning("Signal rejected: %s — %s", signal, exc)
  ```
  Note: when `enabled=false`, method returns early before accessing `self._client` (which is `None`). When `enabled=true` + `test_mode=true`, `get_asset_balance()` returns mock `1.0` (from Task 2).

**Tests** (`tests/test_strategy_integration.py`):
- `test_fetch_indicators_returns_latest` — mocked reader returns data
- `test_fetch_indicators_warmup` — < 2 rows → returns None
- `test_evaluate_buy_signal_triggers_callback` — EMA crossover up → BUY
- `test_evaluate_sell_signal_triggers_callback` — EMA crossover down → SELL
- `test_hold_does_not_trigger_callback` — HOLD → callback not called
- `test_on_signal_buy_uses_quote_qty` — BUY → quoteOrderQty parameter used
- `test_on_signal_sell_queries_balance` — SELL → get_asset_balance called
- `test_on_signal_sell_no_balance_skips` — SELL with 0 balance → no order
- `test_on_signal_disabled_logs_and_skips` — enabled=false → no order, no client access
- `test_on_signal_risk_block` — RiskManager rejects → no order
- `test_paper_mode_no_real_orders` — test_mode=true → mock order returned, get_asset_balance returns mock

**Acceptance Criteria**:
- [ ] `_fetch_indicators()` replaces placeholder
- [ ] Only BUY/SELL forwarded to callback (not HOLD)
- [ ] `on_signal()` handles Spot BUY (quoteOrderQty) and SELL (balance check)
- [ ] Paper mode: no real API calls (get_asset_balance returns mock, orders return mock)
- [ ] Disabled mode: no client access (early return before `self._client`)
- [ ] `pytest tests/test_strategy_integration.py -v` → PASS

**Commit**: `feat(strategy): wire StrategyEngine evaluation and Spot-aware signal handler`

---

### Task 4: main.py wiring + final integration + docs

**What to do**:

**4a. Add strategy config to `config/settings.yaml`**:
```yaml
strategy:
  evaluation_interval_seconds: 60
```

**4b. Update `src/main.py`**:
- Add `strategy` section to `Settings` dataclass (just `evaluation_interval_seconds`)
- Parse in `load_settings()`
- Initialize `IndicatorReader` with database config
- Build `EngineConfig` from settings + database + trading pairs
- Initialize `StrategyEngine` with config + reader
- Create async task: `engine.run(on_signal=trading_executor.on_signal)`
- Add `IndicatorReader` and `StrategyEngine` to async context manager chain
- Cancel and await strategy task on shutdown

**4c. Update docs**:
- `README.md` — change "Futures" to "Spot"
- `TRADING_EXECUTION.md` — update for Spot API (endpoints, no positions, quoteOrderQty)

**Tests** (add to `tests/test_strategy_integration.py`):
- `test_settings_default_safe` — load settings.yaml, verify `enabled=false`, `test_mode=true`
- `test_full_flow_engine_to_executor` — mock reader → engine evaluates → signal → executor mock → order

**Acceptance Criteria**:
- [ ] `settings.yaml` has `strategy` section
- [ ] `load_settings()` parses it
- [ ] `main.py` starts StrategyEngine alongside existing tasks
- [ ] Docs updated (no more "Futures" references)
- [ ] `pytest -v` → ALL tests pass (existing + new)

**Commit**: `feat(main): wire StrategyEngine into main loop and update docs for Spot`

---

## Commit Strategy

| Task | Message | Type | Verification |
|------|---------|------|--------------|
| 1 | `feat(features): add IndicatorReader for strategy engine` | Additive | `pytest tests/test_indicator_reader.py -v && pytest` |
| 2 | `refactor(execution): clean Futures artifacts for Spot API` | Destructive | `pytest` (full suite, test fixes included) |
| 3 | `feat(strategy): wire StrategyEngine evaluation and Spot-aware signal handler` | Additive | `pytest tests/test_strategy_integration.py -v` |
| 4 | `feat(main): wire StrategyEngine into main loop and update docs for Spot` | Additive | `pytest -v` |

---

## Success Criteria

```bash
pytest tests/test_indicator_reader.py -v
pytest tests/test_strategy_integration.py -v
pytest -v  # Full suite
```

### Final Checklist
- [ ] IndicatorReader fetches indicator rows + close_price (SQLite fallback)
- [ ] EngineConfig bugs fixed (Callable, mutable default, database/timeframe fields)
- [ ] Futures artifacts cleaned (PositionInfo, get_positions, leverage, position metrics)
- [ ] `place_market_order` BUY uses `quoteOrderQty` (Spot-correct)
- [ ] `get_asset_balance()` returns mock in test_mode (no real API calls in paper mode)
- [ ] SELL checks base asset balance before selling
- [ ] EMA crossover emits signal once per crossover (stateful strategy handles this)
- [ ] HOLD signals never reach TradingExecutor
- [ ] Paper mode prevents real orders AND real balance queries
- [ ] StrategyEngine started in main.py
- [ ] No "Futures" references in docs
- [ ] Symbol parsing uses `replace("USDT", "")` — works for all 10 configured pairs (known limitation for non-USDT pairs)
- [ ] All tests pass
- [ ] `trading_execution.enabled` remains false by default
