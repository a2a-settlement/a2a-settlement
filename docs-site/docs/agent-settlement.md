---
sidebar_position: 1
slug: /agent-settlement/
title: What is Agent Settlement?
description: Agent Settlement is the layer that decides whether an economic obligation between agents was satisfied — and what happens to committed value.
---

# What is Agent Settlement?

**Agent Settlement** is the layer of autonomous agent commerce that answers:

> Was the economic obligation satisfied, and what happens to the committed value?

It sits beside — not instead of — **payments** (how value moves) and **authorization** (whether an agent may spend).

**A2A-SE (A2A Settlement Extension)** is an open standard for that settlement layer on top of the [Agent2Agent (A2A) protocol](https://google.github.io/a2a/).

## The problem

A2A lets agents discover each other, exchange tasks, and collaborate. It does not define how agents compensate each other, hold value during multi-step work, refund failure, resolve disputes, or record finality.

Without settlement semantics, agent commerce collapses into “send money and hope” — or into product-specific escrow that cannot be tested or federated.

## Three layers

| Layer | Question | Examples |
|-------|----------|----------|
| **Payments** | How does value move? | Bank rails, USDC, x402 micropayments, ATE |
| **Authorization** | May this agent spend? | AP2, OAuth settlement scopes, spend caps |
| **Settlement** | Was the obligation satisfied? | A2A-SE escrow, release, refund, disputes, reputation |

A2A-SE does **not** replace payment rails. It is designed to sit above or alongside them. Escrow is one mechanism that implements an **economic commitment**; **settlement is the abstraction**.

## Settlement lifecycle

```text
Identity → Authorization → Economic Commitment → Work → Verification → Settlement → Finality
```

1. **Identity** — Who is the agent? (DID / AgentCard; A2A-SE integrates, does not own)
2. **Authorization** — Is spend allowed? (AP2 / OAuth scopes; A2A-SE integrates)
3. **Economic commitment** — Value is locked for a task (escrow or equivalent)
4. **Work** — Provider executes under A2A task semantics
5. **Verification** — Deliverable checked (provenance, grounding, human review, …)
6. **Settlement** — Release, refund, partial release, or dispute resolution
7. **Finality** — Outcome recorded; reputation and audit trail updated

## A2A-SE

A2A-SE defines settlement semantics for A2A tasks: AgentCard pricing extensions, escrow lifecycle aligned to A2A task states, exchange API as an **interface** (not a single hosted service), currency-agnostic tokens, disputes, and reputation.

- Spec: [SPEC.md](https://github.com/a2a-settlement/a2a-settlement/blob/main/SPEC.md) (v0.11.0)
- Reference exchange: [exchange.a2a-settlement.org](https://exchange.a2a-settlement.org/docs)
- Conformance: [settlement-conformance](https://github.com/a2a-settlement/settlement-conformance)

## Interoperability

- **x402** gates access (can I talk to this agent?).
- **AP2** authorizes spending (may the agent spend?).
- **A2A-SE** settles the obligation (was the work done before value moves to finality?).

They are complementary. See [x402 vs AP2 vs A2A-SE](/docs/architecture/protocol-comparison).

**Federation** (cross-exchange peering, portable reputation) is specified separately in [a2a-federation-rfc](https://github.com/a2a-settlement/a2a-federation-rfc). Federation is **not** required to understand or implement A2A-SE Core.

## Why settlement matters

Without a settlement layer, agent economies cannot prove that work was done before value finalized, cannot refund failure cleanly, and cannot build portable reputation. Settlement turns “agents paid each other” into an auditable economic outcome.

Next: [Big Picture: Agent Transaction Lifecycle](/docs/big-picture/) · [Run a Settlement](https://exchange.a2a-settlement.org/docs) · [Standards & Provenance](/docs/standards/)
