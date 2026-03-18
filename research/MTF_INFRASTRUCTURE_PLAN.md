# Multi-Timeframe (MTF) Infrastructure Implementation Plan

## Overview

Build reusable MTF support for the backtest engine to enable 4h regime + 1h entry (and similar combinations) across BTC/ETH and other pairs.

**NOT** a rescue plan for the abandoned BTC regime thesis.
**IS** general research infrastructure for testing MTF strategies.

## Architecture

### Design Principles

1. **Explicit Declaration**: Strategies declare required higher-timeframe inputs
2. **No Lookahead**: Higher-timeframe data aligned to entry bars without future leakage
3. **Backward Compatible**: Single-timeframe strategies continue working unchanged
4. **Clean Interface**: Simple API for strategy authors

### Data Flow

```
BacktestEngine
    ↓
IndicatorReader.fetch_multi_timeframe(
    symbol="BTCUSDT",
    entry_timeframe="1h",
    regime_timeframe="4h",
    start="2024-01-01",
    end="2024-12-31"
)
    ↓
SQL: Join 4h regime onto 1h bars (forward-fill, no lookahead)
    ↓
Return: List[dict] with both 1h and 4h indicators
    ↓
Strategy.evaluate(indicators)  # dict contains both timeframes
```

## Phase 1: Core Infrastructure (Day 1)

### 1.1 IndicatorReader Changes

**File**: `src/features/reader.py`

**Add new method**:

```python
async def fetch_multi_timeframe(
    self,
    symbol: str,
    entry_timeframe: str,
    regime_timeframe: str,
    start_time: str,
    end_time: str,
    regime_lookback: int = 200,  # Bars needed for regime calculation
) -> list[dict[str, float]]:
    """Fetch entry-timeframe data with regime-timeframe indicators joined.
    
    Args:
        symbol: Trading pair (e.g., "BTCUSDT")
        entry_timeframe: Lower timeframe for entries (e.g., "1h")
        regime_timeframe: Higher timeframe for regime (e.g., "4h")
        start_time: Start of range
        end_time: End of range
        regime_lookback: Extra regime bars needed for lookback calculations
        
    Returns:
        List of dicts, each containing:
        - All entry-timeframe indicators (close, vwap, rsi, etc.)
        - All regime-timeframe indicators (ema_slope, trend_consistency, etc.)
        - Regime indicators are forward-filled from last known 4h bar
        
    Example return dict:
    {
        # 1h indicators
        "time": datetime,
        "close_price": float,
        "vwap": float,
        "rsi_14": float,
        ...
        # 4h regime indicators (forward-filled)
        "ema_slope_50_4h": float,
        "trend_consistency_4h": float,
        "volatility_percentile_4h": float,
        ...
    }
    """
```

**Implementation Details**:

```python
async def fetch_multi_timeframe(
    self,
    symbol: str,
    entry_timeframe: str,
    regime_timeframe: str,
    start_time: str,
    end_time: str,
    regime_lookback: int = 200,
) -> list[dict[str, float]]:
    """Fetch multi-timeframe data with proper alignment."""
    
    # Calculate extended range for regime lookback
    # Need extra regime data before entry start for indicators
    regime_start = self._calculate_earliest_regime_time(
        start_time, regime_timeframe, regime_lookback
    )
    
    # Fetch entry-timeframe data
    entry_data = await self._fetch_range_rows(
        symbol, entry_timeframe, start_time, end_time
    )
    
    # Fetch regime-timeframe data (with lookback)
    regime_data = await self._fetch_range_rows(
        symbol, regime_timeframe, regime_start, end_time
    )
    
    # Join: Forward-fill regime indicators onto entry bars
    # For each 1h bar, use the most recent 4h regime values
    joined_data = self._join_timeframes(entry_data, regime_data)
    
    return joined_data


def _join_timeframes(
    self,
    entry_data: list[dict],
    regime_data: list[dict],
) -> list[dict]:
    """Join regime indicators onto entry bars without lookahead.
    
    For each entry bar, find the most recent completed regime bar.
    A 4h regime bar at 08:00 applies to 1h bars at 08:00, 09:00, 10:00, 11:00.
    """
    joined = []
    regime_idx = 0
    current_regime = {}
    
    for entry_bar in entry_data:
        entry_time = entry_bar["time"]
        
        # Advance regime index to most recent bar <= entry_time
        while (regime_idx < len(regime_data) and 
               regime_data[regime_idx]["time"] <= entry_time):
            current_regime = regime_data[regime_idx]
            regime_idx += 1
        
        # Create joined dict
        joined_bar = {**entry_bar}  # Copy entry indicators
        
        # Add regime indicators with _4h suffix
        for key, value in current_regime.items():
            if key != "time":  # Don't duplicate time
                joined_bar[f"{key}_4h"] = value
        
        joined.append(joined_bar)
    
    return joined
```

