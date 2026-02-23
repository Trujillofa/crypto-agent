-- Migration: 004_add_strategy_lifecycle
-- Description: Add tables for strategy lifecycle governance and promotion gates
-- Date: 2026-02-16

-- Strategy versions table: tracks each version of a strategy
CREATE TABLE IF NOT EXISTS strategy_versions (
    id SERIAL PRIMARY KEY,
    name TEXT NOT NULL,
    version TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'candidate' CHECK (status IN ('candidate', 'prototype', 'validated', 'paper', 'live', 'retired')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    promoted_at TIMESTAMPTZ,
    promoted_by TEXT,
    backtest_id INTEGER REFERENCES strategy_backtests(id),
    
    -- Strategy configuration snapshot
    config JSONB NOT NULL DEFAULT '{}',
    
    -- Performance metrics at promotion time
    backtest_sharpe REAL,
    backtest_win_rate REAL,
    backtest_max_drawdown REAL,
    backtest_total_trades INTEGER,
    
    -- Notes
    notes TEXT,
    
    UNIQUE(name, version)
);

-- Strategy backtests table: stores backtest results for each version
CREATE TABLE IF NOT EXISTS strategy_backtests (
    id SERIAL PRIMARY KEY,
    strategy_version_id INTEGER REFERENCES strategy_versions(id) ON DELETE CASCADE,
    started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at TIMESTAMPTZ,
    
    -- Backtest parameters
    symbol TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    start_date DATE NOT NULL,
    end_date DATE NOT NULL,
    initial_capital REAL NOT NULL DEFAULT 10000.0,
    fee_rate REAL NOT NULL DEFAULT 0.001,
    
    -- Results
    status TEXT NOT NULL DEFAULT 'running' CHECK (status IN ('running', 'completed', 'failed')),
    total_trades INTEGER,
    win_rate REAL,
    total_return_pct REAL,
    max_drawdown_pct REAL,
    sharpe_ratio REAL,
    
    -- Error info
    error_message TEXT,
    
    -- Raw results JSON
    results JSONB
);

-- Strategy experiments table: tracks parameter sweeps and optimization runs
CREATE TABLE IF NOT EXISTS strategy_experiments (
    id SERIAL PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT,
    
    -- Experiment parameters
    strategy_name TEXT NOT NULL,
    parameter_grid JSONB NOT NULL DEFAULT '{}',
    
    -- Status
    status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'running', 'completed', 'failed')),
    started_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    
    -- Best result from grid search
    best_parameters JSONB,
    best_sharpe REAL,
    
    -- Notes
    notes TEXT
);

-- Indexes for common queries
CREATE INDEX IF NOT EXISTS idx_strategy_versions_name ON strategy_versions(name);
CREATE INDEX IF NOT EXISTS idx_strategy_versions_status ON strategy_versions(status);
CREATE INDEX IF NOT EXISTS idx_strategy_backtests_version ON strategy_backtests(strategy_version_id);
CREATE INDEX IF NOT EXISTS idx_strategy_backtests_symbol ON strategy_backtests(symbol);
CREATE INDEX IF NOT EXISTS idx_strategy_experiments_status ON strategy_experiments(status);

-- Comments
COMMENT ON TABLE strategy_versions IS 'Strategy lifecycle: candidate → prototype → validated → paper → live → retired';
COMMENT ON TABLE strategy_backtests IS 'Historical backtest runs for strategy versions';
COMMENT ON TABLE strategy_experiments IS 'Parameter optimization experiments';
