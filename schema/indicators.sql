-- TimescaleDB Schema for Technical Indicators
-- This script creates the indicators table and hypertable for efficient time-series storage

-- Drop existing table if it exists (for development)
DROP TABLE IF EXISTS indicators CASCADE;

-- Create indicators table
CREATE TABLE IF NOT EXISTS indicators (
    time TIMESTAMPTZ NOT NULL,
    symbol TEXT NOT NULL,
    timeframe TEXT NOT NULL,

    -- RSI (Relative Strength Index)
    rsi_14 DOUBLE PRECISION,
    rsi_7 DOUBLE PRECISION,

    -- MACD (Moving Average Convergence Divergence)
    macd DOUBLE PRECISION,
    macd_signal DOUBLE PRECISION,
    macd_hist DOUBLE PRECISION,

    -- Bollinger Bands (distance from upper/lower bands as percentage)
    bb_upper_dist DOUBLE PRECISION,
    bb_lower_dist DOUBLE PRECISION,

    -- ATR (Average True Range)
    atr_14 DOUBLE PRECISION,
    atr_pct DOUBLE PRECISION,

    -- EMA (Exponential Moving Average) - multiple periods
    ema_12 DOUBLE PRECISION,
    ema_26 DOUBLE PRECISION,
    ema_50 DOUBLE PRECISION,
    ema_200 DOUBLE PRECISION,

    -- SMA (Simple Moving Average) - multiple periods
    sma_20 DOUBLE PRECISION,
    sma_50 DOUBLE PRECISION,
    sma_200 DOUBLE PRECISION,

    -- VWAP (Volume Weighted Average Price)
    vwap DOUBLE PRECISION,

    -- Stochastic Oscillator
    stoch_k DOUBLE PRECISION,
    stoch_d DOUBLE PRECISION,

    -- Commodity Channel Index
    cci DOUBLE PRECISION,

    PRIMARY KEY (time, symbol, timeframe)
);

-- Create hypertable for efficient time-series queries
SELECT create_hypertable('indicators', 'time', if_not_exists => TRUE);

-- Create indexes for common query patterns
CREATE INDEX IF NOT EXISTS idx_indicators_symbol_time ON indicators (symbol, time DESC);
CREATE INDEX IF NOT EXISTS idx_indicators_timeframe ON indicators (timeframe);
CREATE INDEX IF NOT EXISTS idx_indicators_symbol_timeframe ON indicators (symbol, timeframe, time DESC);

-- Add comments for documentation
COMMENT ON TABLE indicators IS 'Technical indicators computed from OHLCV data for trading signals and ML/RL model inputs';
COMMENT ON COLUMN indicators.time IS 'Timestamp of the OHLCV candle these indicators are computed from';
COMMENT ON COLUMN indicators.symbol IS 'Trading pair symbol (e.g., BTCUSDT, ETHUSDT)';
COMMENT ON COLUMN indicators.timeframe IS 'Timeframe of the OHLCV candle (e.g., 1m, 5m, 15m, 1h, 4h, 1d)';
COMMENT ON COLUMN indicators.rsi_14 IS 'RSI with 14-period - overbought >70, oversold <30';
COMMENT ON COLUMN indicators.rsi_7 IS 'RSI with 7-period - more sensitive short-term indicator';
COMMENT ON COLUMN indicators.macd IS 'MACD line (12-period EMA - 26-period EMA)';
COMMENT ON COLUMN indicators.macd_signal IS 'MACD signal line (9-period EMA of MACD)';
COMMENT ON COLUMN indicators.macd_hist IS 'MACD histogram (MACD - signal line)';
COMMENT ON COLUMN indicators.bb_upper_dist IS 'Distance from upper Bollinger band as % of price (positive = below band)';
COMMENT ON COLUMN indicators.bb_lower_dist IS 'Distance from lower Bollinger band as % of price (positive = above band)';
COMMENT ON COLUMN indicators.atr_14 IS 'Average True Range with 14-period - volatility measure';
COMMENT ON COLUMN indicators.atr_pct IS 'ATR as percentage of price - relative volatility';
COMMENT ON COLUMN indicators.ema_12 IS '12-period Exponential Moving Average';
COMMENT ON COLUMN indicators.ema_26 IS '26-period Exponential Moving Average';
COMMENT ON COLUMN indicators.ema_50 IS '50-period Exponential Moving Average';
COMMENT ON COLUMN indicators.ema_200 IS '200-period Exponential Moving Average';
COMMENT ON COLUMN indicators.sma_20 IS '20-period Simple Moving Average';
COMMENT ON COLUMN indicators.sma_50 IS '50-period Simple Moving Average';
COMMENT ON COLUMN indicators.sma_200 IS '200-period Simple Moving Average';
COMMENT ON COLUMN indicators.vwap IS 'Volume Weighted Average Price';
COMMENT ON COLUMN indicators.stoch_k IS 'Stochastic %K line (14-period)';
COMMENT ON COLUMN indicators.stoch_d IS 'Stochastic %D line (3-period SMA of %K)';
COMMENT ON COLUMN indicators.cci IS 'Commodity Channel Index (20-period)';

-- Create a view for latest indicators per symbol
CREATE OR REPLACE VIEW latest_indicators AS
SELECT DISTINCT ON (symbol, timeframe)
    symbol,
    timeframe,
    time,
    rsi_14,
    rsi_7,
    macd,
    macd_signal,
    macd_hist,
    bb_upper_dist,
    bb_lower_dist,
    atr_14,
    atr_pct,
    ema_12,
    ema_26,
    ema_50,
    ema_200,
    sma_20,
    sma_50,
    sma_200,
    vwap,
    stoch_k,
    stoch_d,
    cci
FROM indicators
ORDER BY symbol, timeframe, time DESC;

COMMENT ON VIEW latest_indicators IS 'Latest computed indicators for each symbol and timeframe';
