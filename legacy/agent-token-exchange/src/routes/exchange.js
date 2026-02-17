const express = require('express');
const router = express.Router();
const { ExchangeService } = require('../services/exchange');
const { authenticateBot } = require('../middleware/auth');

// All exchange routes require authentication
router.use(authenticateBot);

// ─────────────────────────────────────────────
//  POST /exchange/escrow
//  Lock tokens for a pending task
// ─────────────────────────────────────────────
router.post('/escrow', async (req, res, next) => {
  try {
    const { provider_id, amount, task_id, task_type, ttl_minutes } = req.body;

    if (!provider_id || !amount) {
      return res.status(400).json({ error: 'provider_id and amount are required' });
    }

    const result = await ExchangeService.createEscrow({
      requesterId: req.bot.id,
      providerId: provider_id,
      amount: parseInt(amount),
      taskId: task_id,
      taskType: task_type,
      ttlMinutes: ttl_minutes,
    });

    res.status(201).json(result);
  } catch (err) {
    next(err);
  }
});

// ─────────────────────────────────────────────
//  POST /exchange/release
//  Task completed - pay the provider
// ─────────────────────────────────────────────
router.post('/release', async (req, res, next) => {
  try {
    const { escrow_id } = req.body;

    if (!escrow_id) {
      return res.status(400).json({ error: 'escrow_id is required' });
    }

    const result = await ExchangeService.releaseEscrow({
      escrowId: escrow_id,
      requesterId: req.bot.id,
    });

    res.json(result);
  } catch (err) {
    next(err);
  }
});

// ─────────────────────────────────────────────
//  POST /exchange/refund
//  Task failed - return tokens to requester
// ─────────────────────────────────────────────
router.post('/refund', async (req, res, next) => {
  try {
    const { escrow_id, reason } = req.body;

    if (!escrow_id) {
      return res.status(400).json({ error: 'escrow_id is required' });
    }

    const result = await ExchangeService.refundEscrow({
      escrowId: escrow_id,
      requesterId: req.bot.id,
      reason,
    });

    res.json(result);
  } catch (err) {
    next(err);
  }
});

// ─────────────────────────────────────────────
//  GET /exchange/balance
//  Check your token balance
// ─────────────────────────────────────────────
router.get('/balance', async (req, res, next) => {
  try {
    const result = await ExchangeService.getBalance(req.bot.id);
    res.json(result);
  } catch (err) {
    next(err);
  }
});

// ─────────────────────────────────────────────
//  GET /exchange/transactions
//  View transaction history
// ─────────────────────────────────────────────
router.get('/transactions', async (req, res, next) => {
  try {
    const { limit, offset } = req.query;
    const result = await ExchangeService.getTransactions(req.bot.id, {
      limit: parseInt(limit) || 50,
      offset: parseInt(offset) || 0,
    });
    res.json({ transactions: result });
  } catch (err) {
    next(err);
  }
});

module.exports = router;
