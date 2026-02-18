# A2A Settlement Extension (A2A-SE)

Specification v0.5.0

Extension URI: `https://a2a-settlement.org/extensions/settlement/v1`

---

## 1. Introduction

The Agent2Agent (A2A) protocol enables agents to discover capabilities, communicate, and collaborate on tasks. However, A2A does not define how agents compensate each other for services rendered. This creates a gap: agents can find each other and exchange work, but there is no in-protocol mechanism for economic settlement.

The A2A Settlement Extension (A2A-SE) fills this gap by adding escrow-based token settlement to the A2A task lifecycle. It is designed as a native A2A Extension per Section 4.6 of the A2A specification, requiring zero modifications to the core protocol.

### 1.1. Design Principles

- Non-invasive. Uses A2A's existing Extension, metadata, and AgentCard mechanisms. No core protocol changes required.
- Optional. Agents that don't support settlement ignore it. Agents that do can transact seamlessly.
- Lifecycle-aligned. Settlement states map directly to A2A TaskState transitions. Escrow holds on task creation, releases on completion, refunds on failure or cancellation.
- Exchange as interface, not service. The settlement exchange is an API contract, not a single hosted service. Any conforming implementation -- hosted SaaS, self-hosted behind a firewall, or a smart contract fronted by the same REST surface -- can serve as the exchange.
- Currency-agnostic. The token model is abstract. Exchanges choose which currencies to support; agents declare which currency they price in. No blockchain or specific token standard is required.
- Multi-exchange. Agents may register on multiple exchanges. Two agents in the same task need not share the same exchange, and the protocol supports negotiating a mutually trusted exchange during task setup.

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

### 1.3. Notation and Conventions

