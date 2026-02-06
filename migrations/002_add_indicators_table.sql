-- Migration: 002_add_indicators_table
-- Description: Add table for storing computed technical indicators
-- Date: 2024-01-01
-- Author: crypto-agent

-- Create indicators table for storing computed technical indicators
CREATE TABLE IF NOT EXISTS indicators (
    time TIMESTAMPTZ NOT NULL,
    symbol TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    -- RSI indicators
    rsi_14 DOUBLE PRECISION,
    rsi_7 DOUBLE PRECISION,
    -- MACD indicators
    macd DOUBLE PRECISION,
    macd_signal DOUBLE PRECISION,
    macd_hist DOUBLE PRECISION,
    -- Bollinger Bands
    bb_upper_dist DOUBLE PRECISION,
    bb_lower_dist DOUBLE PRECISION,
    -- ATR
    atr_14 DOUBLE PRECISION,
    atr_pct DOUBLE PRECISION,
    -- Metadata
    computed_at TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (time, symbol, timeframe)
);

-- Convert to hypertable
SELECT create_hypertable('indicators', 'time', if_not_exists => TRUE);

-- Create indexes
CREATE INDEX IF NOT EXISTS idx_indicators_symbol_time ON indicators (symbol, time DESC);

-- Add comments
COMMENT ON TABLE indicators IS 'Computed technical indicators for trading signals';
