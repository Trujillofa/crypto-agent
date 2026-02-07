-- Migration: 003_add_portfolio_tables
-- Description: Add tables for position tracking and trade history
-- Date: 2024-02-07

-- Create positions table for tracking open/closed positions
CREATE TABLE IF NOT EXISTS positions (
    id SERIAL PRIMARY KEY,
    symbol TEXT NOT NULL,
    entry_time TIMESTAMPTZ NOT NULL,
    entry_price DOUBLE PRECISION NOT NULL,
    quantity DOUBLE PRECISION NOT NULL,
    status TEXT NOT NULL DEFAULT 'open',
    exit_time TIMESTAMPTZ,
    exit_price DOUBLE PRECISION,
    realized_pnl DOUBLE PRECISION
);

-- Create trades table for trade history
CREATE TABLE IF NOT EXISTS trades (
    id SERIAL PRIMARY KEY,
    time TIMESTAMPTZ NOT NULL,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL,
    quantity DOUBLE PRECISION NOT NULL,
    price DOUBLE PRECISION NOT NULL,
    order_id TEXT,
    pnl DOUBLE PRECISION,
    position_id INTEGER REFERENCES positions(id)
);

-- Create indexes for common queries
CREATE INDEX IF NOT EXISTS idx_positions_symbol ON positions (symbol);
CREATE INDEX IF NOT EXISTS idx_positions_status ON positions (status);
CREATE INDEX IF NOT EXISTS idx_trades_symbol ON trades (symbol);
CREATE INDEX IF NOT EXISTS idx_trades_time ON trades (time DESC);
CREATE INDEX IF NOT EXISTS idx_trades_position_id ON trades (position_id);

-- Add comments
COMMENT ON TABLE positions IS 'Trading positions (open and closed)';
COMMENT ON TABLE trades IS 'Trade execution history';
