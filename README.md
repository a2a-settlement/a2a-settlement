# A2A Settlement Extension (A2A-SE)

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://python.org)
[![Spec: v0.11.0](https://img.shields.io/badge/spec-v0.11.0-green.svg)](SPEC.md)
[![Node 18+](https://img.shields.io/badge/node-18%2B-green.svg)](sdk-ts/)

**A2A-SE defines settlement semantics for A2A tasks.** It is an open standard for the **settlement layer** of autonomous agent commerce: economic commitment, delivery, verification, release/refund, disputes, reputation, and finality. Escrow is one mechanism that implements commitment; settlement is the abstraction. Zero modifications to A2A core. Currency-agnostic. The exchange is an **interface**, not a single hosted service.

> Spec first published **February 17, 2026** — initial commit [`c5ba9aaa`](https://github.com/a2a-settlement/a2a-settlement/commit/c5ba9aaa8bfca489d1f95cd78a695b98988dacc2).

```
RequesterAgent  <---- A2A ---->  ProviderAgent
     |                               |
     +--------- HTTPS (A2A-SE) ------+
                 Exchange
          escrow / release / refund
```

## Scope

| Layer | A2A-SE |
|-------|--------|
| Communication (A2A) | No — uses A2A as-is |
| Identity | Integrates |
| Spending authorization | Integrates (AP2 / OAuth scopes) |
| Payment rail | No |
| **Settlement** | **Yes** |

### Non-Goals

- Providing the underlying payment rail (USDC, bank wires, x402, …)
- Being the system of record for agent identity
- Requiring federation to use Core settlement semantics
- Replacing A2A messaging

## Normative links

| Resource | Link |
|----------|------|
| **Specification** | [SPEC.md](SPEC.md) (v0.11.0) |
| **Conformance** | [settlement-conformance](https://github.com/a2a-settlement/settlement-conformance) |
| **Federation** (optional) | [a2a-federation-rfc](https://github.com/a2a-settlement/a2a-federation-rfc) |
| **Reference implementation** | this repo (`exchange/`) |
| **Docs / category definition** | [What is Agent Settlement?](https://docs.a2a-settlement.org/docs/agent-settlement/) |
| **DOI / archival** | SPEC v0.11.0 [10.5281/zenodo.21953795](https://doi.org/10.5281/zenodo.21953795) ([all versions](https://doi.org/10.5281/zenodo.21953794)) · NIST comments [CAISI](https://doi.org/10.5281/zenodo.21745191) · [NCCoE](https://doi.org/10.5281/zenodo.21745274) |

## Run a settlement

Interactive demo (state machine): **https://sandbox.a2a-settlement.org**

Live OpenAPI UI: **https://exchange.a2a-settlement.org/docs**

```bash
git clone https://github.com/a2a-settlement/a2a-settlement
cd a2a-settlement
pip install -e ./sdk
A2A_EXCHANGE_URL=https://exchange.a2a-settlement.org python examples/quickstart.py
```

Local exchange:

```bash
python exchange/app.py &
python examples/quickstart.py
```

## SDKs & integrations

| Language | Package | Install |
|----------|---------|---------|
| Python | `a2a-settlement` | `pip install -e ./sdk` |
| TypeScript/JS | `@a2a-settlement/sdk` | `cd sdk-ts && npm install` |

Framework integrations (move higher intentionally — infrastructure is only as valuable as what plugs into it):

| Project | Framework |
|---------|-----------|
| [adk-a2a-settlement](https://github.com/a2a-settlement/adk-a2a-settlement) | Google ADK |
| [langgraph-a2a-settlement](https://github.com/a2a-settlement/langgraph-a2a-settlement) | LangGraph |
| [crewai-a2a-settlement](https://github.com/a2a-settlement/crewai-a2a-settlement) | CrewAI |
| [litellm-a2a-settlement](https://github.com/a2a-settlement/litellm-a2a-settlement) | LiteLLM |
| [a2a-settlement-mcp](https://github.com/a2a-settlement/a2a-settlement-mcp) | MCP |

## Deploy your own exchange

**Docker Compose** (recommended):

```bash
docker compose up -d
curl http://localhost:3000/health
```

See [docs/self-hosting.md](docs/self-hosting.md) for Fly.io, Railway, and environment variables.

**Optional:** [a2a-settlement-auth](https://github.com/a2a-settlement/a2a-settlement-auth), Security Shim (`shim/`), [a2a-settlement-mediator](https://github.com/a2a-settlement/a2a-settlement-mediator), [a2a-settlement-dashboard](https://github.com/a2a-settlement/a2a-settlement-dashboard), [Self-Improving Agent Loop](docs/self-improving-agents.md).

## Repo structure

- `SPEC.md` — extension specification (**v0.11.0**)
- `openapi.yaml` — OpenAPI 3.1 exchange API
- `docs-site/` — Docusaurus docs (docs.a2a-settlement.org)
- `site/` — marketing homepage (a2a-settlement.org)
- `exchange/` — reference FastAPI exchange
- `shim/` — Security Shim (Economic Air Gap)
- `sdk/` / `sdk-ts/` — Python and TypeScript SDKs
- `examples/` — runnable demos

## How A2A-SE compares to AP2 and x402

| Layer | Protocol | Question |
|-------|----------|----------|
| Payments / access | **x402** | Can I talk to / pay for this call? |
| Authorization | **AP2** | May this agent spend? |
| **Settlement** | **A2A-SE** | Was the obligation satisfied? |

They are complementary. Full write-up: [docs.a2a-settlement.org](https://docs.a2a-settlement.org/docs/architecture/protocol-comparison).

## Ecosystem

### Standard & conformance

| Project | Description |
|---------|-------------|
| [settlement-conformance](https://github.com/a2a-settlement/settlement-conformance) | Neutral conformance suite — prove settlement-capable without our exchange |
| [a2a-federation-rfc](https://github.com/a2a-settlement/a2a-federation-rfc) | Optional federation across independent exchanges |

### Reference infrastructure

| Project | Description |
|---------|-------------|
| [a2a-settlement-auth](https://github.com/a2a-settlement/a2a-settlement-auth) | OAuth 2.0 settlement scopes |
| [a2a-settlement-mediator](https://github.com/a2a-settlement/a2a-settlement-mediator) | AI-powered dispute resolution |
| [a2a-settlement-dashboard](https://github.com/a2a-settlement/a2a-settlement-dashboard) | Human oversight dashboard |
| [a2a-settlement-mcp](https://github.com/a2a-settlement/a2a-settlement-mcp) | MCP settlement tools |
| [settlebridge-ai](https://github.com/a2a-settlement/settlebridge-ai) | SettleBridge Gateway (product) |
| [mcp-trust-gateway](https://github.com/a2a-settlement/mcp-trust-gateway) | MCP trust evaluation |
| [otel-agent-provenance](https://github.com/a2a-settlement/otel-agent-provenance) | OTel provenance conventions |

## License

MIT. See `LICENSE`.
