-- Migration: 005_add_agent_isolation
-- Description: Add agent_id columns for multi-agent isolation
-- Date: 2026-02-27

-- Add agent_id column to positions table
ALTER TABLE positions 
ADD COLUMN IF NOT EXISTS agent_id TEXT DEFAULT 'default';

-- Add agent_id column to trades table
ALTER TABLE trades 
ADD COLUMN IF NOT EXISTS agent_id TEXT DEFAULT 'default';

-- Backfill existing rows with 'default' agent_id
UPDATE positions SET agent_id = 'default' WHERE agent_id IS NULL;
UPDATE trades SET agent_id = 'default' WHERE agent_id IS NULL;

-- Create composite indexes for efficient per-agent queries
CREATE INDEX IF NOT EXISTS idx_positions_agent_symbol 
ON positions (agent_id, symbol);

CREATE INDEX IF NOT EXISTS idx_positions_agent_status 
ON positions (agent_id, status);

CREATE INDEX IF NOT EXISTS idx_trades_agent_symbol 
ON trades (agent_id, symbol);

CREATE INDEX IF NOT EXISTS idx_trades_agent_time 
ON trades (agent_id, time DESC);

-- Add comments
COMMENT ON COLUMN positions.agent_id IS 'Agent identifier for multi-agent isolation (default, agent2, etc.)';
COMMENT ON COLUMN trades.agent_id IS 'Agent identifier for multi-agent isolation (default, agent2, etc.)';