The key words "MUST", "MUST NOT", "REQUIRED", "SHALL", "SHOULD", "SHOULD NOT", "RECOMMENDED", "MAY", and "OPTIONAL" in this document are to be interpreted as described in RFC 2119.

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
          "exchangeUrls": [
            "https://exchange.a2a-settlement.org/api/v1"
          ],
          "preferredExchange": "https://exchange.a2a-settlement.org/api/v1",
          "accountIds": {
            "https://exchange.a2a-settlement.org/api/v1": "agent-uuid-on-exchange"
          }
        }
      }
    ]
  }
}
```

When `required` is `false`, the agent accepts both paid and unpaid requests (freemium model). When `required` is `true`, the agent rejects tasks that do not include settlement metadata.

**Backward compatibility:** The singular `exchangeUrl` and `accountId` fields from v0.2.0 are still accepted. An agent card with `"exchangeUrl": "https://..."` and `"accountId": "uuid"` is treated as a single-element `exchangeUrls` array.

### 2.2. Skill-Level Pricing

Pricing is declared per skill using A2A's existing `AgentSkill` structure. Pricing metadata is placed in the extension params, keyed by skill ID:

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
          "exchangeUrls": ["https://exchange.a2a-settlement.org/api/v1"],
          "preferredExchange": "https://exchange.a2a-settlement.org/api/v1",
          "accountIds": {
            "https://exchange.a2a-settlement.org/api/v1": "provider-uuid"
          },
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

The Settlement Exchange is defined as an **interface specification**, not a single service. Any implementation that conforms to the REST API below is a valid exchange. Agents declare which exchange they use via the `exchangeUrls` field in their AgentCard extension params.

### 4.0. Deployment Models

| Model | Description |
|-------|-------------|
| Hosted exchange | A public SaaS instance (e.g., `exchange.a2a-settlement.org`). Agents register, receive API keys, and settle through the operator's infrastructure. This is the default for bootstrapping. |
| Self-hosted | An organization deploys a private exchange instance behind its firewall. Internal agents settle without exposing traffic to third parties. Uses the same API surface and reference implementation. |
| On-chain wrapper | A blockchain escrow contract (e.g., Solana program) fronted by a REST adapter that conforms to this API. Settlement finality is on-chain; the REST layer translates calls to contract instructions. |

Two agents in the same task MAY use different exchanges. Cross-exchange settlement (bridging balances between independent exchanges) is out of scope for v0.5 but is enabled by the per-agent `exchangeUrls` design.

### 4.1. Common Conventions

#### Authentication

All authenticated endpoints require the `Authorization` header:

```
Authorization: Bearer ate_<api_key>
```

The `ate_` prefix makes keys grep-able in logs and distinguishes them from other bearer tokens. Keys are bcrypt-hashed at rest.

#### Request IDs

Every request MAY include an `X-Request-Id` header. If omitted, the exchange generates one. The exchange MUST return the `X-Request-Id` header on every response, including errors.

#### Idempotency Keys

All `POST` endpoints accept an optional `Idempotency-Key` header. When provided:

- If the key has not been seen before, the request is processed normally and the response is cached.
- If the key has been seen within the last 24 hours with an identical request body, the cached response is returned.
- If the key has been seen with a different request body, the exchange returns `409 Conflict` with error code `IDEMPOTENCY_CONFLICT`.

Clients SHOULD include idempotency keys on all mutating operations to safely retry on network failures.

#### Content Type

All request and response bodies use `application/json`. The exchange MUST return `Content-Type: application/json` on all responses, including errors.

### 4.2. Error Response Format

All error responses use a consistent envelope:

```json
{
  "error": {
    "code": "INSUFFICIENT_BALANCE",
    "message": "Need 11 tokens (10 + 1 fee), have 5",
    "request_id": "req_a1b2c3d4",
    "details": {}
  }
}
```

| Field | Type | Description |
|-------|------|-------------|
| `code` | string | Machine-readable error code from the catalog below |
| `message` | string | Human-readable explanation |
| `request_id` | string | The request ID for correlation |
| `details` | object | Optional additional context (e.g., field-level validation errors) |

#### Error Code Catalog

| Code | HTTP Status | Description |
|------|-------------|-------------|
| `INVALID_REQUEST` | 400 | Malformed request body or missing required fields |
| `INVALID_AMOUNT` | 400 | Escrow amount outside allowed range |
| `SELF_ESCROW` | 400 | Requester and provider are the same account |
| `ESCROW_ALREADY_RESOLVED` | 400 | Escrow has already been released, refunded, or expired |
| `ESCROW_NOT_DISPUTED` | 400 | Attempted to resolve an escrow that is not in `disputed` state |
| `INVALID_RESOLUTION` | 400 | Resolution must be `"release"` or `"refund"` |
| `INSUFFICIENT_BALANCE` | 400 | Account does not have enough available tokens |
| `PROVIDER_INACTIVE` | 400 | Provider account is suspended or inactive |
| `INVALID_API_KEY` | 401 | API key is missing, malformed, or not recognized |
| `NOT_AUTHORIZED` | 403 | Caller lacks permission for this operation |
| `ESCROW_NOT_FOUND` | 404 | No escrow exists with the given ID |
| `ACCOUNT_NOT_FOUND` | 404 | No account exists with the given ID |
| `IDEMPOTENCY_CONFLICT` | 409 | Idempotency key reused with a different request body |
| `RATE_LIMITED` | 429 | Too many requests; retry after the indicated interval |

### 4.3. Endpoints

| Method | Endpoint | Auth | Description |
|--------|----------|------|-------------|
| `POST` | `/accounts/register` | Public | Register a new agent, receive API key and starter tokens |
| `GET` | `/accounts/directory` | Public | Browse registered agents and their skills |
| `GET` | `/accounts/{account_id}` | Public | Get account details |
| `PUT` | `/accounts/skills` | API Key | Update agent skills |
| `POST` | `/accounts/rotate-key` | API Key | Rotate API key with grace period |
| `PUT` | `/accounts/webhook` | API Key | Register or update webhook URL |
| `DELETE` | `/accounts/webhook` | API Key | Remove webhook configuration |
| `POST` | `/exchange/escrow` | API Key | Lock tokens for a pending A2A task |
| `POST` | `/exchange/release` | API Key | Task completed -- pay the provider |
| `POST` | `/exchange/refund` | API Key | Task failed -- return tokens to requester |
| `POST` | `/exchange/dispute` | API Key | Flag an escrow as disputed, freezing funds |
| `POST` | `/exchange/resolve` | Operator | Resolve a disputed escrow (release or refund) |
| `GET` | `/exchange/balance` | API Key | Check token balance |
| `GET` | `/exchange/transactions` | API Key | List transaction history |
| `GET` | `/exchange/escrows/{escrow_id}` | API Key | Get escrow details |
| `GET` | `/stats` | Public | Network health: supply, velocity, active escrows |

### 4.4. Escrow

```
POST /exchange/escrow
Authorization: Bearer ate_<api_key>
Idempotency-Key: <client-generated-uuid>

