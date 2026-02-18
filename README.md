# A2A Settlement Extension (A2A-SE)

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://python.org)
[![Spec: v0.7.0](https://img.shields.io/badge/spec-v0.7.0-green.svg)](SPEC.md)
[![Node 18+](https://img.shields.io/badge/node-18%2B-green.svg)](sdk-ts/)

**A2A-SE adds escrow-based payment to the A2A protocol in under 100 lines of integration code.** When two agents discover each other through A2A and one performs work for the other, A2A-SE holds funds in escrow during task execution and releases them on completion -- or refunds them on failure. Zero modifications to A2A core. Currency-agnostic. The exchange is an interface, not a service: agents can point to any conforming implementation (hosted, self-hosted, or on-chain).

```
RequesterAgent  <---- A2A ---->  ProviderAgent
     |                               |
     +--------- HTTPS (A2A-SE) ------+
                 Exchange
          escrow / release / refund
```

## Get started in 60 seconds

```bash
git clone https://github.com/widrss/a2a-settlement
cd a2a-settlement
pip install -e ./sdk
python exchange/app.py &
python examples/quickstart.py
```

You should see an escrow created and released, and balances updated.

## SDKs

| Language | Package | Install |
|----------|---------|---------|
| Python | `a2a-settlement` | `pip install -e ./sdk` |
| TypeScript/JS | `@a2a-settlement/sdk` | `cd sdk-ts && npm install` |

Both SDKs mirror the same method signatures. See [sdk/](sdk/) and [sdk-ts/](sdk-ts/) for docs.

## Deploy your own exchange

**Docker Compose** (recommended):

```bash
docker compose up -d
curl http://localhost:3000/health
```

**Fly.io**:

```bash
fly launch --copy-config
fly postgres create --name a2a-exchange-db
fly postgres attach a2a-exchange-db
fly deploy
```

**Railway**: Fork the repo, connect Railway, add the PostgreSQL plugin, deploy.

See [docs/self-hosting.md](docs/self-hosting.md) for full environment variable reference.

## Repo structure

- `SPEC.md` -- the extension specification (v0.6.0)
- `openapi.yaml` -- OpenAPI 3.1 spec for the exchange API
- `exchange/` -- FastAPI + SQLAlchemy settlement exchange (SQLite dev, Postgres prod)
- `sdk/` -- pip-installable Python SDK
- `sdk-ts/` -- npm-installable TypeScript/JavaScript SDK
- `examples/` -- runnable demos (including A2A SDK integration)
- `docs/` -- architecture, integration guide, pricing models, self-hosting
- `Dockerfile` + `docker-compose.yml` -- containerized deployment
- `fly.toml` + `railway.json` -- one-click cloud deploy configs

## How A2A-SE compares to AP2 and x402

These three protocols address different layers of the agent payment stack. They are complementary, not competing.

- **x402** is an access gate: pay-per-call micropayments to talk to an agent. Think of it as a toll booth.
- **AP2** (Agent Payments Protocol) handles payment negotiation: "how will we pay?" It defines flows for agents to agree on payment methods and amounts.
- **A2A-SE** handles task escrow: "hold these funds while I work, then release them." It provides escrow, multi-step settlement, dispute resolution, and reputation tracking.

| Concern | AP2 | x402 | A2A-SE |
|---------|-----|------|--------|
| Payment negotiation | Yes | -- | Lightweight |
| Access gating | -- | Yes | -- |
| Task escrow | -- | -- | Yes |
| Dispute resolution | -- | -- | Yes |
| Reputation | -- | -- | Yes |
| Multi-turn tasks | -- | -- | Yes |

An agent can use all three: x402 gates discovery, AP2 negotiates terms, A2A-SE escrows the payment.

## API documentation

When the exchange is running, visit:
- **Swagger UI**: http://localhost:3000/docs
- **ReDoc**: http://localhost:3000/redoc
- **OpenAPI JSON**: http://localhost:3000/openapi.json

Or see `openapi.yaml` in the repo root for the normative spec.

## Development

```bash
pip install -e ".[exchange,examples,dev]"
pytest -q
```

## License

MIT. See `LICENSE`.
