# Integration Guide

This guide shows how to add the A2A Settlement Extension (A2A-SE) to an existing A2A client/provider setup without modifying A2A core.

## Provider (agent offering a paid skill)

1. Register an account on the exchange to obtain an `accountId` and API key.
2. Add the settlement extension block to your AgentCard `capabilities.extensions`:
   - `uri`: `https://a2a-settlement.org/extensions/settlement/v1`
   - `params.exchangeUrl`, `params.accountId`, and optional `params.pricing`
3. When receiving an incoming `a2a.types.Message`, read `message.metadata["a2a-se"]`:
   - verify `escrowId` exists and is valid by calling the exchange
   - reject work if escrow is missing and your agent marks settlement as `required: true`

## Requester (client agent paying for a skill)

1. Discover provider AgentCard and inspect `capabilities.extensions` for the A2A-SE URI.
2. Compute price from `params.pricing[skill_id]`.
3. Call the exchange `POST /exchange/escrow` to lock `amount` + fee.
4. Send your A2A request message with `message.metadata["a2a-se"]` containing:
   - `escrowId`, `amount`, `feeAmount`, `exchangeUrl`, `expiresAt`
5. On terminal A2A TaskState:
   - `completed` -> `POST /exchange/release`
   - `failed` / `canceled` / `rejected` -> `POST /exchange/refund`

## Using the Python SDK in this repo

See `a2a_settlement.sdk` for helpers:
- Exchange client: `SettlementExchangeClient`
- Metadata builder: `a2a_settlement.sdk.metadata.build_settlement_metadata()`
- AgentCard extension: `a2a_settlement.sdk.agentcard.build_settlement_extension()`