{
  "provider_id": "provider-agent-uuid",
  "amount": 10,
  "task_id": "a2a-task-uuid",
  "task_type": "sentiment-analysis",
  "ttl_minutes": 30
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `provider_id` | string | Yes | Account ID of the provider agent |
| `amount` | integer | Yes | Token amount to escrow (before fees) |
| `task_id` | string | No | A2A task ID for correlation |
| `task_type` | string | No | Skill/task type identifier |
| `ttl_minutes` | integer | No | Time-to-live in minutes (default: 30) |

Response `201 Created`:

```json
{
  "escrow_id": "escrow-uuid",
  "requester_id": "requester-agent-uuid",
  "provider_id": "provider-agent-uuid",
  "amount": 10,
  "fee_amount": 1,
  "total_held": 11,
  "status": "held",
  "expires_at": "2026-02-17T12:30:00Z"
}
```

### 4.5. Release

```
POST /exchange/release
Authorization: Bearer ate_<api_key>
Idempotency-Key: <client-generated-uuid>

{
  "escrow_id": "escrow-uuid"
}
```

Response `200 OK`:

```json
{
  "escrow_id": "escrow-uuid",
  "status": "released",
  "amount_paid": 10,
  "fee_collected": 1,
  "provider_id": "provider-agent-uuid"
}
```

### 4.6. Refund

```
POST /exchange/refund
Authorization: Bearer ate_<api_key>
Idempotency-Key: <client-generated-uuid>

{
  "escrow_id": "escrow-uuid",
  "reason": "Task failed: provider returned error"
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `escrow_id` | string | Yes | The escrow to refund |
| `reason` | string | No | Human-readable explanation |

Response `200 OK`:

```json
{
  "escrow_id": "escrow-uuid",
  "status": "refunded",
  "amount_returned": 11,
  "requester_id": "requester-agent-uuid"
}
```

### 4.7. Dispute

Either party (requester or provider) may flag an active escrow as disputed. This freezes the escrow -- neither release nor refund can proceed until an operator resolves it.

```
POST /exchange/dispute
Authorization: Bearer ate_<api_key>

{
  "escrow_id": "escrow-uuid",
  "reason": "Provider delivered incomplete results"
}
```

Response `200 OK`:

```json
{
  "escrow_id": "escrow-uuid",
  "status": "disputed",
  "reason": "Provider delivered incomplete results"
}
```

### 4.8. Resolve (Operator Only)

The exchange operator resolves a disputed escrow by directing it to either release (pay the provider) or refund (return tokens to the requester). This endpoint requires operator-level authentication.

```
POST /exchange/resolve
Authorization: Bearer ate_<operator_key>

{
  "escrow_id": "escrow-uuid",
  "resolution": "release"
}
```

`resolution` MUST be one of `"release"` or `"refund"`.

### 4.9. Webhooks

Agents MAY register a webhook URL to receive real-time notifications about escrow events instead of polling.

#### Registration

```
PUT /accounts/webhook
Authorization: Bearer ate_<api_key>

{
  "url": "https://my-agent.example.com/webhooks/settlement",
  "events": ["escrow.created", "escrow.released", "escrow.refunded",
             "escrow.expired", "escrow.disputed", "escrow.resolved"]
}
```

Response `200 OK`:

```json
{
  "webhook_url": "https://my-agent.example.com/webhooks/settlement",
  "secret": "whsec_abc123...",
  "events": ["escrow.created", "escrow.released", "escrow.refunded",
             "escrow.expired", "escrow.disputed", "escrow.resolved"],
  "active": true
}
```

The `secret` is returned only on creation. Agents MUST store it securely for signature verification.

#### Event Delivery

The exchange delivers events as `POST` requests to the registered URL:

```
POST https://my-agent.example.com/webhooks/settlement
Content-Type: application/json
X-A2ASE-Signature: sha256=<hmac-sha256-hex>
X-A2ASE-Event: escrow.released
X-A2ASE-Delivery: evt_delivery-uuid

{
  "event": "escrow.released",
  "timestamp": "2026-02-17T12:05:00Z",
  "data": {
    "escrow_id": "escrow-uuid",
    "requester_id": "requester-uuid",
    "provider_id": "provider-uuid",
    "amount": 10,
    "fee_amount": 1,
    "status": "released"
  }
}
```

#### Signature Verification

The `X-A2ASE-Signature` header contains an HMAC-SHA256 of the raw request body, computed using the webhook secret. Agents SHOULD verify this signature before processing the event.

```
expected = HMAC-SHA256(webhook_secret, raw_request_body)
valid = constant_time_compare(expected, signature_from_header)
```

#### Delivery Guarantees

- **Best-effort delivery.** Events are delivered at least once but may be duplicated.
- **Retries.** Failed deliveries (non-2xx response or timeout) are retried 3 times with exponential backoff: 5 seconds, 25 seconds, 125 seconds.
- **Timeout.** Each delivery attempt times out after 10 seconds.
- **Deactivation.** After all retries are exhausted, the webhook remains active. Persistent failures over 24 hours MAY result in the exchange deactivating the webhook and notifying the account owner.

#### Event Types

| Event | Trigger |
|-------|---------|
| `escrow.created` | New escrow is created via `POST /exchange/escrow` |
| `escrow.released` | Escrow is released via `POST /exchange/release` or dispute resolution |
| `escrow.refunded` | Escrow is refunded via `POST /exchange/refund` or dispute resolution |
| `escrow.expired` | Escrow TTL exceeded, auto-refunded |
| `escrow.disputed` | Escrow flagged as disputed |
| `escrow.resolved` | Disputed escrow resolved by operator |

---

## 5. Client Agent Workflow

This is the complete sequence a client agent follows when using A2A with the Settlement Extension.

### Step 1: Discovery (Standard A2A)

Client discovers remote agent via A2A Agent Card at `/.well-known/agent.json`. Client checks `capabilities.extensions` for the settlement extension URI. If present, client reads `params.exchangeUrls`, `params.accountIds`, and `params.pricing`.

### Step 2: Exchange Negotiation (A2A-SE)

If the provider supports multiple exchanges, the client selects one it also has an account on (see Section 5.1). If no overlap exists, the client may register on the provider's preferred exchange or propose an alternative.

### Step 3: Escrow (A2A-SE)

Before sending the A2A task message, client calls the Settlement Exchange to create an escrow, locking the required tokens.

### Step 4: Send Message (Standard A2A)

Client sends `message/send` per normal A2A protocol, including the `a2a-se` metadata block with the escrow reference.

### Step 5: Task Processing (Standard A2A)

Remote agent processes the task normally. Settlement has no effect on task execution.

### Step 6: Settlement (A2A-SE)

Based on the terminal TaskState:
- `COMPLETED` -> Client calls `/exchange/release`
- `FAILED` / `CANCELED` / `REJECTED` -> Client calls `/exchange/refund`

### Step 7: Reputation Update (A2A-SE)

Exchange updates the provider's reputation score based on outcome. Updated reputation is reflected in the provider's AgentCard extension params on next discovery.

### 5.1. Exchange Negotiation Protocol

When a client discovers a provider's AgentCard, it follows this algorithm to select a mutually trusted exchange:

1. Read the provider's `exchangeUrls` array from the settlement extension params.
2. Intersect with the client's own list of exchanges where it holds accounts.
3. If the intersection is non-empty:
   - If the provider's `preferredExchange` is in the intersection, use it.
   - Otherwise, use the first exchange in the intersection.
4. If the intersection is empty:
   - Option A: Client registers on the provider's `preferredExchange`.
   - Option B: Client includes `"proposedExchange": "<url>"` in the `a2a-se` metadata of the initial message. The provider may accept or reject in its response metadata.
5. The selected exchange URL and corresponding `accountId` are used for escrow creation.

Exchange negotiation metadata in the initial message:

```json
{
  "metadata": {
    "a2a-se": {
      "proposedExchange": "https://exchange.example.com/api/v1",
      "escrowId": null
    }
  }
}
```

Provider response accepting or counter-proposing:

```json
{
  "metadata": {
    "a2a-se": {
      "acceptedExchange": "https://exchange.example.com/api/v1",
      "accountId": "provider-uuid-on-that-exchange"
    }
  }
}
```

---

## 6. Token Model

### 6.1. Design Philosophy

A2A-SE is **currency-agnostic by design**. The protocol does not define a token standard, does not require a blockchain, and does not mandate a specific currency. Instead, it defines a `currency` field that each exchange and agent uses to declare their settlement denomination.

### 6.2. ATE: The Reference Currency

`ATE` (A2A Token Exchange) is the default unit used by the reference exchange implementation. It is:

- A ledger entry on the exchange, not a blockchain token or ERC-20.
- Created when agents register (starter allocation) or purchase tokens.
- Transferred between agents via escrow operations on the exchange ledger.

ATE exists to bootstrap the ecosystem. It requires no external dependencies and works out of the box with the reference exchange.

### 6.3. Alternative Currencies

Exchanges MAY support any currency by accepting it as the `currency` value in pricing and configuring their ledger accordingly:

| Currency | Type | Example Use |
|----------|------|-------------|
| `ATE` | Exchange ledger | Default bootstrap token, no external dependencies |
| `USDC` | Stablecoin (ERC-20) | Real-value settlement on Ethereum/Solana/Base |
| `USD` | Fiat denomination | Fiat-denominated ledger (exchange handles fiat rails) |
| `credits` | Platform points | Internal enterprise token (e.g., "AI compute credits") |
| Custom | Any | Exchange operator defines semantics |

### 6.4. Cross-Currency Considerations

A2A-SE does not define currency conversion. If a requester prices in `USDC` and a provider prices in `ATE`, the agents must either:

1. Use an exchange that supports both currencies and provides conversion.
2. Agree on a common currency during exchange negotiation (Section 5.1).
3. Use a third-party conversion service outside the A2A-SE protocol.

### 6.5. Interoperability with x402 and On-Chain Systems

- **x402 deposit proofs.** An exchange MAY accept x402 payment receipts as proof of deposit, crediting the payer's exchange balance. This bridges x402 micropayments into the A2A-SE escrow model.
- **On-chain escrow.** An exchange MAY be backed by a smart contract (e.g., Solidity, Solana program). The REST API surface remains identical; settlement finality is on-chain rather than on the exchange's internal ledger.
- **Explicit non-goals.** A2A-SE does not define a token standard (no ERC-20 interface), does not require any blockchain, and does not define on-chain escrow contract interfaces. These are left to specific exchange implementations.

---

## 7. Token Economics (Reference Exchange)

The defaults below apply to the reference exchange at `exchange.a2a-settlement.org`. Alternative exchange implementations MAY define their own currency, fee schedule, starter allocation, and escrow limits.

| Parameter | Default | Description |
|-----------|---------|-------------|
| Currency | ATE | Default settlement unit. Exchanges MAY support additional currencies via the `currency` pricing field. |
| Starter allocation | 100 tokens | Free tokens on agent registration |
| Transaction fee | 3% | Deducted from escrow on release, credited to exchange operator treasury |
| Escrow TTL | 30 minutes | Auto-refund if not resolved |
| Min escrow | 1 token | Minimum per transaction |
| Max escrow | 10,000 tokens | Maximum per transaction |

### 7.1. Token Flow

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

### 7.2. Exchange Operator Revenue Model

Any exchange operator can generate revenue from the following mechanisms. These apply to whichever exchange implementation is deployed -- hosted, self-hosted, or on-chain.

1. Token sales -- developers purchase tokens (ATE or operator-chosen currency) with fiat currency
2. Transaction fees -- a percentage of each released escrow is credited to the operator's treasury
3. Premium tiers -- priority matching, analytics dashboards, bulk escrow
4. Market data -- anonymized demand signals, pricing trends, skill gap analysis

### 7.3. Dispute Resolution

When either party flags a transaction as `disputed`, the exchange freezes the escrow: no release or refund can proceed until the dispute is resolved.

**v0.5 resolution model (manual):**

1. Either the requester or provider calls `POST /exchange/dispute` with the `escrow_id` and a `reason`.
2. The escrow status transitions to `disputed`. All release and refund calls are rejected while in this state.
3. The exchange operator reviews the dispute out-of-band (support channel, logs, task artifacts).
4. The operator calls `POST /exchange/resolve` with the `escrow_id` and a `resolution` of `"release"` or `"refund"`.
5. The escrow settles according to the resolution. Reputation is updated as if the task completed or failed normally.

**Future versions** will support pluggable resolution strategies, including third-party arbitrators and AI mediator panels. The `POST /exchange/resolve` endpoint will accept an optional `strategy` field to select a resolution mechanism.

---

## 8. Reputation System

### 8.1. Score Computation

Reputation is computed as an exponential moving average updated on each completed or failed task:

```
R_new = lambda * outcome + (1 - lambda) * R_old

where:
  outcome = 1.0  (task completed successfully)
  outcome = 0.0  (task failed / disputed)
  lambda = 0.1   (learning rate; recent performance weighted 10%)
```

New agents start at `R = 0.5000` (neutral). Reputation is bounded `[0.0, 1.0]`.

### 8.2. Reputation in Agent Discovery

Reputation scores are exposed through the settlement extension params in the AgentCard. Client agents MAY use reputation as a selection criterion when multiple agents offer the same skill. The Composite Bid Score (CBS) formula for agent selection is:

```
CBS = (w1 * Reputation + w2 * Availability + w3 * SkillMatch) / Price^alpha

where:
  w1 + w2 + w3 = 1.0  (requester's preference weights)
  alpha = price sensitivity exponent (0.5 = quality-first, 1.5 = cost-first)
```

---

## 9. Security Considerations

### 9.1. Threat Model

This section enumerates known threats and the mitigations A2A-SE provides.

#### 9.1.1. Spoofed Escrow References

**Threat:** A malicious requester sends a fabricated `escrowId` in the `a2a-se` metadata, hoping the provider performs work without payment backing.

**Mitigation:** The provider MUST verify the escrow exists and is valid by calling `GET /exchange/escrows/{escrow_id}` before beginning work. Providers SHOULD check that the escrow's `requester_id`, `provider_id`, and `amount` match expectations.

#### 9.1.2. Replay Attacks

**Threat:** An attacker captures a legitimate `POST /exchange/escrow` request and replays it to create duplicate escrows.

**Mitigation:** All `POST` endpoints accept an `Idempotency-Key` header. The exchange rejects duplicate keys within a 24-hour window (returning the cached response) or returns `409 Conflict` if the key is reused with different parameters. Clients SHOULD always include idempotency keys.

#### 9.1.3. API Key Compromise

**Threat:** An agent's API key is leaked (e.g., committed to a public repo, logged in plaintext).

**Mitigation:**
- Keys use the `ate_` prefix, making them easy to detect in automated secret scanners.
- Keys are bcrypt-hashed at rest; the exchange never stores plaintext keys.
- The `POST /accounts/rotate-key` endpoint lets agents rotate keys immediately. The old key remains valid for a configurable grace period (default: 5 minutes) to avoid breaking in-flight requests.
- Agents SHOULD rotate keys regularly and MUST rotate immediately on suspected compromise.

#### 9.1.4. Exchange Downtime

**Threat:** The settlement exchange becomes unavailable, leaving escrows unresolvable.

**Mitigation:**
- Escrow TTL with auto-refund ensures no permanent fund lock. Even if the exchange is down, tokens are automatically refunded when the exchange recovers and processes the expiration.
- Agents SHOULD treat exchange unavailability as a transient error and retry with exponential backoff.
- The spec defines "exchange unavailable" as a non-fatal condition: A2A task processing MAY proceed without settlement (degraded mode), and settlement can be retried later.

#### 9.1.5. Man-in-the-Middle

**Threat:** An attacker intercepts exchange API traffic or webhook deliveries.

**Mitigation:**
- All exchange traffic MUST use HTTPS (TLS 1.2 or higher).
- Webhook deliveries are signed with HMAC-SHA256 using a per-account secret. Agents MUST verify signatures before processing webhook events.
- The `a2a-se` metadata block contains no secrets (escrow IDs are references, not credentials).

#### 9.1.6. Metadata Tampering

**Threat:** A2A message metadata is modified in transit, changing the `escrowId` or `amount`.

**Mitigation:** The `a2a-se` metadata is informational only. The authoritative escrow state lives on the exchange. Agents MUST NOT trust metadata alone -- they MUST verify escrow state via the exchange API before acting on it. The exchange is the single source of truth.

#### 9.1.7. Denial of Service via Escrow Spam

**Threat:** An attacker creates many small escrows to exhaust another agent's balance or overload the exchange.

**Mitigation:**
- Per-account rate limiting (default: 60 requests/minute).
- Minimum escrow amount (default: 1 token) prevents zero-cost spam.
- Escrow TTL ensures spam escrows are automatically cleaned up.
- Exchanges MAY implement additional anti-abuse measures (CAPTCHA on registration, deposit requirements, account age restrictions).

### 9.2. Authentication Model

- All authenticated exchange API calls require `Authorization: Bearer ate_<api_key>`.
- Keys are issued on registration and can be rotated via `POST /accounts/rotate-key`.
- The exchange authenticates by bcrypt-comparing the provided key against stored hashes.
- Operator-level endpoints (e.g., dispute resolution) require an operator-class API key.

### 9.3. Transport Security

- Exchange endpoints MUST be served over HTTPS in production.
- Self-signed certificates are acceptable for development but MUST NOT be used in production.
- Webhook URLs MUST use HTTPS. The exchange MUST NOT deliver events to HTTP URLs in production.

---

## 10. Adoption Path

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

## 11. Sequence Diagrams

### 11.1. Happy Path: Escrow, Task Completion, and Release

```mermaid
sequenceDiagram
    participant Client as Client Agent
    participant Provider as Provider Agent
    participant Exchange as Settlement Exchange

    Client->>Provider: GET /.well-known/agent.json
    Provider-->>Client: AgentCard (with A2A-SE extension, pricing)

    Client->>Exchange: POST /exchange/escrow
    Exchange-->>Client: 201 {escrow_id, status: "held"}

    Client->>Provider: A2A message/send (metadata: {a2a-se: {escrowId}})
    Provider->>Exchange: GET /exchange/escrows/{escrow_id} (verify)
    Exchange-->>Provider: 200 {status: "held", amount, requester_id}

    Provider-->>Client: A2A Task {state: WORKING, metadata: {a2a-se: {settlementStatus: "acknowledged"}}}
    Provider-->>Client: A2A Task {state: COMPLETED, artifacts: [...]}

    Client->>Exchange: POST /exchange/release {escrow_id}
    Exchange-->>Client: 200 {status: "released", amount_paid, fee_collected}

    Note over Exchange: Reputation updated (+)
    Note over Exchange: Webhook: escrow.released
```

### 11.2. Failure Path: Task Fails, Escrow Refunded

```mermaid
sequenceDiagram
    participant Client as Client Agent
    participant Provider as Provider Agent
    participant Exchange as Settlement Exchange

    Client->>Exchange: POST /exchange/escrow
    Exchange-->>Client: 201 {escrow_id, status: "held"}

    Client->>Provider: A2A message/send (metadata: {a2a-se: {escrowId}})
    Provider->>Exchange: GET /exchange/escrows/{escrow_id} (verify)
    Exchange-->>Provider: 200 {status: "held"}

    Provider-->>Client: A2A Task {state: WORKING}
    Provider-->>Client: A2A Task {state: FAILED, message: "Processing error"}

    Client->>Exchange: POST /exchange/refund {escrow_id, reason: "Task failed"}
    Exchange-->>Client: 200 {status: "refunded", amount_returned}

    Note over Exchange: Reputation updated (-)
    Note over Exchange: Webhook: escrow.refunded
```

### 11.3. Multi-Turn Negotiation with Negotiable Pricing

```mermaid
sequenceDiagram
    participant Client as Client Agent
    participant Provider as Provider Agent
    participant Exchange as Settlement Exchange

    Client->>Provider: GET /.well-known/agent.json
    Provider-->>Client: AgentCard (pricing model: "negotiable")

    Client->>Provider: A2A message/send "Translate 50-page document"
    Provider-->>Client: A2A Task {state: INPUT_REQUIRED, metadata: {a2a-se: {proposedPrice: 150, currency: "ATE"}}}

    Client->>Provider: A2A message/send {metadata: {a2a-se: {counterPrice: 100}}}
    Provider-->>Client: A2A Task {state: INPUT_REQUIRED, metadata: {a2a-se: {agreedPrice: 120}}}

    Client->>Exchange: POST /exchange/escrow {amount: 120, provider_id}
    Exchange-->>Client: 201 {escrow_id, status: "held", total_held: 124}

    Client->>Provider: A2A message/send {metadata: {a2a-se: {escrowId, amount: 120}}}
    Provider->>Exchange: GET /exchange/escrows/{escrow_id} (verify)
    Exchange-->>Provider: 200 {status: "held", amount: 120}

    Provider-->>Client: A2A Task {state: WORKING}
    Provider-->>Client: A2A Task {state: COMPLETED}

    Client->>Exchange: POST /exchange/release {escrow_id}
    Exchange-->>Client: 200 {status: "released"}
```

### 11.4. Dispute Resolution

```mermaid
sequenceDiagram
    participant Client as Client Agent
    participant Provider as Provider Agent
    participant Exchange as Settlement Exchange
    participant Operator as Exchange Operator

    Client->>Exchange: POST /exchange/escrow
    Exchange-->>Client: 201 {escrow_id, status: "held"}

    Client->>Provider: A2A message/send (metadata: {a2a-se: {escrowId}})
    Provider-->>Client: A2A Task {state: COMPLETED, artifacts: [...]}

    Note over Client: Client reviews output,<br/>finds it unacceptable

    Client->>Exchange: POST /exchange/dispute {escrow_id, reason: "Incomplete results"}
    Exchange-->>Client: 200 {status: "disputed"}
    Note over Exchange: Webhook: escrow.disputed

    Note over Operator: Reviews dispute out-of-band<br/>(logs, artifacts, communication)

    Operator->>Exchange: POST /exchange/resolve {escrow_id, resolution: "refund"}
    Exchange-->>Operator: 200 {status: "refunded", amount_returned}

    Note over Exchange: Reputation updated (-)
    Note over Exchange: Webhook: escrow.resolved
```

---

## 12. Relationship to A2A Core

This extension is designed to be contributed to the A2A ecosystem. It does not modify, replace, or conflict with any core A2A operations, data model objects, or protocol bindings. It uses only officially sanctioned extension points:

- `AgentCard.capabilities.extensions`
- `Message.metadata`
- `Task.metadata`
- `Message.extensions` URI list

The settlement exchange operates as a separate service. A2A agents interact with it using standard HTTPS REST calls, independent of which A2A protocol binding (JSON-RPC, gRPC, HTTP+JSON) they use for agent-to-agent communication.

### 12.1. Relationship to x402 Payment Protocol

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

### 12.2. Relationship to AP2 (Agent Payments Protocol)

AP2 and A2A-SE address different parts of the payment lifecycle:

- **AP2** focuses on payment negotiation and method selection -- "how will we pay?" It defines flows for agents to agree on payment methods, currencies, and amounts.
- **A2A-SE** focuses on escrow and settlement -- "hold these funds while I work, then release them." It assumes the price and currency are already agreed upon (via pricing in the AgentCard or negotiation in Section 5.1).

A2A-SE can use AP2 as an upstream negotiation layer: AP2 negotiates the payment terms, then A2A-SE executes the escrow/release flow based on those terms.

| Concern | AP2 | x402 | A2A-SE |
|---------|-----|------|--------|
| Payment negotiation | Primary focus | N/A | Lightweight (Section 5.1) |
| Access gating | N/A | Primary focus | N/A |
| Escrow for tasks | N/A | N/A | Primary focus |
| Dispute resolution | N/A | N/A | Built-in (Section 7.3) |
| Reputation | N/A | N/A | Built-in (Section 8) |
| Multi-turn tasks | N/A | N/A | Native support |

---

## 13. Changelog

### v0.5.0 (2026-02-18)

- Added Token Model section (Section 6) making the protocol currency-agnostic.
- Added formal error response format with error code catalog (Section 4.2).
- Expanded Security Considerations into a full threat model (Section 9).
- Added webhook specification for async event delivery (Section 4.9).
- Added support for multiple exchange URLs in AgentCard params (Section 2.1).
- Added exchange negotiation protocol (Section 5.1).
- Added API key rotation (Section 4.3).
- Added idempotency keys and request IDs (Section 4.1).
- Added Mermaid sequence diagrams for all major flows (Section 11).
- Added comparison with AP2 (Section 12.2).
- Added changelog.

### v0.2.0

- Added pluggable exchange model and deployment options.
- Added token economics and operator revenue model.
- Added dispute resolution flow.
- Added x402 interoperability section.

### v0.1.0

- Initial draft: AgentCard integration, task lifecycle mapping, basic escrow API, reputation system.
