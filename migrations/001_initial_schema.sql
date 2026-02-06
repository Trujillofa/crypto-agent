-- Migration: 001_initial_schema
-- Description: Create initial OHLCV table with TimescaleDB hypertable
-- Date: 2024-01-01
-- Author: crypto-agent

-- Create OHLCV table for storing candlestick data
CREATE TABLE IF NOT EXISTS ohlcv (
    time TIMESTAMPTZ NOT NULL,
    close_time TIMESTAMPTZ NOT NULL,
    symbol TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    open_price DOUBLE PRECISION NOT NULL,
    high_price DOUBLE PRECISION NOT NULL,
    low_price DOUBLE PRECISION NOT NULL,
    close_price DOUBLE PRECISION NOT NULL,
    volume DOUBLE PRECISION NOT NULL,
    PRIMARY KEY (time, symbol, timeframe)
);

-- Convert to TimescaleDB hypertable for time-series optimization
-- This enables automatic partitioning by time for better query performance
SELECT create_hypertable('ohlcv', 'time', if_not_exists => TRUE);

-- Create index for common queries
CREATE INDEX IF NOT EXISTS idx_ohlcv_symbol_time ON ohlcv (symbol, time DESC);
CREATE INDEX IF NOT EXISTS idx_ohlcv_timeframe ON ohlcv (timeframe, time DESC);

-- Add comment for documentation
COMMENT ON TABLE ohlcv IS 'OHLCV candlestick data from Binance Futures';
COMMENT ON COLUMN ohlcv.time IS 'Candle open time (UTC)';
COMMENT ON COLUMN ohlcv.close_time IS 'Candle close time (UTC)';
COMMENT ON COLUMN ohlcv.symbol IS 'Trading pair symbol (e.g., BTCUSDT)';
COMMENT ON COLUMN ohlcv.timeframe IS 'Candle timeframe (1m, 5m, 15m, 1h, 4h)';
COMMENT ON COLUMN ohlcv.open_price IS 'Opening price';
COMMENT ON COLUMN ohlcv.high_price IS 'Highest price during period';
COMMENT ON COLUMN ohlcv.low_price IS 'Lowest price during period';
COMMENT ON COLUMN ohlcv.close_price IS 'Closing price';
COMMENT ON COLUMN ohlcv.volume IS 'Trading volume in base currency';
