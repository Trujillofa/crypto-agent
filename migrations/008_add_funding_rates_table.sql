-- Migration: 008_add_funding_rates_table
-- Description: Create table for storing historical funding rates
-- Created: 2026-04-08

CREATE TABLE IF NOT EXISTS funding_rates (
    id SERIAL PRIMARY KEY,
    symbol TEXT NOT NULL,
    funding_time TIMESTAMPTZ NOT NULL,
    funding_rate DOUBLE PRECISION NOT NULL,
    mark_price DOUBLE PRECISION,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(symbol, funding_time)
);

-- Index for efficient querying by symbol and time
CREATE INDEX IF NOT EXISTS idx_funding_rates_symbol_time
    ON funding_rates (symbol, funding_time);

-- Index for efficient querying of recent rates
CREATE INDEX IF NOT EXISTS idx_funding_rates_time
    ON funding_rates (funding_time DESC);

-- Index for getting latest rate per symbol
CREATE INDEX IF NOT EXISTS idx_funding_rates_latest
    ON funding_rates (symbol, funding_time DESC);

COMMENT ON TABLE funding_rates IS 'Historical funding rates for futures perpetual contracts';
COMMENT ON COLUMN funding_rates.funding_rate IS 'Funding rate (e.g., 0.0001 = 0.01%)';
COMMENT ON COLUMN funding_rates.funding_time IS 'UTC timestamp when funding payment occurs (every 8 hours)';