### 1.2 SQL Query Design

**Entry Timeframe Query** (existing):
```sql
SELECT
    i.time,
    o.close_price,
    i.vwap,
    i.rsi_14,
    ...
FROM indicators i
INNER JOIN ohlcv o ON i.time = o.time
WHERE i.symbol = $1 AND i.timeframe = $2
AND i.time >= $3 AND i.time <= $4
ORDER BY i.time ASC
```

**Regime Timeframe Query** (existing, just different timeframe):
```sql
SELECT
    i.time,
    i.ema_slope_50,
    i.trend_consistency,
    i.volatility_percentile,
    ...
FROM indicators i
WHERE i.symbol = $1 AND i.timeframe = $2
AND i.time >= $3 AND i.time <= $4
ORDER BY i.time ASC
```

**Key**: No complex SQL join needed. Fetch both timeframes separately, then align in Python. Cleaner and more explicit.

### 1.3 Backtest Engine Changes

**File**: `src/backtest/engine.py`

**Modify** `BacktestEngine.__init__` to detect MTF strategies:

```python
def __init__(self, config: BacktestConfig):
    ...
    # Check if strategy requires multi-timeframe
    self._is_mtf_strategy = hasattr(self._strategy, 'REQUIRED_TIMEFRAMES')
    
    if self._is_mtf_strategy:
        self._entry_timeframe = config.timeframe
        self._regime_timeframe = self._strategy.REQUIRED_TIMEFRAMES.get('regime', '4h')
```

**Modify** `BacktestEngine.run` to use MTF fetching:

```python
async def run(self) -> BacktestResult:
    """Run backtest."""
    if self._is_mtf_strategy:
        # Fetch multi-timeframe data
        data = await self._reader.fetch_multi_timeframe(
            symbol=self._config.symbol,
            entry_timeframe=self._entry_timeframe,
            regime_timeframe=self._regime_timeframe,
            start_time=self._config.start_date,
            end_time=self._config.end_date,
        )
    else:
        # Fetch single-timeframe data (existing behavior)
        data = await self._reader.fetch_range(
            symbol=self._config.symbol,
            timeframe=self._config.timeframe,
            start_time=self._config.start_date,
            end_time=self._config.end_date,
        )
    
    # Rest of backtest logic unchanged
    ...
```

## Phase 2: Strategy Interface (Day 1-2)

### 2.1 BaseStrategy Extension

**File**: `src/strategy/base.py`

**Add optional class attribute**:

```python
class BaseStrategy(ABC):
    """Base class for all strategies."""
    
    # Optional: Declare required timeframes for MTF strategies
    REQUIRED_TIMEFRAMES: dict[str, str] = {}
    
    # Example for MTF regime strategy:
    # REQUIRED_TIMEFRAMES = {
    #     'entry': '1h',
    #     'regime': '4h',
    # }
```

### 2.2 Example MTF Strategy Template

**File**: `src/strategy/mtf_template.py` (new)

