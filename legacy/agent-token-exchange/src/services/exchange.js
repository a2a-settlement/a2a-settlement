const { pool } = require('../../config/database');

const FEE_PERCENT = parseFloat(process.env.TRANSACTION_FEE_PERCENT || '3') / 100;
const MIN_ESCROW = parseInt(process.env.MIN_ESCROW_AMOUNT || '1');
const MAX_ESCROW = parseInt(process.env.MAX_ESCROW_AMOUNT || '10000');
const DEFAULT_ESCROW_TTL_MINUTES = 30;

class ExchangeService {

  // ─────────────────────────────────────────────
  //  ESCROW - Lock tokens for a pending task
  // ─────────────────────────────────────────────
  async createEscrow({ requesterId, providerId, amount, taskId, taskType, ttlMinutes }) {
    if (amount < MIN_ESCROW || amount > MAX_ESCROW) {
      throw new ExchangeError(`Amount must be between ${MIN_ESCROW} and ${MAX_ESCROW}`, 400);
    }

    if (requesterId === providerId) {
      throw new ExchangeError('Cannot escrow to yourself', 400);
    }

    const feeAmount = Math.ceil(amount * FEE_PERCENT);
    const totalHold = amount + feeAmount;
    const ttl = ttlMinutes || DEFAULT_ESCROW_TTL_MINUTES;
    const expiresAt = new Date(Date.now() + ttl * 60 * 1000);

    const client = await pool.connect();
    try {
      await client.query('BEGIN');

      // Check and lock the requester's balance
      const balResult = await client.query(
        'SELECT available FROM balances WHERE account_id = $1 FOR UPDATE',
        [requesterId]
      );

      if (!balResult.rows.length) {
        throw new ExchangeError('Requester account not found', 404);
      }

      if (balResult.rows[0].available < totalHold) {
        throw new ExchangeError(
          `Insufficient balance. Need ${totalHold} (${amount} + ${feeAmount} fee), have ${balResult.rows[0].available}`,
          400
        );
      }

      // Verify provider exists and is active
      const provResult = await client.query(
        'SELECT status FROM accounts WHERE id = $1',
        [providerId]
      );

      if (!provResult.rows.length) {
        throw new ExchangeError('Provider account not found', 404);
      }

      if (provResult.rows[0].status !== 'active') {
        throw new ExchangeError('Provider account is not active', 400);
      }

      // Deduct from available, add to held
      await client.query(
        `UPDATE balances 
         SET available = available - $1, held_in_escrow = held_in_escrow + $1
         WHERE account_id = $2`,
        [totalHold, requesterId]
      );

      // Create escrow record
      const escrowResult = await client.query(
        `INSERT INTO escrows (requester_id, provider_id, amount, fee_amount, task_id, task_type, expires_at)
         VALUES ($1, $2, $3, $4, $5, $6, $7)
         RETURNING *`,
        [requesterId, providerId, amount, feeAmount, taskId, taskType, expiresAt]
      );

      const escrow = escrowResult.rows[0];

      // Record transaction
      await client.query(
        `INSERT INTO transactions (escrow_id, from_account, to_account, amount, tx_type, description)
         VALUES ($1, $2, NULL, $3, 'escrow_hold', $4)`,
        [escrow.id, requesterId, totalHold, `Escrow for task: ${taskType || taskId || 'unspecified'}`]
      );

      await client.query('COMMIT');

      return {
        escrow_id: escrow.id,
        requester_id: requesterId,
        provider_id: providerId,
        amount,
        fee_amount: feeAmount,
        total_held: totalHold,
        status: escrow.status,
        expires_at: escrow.expires_at,
      };
    } catch (err) {
      await client.query('ROLLBACK');
      throw err;
    } finally {
      client.release();
    }
  }

