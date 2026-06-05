-- Migration: 010_add_perp_basis_metrics
-- Description: Historical mark/index/premium index bars for perp basis research
-- Created: 2026-06-05

CREATE TABLE IF NOT EXISTS perp_basis_metrics (
    time TIMESTAMPTZ NOT NULL,
    close_time TIMESTAMPTZ NOT NULL,
    exchange TEXT NOT NULL DEFAULT 'binance_usdm',
    symbol TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    mark_price DOUBLE PRECISION NOT NULL,
    index_price DOUBLE PRECISION NOT NULL,
    premium_index DOUBLE PRECISION NOT NULL,
    basis_bps DOUBLE PRECISION NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (time, exchange, symbol, timeframe)
);

SELECT create_hypertable('perp_basis_metrics', 'time', if_not_exists => TRUE);

CREATE INDEX IF NOT EXISTS idx_perp_basis_metrics_symbol_time
    ON perp_basis_metrics (exchange, symbol, timeframe, time DESC);

CREATE INDEX IF NOT EXISTS idx_perp_basis_metrics_symbol_tf
    ON perp_basis_metrics (symbol, timeframe, time DESC);

COMMENT ON TABLE perp_basis_metrics IS
    'Perp mark/index/premium index bars (Binance USDT-M v0) for basis and crowding research';
COMMENT ON COLUMN perp_basis_metrics.premium_index IS
    'Binance premium index kline close (exchange-native perp premium)';
COMMENT ON COLUMN perp_basis_metrics.basis_bps IS
    'Computed basis in bps: (mark_price - index_price) / index_price * 10000';
