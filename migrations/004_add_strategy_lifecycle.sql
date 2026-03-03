-- Migration: 004_add_strategy_lifecycle
-- Description: Add tables for strategy lifecycle governance and promotion gates
-- Date: 2026-02-16

-- This migration must work both on fresh databases and on environments where
-- lifecycle tables were created manually before schema_migrations tracking was
-- initialized. Build the tables first, then add missing columns/constraints.

CREATE TABLE IF NOT EXISTS strategy_versions (
    id SERIAL PRIMARY KEY,
    name TEXT NOT NULL,
    version TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'candidate',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    promoted_at TIMESTAMPTZ,
    promoted_by TEXT,
    backtest_id INTEGER,
    config JSONB NOT NULL DEFAULT '{}',
    backtest_sharpe REAL,
    backtest_win_rate REAL,
    backtest_max_drawdown REAL,
    backtest_total_trades INTEGER,
    notes TEXT,
    UNIQUE(name, version)
);

CREATE TABLE IF NOT EXISTS strategy_backtests (
    id SERIAL PRIMARY KEY,
    strategy_version_id INTEGER,
    started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at TIMESTAMPTZ,
    symbol TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    start_date DATE NOT NULL,
    end_date DATE NOT NULL,
    initial_capital REAL NOT NULL DEFAULT 10000.0,
    fee_rate REAL NOT NULL DEFAULT 0.001,
    status TEXT NOT NULL DEFAULT 'running',
    total_trades INTEGER,
    win_rate REAL,
    total_return_pct REAL,
    max_drawdown_pct REAL,
    sharpe_ratio REAL,
    error_message TEXT,
    results JSONB
);

CREATE TABLE IF NOT EXISTS strategy_experiments (
    id SERIAL PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT,
    strategy_name TEXT NOT NULL,
    parameter_grid JSONB NOT NULL DEFAULT '{}',
    status TEXT NOT NULL DEFAULT 'pending',
    started_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    best_parameters JSONB,
    best_sharpe REAL,
    notes TEXT
);

ALTER TABLE strategy_versions
    ADD COLUMN IF NOT EXISTS status TEXT NOT NULL DEFAULT 'candidate',
    ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    ADD COLUMN IF NOT EXISTS promoted_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS promoted_by TEXT,
    ADD COLUMN IF NOT EXISTS backtest_id INTEGER,
    ADD COLUMN IF NOT EXISTS config JSONB NOT NULL DEFAULT '{}',
    ADD COLUMN IF NOT EXISTS backtest_sharpe REAL,
    ADD COLUMN IF NOT EXISTS backtest_win_rate REAL,
    ADD COLUMN IF NOT EXISTS backtest_max_drawdown REAL,
    ADD COLUMN IF NOT EXISTS backtest_total_trades INTEGER,
    ADD COLUMN IF NOT EXISTS notes TEXT;

ALTER TABLE strategy_backtests
    ADD COLUMN IF NOT EXISTS strategy_version_id INTEGER,
    ADD COLUMN IF NOT EXISTS started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    ADD COLUMN IF NOT EXISTS completed_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS symbol TEXT,
    ADD COLUMN IF NOT EXISTS timeframe TEXT,
    ADD COLUMN IF NOT EXISTS start_date DATE,
    ADD COLUMN IF NOT EXISTS end_date DATE,
    ADD COLUMN IF NOT EXISTS initial_capital REAL NOT NULL DEFAULT 10000.0,
    ADD COLUMN IF NOT EXISTS fee_rate REAL NOT NULL DEFAULT 0.001,
    ADD COLUMN IF NOT EXISTS status TEXT NOT NULL DEFAULT 'running',
    ADD COLUMN IF NOT EXISTS total_trades INTEGER,
    ADD COLUMN IF NOT EXISTS win_rate REAL,
    ADD COLUMN IF NOT EXISTS total_return_pct REAL,
    ADD COLUMN IF NOT EXISTS max_drawdown_pct REAL,
    ADD COLUMN IF NOT EXISTS sharpe_ratio REAL,
    ADD COLUMN IF NOT EXISTS error_message TEXT,
    ADD COLUMN IF NOT EXISTS results JSONB;

ALTER TABLE strategy_experiments
    ADD COLUMN IF NOT EXISTS description TEXT,
    ADD COLUMN IF NOT EXISTS strategy_name TEXT,
    ADD COLUMN IF NOT EXISTS parameter_grid JSONB NOT NULL DEFAULT '{}',
    ADD COLUMN IF NOT EXISTS status TEXT NOT NULL DEFAULT 'pending',
    ADD COLUMN IF NOT EXISTS started_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS completed_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS best_parameters JSONB,
    ADD COLUMN IF NOT EXISTS best_sharpe REAL,
    ADD COLUMN IF NOT EXISTS notes TEXT;

-- Backfill strategy_version_id when older columns are present.
DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name = 'strategy_backtests'
          AND column_name = 'strategy_name'
    ) AND EXISTS (
        SELECT 1
        FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name = 'strategy_backtests'
          AND column_name = 'strategy_version'
    ) THEN
        UPDATE strategy_backtests AS sb
        SET strategy_version_id = sv.id
        FROM strategy_versions AS sv
        WHERE sb.strategy_version_id IS NULL
          AND sb.strategy_name = sv.name
          AND sb.strategy_version = sv.version;
    END IF;
END $$;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'chk_strategy_versions_status'
    ) THEN
        ALTER TABLE strategy_versions
            ADD CONSTRAINT chk_strategy_versions_status
            CHECK (status IN ('candidate', 'prototype', 'validated', 'paper', 'live', 'retired'));
    END IF;
END $$;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'chk_strategy_backtests_status'
    ) THEN
        ALTER TABLE strategy_backtests
            ADD CONSTRAINT chk_strategy_backtests_status
            CHECK (status IN ('running', 'completed', 'failed'));
    END IF;
END $$;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'chk_strategy_experiments_status'
    ) THEN
        ALTER TABLE strategy_experiments
            ADD CONSTRAINT chk_strategy_experiments_status
            CHECK (status IN ('pending', 'running', 'completed', 'failed'));
    END IF;
END $$;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'fk_strategy_backtests_strategy_version_id'
    ) THEN
        ALTER TABLE strategy_backtests
            ADD CONSTRAINT fk_strategy_backtests_strategy_version_id
            FOREIGN KEY (strategy_version_id)
            REFERENCES strategy_versions(id)
            ON DELETE CASCADE;
    END IF;
END $$;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'fk_strategy_versions_backtest_id'
    ) THEN
        ALTER TABLE strategy_versions
            ADD CONSTRAINT fk_strategy_versions_backtest_id
            FOREIGN KEY (backtest_id)
            REFERENCES strategy_backtests(id);
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_strategy_versions_name ON strategy_versions(name);
CREATE INDEX IF NOT EXISTS idx_strategy_versions_status ON strategy_versions(status);
CREATE INDEX IF NOT EXISTS idx_strategy_backtests_version ON strategy_backtests(strategy_version_id);
CREATE INDEX IF NOT EXISTS idx_strategy_backtests_symbol ON strategy_backtests(symbol);
CREATE INDEX IF NOT EXISTS idx_strategy_experiments_status ON strategy_experiments(status);

COMMENT ON TABLE strategy_versions IS 'Strategy lifecycle: candidate -> prototype -> validated -> paper -> live -> retired';
COMMENT ON TABLE strategy_backtests IS 'Historical backtest runs for strategy versions';
COMMENT ON TABLE strategy_experiments IS 'Parameter optimization experiments';