  // ─────────────────────────────────────────────
  //  RELEASE - Task completed, pay the provider
  // ─────────────────────────────────────────────
  async releaseEscrow({ escrowId, requesterId }) {
    const client = await pool.connect();
    try {
      await client.query('BEGIN');

      // Lock the escrow row
      const escrowResult = await client.query(
        'SELECT * FROM escrows WHERE id = $1 FOR UPDATE',
        [escrowId]
      );

      if (!escrowResult.rows.length) {
        throw new ExchangeError('Escrow not found', 404);
      }

      const escrow = escrowResult.rows[0];

      if (escrow.requester_id !== requesterId) {
        throw new ExchangeError('Only the requester can release an escrow', 403);
      }

      if (escrow.status !== 'held') {
        throw new ExchangeError(`Escrow is already ${escrow.status}`, 400);
      }

      const totalHeld = escrow.amount + escrow.fee_amount;

      // Remove hold from requester
      await client.query(
        `UPDATE balances 
         SET held_in_escrow = held_in_escrow - $1, total_spent = total_spent + $1
         WHERE account_id = $2`,
        [totalHeld, escrow.requester_id]
      );

      // Pay the provider (amount minus fee)
      await client.query(
        `UPDATE balances 
         SET available = available + $1, total_earned = total_earned + $1
         WHERE account_id = $2`,
        [escrow.amount, escrow.provider_id]
      );

      // Mark escrow as released
      await client.query(
        `UPDATE escrows SET status = 'released', resolved_at = NOW() WHERE id = $1`,
        [escrowId]
      );

      // Record payment transaction
      await client.query(
        `INSERT INTO transactions (escrow_id, from_account, to_account, amount, tx_type, description)
         VALUES ($1, $2, $3, $4, 'escrow_release', 'Task completed - payment released')`,
        [escrowId, escrow.requester_id, escrow.provider_id, escrow.amount]
      );

      // Record fee transaction (to platform treasury = NULL to_account)
      if (escrow.fee_amount > 0) {
        await client.query(
          `INSERT INTO transactions (escrow_id, from_account, to_account, amount, tx_type, description)
           VALUES ($1, $2, NULL, $3, 'fee', 'Platform transaction fee')`,
          [escrowId, escrow.requester_id, escrow.fee_amount]
        );
      }

      // Update provider reputation (successful delivery)
      await client.query(
        `UPDATE accounts 
         SET reputation = LEAST(1.0, reputation * 0.9 + 1.0 * 0.1)
         WHERE id = $1`,
        [escrow.provider_id]
      );

      await client.query('COMMIT');

      return {
        escrow_id: escrowId,
        status: 'released',
        amount_paid: escrow.amount,
        fee_collected: escrow.fee_amount,
        provider_id: escrow.provider_id,
      };
    } catch (err) {
      await client.query('ROLLBACK');
      throw err;
    } finally {
      client.release();
    }
  }

  // ─────────────────────────────────────────────
  //  REFUND - Task failed, return tokens
  // ─────────────────────────────────────────────
  async refundEscrow({ escrowId, requesterId, reason }) {
    const client = await pool.connect();
    try {
      await client.query('BEGIN');

      const escrowResult = await client.query(
        'SELECT * FROM escrows WHERE id = $1 FOR UPDATE',
        [escrowId]
      );

      if (!escrowResult.rows.length) {
        throw new ExchangeError('Escrow not found', 404);
      }

      const escrow = escrowResult.rows[0];

      if (escrow.requester_id !== requesterId) {
        throw new ExchangeError('Only the requester can refund an escrow', 403);
      }

      if (escrow.status !== 'held') {
        throw new ExchangeError(`Escrow is already ${escrow.status}`, 400);
      }

      const totalHeld = escrow.amount + escrow.fee_amount;

      // Return tokens to requester's available balance
      await client.query(
        `UPDATE balances 
         SET available = available + $1, held_in_escrow = held_in_escrow - $1
         WHERE account_id = $2`,
        [totalHeld, escrow.requester_id]
      );

      // Mark escrow as refunded
      await client.query(
        `UPDATE escrows SET status = 'refunded', resolved_at = NOW() WHERE id = $1`,
        [escrowId]
      );

      // Record refund transaction
      await client.query(
        `INSERT INTO transactions (escrow_id, from_account, to_account, amount, tx_type, description)
         VALUES ($1, NULL, $2, $3, 'escrow_refund', $4)`,
        [escrowId, escrow.requester_id, totalHeld, reason || 'Task failed or cancelled']
      );

      // Update provider reputation (failed delivery)
      await client.query(
        `UPDATE accounts 
         SET reputation = GREATEST(0.0, reputation * 0.9 + 0.0 * 0.1)
         WHERE id = $1`,
        [escrow.provider_id]
      );

      await client.query('COMMIT');

      return {
        escrow_id: escrowId,
        status: 'refunded',
        amount_returned: totalHeld,
        requester_id: escrow.requester_id,
      };
    } catch (err) {
      await client.query('ROLLBACK');
      throw err;
    } finally {
      client.release();
    }
  }

