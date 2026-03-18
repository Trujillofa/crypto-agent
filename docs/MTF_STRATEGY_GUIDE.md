# Multi-Timeframe (MTF) Strategy Guide

This guide explains how to build and use multi-timeframe strategies with the backtest engine.

## Overview

Multi-timeframe strategies combine indicators from different time horizons to make trading decisions:

- **Higher timeframe (regime)**: Classify market regime (trending/ranging/uncertain)
- **Entry timeframe**: Execute entries based on regime classification
- **Joined data**: Higher timeframe indicators are suffixed (e.g., `_4h`)

This approach provides better context than single-timeframe strategies while maintaining precise entry timing.

## Architecture

```
┌─────────────────┐     ┌─────────────────┐
│  4h Timeframe   │     │  1h Timeframe   │
│  - EMA slope    │     │  - VWAP         │
│  - Trend const  │     │  - RSI          │
│  - Volatility   │     │  - Close price  │
└────────┬────────┘     └────────┬────────┘
         │                       │
         └───────────┬───────────┘
                     ▼
         ┌─────────────────────┐
         │  As-Of Join Engine  │
         │  (zero lookahead)   │
         └──────────┬──────────┘
                    ▼
         ┌─────────────────────┐
         │  Joined Indicators  │
         │  - vwap (1h)        │
         │  - rsi_14 (1h)      │
         │  - ema_slope_50_4h  │
         │  - trend_consistency_4h
         └──────────┬──────────┘
                    ▼
         ┌─────────────────────┐
         │  Strategy.evaluate()│
         │  - Classify regime  │
         │  - Generate signals │
         └─────────────────────┘
```

## How It Works

### 1. Data Joining (Zero Lookahead)

The MTF infrastructure uses an **as-of join** to combine timeframes:

- Each 1h bar gets the most recent **completed** 4h bar's indicators
- No forward-looking data is included
- Join is explicit: no inferred timestamps

Example:
```
Time        1h Bar    4h Bar (completed)    Joined Result
─────────────────────────────────────────────────────────
04:00       Close     Close @ 04:00         04:00 1h + 04:00 4h
05:00       Close     Close @ 04:00         05:00 1h + 04:00 4h
06:00       Close     Close @ 04:00         06:00 1h + 04:00 4h
07:00       Close     Close @ 04:00         07:00 1h + 04:00 4h
08:00       Close     Close @ 08:00         08:00 1h + 08:00 4h
```

At 05:00, the 1h bar uses the 04:00 4h bar (most recent completed).

### 2. Indicator Suffixes

Higher timeframe indicators are suffixed with the timeframe:

| Timeframe | Indicators |
|-----------|------------|
| Entry (1h) | `close_price`, `vwap`, `rsi_14`, `ema_50` |
| Regime (4h) | `ema_slope_50_4h`, `trend_consistency_4h`, `volatility_percentile_4h` |

### 3. Backtest Engine Integration

The engine automatically detects MTF strategies:

```python
# In backtest/engine.py
if hasattr(strategy, 'REQUIRED_TIMEFRAMES') and strategy.REQUIRED_TIMEFRAMES:
    # Use multi-timeframe data fetching
    data = await self._reader.fetch_multi_timeframe(...)
else:
    # Use single-timeframe (backward compatible)
    data = await self._reader.fetch_range(...)
```

## Building an MTF Strategy

### Step 1: Declare Required Timeframes

```python
class MyMTFStrategy(BaseStrategy):
    """My multi-timeframe strategy."""

    # Required: Declare timeframes for MTF support
    REQUIRED_TIMEFRAMES = {
        "entry": "1h",   # Base timeframe for entries
        "regime": "4h",  # Higher timeframe for regime classification
    }
```

### Step 2: Implement evaluate()

