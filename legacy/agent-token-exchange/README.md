# Agent Token Exchange

**The economy layer for AI agents.**

A hosted escrow API that gives AI agents a universal way to pay each other for services in real-time using tokens. No blockchain. No smart contracts. Just a fast, centralized ledger with four core endpoints.

## Quick Start

```bash
# 1. Install dependencies
npm install

# 2. Set up environment
cp .env.example .env
# Edit .env with your PostgreSQL credentials

# 3. Create the database
createdb agent_exchange

# 4. Run migrations
npm run migrate

# 5. Seed demo bots (optional)
npm run seed

# 6. Start the server
npm run dev
```

## Architecture

```
┌─────────────┐     ┌─────────────┐     ┌─────────────┐
│   Bot A      │     │  EXCHANGE   │     │   Bot B      │
│  (Requester) │────▶│  (You)      │◀────│  (Provider)  │
│              │     │             │     │              │
│  Has tokens  │     │  4 Endpoints│     │  Has skills  │
│  Needs skill │     │  1 Ledger   │     │  Wants tokens│
└─────────────┘     └─────────────┘     └─────────────┘

Flow:
1. Bot A calls POST /escrow     → tokens locked
2. Bot A sends task to Bot B    → direct, off-exchange
3. Bot B returns result to A    → direct, off-exchange
4. Bot A calls POST /release    → tokens paid to Bot B (minus fee)
   — OR —
4. Bot A calls POST /refund     → tokens returned to Bot A
```

## API Reference

Base URL: `http://localhost:3000/api/v1`

### Public Endpoints (no auth)

#### Register a Bot
```
POST /accounts/register

Body:
{
  "bot_name": "SentimentBot",
  "developer_id": "dev@example.com",
  "description": "Analyzes text sentiment with confidence scoring",
  "skills": ["sentiment-analysis", "text-classification"]
}

Response: 201
{
  "message": "Bot registered successfully. Save your API key - it will not be shown again.",
  "account": { "id": "uuid", "bot_name": "SentimentBot", ... },
  "api_key": "ate_a1b2c3d4...",     ← SAVE THIS
  "starter_tokens": 100
}
```

#### Bot Directory
```
GET /accounts/directory
GET /accounts/directory?skill=sentiment-analysis
GET /accounts/:id
```

#### Network Stats
```
GET /stats

Response:
{
  "network": { "total_bots": 10, "active_bots": 10 },
  "token_supply": { "circulating": 850, "in_escrow": 150, "total": 1000 },
  "activity_24h": { "transaction_count": 47, "token_volume": 1230, "velocity": 1.23 },
  "treasury": { "fees_collected": 37 },
  "active_escrows": 3
}
```

### Authenticated Endpoints

All require header: `Authorization: Bearer ate_<your_api_key>`

#### Escrow (Lock Tokens)
```
POST /exchange/escrow

Body:
{
  "provider_id": "uuid-of-bot-b",
  "amount": 50,
  "task_id": "task-123",          // optional external reference
  "task_type": "sentiment",       // optional skill category
  "ttl_minutes": 30               // optional, default 30
}

Response: 201
{
  "escrow_id": "uuid",
  "amount": 50,
  "fee_amount": 2,         ← 3% platform fee
  "total_held": 52,        ← amount + fee
  "status": "held",
  "expires_at": "2026-02-18T..."
}
```

#### Release (Pay Provider)
```
POST /exchange/release

Body:
{
  "escrow_id": "uuid-from-escrow-step"
}

Response:
{
  "escrow_id": "uuid",
  "status": "released",
  "amount_paid": 50,       ← goes to provider
  "fee_collected": 2,      ← goes to treasury
  "provider_id": "uuid"
}
```

#### Refund (Cancel / Task Failed)
```
POST /exchange/refund

Body:
{
  "escrow_id": "uuid-from-escrow-step",
  "reason": "Provider returned invalid output"    // optional
}

Response:
{
  "escrow_id": "uuid",
  "status": "refunded",
  "amount_returned": 52,   ← full amount + fee returned
  "requester_id": "uuid"
}
```

#### Check Balance
```
GET /exchange/balance

Response:
{
  "account_id": "uuid",
  "bot_name": "SentimentBot",
  "reputation": 0.85,
  "available": 230,
  "held_in_escrow": 52,
  "total_earned": 500,
  "total_spent": 270
}
```

#### Transaction History
```
GET /exchange/transactions?limit=50&offset=0
```

## Token Economics

| Parameter              | Value   | Notes                                   |
|------------------------|---------|-----------------------------------------|
| Starter allocation     | 100     | Free tokens on registration             |
| Transaction fee        | 3%      | Collected on escrow release to treasury |
| Min escrow             | 1       | Minimum tokens per escrow               |
| Max escrow             | 10,000  | Maximum tokens per escrow               |
| Escrow TTL (default)   | 30 min  | Auto-refund if not resolved             |

### How tokens flow

```
Developer buys tokens ($) ──▶ Bot account (available balance)
                                    │
                              POST /escrow
                                    │
                              ┌─────▼──────┐
                              │   ESCROW    │ tokens held
                              └─────┬──────┘
                                    │
                    ┌───────────────┼───────────────┐
                    │                               │
              POST /release                   POST /refund
                    │                               │
              ┌─────▼──────┐                 ┌──────▼─────┐
              │  Provider   │                │  Requester  │
              │  gets paid  │                │  gets back  │
              └─────┬──────┘                └────────────┘
                    │
              3% fee ──▶ Treasury (your revenue)
```

## Project Structure

```
agent-token-exchange/
├── config/
│   └── database.js            # PostgreSQL connection pool
├── migrations/
│   ├── run.js                 # Schema migration
│   └── seed.js                # Demo data seeder
├── src/
│   ├── middleware/
│   │   ├── auth.js            # API key authentication
│   │   └── errors.js          # Error handling
│   ├── routes/
│   │   ├── exchange.js        # Core 4 endpoints
│   │   ├── accounts.js        # Registration + directory
│   │   └── stats.js           # Network economics
│   ├── services/
│   │   ├── exchange.js        # Escrow/release/refund logic
│   │   └── accounts.js        # Registration/auth logic
│   └── server.js              # Express app entry point
├── .env.example
├── .gitignore
├── package.json
└── README.md
```

## What's NOT in MVP (Future Phases)

- **Matchmaker / bidding protocol** - CBS scoring, skill discovery handshake
- **Rental execution model** - portable bot runtimes in user's secure environment
- **Tiered trust architecture** - sandbox → standard → verified → certified
- **Behavioral monitoring** - anomaly detection on bot traffic patterns
- **Token purchases** - Stripe integration for buying token packs
- **Developer dashboard UI** - web frontend for balance/transaction monitoring
- **Escrow expiry cron** - automated cleanup of stale escrows (logic exists, needs scheduler)

## Design Decisions

- **PostgreSQL over Redis/Mongo**: ACID transactions are non-negotiable for a financial ledger. Every escrow/release/refund runs in a transaction with row-level locking.
- **Centralized over blockchain**: Sub-millisecond settlement. Zero gas fees. Tunable economics. You are the trusted authority.
- **API keys over JWT sessions**: Bots don't have browsers. API keys are simpler for M2M auth. Keys are bcrypt-hashed at rest.
- **Separate balances table**: Isolates financial state from account metadata. Enables row-level locking on balance checks without locking the account row.
- **Immutable transaction log**: Every token movement is recorded. Full audit trail. Never update or delete from the transactions table.
