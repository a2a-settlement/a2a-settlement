const express = require('express');
const router = express.Router();
const { pool } = require('../../config/database');

// ─────────────────────────────────────────────
//  GET /stats
//  Public network statistics - the economics dashboard
// ─────────────────────────────────────────────
router.get('/', async (req, res, next) => {
  try {
    // Total accounts
    const accounts = await pool.query(
      `SELECT 
         COUNT(*) as total_bots,
         COUNT(*) FILTER (WHERE status = 'active') as active_bots
       FROM accounts`
    );

    // Token supply
    const supply = await pool.query(
      `SELECT 
         COALESCE(SUM(available), 0) as circulating,
         COALESCE(SUM(held_in_escrow), 0) as in_escrow,
         COALESCE(SUM(available + held_in_escrow), 0) as total_supply
       FROM balances`
    );

    // Transaction volume (last 24h)
    const volume = await pool.query(
      `SELECT 
         COUNT(*) as tx_count_24h,
         COALESCE(SUM(amount), 0) as tx_volume_24h
       FROM transactions 
       WHERE created_at > NOW() - INTERVAL '24 hours'`
    );

    // Total fees collected (returned to treasury)
    const fees = await pool.query(
      `SELECT COALESCE(SUM(amount), 0) as total_fees_collected
       FROM transactions WHERE tx_type = 'fee'`
    );

    // Active escrows
    const escrows = await pool.query(
      `SELECT COUNT(*) as active_escrows
       FROM escrows WHERE status = 'held'`
    );

    // Token velocity = tx volume / total supply (24h)
    const totalSupply = parseInt(supply.rows[0].total_supply) || 1;
    const txVolume = parseInt(volume.rows[0].tx_volume_24h);
    const velocity = (txVolume / totalSupply).toFixed(4);

    res.json({
      network: {
        total_bots: parseInt(accounts.rows[0].total_bots),
        active_bots: parseInt(accounts.rows[0].active_bots),
      },
      token_supply: {
        circulating: parseInt(supply.rows[0].circulating),
        in_escrow: parseInt(supply.rows[0].in_escrow),
        total: parseInt(supply.rows[0].total_supply),
      },
      activity_24h: {
        transaction_count: parseInt(volume.rows[0].tx_count_24h),
        token_volume: txVolume,
        velocity: parseFloat(velocity),
      },
      treasury: {
        fees_collected: parseInt(fees.rows[0].total_fees_collected),
      },
      active_escrows: parseInt(escrows.rows[0].active_escrows),
    });
  } catch (err) {
    next(err);
  }
});

module.exports = router;
