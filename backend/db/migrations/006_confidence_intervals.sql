-- Migration 006: Add confidence interval columns to forecast_rows
-- Note: forecasts are currently stored as JSON blobs in session_store.
-- This migration is a forward-compatibility stub for when forecast_rows is introduced.

ALTER TABLE forecast_rows ADD COLUMN IF NOT EXISTS p10 FLOAT;
ALTER TABLE forecast_rows ADD COLUMN IF NOT EXISTS p90 FLOAT;
