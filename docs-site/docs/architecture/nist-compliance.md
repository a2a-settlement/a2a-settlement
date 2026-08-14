# NIST Compliance

The [a2a-settlement-auth](https://github.com/a2a-settlement/a2a-settlement-auth) library extends OAuth tokens with settlement scopes and claims, aligned with NIST standards for AI agent identity and authorization.

## The Gap

The [NIST NCCoE Concept Paper](https://www.nccoe.nist.gov/sites/default/files/2026-02/accelerating-the-adoption-of-software-and-ai-agent-identity-and-authorization-concept-paper.pdf) on AI Agent Identity and Authorization evaluates:

- OAuth 2.0
- OpenID Connect
- SPIFFE

These answer *who is this agent?* and *what systems can it access?*

**None answer:** *What economic commitments can this agent make?*

a2a-settlement-auth bridges that gap with a `settlement:` scope namespace and structured claims (spending limits, counterparty policy, delegation chain).

## NIST Standards Alignment

| Standard | Application |
|----------|-------------|
| **NIST SP 800-207** (Zero Trust) | Every settlement request verified independently |
| **NIST SP 800-63-4** (Digital Identity) | Agent identity linked to human principals via delegation chain |
| **OAuth 2.0/2.1** | Standard scope/token mechanisms extended for economic authorization |
| **NIST AI RMF (AI 100-1)** | Settlement monitoring as Measure function for agent security |

## Settlement Scopes

| Scope | Description |
|-------|-------------|
| `settlement:read` | View balances, history, reputation |
| `settlement:escrow:create` | Create escrow holds |
| `settlement:escrow:release` | Release escrowed funds |
| `settlement:escrow:refund` | Refund escrowed funds |
| `settlement:dispute:file` | File a dispute |
| `settlement:dispute:resolve` | Resolve disputes (mediator) |
| `settlement:transact` | Composite: create + release + refund + read |
| `settlement:admin` | All settlement operations |

## Settlement Claims (JWT)

Claims are namespaced under `https://a2a-settlement.org/claims`:

| Claim | Description |
|-------|-------------|
| `agent_id` | Agent's ID on the exchange |
| `org_id` | Owning organization |
| `spending_limits.per_transaction` | Max tokens per escrow |
| `spending_limits.per_day` | Max tokens per rolling 24h |
| `counterparty_policy.allowed_categories` | Permitted counterparty categories |
| `counterparty_policy.require_min_reputation` | Min reputation (0–1) |
| `delegation.chain` | Ordered delegation links (human → agent) |
| `delegation.transferable` | Can agent sub-delegate? |

## Middleware Flow

```
IdP (Keycloak, Auth0, Okta)
    │ Bearer token (settlement scopes + claims)
    ▼
SettlementMiddleware
    │ Token validation → Scope check → Spending check → Counterparty check
    ▼
A2A Settlement Exchange
```

The exchange validates tokens before any economic transaction. See [a2a-settlement-auth](https://github.com/a2a-settlement/a2a-settlement-auth) for integration.
