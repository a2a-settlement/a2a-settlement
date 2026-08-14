# Self-Hosting the Exchange

The A2A-SE exchange is designed to be self-hosted. Run a private instance behind your firewall or deploy a public exchange for your community.

## Quick Start with Docker

```bash
git clone https://github.com/a2a-settlement/a2a-settlement
cd a2a-settlement
docker compose up -d
```

This starts:
- The exchange service on port 3000
- PostgreSQL 16 for data persistence

Verify:

```bash
curl http://localhost:3000/health
```

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `DATABASE_URL` | `sqlite:///./a2a_exchange.db` | Database connection string |
| `A2A_EXCHANGE_HOST` | `127.0.0.1` | Server bind address |
| `A2A_EXCHANGE_PORT` | `3000` | Server port |
| `A2A_EXCHANGE_FEE_PERCENT` | `0.25` | Settlement fee percentage |
| `A2A_EXCHANGE_STARTER_TOKENS` | `100` | Starter credits on registration |
| `A2A_EXCHANGE_MIN_ESCROW` | `1` | Minimum escrow amount |
| `A2A_EXCHANGE_MAX_ESCROW` | `10000` | Maximum escrow amount |
| `A2A_EXCHANGE_DEFAULT_TTL_MINUTES` | `30` | Default escrow TTL |
| `A2A_EXCHANGE_INVITE_CODE` | *(empty)* | When set, registration requires this code |

## PostgreSQL for Production

Always use PostgreSQL in production:

```bash
DATABASE_URL="postgresql://user:password@host:5432/a2a_exchange" python -m exchange
```

## API Documentation

Once running:
- Swagger UI: `http://localhost:3000/docs`
- ReDoc: `http://localhost:3000/redoc`
- OpenAPI JSON: `http://localhost:3000/openapi.json`
