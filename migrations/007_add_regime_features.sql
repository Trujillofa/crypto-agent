-- Migration: Add regime features to indicators table
-- Created: 2026-03-17

-- Add new regime feature columns to indicators table
ALTER TABLE indicators
    ADD COLUMN IF NOT EXISTS ema_slope_50 DOUBLE PRECISION,
    ADD COLUMN IF NOT EXISTS volatility_percentile DOUBLE PRECISION,
    ADD COLUMN IF NOT EXISTS atr_percentile DOUBLE PRECISION,
    ADD COLUMN IF NOT EXISTS volume_regime DOUBLE PRECISION,
    ADD COLUMN IF NOT EXISTS price_vs_weekly DOUBLE PRECISION,
    ADD COLUMN IF NOT EXISTS price_vs_monthly DOUBLE PRECISION,
    ADD COLUMN IF NOT EXISTS rsi_slope DOUBLE PRECISION,
    ADD COLUMN IF NOT EXISTS trend_consistency DOUBLE PRECISION;

-- Create indexes for efficient querying of new regime features
CREATE INDEX IF NOT EXISTS idx_indicators_ema_slope_50 
    ON indicators (symbol, timeframe, ema_slope_50) 
    WHERE ema_slope_50 IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_indicators_volatility_pct 
    ON indicators (symbol, timeframe, volatility_percentile) 
    WHERE volatility_percentile IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_indicators_trend_consistency 
    ON indicators (symbol, timeframe, trend_consistency) 
    WHERE trend_consistency IS NOT NULL;
