# Technical Indicators Pipeline

This document describes the technical indicators pipeline that computes and stores technical analysis indicators from OHLCV data.

## Overview

The indicators pipeline:
1. **Reads** OHLCV data from TimescaleDB
2. **Computes** technical indicators (RSI, MACD, Bollinger Bands, ATR, EMA, SMA, VWAP, Stochastic, CCI)
3. **Stores** indicators in TimescaleDB for ML/RL model inputs and trading signals
4. **Exports** Prometheus metrics for monitoring

## Architecture

```
OHLCV Data (TimescaleDB)
    ↓
IndicatorComputer (periodic: 60s)
    ↓
compute_indicators() → TechnicalIndicators
    ↓
IndicatorWriter
    ↓
Indicators Table (TimescaleDB)
    ↓
Grafana Dashboard + ML/RL Models
```

## Implemented Indicators

### Trend Indicators
- **RSI** (Relative Strength Index): Periods 7, 14
  - Overbought: >70, Oversold: <30
- **MACD** (Moving Average Convergence Divergence)
  - MACD line, Signal line (9-period EMA), Histogram
- **EMA** (Exponential Moving Average): Periods 12, 26, 50, 200
- **SMA** (Simple Moving Average): Periods 20, 50, 200

### Volatility Indicators
- **ATR** (Average True Range): Period 14
  - Absolute ATR value and percentage of price
- **Bollinger Bands**: 20-period, 2 standard deviations
  - Distance from upper and lower bands as percentage of price

### Momentum Indicators
- **Stochastic Oscillator**: %K (14-period), %D (3-period SMA of %K)
- **CCI** (Commodity Channel Index): Period 20

### Volume-Based Indicators
- **VWAP** (Volume Weighted Average Price)

## File Structure

```
src/features/
├── technical.py       # Indicator computation functions
├── writer.py         # Database writer for indicators
├── computer.py       # Periodic indicator computation loop
├── metrics.py        # Prometheus metrics
└── __init__.py      # Module exports

schema/
└── indicators.sql    # TimescaleDB schema for indicators table

config/grafana/dashboards/
└── indicators.json   # Grafana dashboard for visualization
```

## Configuration

### Indicator Computation Settings

In `src/main.py`, the `IndicatorComputer` is initialized with:

```python
indicator_computer = IndicatorComputer(
    config=settings.database,
    symbols=settings.trading_pairs,  # From settings.yaml
    timeframe=settings.timeframe,       # From settings.yaml
    writer=indicator_writer,
    metrics=indicator_metrics,
    compute_interval=60,  # Compute every 60 seconds
)
```

### Computation Interval

The indicator pipeline runs every 60 seconds by default. This can be adjusted in `src/main.py`:

```python
compute_interval=120,  # Compute every 2 minutes
```

### Data Requirements

Minimum data points required for each indicator:
- **RSI**: 30+ periods
- **MACD**: 26+ periods
- **Bollinger Bands**: 20+ periods
- **ATR**: 14+ periods
- **EMA 12**: 12+ periods
- **EMA 50**: 50+ periods
- **EMA 200**: 200+ periods
- **SMA**: Same as EMA periods
- **Stochastic**: 14+ periods
- **CCI**: 20+ periods
- **VWAP**: 1+ period

The pipeline reads 200 periods of OHLCV data to ensure all indicators can be computed.

## Database Schema

### Indicators Table

```sql
CREATE TABLE indicators (
    time TIMESTAMPTZ NOT NULL,
    symbol TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    rsi_14 DOUBLE PRECISION,
    rsi_7 DOUBLE PRECISION,
    macd DOUBLE PRECISION,
    macd_signal DOUBLE PRECISION,
    macd_hist DOUBLE PRECISION,
    bb_upper_dist DOUBLE PRECISION,
    bb_lower_dist DOUBLE PRECISION,
    atr_14 DOUBLE PRECISION,
    atr_pct DOUBLE PRECISION,
    ema_12 DOUBLE PRECISION,
    ema_26 DOUBLE PRECISION,
    ema_50 DOUBLE PRECISION,
    ema_200 DOUBLE PRECISION,
    sma_20 DOUBLE PRECISION,
    sma_50 DOUBLE PRECISION,
    sma_200 DOUBLE PRECISION,
    vwap DOUBLE PRECISION,
    stoch_k DOUBLE PRECISION,
    stoch_d DOUBLE PRECISION,
    cci DOUBLE PRECISION,
    PRIMARY KEY (time, symbol, timeframe)
);
```

### Hypertable

The `indicators` table is configured as a TimescaleDB hypertable partitioned by `time`:

```sql
SELECT create_hypertable('indicators', 'time', if_not_exists => TRUE);
```

This enables:
- Efficient time-series queries
- Automatic data retention policies
- Compression for older data

## Prometheus Metrics

The pipeline exports the following metrics:

### Counters
- `indicator_computations_total{symbol, status}` - Total computations (success/error)
- `indicator_computation_errors_total{symbol, error_type}` - Error counts
- `indicator_writes_total{symbol, status}` - Database write counts

### Gauges
- `indicator_rsi{symbol, period}` - Latest RSI values
- `indicator_macd{symbol, component}` - Latest MACD values (macd/signal/hist)
- `indicator_atr{symbol, period}` - Latest ATR values
- `indicator_last_computation_timestamp_seconds{symbol}` - Last successful computation time
- `indicator_computation_running` - Whether computation loop is running

### Histograms
- `indicator_computation_latency_seconds{symbol}` - Computation time distribution
- `indicator_write_latency_seconds{symbol}` - Write latency distribution

### Summary
- `indicator_computation_duration_seconds{symbol}` - Computation duration summary

Access metrics: `http://localhost:8000/metrics`

