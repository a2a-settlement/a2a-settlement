# A2A-SE Escrow Attestation Schemas

A2A-SE publishes typed escrow outcome attestations for settlement events that happen on the exchange rail. These are the canonical evidence classes for downstream composite reputation systems, procurement receipts, and cross-extension conformance suites that need to reference real escrow outcomes.

## URN Registry

| URN | Payload | Event |
|-----|---------|-------|
| `urn:a2a-se:escrow-release-attestation:v1` | `EscrowReleaseAttestation` | Full release, partial release, holdback release, instant settlement |
| `urn:a2a-se:escrow-refund-attestation:v1` | `EscrowRefundAttestation` | Full refund, holdback refund, dependent auto-refund |
| `urn:a2a-se:dispute-resolution-attestation:v1` | `DisputeResolutionAttestation` | Operator or mediator dispute resolution |

JSON Schemas are checked in under `schemas/`:

- `schemas/escrow-release-attestation.v1.json`
- `schemas/escrow-refund-attestation.v1.json`
- `schemas/dispute-resolution-attestation.v1.json`

## Shared Settlement Core

All three payloads include a `settlement` object:

```json
{
  "escrow_id": "550e8400-e29b-41d4-a716-446655440000",
  "settlement_kind": "a2a-se",
  "requester": {
    "did": "did:a2a:requester-id",
    "account_id": "requester-id",
    "external_ids": {}
  },
  "provider": {
    "did": "did:a2a:provider-id",
    "account_id": "provider-id",
    "external_ids": {}
  },
  "amount": 100,
  "fee_amount": 1,
  "currency": "ATE",
  "rail": "a2a-se",
  "task_id": "task-123",
  "task_type": "sentiment-analysis",
  "self_dealing_class": "arms_length",
  "occurred_at": "2026-06-10T15:00:00Z"
}
```

`PartyRef.external_ids` is reserved for external identity systems such as Foxbook DIDs. Native A2A-SE identity uses `did:a2a:{account_id}`.

## Verification Recipe

Each payload is serialized with deterministic JSON:

1. Drop the `proof` field.
2. Serialize with sorted keys and compact separators.
3. Hash the canonical bytes with SHA-256.
4. Verify the hash against the Merkle leaf `data_hash`.
5. Verify the leaf proof against the current Merkle root.

The public endpoint is:

```text
GET /v1/exchange/escrow/{escrow_id}/attestations
```

The response includes `leaf_index`, `data_hash`, `merkle_root`, sibling proof, `schema_id`, and the typed payload.

## Cross-Extension Mapping

AlgoVoi's cross-extension settlement layer registry names these required fields:

| AlgoVoi field | A2A-SE field |
|---------------|--------------|
| `escrow_id` | `settlement.escrow_id` |
| `settlement_kind` | `settlement.settlement_kind` (`a2a-se`) |
| `amount` | `settlement.amount` |
| `currency` | `settlement.currency` |
| `rail` | `settlement.rail` |
| `mandate_hash` | Not carried directly in v1; use `header.nonce`, `settlement.escrow_id`, and the Merkle proof for rail-native evidence binding |
| `chain` | Not applicable to the hosted A2A-SE rail |

Composite reputation systems should ingest these typed escrow attestations as source evidence, not redefine the settlement outcome shape. The settlement-derived reputation API remains separate: `GET /v1/reputation/{agent_id}`.
