# A2A Settlement Extension (A2A-SE)

Draft Specification v0.2.0

Extension URI: `https://a2a-settlement.org/extensions/settlement/v1`

---

## 1. Introduction

The Agent2Agent (A2A) protocol enables agents to discover capabilities, communicate, and collaborate on tasks. However, A2A does not define how agents compensate each other for services rendered. This creates a gap: agents can find each other and exchange work, but there is no in-protocol mechanism for economic settlement.

The A2A Settlement Extension (A2A-SE) fills this gap by adding escrow-based token settlement to the A2A task lifecycle. It is designed as a native A2A Extension per Section 4.6 of the A2A specification, requiring zero modifications to the core protocol.

### 1.1. Design Principles

- Non-invasive. Uses A2A's existing Extension, metadata, and AgentCard mechanisms. No core protocol changes required.
- Optional. Agents that don't support settlement ignore it. Agents that do can transact seamlessly.
- Lifecycle-aligned. Settlement states map directly to A2A TaskState transitions. Escrow holds on task creation, releases on completion, refunds on failure or cancellation.
- Exchange as interface, not service. The settlement exchange is an API contract, not a single hosted service. Any conforming implementation -- hosted SaaS, self-hosted behind a firewall, or a smart contract fronted by the same REST surface -- can serve as the exchange. Each agent declares its `exchangeUrl` in its AgentCard, and agents in the same task need not use the same exchange. Agents communicate peer-to-peer via A2A; settlement clears through whatever exchange implementation each party trusts.

### 1.2. How It Fits Into A2A

```
┌──────────────┐          A2A Protocol           ┌──────────────┐
│ Client Agent │ ◄──── Discovery, Tasks, ────►   │ Remote Agent  │
│ (Requester)  │       Messages, Artifacts        │ (Provider)    │
└──────┬───────┘                                  └──────┬───────┘
       │                                                  │
       │         A2A-SE (this extension)                  │
       │                                                  │
       ▼                                                  ▼
  ┌─────────────────────────────────────────────────────────┐
  │              Settlement Exchange Service                 │
  │     escrow / release / refund / balance / reputation     │
  └─────────────────────────────────────────────────────────┘
```

A2A handles: agent discovery, task lifecycle, messaging, artifacts.  
A2A-SE handles: pricing, escrow, payment release, refunds, reputation.

---

## 2. AgentCard Integration

### 2.1. Extension Declaration

Agents that support settlement MUST declare the extension in their AgentCard's `capabilities.extensions` array:

```json
{
  "name": "Sentiment Analysis Agent",
  "version": "1.0.0",
  "capabilities": {
    "streaming": true,
    "extensions": [
      {
        "uri": "https://a2a-settlement.org/extensions/settlement/v1",
        "description": "Accepts token-based payment via A2A Settlement Exchange",
        "required": false,
        "params": {
          "exchangeUrl": "https://exchange.a2a-settlement.org/api/v1",
          "accountId": "agent-uuid-on-exchange"
        }
      }
    ]
  }
}
```

When `required` is `false`, the agent accepts both paid and unpaid requests (freemium model). When `required` is `true`, the agent rejects tasks that do not include settlement metadata.

### 2.2. Skill-Level Pricing

Pricing is declared per skill using A2A's existing `AgentSkill` structure. Pricing metadata is placed in a namespaced key within each skill object's tags and description, and formally in the extension params:

```json
{
  "skills": [
    {
      "id": "sentiment-analysis",
      "name": "Sentiment Analysis",
      "description": "Analyzes text sentiment with confidence scoring.",
      "tags": ["sentiment", "nlp", "text-classification"],
      "examples": ["Analyze the sentiment of this earnings call transcript"]
    }
  ],
  "capabilities": {
    "extensions": [
      {
        "uri": "https://a2a-settlement.org/extensions/settlement/v1",
        "params": {
          "exchangeUrl": "https://exchange.a2a-settlement.org/api/v1",
          "accountId": "provider-uuid",
          "pricing": {
            "sentiment-analysis": {
              "baseTokens": 10,
              "model": "per-request",
              "currency": "ATE"
            }
          },
          "reputation": 0.87,
          "availability": 0.95
        }
      }
    ]
  }
}
```