```python
from __future__ import annotations

from collections.abc import Mapping

from src.strategy.base import BaseStrategy
from src.strategy.signals import Signal, SignalType


class MTFTrendPullbackStrategy(BaseStrategy):
    """Template for MTF strategy: higher-timeframe regime + lower-timeframe entry.
    
    Example: 4h trend regime with 1h pullback entries.
    """
    
    # Declare required timeframes
    REQUIRED_TIMEFRAMES = {
        'entry': '1h',
        'regime': '4h',
    }
    
    def __init__(self, config: Mapping[str, object] | None = None) -> None:
        super().__init__(config)
        
        # Thresholds for regime classification (4h)
        self._trend_strength_threshold = float(
            self._config.get("trend_strength_threshold", 0.005)
        )
        
        # Entry settings (1h)
        self._entry_zone_pct = float(self._config.get("entry_zone_pct", 0.01))
        self._rsi_oversold = float(self._config.get("rsi_oversold", 45.0))
    
    async def evaluate(self, symbol: str, indicators: dict[str, float]) -> Signal:
        """Evaluate using MTF indicators.
        
        Args:
            symbol: Trading pair
            indicators: Dict containing both timeframes:
                - 1h indicators: close_price, vwap, rsi_14, etc.
                - 4h indicators: ema_slope_50_4h, trend_consistency_4h, etc.
        """
        # Extract 1h indicators for entry
        close_price = indicators.get("close_price", 0.0)
        rsi_14 = indicators.get("rsi_14", 50.0) or 50.0
        vwap = indicators.get("vwap", close_price)
        
        # Extract 4h indicators for regime
        ema_slope_4h = indicators.get("ema_slope_50_4h", 0.0) or 0.0
        trend_consistency_4h = indicators.get("trend_consistency_4h", 50.0) or 50.0
        
        # Classify regime on 4h
        is_trending_up = (
            ema_slope_4h > self._trend_strength_threshold
            and trend_consistency_4h > 60.0
        )
        
        if not is_trending_up:
            return Signal(
                type=SignalType.HOLD,
                symbol=symbol,
                price=close_price,
                confidence=0.0,
                reason="4h regime not trending up",
                indicators={"ema_slope_4h": ema_slope_4h},
            )
        
        # Look for 1h pullback entry
        price_vs_vwap = (close_price - vwap) / vwap if vwap != 0 else 0
        
        if (price_vs_vwap < self._entry_zone_pct and 
            rsi_14 < self._rsi_oversold):
            return Signal(
                type=SignalType.BUY,
                symbol=symbol,
                price=close_price,
                confidence=0.8,
                reason=f"4h trend + 1h pullback: vs_vwap={price_vs_vwap:.4f}",
                indicators={
                    "ema_slope_4h": ema_slope_4h,
                    "price_vs_vwap": price_vs_vwap,
                    "rsi_14": rsi_14,
                },
            )
        
        return Signal(
            type=SignalType.HOLD,
            symbol=symbol,
            price=close_price,
            confidence=0.0,
            reason="Waiting for 1h pullback",
            indicators={"price_vs_vwap": price_vs_vwap},
        )
```

## Phase 3: Test Suite (Day 2)

### 3.1 Unit Tests for Join Logic

**File**: `tests/test_mtf_join.py` (new)

```python
import pytest
from datetime import datetime, timedelta

from src.features.reader import IndicatorReader


class TestMTFJoin:
    """Test multi-timeframe data joining."""
    
    def test_join_forward_fill(self):
        """Test that regime indicators are forward-filled correctly."""
        # Entry bars: 1h at 08:00, 09:00, 10:00, 11:00
        # Regime bar: 4h at 08:00
        
        entry_data = [
            {"time": datetime(2024, 1, 1, 8, 0), "close": 100},
            {"time": datetime(2024, 1, 1, 9, 0), "close": 101},
            {"time": datetime(2024, 1, 1, 10, 0), "close": 102},
            {"time": datetime(2024, 1, 1, 11, 0), "close": 103},
        ]
        
        regime_data = [
            {"time": datetime(2024, 1, 1, 8, 0), "ema_slope": 0.01},
        ]
        
        reader = IndicatorReader({})
        joined = reader._join_timeframes(entry_data, regime_data)
        
        # All 1h bars should have the same 4h regime value
        assert len(joined) == 4
        for bar in joined:
            assert bar["ema_slope_4h"] == 0.01
    
    def test_join_no_lookahead(self):
        """Test that future regime data is NOT used."""
        # Entry bar at 08:00
        # Regime bar at 12:00 (future)
        
        entry_data = [
            {"time": datetime(2024, 1, 1, 8, 0), "close": 100},
        ]
        
        regime_data = [
            {"time": datetime(2024, 1, 1, 12, 0), "ema_slope": 0.01},
        ]
        
        reader = IndicatorReader({})
        joined = reader._join_timeframes(entry_data, regime_data)
        
        # 08:00 bar should NOT have 12:00 regime data
        assert "ema_slope_4h" not in joined[0] or joined[0]["ema_slope_4h"] is None
    
    def test_join_regime_update(self):
        """Test that regime updates at next 4h bar."""
        # Entry bars: 1h at 08:00-15:00
        # Regime bars: 4h at 08:00, 12:00
        
        entry_data = [
            {"time": datetime(2024, 1, 1, 8, 0), "close": 100},
            {"time": datetime(2024, 1, 1, 9, 0), "close": 101},
            {"time": datetime(2024, 1, 1, 10, 0), "close": 102},
            {"time": datetime(2024, 1, 1, 11, 0), "close": 103},
            {"time": datetime(2024, 1, 1, 12, 0), "close": 104},
            {"time": datetime(2024, 1, 1, 13, 0), "close": 105},
        ]
        
        regime_data = [
            {"time": datetime(2024, 1, 1, 8, 0), "ema_slope": 0.01},
            {"time": datetime(2024, 1, 1, 12, 0), "ema_slope": 0.02},
        ]
        
        reader = IndicatorReader({})
        joined = reader._join_timeframes(entry_data, regime_data)
        
        # First 4 bars use 08:00 regime
        for bar in joined[:4]:
            assert bar["ema_slope_4h"] == 0.01
        
        # Last 2 bars use 12:00 regime
        for bar in joined[4:]:
            assert bar["ema_slope_4h"] == 0.02
```

