require('dotenv').config();
const { pool } = require('../config/database');
const bcrypt = require('bcrypt');
const { v4: uuidv4 } = require('uuid');

const STARTER_TOKENS = parseInt(process.env.STARTER_TOKEN_ALLOCATION || '100');

// Demo bots for testing the exchange
const demoBots = [
  {
    bot_name: 'SentimentBot',
    developer_id: 'dev-demo-1',
    description: 'Analyzes text sentiment. Returns confidence-scored positive/negative/neutral.',
    skills: ['sentiment-analysis', 'text-classification'],
  },
  {
    bot_name: 'SummarizerBot',
    developer_id: 'dev-demo-2',
    description: 'Produces concise summaries of long-form content.',
    skills: ['summarization', 'text-extraction'],
  },
  {
    bot_name: 'TranslatorBot',
    developer_id: 'dev-demo-3',
    description: 'Translates text between 40+ language pairs.',
    skills: ['translation', 'language-detection'],
  },
  {
    bot_name: 'CodeReviewBot',
    developer_id: 'dev-demo-4',
    description: 'Reviews code for bugs, security issues, and style.',
    skills: ['code-review', 'security-scan', 'linting'],
  },
  {
    bot_name: 'MessengerBot',
    developer_id: 'dev-rich',
    description: 'Orchestrator bot. Decomposes tasks and brokers skill requests.',
    skills: ['orchestration', 'task-decomposition'],
  },
];

async function seed() {
  const client = await pool.connect();
  console.log('Seeding demo data...');

  try {
    await client.query('BEGIN');

    for (const bot of demoBots) {
      const id = uuidv4();
      // Generate a readable API key for demo purposes
      const apiKey = `ate_${bot.bot_name.toLowerCase()}_${uuidv4().slice(0, 8)}`;
      const apiKeyHash = await bcrypt.hash(apiKey, 10);

      // Create account
      await client.query(
        `INSERT INTO accounts (id, bot_name, developer_id, api_key_hash, description, skills)
         VALUES ($1, $2, $3, $4, $5, $6)
         ON CONFLICT DO NOTHING`,
        [id, bot.bot_name, bot.developer_id, apiKeyHash, bot.description, JSON.stringify(bot.skills)]
      );

      // Create balance with starter tokens
      await client.query(
        `INSERT INTO balances (account_id, available)
         VALUES ($1, $2)
         ON CONFLICT DO NOTHING`,
        [id, STARTER_TOKENS]
      );

      // Record the mint transaction
      await client.query(
        `INSERT INTO transactions (from_account, to_account, amount, tx_type, description)
         VALUES (NULL, $1, $2, 'mint', 'Starter token allocation')`,
        [id, STARTER_TOKENS]
      );

      console.log(`  Created: ${bot.bot_name} (key: ${apiKey})`);
    }

    await client.query('COMMIT');
    console.log('Seed completed. Save the API keys above for testing!');
  } catch (err) {
    await client.query('ROLLBACK');
    console.error('Seed failed:', err.message);
    throw err;
  } finally {
    client.release();
    await pool.end();
  }
}

seed();
