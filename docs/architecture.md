# Architecture

## Roles
- Requester agent: initiates work and pays
- Provider agent: performs work and gets paid
- Settlement Exchange: centralized escrow ledger used by both parties

## Data flow

```
Requester  --A2A-->  Provider
Requester --HTTP-->  Exchange   (escrow/release/refund)
Provider  --HTTP-->  Exchange   (verify escrow before work)
```

## Trust model
- Agents communicate peer-to-peer using A2A.
- Settlement clears via a hosted exchange that both sides trust.
- Escrow IDs are references (not credentials). Exchange operations require the caller's API key, which must never be sent via A2A metadata.

## Lifecycle alignment
- SUBMITTED: requester creates escrow (available -> held)
- WORKING / INPUT_REQUIRED / AUTH_REQUIRED: no settlement action (escrow remains held)
- COMPLETED: requester releases escrow (held -> provider available, fee recorded)
- FAILED / CANCELED / REJECTED: requester refunds escrow (held -> requester available)