### 3.2 Regression Tests

**File**: `tests/test_backtest_mtf.py` (new)

```python
import pytest

from src.backtest.engine import BacktestEngine
from src.backtest.config import BacktestConfig


class TestMTFBacktest:
    """Test MTF backtest functionality."""
    
    async def test_single_timeframe_still_works(self):
        """Ensure single-timeframe strategies still work."""
        # Use existing single-timeframe strategy
        config = BacktestConfig(
            symbol="BTCUSDT",
            timeframe="4h",
            strategy="regime_router",
            start_date="2024-01-01",
            end_date="2024-03-01",
        )
        
        engine = BacktestEngine(config)
        result = await engine.run()
        
        # Should complete without error
        assert result is not None
        assert result.total_trades >= 0
    
    async def test_mtf_strategy_runs(self):
        """Test MTF strategy runs correctly."""
        config = BacktestConfig(
            symbol="BTCUSDT",
            timeframe="1h",  # Entry timeframe
            strategy="mtf_template",  # MTF strategy
            start_date="2024-01-01",
            end_date="2024-03-01",
        )
        
        engine = BacktestEngine(config)
        result = await engine.run()
        
        # Should complete without error
        assert result is not None
        assert result.total_trades >= 0
    
    async def test_no_future_leakage(self):
        """Verify MTF strategy cannot see future regime data."""
        # This is a critical test
        # If regime at time T contains data from > T, we have leakage
        
        # Implementation: check that regime indicators only use completed bars
        pass  # Detailed implementation needed
```

### 3.3 End-to-End Template

**File**: `research/mtf_btc_eth_template.yaml`

```yaml
# Template for MTF research on BTC/ETH

mode: backtest
agent_id: mtf_research_template
log_level: INFO

trading:
  pairs:
    - BTCUSDT
    - ETHUSDT
  timeframe: 1h  # Entry timeframe

database:
  host: localhost
  port: 15432
  name: marketdata
  user: trading
  password: ""

trading_execution:
  enabled: false
  test_mode: true
  order_size_usdt: 500
  stop_loss_pct: 0.02
  take_profit_pct: 0.06
  use_atr_sizing: true
  risk_per_trade_pct: 0.02

strategy:
  evaluation_interval_seconds: 3600  # 1h
  strategies:
    - name: mtf_template
      config:
        # 4h regime thresholds
        trend_strength_threshold: 0.005
        volatility_percentile_threshold: 60.0
        trend_consistency_threshold: 60.0
        
        # 1h entry settings
        entry_zone_pct: 0.01
        rsi_oversold: 45.0
        rsi_overbought: 55.0

# Research gates
research:
  min_trades: 15
  min_win_rate: 0.40
  min_return_pct: 0.0
  max_drawdown_pct: 15.0
  min_sharpe: 0.5
```

