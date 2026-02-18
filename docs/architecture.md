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
- Each agent declares its `exchangeUrl` in its AgentCard. Two agents in the same task may use different exchanges; cross-exchange bridging is a future concern.
- Escrow IDs are references (not credentials). Exchange operations require the caller's API key, which must never be sent via A2A metadata.

## Complementary protocols
- **x402**: instant access payment (toll booth). Handles "can I talk to this agent?" via HTTP 402 + micropayment.
- **A2A-SE**: task settlement (escrow). Handles "hold funds while the agent does multi-step work."
- An agent MAY use both: x402 gates discovery, A2A-SE escrows the actual task payment. They are independent layers.

## Lifecycle alignment
- SUBMITTED: requester creates escrow (available -> held)
- WORKING / INPUT_REQUIRED / AUTH_REQUIRED: no settlement action (escrow remains held)
- COMPLETED: requester releases escrow (held -> provider available, fee recorded)
- FAILED / CANCELED / REJECTED: requester refunds escrow (held -> requester available)