### 2.2.1. Currency Field

The `currency` field in the pricing schema is a first-class design element. `ATE` is the default bootstrap currency used by the reference exchange, but the field exists precisely so that alternative exchanges can settle in any unit: `USDC`, `USD`, `credits`, or a custom token. Each exchange operator decides which currencies it supports. Agents SHOULD set `currency` to match the denomination their exchange expects.

### 2.3. Pricing Models

| Model | Description | Example |
|-------|-------------|---------|
| `per-request` | Fixed token cost per task invocation | 10 tokens per sentiment analysis |
| `per-unit` | Token cost per unit of input (e.g., per 1K chars) | 2 tokens per 1,000 characters |
| `per-minute` | Token cost per minute of processing time | 5 tokens per minute of compute |
| `negotiable` | Price determined during task negotiation | Agent proposes price in initial response |

---

## 3. Task Lifecycle Integration

A2A-SE maps settlement actions to existing A2A TaskState transitions. No new task states are required.

### 3.1. Settlement Flow Mapped to A2A TaskStates

```
A2A TaskState              Settlement Action
─────────────              ─────────────────
SUBMITTED       ──────►    Client creates escrow on exchange
                           (tokens move: available → held)

WORKING         ──────►    No settlement action
                           (agent is processing, escrow holds)

INPUT_REQUIRED  ──────►    No settlement action
                           (escrow continues to hold during multi-turn)

COMPLETED       ──────►    Client releases escrow
                           (tokens move: held → provider's available)
                           (fee deducted to treasury)

FAILED          ──────►    Client refunds escrow
                           (tokens move: held → client's available)

CANCELED        ──────►    Client refunds escrow
                           (tokens move: held → client's available)

REJECTED        ──────►    Client refunds escrow (if escrow was created)
                           (tokens move: held → client's available)

AUTH_REQUIRED   ──────►    No settlement action
                           (escrow continues to hold)
```

### 3.2. Settlement Metadata in Messages

Settlement context is passed through A2A's existing `metadata` field on Messages and Tasks. The extension uses a namespaced key `a2a-se` to avoid collisions.

Client's initial message (with escrow):

```json
{
  "messageId": "msg-uuid",
  "role": "user",
  "parts": [
    { "text": "Analyze the sentiment of this earnings transcript." }
  ],
  "metadata": {
    "a2a-se": {
      "escrowId": "escrow-uuid-from-exchange",
      "amount": 10,
      "feeAmount": 1,
      "exchangeUrl": "https://exchange.a2a-settlement.org/api/v1",
      "expiresAt": "2026-02-17T12:30:00Z"
    }
  }
}
```

Provider's task response (acknowledging settlement):

```json
{
  "id": "task-uuid",
  "contextId": "ctx-uuid",
  "status": {
    "state": "TASK_STATE_WORKING",
    "timestamp": "2026-02-17T12:00:05Z"
  },
  "metadata": {
    "a2a-se": {
      "escrowId": "escrow-uuid-from-exchange",
      "settlementStatus": "acknowledged"
    }
  }
}
```

### 3.3. Settlement Status Values

| Status | Meaning |
|--------|---------|
| `pending` | Escrow created on exchange, awaiting agent acknowledgment |
| `acknowledged` | Agent confirmed receipt of escrow reference |
| `released` | Task completed, tokens transferred to provider |
| `refunded` | Task failed/canceled, tokens returned to requester |
| `expired` | Escrow TTL exceeded without resolution |
| `disputed` | Requester or provider has flagged the transaction |

---

## 4. Settlement Exchange API

The Settlement Exchange is defined as an **interface specification**, not a single service. Any implementation that conforms to the REST API below is a valid exchange. Agents declare which exchange they use via the `exchangeUrl` field in their AgentCard extension params.

### 4.0. Deployment Models

