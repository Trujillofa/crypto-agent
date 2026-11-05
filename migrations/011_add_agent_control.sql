-- Migration: 011_add_agent_control
-- Description: Durable entry brake read by the execution path before every entry
-- Date: 2026-11-05
--
-- The table and its armed row are created by the same migration, executed by
-- scripts/migrate.py inside one transaction, so there is no valid state in which
-- the table exists without a row. That is what lets the reader be strictly
-- fail-closed: "no row" can only mean someone deleted it, which is a fault.
--
-- agent_id = 'all' is the global row; a per-agent row (e.g. 'agent2', 'eth-4h')
-- refines it. The reader takes the most restrictive combination: any disarmed
-- row blocks entries, and any reduce_only row permits exits only.

CREATE TABLE IF NOT EXISTS agent_control (
    agent_id    TEXT PRIMARY KEY,
    armed       BOOLEAN NOT NULL,
    reduce_only BOOLEAN NOT NULL DEFAULT FALSE,
    reason      TEXT NOT NULL,
    set_by      TEXT NOT NULL,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

COMMENT ON TABLE agent_control IS
    'Durable entry brake. Read before every entry order; absent/unreadable means disarmed.';
COMMENT ON COLUMN agent_control.set_by IS
    'Who wrote this row: hermes-coordinator, an operator, or a migration.';

INSERT INTO agent_control (agent_id, armed, reduce_only, reason, set_by)
VALUES ('all', TRUE, FALSE, 'initial install: armed by migration 011', 'migration-011')
ON CONFLICT (agent_id) DO NOTHING;
