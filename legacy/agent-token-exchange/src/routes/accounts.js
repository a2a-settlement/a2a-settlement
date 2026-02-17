const express = require('express');
const router = express.Router();
const { AccountService } = require('../services/accounts');
const { authenticateBot } = require('../middleware/auth');

// ─────────────────────────────────────────────
//  POST /accounts/register
//  Register a new bot and receive API key + starter tokens
//  PUBLIC - no auth required
// ─────────────────────────────────────────────
router.post('/register', async (req, res, next) => {
  try {
    const { bot_name, developer_id, description, skills } = req.body;

    const result = await AccountService.register({
      botName: bot_name,
      developerId: developer_id,
      description,
      skills,
    });

    res.status(201).json({
      message: 'Bot registered successfully. Save your API key - it will not be shown again.',
      ...result,
    });
  } catch (err) {
    next(err);
  }
});

// ─────────────────────────────────────────────
//  GET /accounts/directory
//  Public directory of registered bots
//  PUBLIC - no auth required
// ─────────────────────────────────────────────
router.get('/directory', async (req, res, next) => {
  try {
    const { skill, limit, offset } = req.query;
    const bots = await AccountService.listBots({
      skill,
      limit: parseInt(limit) || 50,
      offset: parseInt(offset) || 0,
    });
    res.json({ bots, count: bots.length });
  } catch (err) {
    next(err);
  }
});

// ─────────────────────────────────────────────
//  GET /accounts/:id
//  View a specific bot's public profile
//  PUBLIC - no auth required
// ─────────────────────────────────────────────
router.get('/:id', async (req, res, next) => {
  try {
    const account = await AccountService.getAccount(req.params.id);
    res.json(account);
  } catch (err) {
    next(err);
  }
});

// ─────────────────────────────────────────────
//  PUT /accounts/skills
//  Update your bot's skill list
//  AUTHENTICATED
// ─────────────────────────────────────────────
router.put('/skills', authenticateBot, async (req, res, next) => {
  try {
    const { skills } = req.body;
    if (!Array.isArray(skills)) {
      return res.status(400).json({ error: 'skills must be an array' });
    }
    const result = await AccountService.updateSkills(req.bot.id, skills);
    res.json(result);
  } catch (err) {
    next(err);
  }
});

module.exports = router;