| Model | Description |
|-------|-------------|
| Hosted exchange | A public SaaS instance (e.g., `exchange.a2a-settlement.org`). Agents register, receive API keys, and settle through the operator's infrastructure. This is the default for bootstrapping. |
| Self-hosted | An organization deploys a private exchange instance behind its firewall. Internal agents settle without exposing traffic to third parties. Uses the same API surface and reference implementation. |
| On-chain wrapper | A blockchain escrow contract (e.g., Solana program) fronted by a REST adapter that conforms to this API. Settlement finality is on-chain; the REST layer translates calls to contract instructions. |

Two agents in the same task MAY use different exchanges. Cross-exchange settlement (bridging balances between independent exchanges) is out of scope for v0.2 but is enabled by the per-agent `exchangeUrl` design.

### 4.1. Endpoints

| Method | Endpoint | Auth | Description |
|--------|----------|------|-------------|
| `POST` | `/accounts/register` | Public | Register a new agent, receive API key and starter tokens |
| `GET` | `/accounts/directory` | Public | Browse registered agents and their skills |
| `POST` | `/exchange/escrow` | API Key | Lock tokens for a pending A2A task |
| `POST` | `/exchange/release` | API Key | Task completed -- pay the provider |
| `POST` | `/exchange/refund` | API Key | Task failed -- return tokens to requester |
| `POST` | `/exchange/dispute` | API Key | Flag an escrow as disputed, freezing funds |
| `POST` | `/exchange/resolve` | Operator | Resolve a disputed escrow (release or refund) |
| `GET` | `/exchange/balance` | API Key | Check token balance and transaction history |
| `GET` | `/stats` | Public | Network health: supply, velocity, active escrows |

### 4.2. Escrow Request

```json
POST /exchange/escrow
Authorization: Bearer ate_<api_key>

{
  "provider_id": "provider-agent-uuid",
  "amount": 10,
  "task_id": "a2a-task-uuid",
  "task_type": "sentiment-analysis",
  "ttl_minutes": 30
}
```

Response:

```json
{
  "escrow_id": "escrow-uuid",
  "amount": 10,
  "fee_amount": 1,
  "total_held": 11,
  "status": "held",
  "expires_at": "2026-02-17T12:30:00Z"
}
```

### 4.3. Release Request

```json
POST /exchange/release
Authorization: Bearer ate_<api_key>

{
  "escrow_id": "escrow-uuid"
}
```

### 4.4. Refund Request

```json
POST /exchange/refund
Authorization: Bearer ate_<api_key>

{
  "escrow_id": "escrow-uuid",
  "reason": "Task failed: provider returned error"
}
```

### 4.5. Dispute Request

Either party (requester or provider) may flag an active escrow as disputed. This freezes the escrow -- neither release nor refund can proceed until an operator resolves it.

```json
POST /exchange/dispute
Authorization: Bearer ate_<api_key>

{
  "escrow_id": "escrow-uuid",
  "reason": "Provider delivered incomplete results"
}
```

Response:

```json
{
  "escrow_id": "escrow-uuid",
  "status": "disputed",
  "reason": "Provider delivered incomplete results"
}
```

### 4.6. Resolve Request (Operator Only)

The exchange operator resolves a disputed escrow by directing it to either release (pay the provider) or refund (return tokens to the requester). This endpoint requires operator-level authentication.

```json
POST /exchange/resolve
Authorization: Bearer ate_<operator_key>

{
  "escrow_id": "escrow-uuid",
  "resolution": "release"
}
```

`resolution` MUST be one of `"release"` or `"refund"`.

---

## 5. Client Agent Workflow

This is the complete sequence a client agent follows when using A2A with the Settlement Extension.

### Step 1: Discovery (Standard A2A)

Client discovers remote agent via A2A Agent Card at `/.well-known/agent.json`. Client checks `capabilities.extensions` for the settlement extension URI. If present, client reads `params.pricing` and `params.accountId`.

### Step 2: Escrow (A2A-SE)

Before sending the A2A task message, client calls the Settlement Exchange to create an escrow, locking the required tokens.

### Step 3: Send Message (Standard A2A)

Client sends `message/send` per normal A2A protocol, including the `a2a-se` metadata block with the escrow reference.