## Grafana Dashboard

A dedicated dashboard for indicators is provided at:
- **Dashboard**: "Crypto Trading Agent - Technical Indicators"
- **URL**: `/d/crypto-trading-indicators` (after provisioning)
- **Refresh**: 30 seconds

### Dashboard Panels

1. **RSI - Relative Strength Index**
   - BTC RSI 14, RSI 7
   - ETH RSI 14, RSI 7
   - Thresholds: 70 (overbought), 30 (oversold)

2. **MACD - BTC**
   - MACD line, Signal line, Histogram
   - Crossovers indicate buy/sell signals

3. **ATR - Average True Range**
   - ATR 14, ATR %
   - Volatility measure for stop-loss placement

4. **Bollinger Bands - Distance**
   - Upper distance, Lower distance
   - Price position relative to bands

5. **Moving Averages**
   - EMA 12, EMA 26, EMA 50, EMA 200
   - SMA 20, SMA 50, SMA 200
   - Trend identification and support/resistance

6. **Stochastic Oscillator**
   - %K, %D
   - Overbought (>80), Oversold (<20)

7. **Commodity Channel Index**
   - CCI
   - Overbought (>100), Oversold (<-100)

## Usage

### Starting the Pipeline

The indicator pipeline starts automatically with the main agent:

```bash
cd /home/yderf/TRADING/crypto-agent
docker-compose up --build
```

Or manually:

```python
python -m src.main
```

### Manual Indicator Computation

For testing or one-time computation:

```python
from src.features import IndicatorComputer, IndicatorWriter, IndicatorMetrics
from src.ingest.metrics import IngestMetrics

config = {"host": "localhost", "port": 5432, "name": "marketdata", "user": "trading", "password": "change_me"}
writer = IndicatorWriter(config)
metrics = IndicatorMetrics()
computer = IndicatorComputer(config, ["BTCUSDT", "ETHUSDT"], "1m", writer, metrics)

async def main():
    async with writer:
        await computer._compute_all_symbols()

asyncio.run(main())
```

### Querying Indicators

#### Via SQL
```sql
-- Latest indicators for BTC
SELECT * FROM latest_indicators WHERE symbol = 'BTCUSDT';

-- RSI history
SELECT time, rsi_14, rsi_7
FROM indicators
WHERE symbol = 'BTCUSDT'
ORDER BY time DESC
LIMIT 100;

-- MACD crossover (MACD > Signal)
SELECT time, macd, macd_signal
FROM indicators
WHERE symbol = 'BTCUSDT'
  AND macd > macd_signal
  AND time >= NOW() - INTERVAL '1 hour'
ORDER BY time DESC;
```

#### Via Grafana
- Open indicators dashboard: `http://localhost:3001/d/crypto-trading-indicators`
- Queries are pre-configured for all indicators
- Time range can be adjusted

## ML/RL Integration

Indicators can be retrieved for model training/inference:

### Training Data
```python
import pandas as pd
import pg8000

conn = pg8000.connect(...)
df = pd.read_sql(
    "SELECT * FROM indicators WHERE symbol = 'BTCUSDT' AND time >= NOW() - INTERVAL '30 days'",
    conn
)

# Feature engineering
features = df[['rsi_14', 'macd', 'atr_pct', 'ema_50']]
```

### Real-time Features
```python
# Fetch latest indicators for prediction
latest = pd.read_sql(
    "SELECT * FROM latest_indicators",
    conn
)

# Use features for model prediction
prediction = model.predict(latest[['rsi_14', 'macd', 'atr_pct']])
```

## Troubleshooting

### No Indicators Computed

1. **Check OHLCV data**:
   ```sql
   SELECT COUNT(*) FROM ohlcv WHERE symbol = 'BTCUSDT';
   ```

2. **Check indicator computation status**:
   ```bash
   curl http://localhost:8000/metrics | grep indicator_computation_running
   ```

3. **Check logs**:
   ```bash
   docker-compose logs agent | grep -i indicator
   ```

### Indicators Not Updating

1. **Check computation interval** - Default is 60 seconds
2. **Check if computer is running** - Look at `indicator_computation_running` metric
3. **Check for errors** - Look at `indicator_computation_errors_total` metric

### High Computation Latency

1. **Check database connection** - TimescaleDB should be on localhost
2. **Check data volume** - Reading 200 periods may be slow with large data
3. **Adjust computation interval** - Increase if 60s is too frequent

## Performance Considerations

### Database Optimization

The `indicators` table is a TimescaleDB hypertable for:
- Efficient time-series queries
- Automatic partitioning by time
- Compression for older data

### Computation Optimization

- Reads 200 periods at once (caches all indicators)
- Uses vectorized operations where possible
- Computes all indicators in a single pass

### Metrics Overhead

- Minimal overhead (few metric updates per computation)
- All operations are async to avoid blocking
- Separate metrics for each symbol

## Future Enhancements

- [ ] Support for custom indicator periods
- [ ] Multi-timeframe indicators (1m, 5m, 15m, 1h, 4h, 1d)
- [ ] Indicator alerts (RSI crossovers, MACD signals)
- [ ] Real-time indicator streaming via WebSocket
- [ ] Backtest strategies using historical indicators
- [ ] ML model training pipeline integration

## References

- [RSI Calculation](https://www.investopedia.com/terms/r/rsi.asp)
- [MACD Strategy](https://www.investopedia.com/terms/m/macd.asp)
- [Bollinger Bands](https://www.investopedia.com/terms/b/bollingerbands.asp)
- [ATR](https://www.investopedia.com/terms/a/atr.asp)
- [TimescaleDB Documentation](https://docs.timescale.com/)
- [Prometheus Metrics](https://prometheus.io/docs/practices/naming/)
