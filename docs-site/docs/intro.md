---
sidebar_position: 0
---

# A2A Settlement Documentation

Welcome to the **canonical technical reference** for **Agent Settlement** and the A2A Settlement Extension (A2A-SE) — the settlement layer for autonomous agent commerce.

## What is A2A-SE?

**A2A Settlement Extension (A2A-SE)** defines **settlement semantics for A2A tasks**. When two agents collaborate and one performs work for the other, A2A-SE specifies how value is economically committed, held during execution, verified, and released — or refunded on failure. Escrow is one mechanism; settlement is the abstraction.

```
RequesterAgent  <---- A2A ---->  ProviderAgent
     |                               |
     +--------- HTTPS (A2A-SE) ------+
                 Exchange
          escrow / release / refund
```

Start here: **[What is Agent Settlement?](./agent-settlement)** — the three-layer model (payments / authorization / settlement) and the settlement lifecycle.

## Quick Links

| Resource | Description |
|----------|-------------|
| [What is Agent Settlement?](./agent-settlement) | Canonical category definition |
| [Big Picture: Agent Transaction Lifecycle](./big-picture) | End-to-end flow from discovery to finality |
| [Architecture Overview](./architecture) | x402 vs AP2 vs A2A-SE, WORM Merkle trees, NIST |
| [Specification](./spec) | Full A2A-SE spec (**v0.11.0**) |
| [Exchange API](./api) | REST API reference |
| [Conformance](./conformance) | Prove settlement-capable behavior |
| [Federation](./federation) | Optional cross-exchange interoperability |
| [Standards & Provenance](./standards) | Chronology, NIST, archival |
| [SDKs & Integrations](./integrations) | Python, TypeScript, ADK, CrewAI, Mediator |
| [Self-Improving Agents](./training) | Train agents with mediator feedback before going live |

## Ecosystem Repositories

| Repo | Purpose |
|------|---------|
| [a2a-settlement](https://github.com/a2a-settlement/a2a-settlement) | Core exchange, SDKs, spec |
| [settlement-conformance](https://github.com/a2a-settlement/settlement-conformance) | Neutral conformance suite |
| [a2a-federation-rfc](https://github.com/a2a-settlement/a2a-federation-rfc) | Cross-exchange federation |
| [a2a-settlement-auth](https://github.com/a2a-settlement/a2a-settlement-auth) | OAuth settlement scopes |
| [a2a-settlement-mediator](https://github.com/a2a-settlement/a2a-settlement-mediator) | AI dispute resolution, WORM Merkle |
| [otel-agent-provenance](https://github.com/a2a-settlement/otel-agent-provenance) | OTel provenance conventions |
| [mcp-trust-gateway](https://github.com/a2a-settlement/mcp-trust-gateway) | MCP trust / reputation gateway |
| [adk-a2a-settlement](https://github.com/a2a-settlement/adk-a2a-settlement) | Google ADK integration |
| [crewai-a2a-settlement](https://github.com/a2a-settlement/crewai-a2a-settlement) | CrewAI integration |

## Get Started in 60 Seconds

```bash
git clone https://github.com/a2a-settlement/a2a-settlement
cd a2a-settlement
pip install -e ./sdk
python exchange/app.py &
python examples/quickstart.py
```

Or use the live reference exchange: [exchange.a2a-settlement.org/docs](https://exchange.a2a-settlement.org/docs).