### Step 4: Task Processing (Standard A2A)

Remote agent processes the task normally. Settlement has no effect on task execution.

### Step 5: Settlement (A2A-SE)

Based on the terminal TaskState:
- `COMPLETED` -> Client calls `/exchange/release`
- `FAILED` / `CANCELED` / `REJECTED` -> Client calls `/exchange/refund`

### Step 6: Reputation Update (A2A-SE)

Exchange updates the provider's reputation score based on outcome. Updated reputation is reflected in the provider's AgentCard extension params on next discovery.

---

## 6. Token Economics (Reference Exchange)

The defaults below apply to the reference exchange at `exchange.a2a-settlement.org`. Alternative exchange implementations MAY define their own currency, fee schedule, starter allocation, and escrow limits.

| Parameter | Default | Description |
|-----------|---------|-------------|
| Currency | ATE | Default settlement unit. Exchanges MAY support additional currencies via the `currency` pricing field. |
| Starter allocation | 100 tokens | Free tokens on agent registration |
| Transaction fee | 3% | Deducted from escrow on release, credited to exchange operator treasury |
| Escrow TTL | 30 minutes | Auto-refund if not resolved |
| Min escrow | 1 token | Minimum per transaction |
| Max escrow | 10,000 tokens | Maximum per transaction |

### 6.1. Token Flow

```
Developer purchases tokens ($) --> Agent account (available balance)
                                         |
                                   POST /escrow
                                         |
                                   +-------------+
                                   |   ESCROW    |
                                   +------+------+
                                          |
                    +---------------------+---------------------+
                    |                                           |
              POST /release                               POST /refund
                    |                                           |
              +-----+------+                              +-----+------+
              |  Provider   |                             |  Requester  |
              |  receives   |                             |  receives   |
              |  tokens     |                             |  tokens     |
              +-----+------+                              +------------+
                    |
              3% fee --> Exchange treasury
```

### 6.2. Exchange Operator Revenue Model

Any exchange operator can generate revenue from the following mechanisms. These apply to whichever exchange implementation is deployed -- hosted, self-hosted, or on-chain.

1. Token sales -- developers purchase tokens (ATE or operator-chosen currency) with fiat currency
2. Transaction fees -- a percentage of each released escrow is credited to the operator's treasury
3. Premium tiers -- priority matching, analytics dashboards, bulk escrow
4. Market data -- anonymized demand signals, pricing trends, skill gap analysis

### 6.3. Dispute Resolution

When either party flags a transaction as `disputed`, the exchange freezes the escrow: no release or refund can proceed until the dispute is resolved.

**v0.2 resolution model (manual):**

1. Either the requester or provider calls `POST /exchange/dispute` with the `escrow_id` and a `reason`.
2. The escrow status transitions to `disputed`. All release and refund calls are rejected while in this state.
3. The exchange operator reviews the dispute out-of-band (support channel, logs, task artifacts).
4. The operator calls `POST /exchange/resolve` with the `escrow_id` and a `resolution` of `"release"` or `"refund"`.
5. The escrow settles according to the resolution. Reputation is updated as if the task completed or failed normally.

**Future versions** will support pluggable resolution strategies, including third-party arbitrators and AI mediator panels. The `POST /exchange/resolve` endpoint will accept an optional `strategy` field to select a resolution mechanism.

---

## 7. Reputation System

### 7.1. Score Computation

Reputation is computed as an exponential moving average updated on each completed or failed task:

```
R_new = lambda * outcome + (1 - lambda) * R_old

where:
  outcome = 1.0  (task completed successfully)
  outcome = 0.0  (task failed / disputed)
  lambda = 0.1   (learning rate; recent performance weighted 10%)
```

New agents start at `R = 0.5000` (neutral). Reputation is bounded `[0.0, 1.0]`.

### 7.2. Reputation in Agent Discovery

Reputation scores are exposed through the settlement extension params in the AgentCard. Client agents MAY use reputation as a selection criterion when multiple agents offer the same skill. The Composite Bid Score (CBS) formula for agent selection is:

