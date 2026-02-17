require('dotenv').config();

const express = require('express');
const helmet = require('helmet');
const cors = require('cors');
const morgan = require('morgan');
const rateLimit = require('express-rate-limit');

const exchangeRoutes = require('./routes/exchange');
const accountRoutes = require('./routes/accounts');
const statsRoutes = require('./routes/stats');
const { errorHandler } = require('./middleware/errors');

const app = express();
const PORT = process.env.PORT || 3000;

// ─────────────────────────────────────────────
//  MIDDLEWARE
// ─────────────────────────────────────────────
app.use(helmet());
app.use(cors());
app.use(express.json());
app.use(morgan('combined'));

// Rate limiting
const limiter = rateLimit({
  windowMs: parseInt(process.env.RATE_LIMIT_WINDOW_MS) || 15 * 60 * 1000,
  max: parseInt(process.env.RATE_LIMIT_MAX_REQUESTS) || 100,
  message: { error: 'Too many requests. Please try again later.' },
});
app.use(limiter);

// ─────────────────────────────────────────────
//  ROUTES
// ─────────────────────────────────────────────

// Health check
app.get('/health', (req, res) => {
  res.json({ 
    status: 'ok', 
    service: 'agent-token-exchange',
    version: '0.1.0',
    timestamp: new Date().toISOString(),
  });
});

// Core API
app.use('/api/v1/exchange', exchangeRoutes);   // escrow, release, refund, balance
app.use('/api/v1/accounts', accountRoutes);    // register, directory
app.use('/api/v1/stats', statsRoutes);         // network economics

// 404 handler
app.use((req, res) => {
  res.status(404).json({ error: `Route ${req.method} ${req.path} not found` });
});

// Error handler
app.use(errorHandler);

// ─────────────────────────────────────────────
//  START
// ─────────────────────────────────────────────
app.listen(PORT, () => {
  console.log(`
  ╔══════════════════════════════════════════╗
  ║     AGENT TOKEN EXCHANGE  v0.1.0        ║
  ║     The economy layer for AI agents     ║
  ╠══════════════════════════════════════════╣
  ║  Server:    http://localhost:${PORT}        ║
  ║  Env:       ${process.env.NODE_ENV || 'development'}                  ║
  ║  Fee:       ${process.env.TRANSACTION_FEE_PERCENT || '3'}%                           ║
  ║  Starter:   ${process.env.STARTER_TOKEN_ALLOCATION || '100'} tokens                  ║
  ╚══════════════════════════════════════════╝
  `);
});

module.exports = app;
