-- Migration: 000_migrations_tracking
-- Description: Create migrations tracking table
-- This migration is always run first to enable tracking

CREATE TABLE IF NOT EXISTS schema_migrations (
    version TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    applied_at TIMESTAMPTZ DEFAULT NOW(),
    checksum TEXT
);

COMMENT ON TABLE schema_migrations IS 'Tracks applied database migrations';
