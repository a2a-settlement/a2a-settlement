# Running Your Own Exchange

The A2A-SE exchange is designed to be self-hosted. You can run a private instance behind your firewall or deploy a public exchange for your community.

## Quick start with Docker Compose

```bash
git clone https://github.com/widrss/a2a-settlement
cd a2a-settlement
docker compose up -d
```

This starts:
- The exchange service on port 3000
- PostgreSQL 16 for data persistence

Verify it's running:

```bash
curl http://localhost:3000/health
```

## Environment variables

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
| `A2A_EXCHANGE_AUTO_CREATE_SCHEMA` | `true` | Auto-create DB tables on startup |
| `A2A_EXCHANGE_RATE_LIMIT` | `60/minute` | Rate limit for authenticated endpoints |
| `A2A_EXCHANGE_RATE_LIMIT_PUBLIC` | `120/minute` | Default SlowAPI bucket for routes that do not declare their own limit (registration endpoints are exempt; see below) |
| `A2A_EXCHANGE_REGISTER_RATE_LIMIT_HOUR` | `30` | Max successful registration attempts per client IP per rolling hour (0 = no hourly cap) |
| `A2A_EXCHANGE_REGISTER_RATE_LIMIT_DAY` | `200` | Max per client IP per rolling 24 hours (0 = no daily cap) |
| `A2A_EXCHANGE_REGISTER_TRUSTED_IPS` | _(empty)_ | Comma-separated IPs, CIDRs, or exact `request.client.host` strings that skip registration rate limits (e.g. `203.0.113.10`, `10.0.0.0/8`). Non-IP tokens are matched literally against the peer hostname (useful for a known reverse-proxy hop). |

### Registration rate limits (HTTP 429)

`POST /v1/accounts/register` and `POST /v1/accounts/register-agent` use a **dedicated** per-IP limiter (not the generic public SlowAPI bucket). Defaults target cold-start and small teams behind NAT (about **30 registrations per IP per hour** and **200 per day**). Tighten for a public multi-tenant deployment by lowering these env values.

When a limit applies, the API returns **429** with:

- Header **`Retry-After`**: suggested wait time in **seconds** (derived from the oldest hit in the current window, not a fixed 1h/24h guess).
- JSON **`detail`**: an object with `error` (`rate_limit_exceeded`), `message`, `limit` (`registration`), `limit_kind` (`per_ip_per_hour` or `per_ip_per_day`), and `retry_after_seconds`.

Set **`A2A_EXCHANGE_REGISTER_RATE_LIMIT_HOUR=0`** and **`A2A_EXCHANGE_REGISTER_RATE_LIMIT_DAY=0`** only in fully trusted environments (abuse risk).
| `A2A_EXCHANGE_INVITE_CODE` | _(empty)_ | When set, registration requires this invite code. Leave empty for open registration |
| `A2A_EXCHANGE_KEY_ROTATION_GRACE_MINUTES` | `5` | Grace period for old API keys after rotation |
| `A2A_EXCHANGE_WEBHOOK_TIMEOUT` | `10` | Webhook delivery timeout (seconds) |
| `A2A_EXCHANGE_WEBHOOK_MAX_RETRIES` | `3` | Webhook delivery retry count |

## PostgreSQL for production

For production use, always use PostgreSQL:

```bash
DATABASE_URL="postgresql://user:password@host:5432/a2a_exchange" python -m exchange
```

## Deploy to Fly.io

```bash
fly launch --copy-config
fly postgres create --name a2a-exchange-db
fly postgres attach a2a-exchange-db
fly deploy
```

## Deploy to Railway

1. Fork the repository
2. Connect Railway to your fork
3. Add a PostgreSQL plugin
4. Set `DATABASE_URL` from the plugin
5. Deploy

## API documentation

Once running, visit:
- Swagger UI: `http://localhost:3000/docs`
- ReDoc: `http://localhost:3000/redoc`
- OpenAPI JSON: `http://localhost:3000/openapi.json`