  // ─────────────────────────────────────────────
  //  BALANCE - Check account balances
  // ─────────────────────────────────────────────
  async getBalance(accountId) {
    const result = await pool.query(
      `SELECT b.*, a.bot_name, a.reputation, a.status as account_status
       FROM balances b
       JOIN accounts a ON a.id = b.account_id
       WHERE b.account_id = $1`,
      [accountId]
    );

    if (!result.rows.length) {
      throw new ExchangeError('Account not found', 404);
    }

    const row = result.rows[0];
    return {
      account_id: accountId,
      bot_name: row.bot_name,
      reputation: parseFloat(row.reputation),
      account_status: row.account_status,
      available: parseInt(row.available),
      held_in_escrow: parseInt(row.held_in_escrow),
      total_earned: parseInt(row.total_earned),
      total_spent: parseInt(row.total_spent),
    };
  }

  // ─────────────────────────────────────────────
  //  TRANSACTION HISTORY
  // ─────────────────────────────────────────────
  async getTransactions(accountId, { limit = 50, offset = 0 } = {}) {
    const result = await pool.query(
      `SELECT * FROM transactions 
       WHERE from_account = $1 OR to_account = $1
       ORDER BY created_at DESC
       LIMIT $2 OFFSET $3`,
      [accountId, limit, offset]
    );

    return result.rows;
  }

  // ─────────────────────────────────────────────
  //  EXPIRE - Cleanup stale escrows (cron job)
  // ─────────────────────────────────────────────
  async expireStaleEscrows() {
    const client = await pool.connect();
    try {
      await client.query('BEGIN');

      const staleResult = await client.query(
        `SELECT * FROM escrows 
         WHERE status = 'held' AND expires_at < NOW()
         FOR UPDATE`
      );

      let expiredCount = 0;

      for (const escrow of staleResult.rows) {
        const totalHeld = escrow.amount + escrow.fee_amount;

        // Return tokens to requester
        await client.query(
          `UPDATE balances 
           SET available = available + $1, held_in_escrow = held_in_escrow - $1
           WHERE account_id = $2`,
          [totalHeld, escrow.requester_id]
        );

        // Mark expired
        await client.query(
          `UPDATE escrows SET status = 'expired', resolved_at = NOW() WHERE id = $1`,
          [escrow.id]
        );

        // Record transaction
        await client.query(
          `INSERT INTO transactions (escrow_id, from_account, to_account, amount, tx_type, description)
           VALUES ($1, NULL, $2, $3, 'escrow_refund', 'Auto-expired: TTL exceeded')`,
          [escrow.id, escrow.requester_id, totalHeld]
        );

        expiredCount++;
      }

      await client.query('COMMIT');
      return { expired_count: expiredCount };
    } catch (err) {
      await client.query('ROLLBACK');
      throw err;
    } finally {
      client.release();
    }
  }
}

// Custom error class for exchange operations
class ExchangeError extends Error {
  constructor(message, statusCode = 500) {
    super(message);
    this.name = 'ExchangeError';
    this.statusCode = statusCode;
  }
}

module.exports = { ExchangeService: new ExchangeService(), ExchangeError };
