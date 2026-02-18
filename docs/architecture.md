# Architecture

## Roles
- Requester agent: initiates work and pays
- Provider agent: performs work and gets paid
- Settlement Exchange: escrow ledger conforming to the A2A-SE API interface (hosted, self-hosted, or on-chain)

## Data flow

```
Requester  --A2A-->  Provider
Requester --HTTP-->  Exchange   (escrow/release/refund)
Provider  --HTTP-->  Exchange   (verify escrow before work)
```

## Trust model
- Agents communicate peer-to-peer using A2A.
- Settlement clears via an exchange that both sides trust. The exchange is an interface -- any conforming implementation (hosted SaaS, self-hosted, on-chain wrapper) is valid.
- Each agent declares its `exchangeUrls` (plural, v0.5+) in its AgentCard. Agents may be registered on multiple exchanges simultaneously.
- Two agents in the same task negotiate which exchange to use (see SPEC Section 5.1). If they share a common exchange, they use it. If not, one agent may register on the other's preferred exchange or they may propose an alternative.
- Cross-exchange bridging (transferring balances between independent exchanges) is a future concern.
- Escrow IDs are references (not credentials). Exchange operations require the caller's API key, which must never be sent via A2A metadata.

## Multi-exchange negotiation

```
Client reads provider AgentCard
  -> provider.exchangeUrls = [A, B, C]
  -> client has accounts on [B, D, E]
  -> intersection = [B]
  -> use exchange B
```

If the intersection is empty, the client can register on the provider's preferred exchange or propose a mutually trusted alternative via A2A metadata.

## Complementary protocols
- **x402**: instant access payment (toll booth). Handles "can I talk to this agent?" via HTTP 402 + micropayment.
- **AP2**: payment negotiation. Handles "how will we pay?" -- method selection, currencies, amounts.
- **A2A-SE**: task settlement (escrow). Handles "hold funds while the agent does multi-step work."
- An agent MAY use all three: x402 gates discovery, AP2 negotiates terms, A2A-SE escrows the actual task payment. They are independent layers.

## Lifecycle alignment
- SUBMITTED: requester creates escrow (available -> held)
- WORKING / INPUT_REQUIRED / AUTH_REQUIRED: no settlement action (escrow remains held)
- COMPLETED: requester releases escrow (held -> provider available, fee recorded)
- FAILED / CANCELED / REJECTED: requester refunds escrow (held -> requester available)

## Security model
- All exchange traffic over HTTPS (TLS 1.2+)
- API keys with `ate_` prefix, bcrypt-hashed at rest, rotatable via `/accounts/rotate-key`
- Idempotency keys on all POST endpoints to prevent replay attacks
- Request IDs on all requests for tracing
- Webhook deliveries signed with HMAC-SHA256
- Per-account rate limiting
- Escrow TTL with auto-refund prevents permanent fund lock
