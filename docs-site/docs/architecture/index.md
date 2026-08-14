# Architecture Overview

The A2A Settlement ecosystem spans multiple layers. This section explains how they fit together.

## Protocol Stack

```
┌─────────────────────────────────────────────────────────────────┐
│  Application Layer: CrewAI, ADK, LiteLLM, custom orchestrators   │
├─────────────────────────────────────────────────────────────────┤
│  A2A Protocol: Discovery, Tasks, Messages, Artifacts             │
├─────────────────────────────────────────────────────────────────┤
│  Payment Layer: x402 (access) | AP2 (negotiation) | A2A-SE      │
│                 (escrow, release, refund, disputes)             │
├─────────────────────────────────────────────────────────────────┤
│  Identity & Auth: OAuth + settlement scopes (a2a-settlement-auth)  │
├─────────────────────────────────────────────────────────────────┤
│  Settlement Exchange: REST API, ledger, reputation                │
└─────────────────────────────────────────────────────────────────┘
```

## Key Concepts

| Concept | Where | Description |
|---------|-------|--------------|
| [x402 vs AP2 vs A2A-SE](./protocol-comparison) | Payment layer | Complementary protocols for different payment concerns |
| [WORM Merkle Trees](./worm-merkle) | Mediator | SEC 17a-4 compliant append-only audit trail |
| [NIST Compliance](./nist-compliance) | Auth | OAuth settlement scopes aligned with NIST standards |

## Deployment Models

| Model | Description |
|-------|-------------|
| **Hosted exchange** | Public SaaS (e.g., exchange.a2a-settlement.org). Agents register and settle through operator infrastructure. |
| **Self-hosted** | Private instance behind firewall. Same API, internal agents only. |
| **On-chain wrapper** | Smart contract fronted by REST adapter. Settlement finality on-chain. |

Two agents in the same task may use different exchanges. Cross-exchange settlement is out of scope for v0.5.
