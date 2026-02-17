const { pool } = require('../../config/database');
const bcrypt = require('bcrypt');
const crypto = require('crypto');
const { ExchangeError } = require('./exchange');

const STARTER_TOKENS = parseInt(process.env.STARTER_TOKEN_ALLOCATION || '100');
const SALT_ROUNDS = parseInt(process.env.API_KEY_SALT_ROUNDS || '10');

class AccountService {

  // ─────────────────────────────────────────────
  //  REGISTER - Create a new bot account + mint
  // ─────────────────────────────────────────────
  async register({ botName, developerId, description, skills }) {
    if (!botName || !developerId) {
      throw new ExchangeError('bot_name and developer_id are required', 400);
    }

    // Generate API key: ate_<random 32 hex chars>
    const apiKey = `ate_${crypto.randomBytes(16).toString('hex')}`;
    const apiKeyHash = await bcrypt.hash(apiKey, SALT_ROUNDS);

    const client = await pool.connect();
    try {
      await client.query('BEGIN');

      // Create account
      const accountResult = await client.query(
        `INSERT INTO accounts (bot_name, developer_id, api_key_hash, description, skills)
         VALUES ($1, $2, $3, $4, $5)
         RETURNING id, bot_name, developer_id, description, skills, status, reputation, created_at`,
        [botName, developerId, apiKeyHash, description || null, JSON.stringify(skills || [])]
      );

      const account = accountResult.rows[0];

      // Create balance with starter tokens
      await client.query(
        `INSERT INTO balances (account_id, available)
         VALUES ($1, $2)`,
        [account.id, STARTER_TOKENS]
      );

      // Record mint transaction
      await client.query(
        `INSERT INTO transactions (from_account, to_account, amount, tx_type, description)
         VALUES (NULL, $1, $2, 'mint', 'Starter token allocation on registration')`,
        [account.id, STARTER_TOKENS]
      );

      await client.query('COMMIT');

      return {
        account: {
          id: account.id,
          bot_name: account.bot_name,
          developer_id: account.developer_id,
          description: account.description,
          skills: account.skills,
          status: account.status,
          reputation: parseFloat(account.reputation),
          created_at: account.created_at,
        },
        api_key: apiKey,  // Only returned ONCE at registration
        starter_tokens: STARTER_TOKENS,
      };
    } catch (err) {
      await client.query('ROLLBACK');
      if (err.code === '23505') {
        throw new ExchangeError('A bot with this name already exists', 409);
      }
      throw err;
    } finally {
      client.release();
    }
  }

  // ─────────────────────────────────────────────
  //  AUTHENTICATE - Validate API key, return account
  // ─────────────────────────────────────────────
  async authenticate(apiKey) {
    if (!apiKey || !apiKey.startsWith('ate_')) {
      throw new ExchangeError('Invalid API key format', 401);
    }

    // We have to check all accounts since we can't reverse the hash
    // In production, consider a key prefix index strategy
    const result = await pool.query(
      `SELECT id, bot_name, developer_id, api_key_hash, status 
       FROM accounts WHERE status != 'suspended'`
    );

    for (const account of result.rows) {
      const match = await bcrypt.compare(apiKey, account.api_key_hash);
      if (match) {
        return {
          id: account.id,
          bot_name: account.bot_name,
          developer_id: account.developer_id,
          status: account.status,
        };
      }
    }

    throw new ExchangeError('Invalid API key', 401);
  }

  // ─────────────────────────────────────────────
  //  LIST BOTS - Public directory of registered bots
  // ─────────────────────────────────────────────
  async listBots({ skill, status = 'active', limit = 50, offset = 0 } = {}) {
    let query = `
      SELECT id, bot_name, description, skills, status, reputation, created_at
      FROM accounts WHERE status = $1
    `;
    const params = [status];

    if (skill) {
      query += ` AND skills @> $${params.length + 1}::jsonb`;
      params.push(JSON.stringify([skill]));
    }

    query += ` ORDER BY reputation DESC LIMIT $${params.length + 1} OFFSET $${params.length + 2}`;
    params.push(limit, offset);

    const result = await pool.query(query, params);

    return result.rows.map(row => ({
      ...row,
      reputation: parseFloat(row.reputation),
    }));
  }

  // ─────────────────────────────────────────────
  //  GET ACCOUNT - Fetch single account details
  // ─────────────────────────────────────────────
  async getAccount(accountId) {
    const result = await pool.query(
      `SELECT id, bot_name, developer_id, description, skills, status, reputation, created_at
       FROM accounts WHERE id = $1`,
      [accountId]
    );

    if (!result.rows.length) {
      throw new ExchangeError('Account not found', 404);
    }

    return {
      ...result.rows[0],
      reputation: parseFloat(result.rows[0].reputation),
    };
  }

  // ─────────────────────────────────────────────
  //  UPDATE SKILLS - Bot updates its skill list
  // ─────────────────────────────────────────────
  async updateSkills(accountId, skills) {
    const result = await pool.query(
      `UPDATE accounts SET skills = $1 WHERE id = $2 RETURNING skills`,
      [JSON.stringify(skills), accountId]
    );

    if (!result.rows.length) {
      throw new ExchangeError('Account not found', 404);
    }

    return result.rows[0];
  }
}

module.exports = { AccountService: new AccountService() };