```python
async def evaluate(self, symbol: str, indicators: dict[str, float]) -> Signal:
    # Entry timeframe indicators (base - no suffix)
    close_price = indicators.get("close_price", 0.0)
    vwap = indicators.get("vwap", close_price)
    rsi_14 = indicators.get("rsi_14", 50.0)

    # Higher timeframe indicators - SUFFIXED with _4h
    ema_slope_4h = indicators.get("ema_slope_50_4h", 0.0)
    trend_consistency_4h = indicators.get("trend_consistency_4h", 50.0)

    # Classify regime using higher timeframe
    regime = self._classify_regime(ema_slope_4h, trend_consistency_4h)

    # Generate signals based on regime + entry conditions
    if regime == "trending_up":
        return self._generate_long_signal(...)
    # ... etc
```

### Step 3: Classify Regime

```python
def _classify_regime(self, ema_slope: float, trend_consistency: float) -> str:
    """Classify market regime based on 4h indicators."""
    is_trending = (
        abs(ema_slope) > self._threshold
        and trend_consistency > 60.0
    )

    if is_trending:
        return "trending_up" if ema_slope > 0 else "trending_down"

    return "uncertain"
```

## Complete Example

See `src/strategy/mtf_template.py` for a complete working template:

```python
from src.strategy.base import BaseStrategy
from src.strategy.signals import Signal, SignalType

class MTFStrategyTemplate(BaseStrategy):
    """Template for multi-timeframe strategies."""

    REQUIRED_TIMEFRAMES = {
        "entry": "1h",
        "regime": "4h",
    }

    def __init__(self, config):
        super().__init__(config)
        self._threshold = config.get("threshold", 0.005)

    async def evaluate(self, symbol, indicators):
        # Entry timeframe
        close_price = indicators.get("close_price", 0.0)
        vwap = indicators.get("vwap", close_price)
        rsi_14 = indicators.get("rsi_14", 50.0)

        # Higher timeframe (suffixed)
        ema_slope_4h = indicators.get("ema_slope_50_4h", 0.0)
        trend_cons_4h = indicators.get("trend_consistency_4h", 50.0)

        # Classify regime
        regime = self._classify_regime(ema_slope_4h, trend_cons_4h)

        # Generate signal
        if regime == "trending_up":
            return self._generate_long_signal(...)
        # ... etc
```

## Configuration

### Backtest YAML Configuration

```yaml
strategies:
  - name: MTFTrendPullback
    class: src.strategy.mtf_template.MTFStrategyTemplate
    params:
      # Regime classification
      regime_threshold: 0.005

      # Entry conditions
      entry_pullback_pct: 0.01
      rsi_oversold: 40.0
      rsi_overbought: 60.0

      # Confidence
      confidence_boost: 1.2

timeframes:
  entry: 1h
  regime: 4h
```

### Programmatic Usage

```python
from src.strategy.mtf_template import MTFStrategyTemplate

strategy = MTFStrategyTemplate(config={
    "regime_threshold": 0.005,
    "entry_pullback_pct": 0.01,
    "rsi_oversold": 40.0,
})
```

## Available Higher Timeframe Indicators

When using `regime: 4h`, these indicators are available with `_4h` suffix:

| Indicator | Description |
|-----------|-------------|
| `ema_slope_50_4h` | 50-period EMA slope |
| `ema_slope_200_4h` | 200-period EMA slope |
| `trend_consistency_4h` | Trend consistency score (0-100) |
| `volatility_percentile_4h` | Volatility percentile (0-100) |
| `rsi_14_4h` | 14-period RSI |
| `rsi_slope_4h` | RSI slope |
| `macd_hist_4h` | MACD histogram |
| `bb_upper_dist_4h` | Distance to upper Bollinger Band |
| `bb_lower_dist_4h` | Distance to lower Bollinger Band |
| `atr_pct_4h` | ATR as percentage of price |
| `vwap_4h` | Volume-weighted average price |

## Common MTF Patterns

### Pattern 1: Trend + Pullback
```python
# 4h: Strong uptrend confirmed
# 1h: Price pulls back to VWAP/EMA50 + RSI oversold
if regime == "trending_up" and price_vs_vwap < -0.01 and rsi_14 < 40:
    return Signal(type=SignalType.BUY, ...)
```

