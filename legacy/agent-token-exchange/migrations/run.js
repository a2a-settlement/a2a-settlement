require('dotenv').config();
const { pool } = require('../config/database');

const migration = `

-- ============================================
-- AGENT TOKEN EXCHANGE - DATABASE SCHEMA
-- ============================================

-- 1. ACCOUNTS
-- Every registered bot gets an account
CREATE TABLE IF NOT EXISTS accounts (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  bot_name      VARCHAR(255) NOT NULL,
  developer_id  VARCHAR(255) NOT NULL,       -- developer email or identifier
  api_key_hash  VARCHAR(255) NOT NULL,       -- hashed API key for auth
  description   TEXT,                         -- what this bot does
  skills        JSONB DEFAULT '[]',           -- array of skill descriptors
  status        VARCHAR(20) DEFAULT 'active', -- active, suspended, probation
  reputation    DECIMAL(5,4) DEFAULT 0.5000,  -- 0.0000 to 1.0000
  created_at    TIMESTAMPTZ DEFAULT NOW(),
  updated_at    TIMESTAMPTZ DEFAULT NOW()
);

-- 2. BALANCES
-- Current token balance per account
-- Separate from accounts for transactional integrity
CREATE TABLE IF NOT EXISTS balances (
  account_id       UUID PRIMARY KEY REFERENCES accounts(id),
  available        BIGINT NOT NULL DEFAULT 0,  -- tokens free to spend
  held_in_escrow   BIGINT NOT NULL DEFAULT 0,  -- tokens locked in active escrows
  total_earned     BIGINT NOT NULL DEFAULT 0,  -- lifetime earnings (analytics)
  total_spent      BIGINT NOT NULL DEFAULT 0,  -- lifetime spending (analytics)
  updated_at       TIMESTAMPTZ DEFAULT NOW(),
  
  CONSTRAINT positive_available CHECK (available >= 0),
  CONSTRAINT positive_held CHECK (held_in_escrow >= 0)
);

-- 3. ESCROWS
-- Active token holds between two bots
CREATE TABLE IF NOT EXISTS escrows (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  requester_id    UUID NOT NULL REFERENCES accounts(id),
  provider_id     UUID NOT NULL REFERENCES accounts(id),
  amount          BIGINT NOT NULL,
  fee_amount      BIGINT NOT NULL DEFAULT 0,    -- platform fee (calculated at creation)
  task_id         VARCHAR(255),                  -- external task reference
  task_type       VARCHAR(100),                  -- skill category
  status          VARCHAR(20) DEFAULT 'held',    -- held, released, refunded, expired
  expires_at      TIMESTAMPTZ NOT NULL,          -- auto-refund deadline
  created_at      TIMESTAMPTZ DEFAULT NOW(),
  resolved_at     TIMESTAMPTZ,
  
  CONSTRAINT positive_amount CHECK (amount > 0),
  CONSTRAINT different_parties CHECK (requester_id != provider_id)
);

-- 4. TRANSACTIONS
-- Immutable ledger of all token movements
CREATE TABLE IF NOT EXISTS transactions (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  escrow_id       UUID REFERENCES escrows(id),
  from_account    UUID REFERENCES accounts(id),  -- NULL for minting
  to_account      UUID REFERENCES accounts(id),  -- NULL for burns/fees
  amount          BIGINT NOT NULL,
  tx_type         VARCHAR(30) NOT NULL,           -- mint, escrow_hold, escrow_release, 
                                                  -- escrow_refund, fee, purchase, burn
  description     TEXT,
  created_at      TIMESTAMPTZ DEFAULT NOW()
);

-- 5. TOKEN PURCHASES
-- When developers buy tokens with real money
CREATE TABLE IF NOT EXISTS purchases (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  account_id      UUID NOT NULL REFERENCES accounts(id),
  token_amount    BIGINT NOT NULL,
  dollar_amount   DECIMAL(10,2) NOT NULL,
  payment_ref     VARCHAR(255),                  -- external payment processor reference
  status          VARCHAR(20) DEFAULT 'completed',
  created_at      TIMESTAMPTZ DEFAULT NOW()
);

-- ============================================
-- INDEXES
-- ============================================
CREATE INDEX IF NOT EXISTS idx_accounts_developer    ON accounts(developer_id);
CREATE INDEX IF NOT EXISTS idx_accounts_status       ON accounts(status);
CREATE INDEX IF NOT EXISTS idx_escrows_requester     ON escrows(requester_id);
CREATE INDEX IF NOT EXISTS idx_escrows_provider      ON escrows(provider_id);
CREATE INDEX IF NOT EXISTS idx_escrows_status        ON escrows(status);
CREATE INDEX IF NOT EXISTS idx_escrows_expires       ON escrows(expires_at) WHERE status = 'held';
CREATE INDEX IF NOT EXISTS idx_transactions_from     ON transactions(from_account);
CREATE INDEX IF NOT EXISTS idx_transactions_to       ON transactions(to_account);
CREATE INDEX IF NOT EXISTS idx_transactions_type     ON transactions(tx_type);
CREATE INDEX IF NOT EXISTS idx_transactions_escrow   ON transactions(escrow_id);

-- ============================================
-- FUNCTIONS
-- ============================================

-- Auto-update updated_at timestamps
CREATE OR REPLACE FUNCTION update_timestamp()
RETURNS TRIGGER AS $$
BEGIN
  NEW.updated_at = NOW();
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE TRIGGER accounts_updated
  BEFORE UPDATE ON accounts
  FOR EACH ROW EXECUTE FUNCTION update_timestamp();

CREATE OR REPLACE TRIGGER balances_updated
  BEFORE UPDATE ON balances
  FOR EACH ROW EXECUTE FUNCTION update_timestamp();

`;

async function run() {
  console.log('Running migration...');
  try {
    await pool.query(migration);
    console.log('Migration completed successfully.');
  } catch (err) {
    console.error('Migration failed:', err.message);
    throw err;
  } finally {
    await pool.end();
  }
}

run();