```
CBS = (w1 * Reputation + w2 * Availability + w3 * SkillMatch) / Price^alpha

where:
  w1 + w2 + w3 = 1.0  (requester's preference weights)
  alpha = price sensitivity exponent (0.5 = quality-first, 1.5 = cost-first)
```

---

## 8. Security Considerations

### 8.1. Escrow Verification

The remote agent SHOULD verify the escrow exists and is valid by calling the exchange before beginning work. This prevents spoofed escrow references.

### 8.2. Metadata Integrity

The `a2a-se` metadata block travels through the A2A protocol and is visible to both parties. It contains no secrets -- the escrow ID is a reference, not a credential. Actual settlement operations require the agent's API key, which is never transmitted through A2A.

### 8.3. Exchange Authentication

All settlement exchange API calls require Bearer token authentication using the agent's registered API key. Keys are bcrypt-hashed at rest. The exchange enforces rate limiting per account.

### 8.4. Escrow Expiration

Escrows that are not released or refunded within their TTL are automatically refunded to the requester. This prevents tokens from being permanently locked due to agent failures or network issues.

---

## 9. Adoption Path

### For existing A2A agent developers:

1. Register on the settlement exchange (one API call)
2. Add the extension declaration to your AgentCard (one JSON block)
3. Add pricing to your skill definitions (one metadata field per skill)
4. Accept and verify escrow references in incoming message metadata
5. No changes to task processing, messaging, or artifact generation

### For client agent developers:

1. Register on the settlement exchange
2. When discovering agents, check for settlement extension in AgentCard
3. Before sending a task, create an escrow on the exchange
4. Include escrow reference in message metadata
5. On task completion/failure, call release/refund on the exchange

### Lines of code to integrate:

- AgentCard changes: ~15 lines of JSON
- Client-side escrow logic: ~50 lines (3 API calls: escrow, release/refund)
- Server-side verification: ~20 lines (1 API call to verify escrow)

Total integration effort: under 100 lines of code for either side.

---

## 10. Relationship to A2A Core

This extension is designed to be contributed to the A2A ecosystem. It does not modify, replace, or conflict with any core A2A operations, data model objects, or protocol bindings. It uses only officially sanctioned extension points:

- `AgentCard.capabilities.extensions`
- `Message.metadata`
- `Task.metadata`
- `Message.extensions` URI list

The settlement exchange operates as a separate service. A2A agents interact with it using standard HTTPS REST calls, independent of which A2A protocol binding (JSON-RPC, gRPC, HTTP+JSON) they use for agent-to-agent communication.

### 10.1. Relationship to x402 Payment Protocol

x402 and A2A-SE occupy complementary layers of the agent payment stack:

- **x402** is an instant access-payment protocol (pay-per-call). It answers the question: "Can I talk to this agent?" An x402 gate returns HTTP 402 until the caller submits a micropayment, then grants access. Think of it as a toll booth.
- **A2A-SE** is a task-settlement protocol (escrow for work-in-progress). It answers the question: "How do we hold and release funds while the agent does multi-step work?" Think of it as a contractor payment.

These protocols are complementary, not competing. Agents MAY use both simultaneously.

**Hybrid flow:**

```
Client                  Provider                    Exchange
  |                        |                           |
  |---x402 payment-------->|  (access gate: pay to     |
  |<--200 + AgentCard------|   discover capabilities)  |
  |                        |                           |
  |---POST /escrow---------|-------------------------->|  (lock funds for task)
  |---A2A message/send---->|                           |
  |       ...working...    |                           |
  |<--A2A task completed---|                           |
  |---POST /release--------|-------------------------->|  (settle task payment)
```

**Example:** An agent charges 0.001 USDC via x402 for discovery and health-check pings. Once the client decides to submit a task, it uses A2A-SE to escrow 50 ATE for a 10-minute research task. The x402 gate and A2A-SE escrow are independent -- one does not require the other, but together they provide both access control and work-in-progress payment guarantees.

A2A-SE does not replace x402. A2A-SE does not define an access gate. x402 does not define escrow, multi-step settlement, or dispute resolution. Each protocol handles its layer.