## Phase 4: Acceptance Gates

### 4.1 Infrastructure Acceptance

Before declaring MTF infrastructure complete, verify:

**Functional**:
- [ ] `fetch_multi_timeframe` returns correct data shape
- [ ] All entry bars have corresponding regime indicators
- [ ] Regime indicators are forward-filled (not interpolated)
- [ ] No lookahead: regime at time T only uses data <= T
- [ ] Strategy interface is clean and documented

**Regression**:
- [ ] All 558 existing tests still pass
- [ ] Single-timeframe strategies work unchanged
- [ ] Backward compatibility maintained

**Performance**:
- [ ] MTF fetch doesn't slow backtests significantly (< 20% overhead)
- [ ] SQL queries are optimized

### 4.2 Research Gates (For New Theses)

Any MTF strategy must pass:

- [ ] Minimum 15 trades in 8-month backtest
- [ ] Positive net return
- [ ] Win rate >= 40%
- [ ] Max drawdown < 15%
- [ ] Sharpe ratio > 0.5
- [ ] Out-of-sample validation (different time period)

## Implementation Milestones

### Day 1: Core Infrastructure
- [ ] Implement `fetch_multi_timeframe` in IndicatorReader
- [ ] Implement `_join_timeframes` alignment logic
- [ ] Add REQUIRED_TIMEFRAMES to BaseStrategy
- [ ] Modify BacktestEngine to detect and use MTF
- [ ] Unit tests for join logic

### Day 2: Strategy & Integration
- [ ] Create MTF strategy template
- [ ] Backtest regression tests
- [ ] End-to-end BTC/ETH template
- [ ] Documentation

### Day 3: Validation & Polish
- [ ] Run full test suite (558 tests + new MTF tests)
- [ ] Performance benchmarking
- [ ] Acceptance gate verification
- [ ] Merge to main branch

## Risk Mitigation

**Risk**: MTF doesn't increase trade count
- **Mitigation**: Test with multiple thesis families, not just regime-based

**Risk**: Performance degradation
- **Mitigation**: Benchmark before/after, optimize SQL if needed

**Risk**: Complexity makes system fragile
- **Mitigation**: Maintain backward compatibility, single-timeframe strategies unchanged

## Success Criteria

**Infrastructure Success**:
- MTF support is clean, tested, and documented
- No regression in existing functionality
- Strategy authors can easily use MTF with 5 lines of code

**Research Success**:
- Find at least one MTF thesis that passes all gates
- OR: Prove MTF doesn't help and pivot to different approach

## Post-Implementation Research Plan

Once MTF infrastructure is live, test these thesis families:

1. **4h Trend + 1h Pullback**
   - Trend regime on 4h
   - VWAP/EMA50 pullback entries on 1h
   - Multiple assets (BTC, ETH, SOL)

2. **4h Breakout + 1h Retest**
   - Breakout regime on 4h
   - Retest of breakout level on 1h
   - Momentum confirmation

3. **4h Volatility Regime + 1h Mean Reversion**
   - Low volatility regime on 4h
   - Bollinger Band touches on 1h
   - Range-bound market thesis

4. **Two-Sided Futures (BTC/ETH)**
   - Long/short based on 4h regime
   - 1h entries for both directions
   - Hedging capabilities

**If none pass gates**: Pivot to completely different strategy families.

## Files to Create

1. `src/features/reader.py` - Add MTF methods
2. `src/strategy/base.py` - Add REQUIRED_TIMEFRAMES
3. `src/strategy/mtf_template.py` - Example strategy
4. `tests/test_mtf_join.py` - Unit tests
5. `tests/test_backtest_mtf.py` - Regression tests
6. `research/mtf_btc_eth_template.yaml` - Research template
7. `docs/MTF_STRATEGY_GUIDE.md` - Documentation

## Estimated Timeline

- **Day 1**: Core infrastructure (6-8 hours)
- **Day 2**: Strategy interface + tests (6-8 hours)
- **Day 3**: Validation + polish (4-6 hours)

**Total**: 2-3 days as estimated.

---

Ready to proceed? I can start with Phase 1 implementation.