### Pattern 2: Breakout + Retest
```python
# 4h: Volatility regime (high percentile)
# 1h: Price retests breakout level
if volatility_4h > 70 and price_retesting_level:
    return Signal(type=SignalType.BUY, ...)
```

### Pattern 3: Volatility Regime + Mean Reversion
```python
# 4h: Low volatility (ranging)
# 1h: Price at Bollinger Band extreme
if regime == "ranging" and bb_lower_dist < 0.01:
    return Signal(type=SignalType.BUY, ...)
```

## Testing

### Unit Tests

```python
# tests/test_mtf_join.py
def test_mtf_join_no_lookahead():
    """Verify no future data leakage in join."""
    entry_df = create_entry_data()
    regime_df = create_regime_data()

    result = _join_timeframes(entry_df, regime_df)

    # Each 1h bar should only see completed 4h data
    for idx, row in result.iterrows():
        regime_time = row['regime_time']
        entry_time = row['time']
        assert regime_time <= entry_time
```

### Integration Tests

```python
# tests/test_mtf_integration.py
async def test_mtf_strategy_evaluate():
    """Test full MTF strategy evaluation."""
    strategy = MTFStrategyTemplate(config={...})

    indicators = {
        "close_price": 50000.0,
        "vwap": 49500.0,
        "rsi_14": 35.0,
        "ema_slope_50_4h": 0.01,
        "trend_consistency_4h": 75.0,
    }

    signal = await strategy.evaluate("BTCUSDT", indicators)

    assert signal.type == SignalType.BUY
    assert signal.confidence > 0.7
```

## Backward Compatibility

Single-timeframe strategies continue to work unchanged:

```python
class SingleTimeframeStrategy(BaseStrategy):
    """This works exactly as before."""
    # No REQUIRED_TIMEFRAMES = single timeframe

    async def evaluate(self, symbol, indicators):
        # Only receives single timeframe data
        close = indicators.get("close_price")
        ...
```

The engine detects MTF strategies and only uses multi-timeframe fetching when `REQUIRED_TIMEFRAMES` is declared.

## Performance Considerations

### Data Fetching
- MTF strategies fetch data for both timeframes
- Join operation is O(n) where n = entry bars
- Minimal overhead (~1-2ms per 1000 bars)

### Memory Usage
- Joined data has additional columns (regime indicators)
- Memory increase: ~30-40% more columns
- Still fits easily in memory for typical backtests

### Recommended Timeframe Combinations

| Entry | Regime | Use Case |
|-------|--------|----------|
| 1h | 4h | Swing trading |
| 15m | 1h | Day trading |
| 4h | 1d | Position trading |
| 1h | 1d | Multi-day swings |

## Troubleshooting

### "Missing regime indicator"
Check that you're using the correct suffix:
```python
# Wrong
ema_slope = indicators.get("ema_slope")  # Missing _4h suffix

# Correct
ema_slope = indicators.get("ema_slope_50_4h")
```

### "Regime never triggers"
Check your thresholds and data:
```python
# Add debugging
print(f"ema_slope_4h: {indicators.get('ema_slope_50_4h')}")
print(f"trend_consistency_4h: {indicators.get('trend_consistency_4h')}")
```

### "Too few trades"
MTF strategies are naturally more selective. Try:
- Relaxing entry conditions
- Adding more entry patterns
- Using shorter regime timeframe

## Migration Guide: Single → Multi-Timeframe

1. Add `REQUIRED_TIMEFRAMES` class attribute
2. Update indicator access to use suffixes
3. Add regime classification logic
4. Test with unit tests first
5. Verify backward compatibility

See `src/strategy/mtf_template.py` for a complete example.

## Further Reading

- Template: `src/strategy/mtf_template.py`
- Tests: `tests/test_mtf_join.py`, `tests/test_mtf_integration.py`
- Infrastructure: `src/features/reader.py` (`_join_timeframes`, `fetch_multi_timeframe`)
- Base class: `src/strategy/base.py`
