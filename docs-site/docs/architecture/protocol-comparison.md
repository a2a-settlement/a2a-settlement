# x402 vs AP2 vs A2A-SE

These three protocols address different layers of agent commerce. They are **complementary, not competing**.

**Thesis:** AP2 authorizes the agent. A2A-SE ensures the agent actually did the work before the money settles. x402 moves or gates value at the edge.

## Quick Comparison

| Concern | x402 | AP2 | A2A-SE |
|---------|------|-----|--------|
| Access gating | Yes | — | — |
| Spending authorization | — | Yes | Integrates |
| Payment / value movement | Micropayment | Negotiation | No (rail-agnostic) |
| Economic commitment / escrow | — | — | **Yes** |
| Dispute resolution | — | — | **Yes** |
| Reputation / finality | — | — | **Yes** |
| Multi-turn tasks | — | — | **Yes** |

## x402 — Access / value at the edge

**Question:** *"Can I talk to this agent?"* (or *"pay to unlock this call"*)

x402 is a **pay-per-call** micropayment / access protocol. An agent behind an x402 gate returns HTTP 402 until the caller submits a micropayment. Think of it as a **toll booth** on the payment layer.

## AP2 — Authorization

**Question:** *"May this agent spend?"*

AP2 (Agent Payments Protocol) focuses on **authorization and payment negotiation** — method selection, limits, and permission for an agent to initiate spend. It does not define whether work was completed before value reaches finality.

**Relationship:** AP2 authorizes. A2A-SE settles.

## A2A-SE — Settlement

**Question:** *"Was the obligation satisfied, and what happens to committed value?"*

A2A-SE provides **settlement semantics** for A2A tasks:

- Economic commitment (escrow or equivalent) before work
- Hold during multi-turn execution
- Release on verification, refund on failure
- Disputes, reputation, auditability

Escrow is one mechanism; **settlement is the abstraction**.

## Hybrid flow

```
Client                  Provider                    Exchange
  |                        |                           |
  |---x402 (optional)----->|  (access gate)            |
  |<--200 + AgentCard------|                           |
  |                        |                           |
  |---authorize (AP2)------|  (may the agent spend?)   |
  |---POST /escrow---------|-------------------------->|  (commit)
  |---A2A message/send---->|                           |
  |       ...working...    |                           |
  |<--A2A task completed---|                           |
  |---POST /release--------|-------------------------->|  (settle)
```

## When to use what

- **x402:** Gate access or collect per-call micropayments
- **AP2:** Authorize and negotiate spend
- **A2A-SE:** Settle task-based work with commitment, verification, and finality

Most A2A agents integrating settlement use **A2A-SE** directly. x402 and AP2 are optional adjacent layers.

See also: [What is Agent Settlement?](../agent-settlement) · [Agent Escrow](../concepts/agent-escrow) · [AP2 vs A2A-SE](../comparisons/ap2-vs-a2ase)
