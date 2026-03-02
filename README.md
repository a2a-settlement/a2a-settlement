# A2A Settlement Extension (A2A-SE)

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://python.org)
[![Spec: v0.8.1](https://img.shields.io/badge/spec-v0.8.1-green.svg)](SPEC.md)
[![Node 18+](https://img.shields.io/badge/node-18%2B-green.svg)](sdk-ts/)

**A2A-SE adds escrow-based payment to the A2A protocol in under 100 lines of integration code.** When two agents discover each other through A2A and one performs work for the other, A2A-SE holds funds in escrow during task execution and releases them on completion -- or refunds them on failure. Zero modifications to A2A core. Currency-agnostic. The exchange is an interface, not a service: agents can point to any conforming implementation (hosted, self-hosted, or on-chain).

```
RequesterAgent  <---- A2A ---->  ProviderAgent
     |                               |
     +--------- HTTPS (A2A-SE) ------+
                 Exchange
          escrow / release / refund
```

## Try the hosted sandbox (zero install)

Test agent payments without running any infrastructure. The public sandbox at **https://sandbox.a2a-settlement.org** gives you starter credits on registration.

```bash
git clone https://github.com/widrss/a2a-settlement
cd a2a-settlement
pip install -e ./sdk
A2A_EXCHANGE_URL=https://sandbox.a2a-settlement.org python examples/quickstart.py
```

You should see an escrow created and released, and balances updated. Registration is open; no API key needed beforehand — the quickstart registers two demo accounts and runs a full escrow cycle.

## Get started in 60 seconds (local exchange)

To run the exchange locally instead:

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

**Optional integrations:** Add [a2a-settlement-auth](https://github.com/a2a-settlement/a2a-settlement-auth) middleware for OAuth-based economic authorization. Run [a2a-settlement-mediator](https://github.com/a2a-settlement/a2a-settlement-mediator) as a sidecar for AI-powered dispute resolution. Use [a2a-settlement-dashboard](https://github.com/a2a-settlement/a2a-settlement-dashboard) for human oversight.

## Repo structure

- `SPEC.md` -- the extension specification (v0.8.1)
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

## Security: The Zero-Trust Bridge

The a2a-settlement bridge serves as the authoritative security layer between autonomous agents (LangGraph, CrewAI, LiteLLM) and sensitive infrastructure. It mitigates **Agent-on-Agent (A2A) attacks**—such as the hackerbot-claw exploit—by replacing static API permissions with dynamic, reputation-gated execution.

### Core Security Primitives

| Primitive | Description |
|-----------|-------------|
| **Reputation-Gated Execution (EMA)** | Every agent action is filtered through an Exponential Moving Average trust score. High-risk tools (Shell, PR Merge, Cloud Console) require a minimum $Rep_{EMA}$ threshold. A single logic dispute triggers an immediate reputation decay, isolating the agent before escalation. |
| **Ephemeral AgentCards** | Replaces persistent environment variables with task-specific, cryptographically signed identities. These cards define a strict "Intent Scope"—any command execution outside this scope (e.g., unauthorized `curl` to metadata services) results in immediate credential revocation. |
| **Security-Weighted CBS** | The Composite Bid Score (CBS) forces a trade-off between performance and security. For mission-critical tasks, the bridge prioritizes agents with high Verifiable Logic Proofs ($w_2$) over raw speed or cost. |
| **Logic Verifiability Layer** | Before an action is committed to the bridge, the agent must submit a "pre-flight" logic proof. The settlement layer validates this against defined safety policies, preventing "hallucinated" or malicious privilege escalations. |

### Defensive Mapping: a2a-settlement vs. hackerbot-claw

| Threat Vector | a2a-settlement Mitigation |
|---------------|---------------------------|
| Privilege Escalation | EMA Thresholding prevents unvetted agents from accessing sudo/shell. |
| Identity Hijacking | AgentCards ensure only the specific "Identified Agent" can execute the signed task. |
| Payload Injection | Logic Proofs require the agent to declare intent before writing to a repo/pipeline. |
| Lateral Movement | Settlement Isolation freezes an agent's status across the entire network upon a single dispute. |

## API documentation

When the exchange is running (locally or sandbox), visit:
- **Sandbox Swagger UI**: https://sandbox.a2a-settlement.org/docs
- **Sandbox ReDoc**: https://sandbox.a2a-settlement.org/redoc
- **Local**: http://localhost:3000/docs and http://localhost:3000/redoc

Or see `openapi.yaml` in the repo root for the normative spec.

## Development

```bash
pip install -e ".[exchange,examples,dev]"
pytest -q
```

## Ecosystem

| Project | Description |
|---------|-------------|
| [a2a-settlement-auth](https://github.com/a2a-settlement/a2a-settlement-auth) | OAuth 2.0 settlement scopes for agent economic authorization — spending limits, counterparty policies, delegation chains |
| [a2a-settlement-mediator](https://github.com/a2a-settlement/a2a-settlement-mediator) | AI-powered dispute resolution — evaluates disputed escrows, auto-resolves clear cases, escalates ambiguous ones |
| [a2a-settlement-dashboard](https://github.com/a2a-settlement/a2a-settlement-dashboard) | Human oversight dashboard — monitor agent spending, audit transactions, revoke economic authority |
| [a2a-settlement-mcp](https://github.com/a2a-settlement/a2a-settlement-mcp) | MCP server — exposes exchange operations as tools for Claude, Cursor, LangGraph, or any MCP client |
| [langgraph-a2a-settlement](https://github.com/a2a-settlement/langgraph-a2a-settlement) | LangGraph integration — escrow-gated graph nodes, `create_settlement_graph` |
| [crewai-a2a-settlement](https://github.com/a2a-settlement/crewai-a2a-settlement) | CrewAI integration — `SettledCrew` / `SettledTask` wrappers |
| [litellm-a2a-settlement](https://github.com/a2a-settlement/litellm-a2a-settlement) | LiteLLM integration — callback hooks for escrow on A2A agent calls |
| [adk-a2a-settlement](https://github.com/a2a-settlement/adk-a2a-settlement) | Google ADK integration — `to_settled_a2a`, `SettledRemoteAgent`, settlement tools |

**Environment variables:** Most integrations use `A2A_EXCHANGE_URL` (e.g. `http://localhost:3000`) and `A2A_API_KEY`. ADK and CrewAI use `A2ASE_EXCHANGE_URL` / `A2ASE_API_KEY`. The exchange API lives under `/v1`; the SDK appends this automatically.

## License

MIT. See `LICENSE`.
