-- One-shot ALTER for live Postgres exchanges.
-- create_all does not add columns to existing tables.
-- Run against the exchange DATABASE_URL, then restart a2a-exchange.

ALTER TABLE escrows ADD COLUMN IF NOT EXISTS requester_trust_state VARCHAR(30);
ALTER TABLE escrows ADD COLUMN IF NOT EXISTS provider_trust_state VARCHAR(30);
ALTER TABLE escrows ADD COLUMN IF NOT EXISTS requester_task_count INTEGER;
ALTER TABLE escrows ADD COLUMN IF NOT EXISTS requester_dispute_rate DOUBLE PRECISION;
ALTER TABLE escrows ADD COLUMN IF NOT EXISTS trust_tier_shadow BOOLEAN NOT NULL DEFAULT TRUE;
