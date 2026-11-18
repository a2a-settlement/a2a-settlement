# Cross-Extension Conformance Results (AlgoVoi v0)

Artefact: `https://api.algovoi.co.uk/.well-known/cross-extension/v0.json`  
Fixture: `tests/conformance/fixtures/cross-extension-v0.json` (schema_version 1.1, revised 2026-06-01)  
Rail: A2A-SE reference exchange (`a2a-settlement` / `exchange.a2a-settlement.org`)

## Summary

| Vector ID | Attack class | Expected | A2A-SE mapping | Result |
|-----------|--------------|----------|----------------|--------|
| `cross-ext-v0-escrow-double-release-001` | `escrow_double_release` | BLOCK | Second `POST /exchange/release` on terminal escrow → HTTP 400 `Escrow is already released` | **PASS** |
| `cross-ext-v0-refund-replay-001` | `refund_replay` | BLOCK | Second `POST /exchange/refund` on terminal escrow → HTTP 400 `Escrow is already refunded`; refund after release → HTTP 400 | **PASS** |
| `cross-ext-v0-dispute-amplification-001` | `dispute_amplification` | BLOCK | Not mapped — no concurrent-dispute rate gate in A2A-SE yet | N/A |
| `cross-ext-v0-synthetic-artifact-dispute-001` | `synthetic_artifact_dispute` | REVIEW | Partial — evidence hash mismatch handled in dispute/evidence path, no `SYNTHETIC_ARTIFACT_DETECTED` code | N/A |
| `cross-ext-v0-mediator-grooming-001` | `mediator_grooming` | REVIEW | Not mapped — reputation velocity anomaly gate not implemented | N/A |

All five artefact JWS signatures verify against AlgoVoi JWKS (`kid: d0481df4cbbda8e8aba86709419884ef`).

AlgoVoi independently re-ran these vectors and the schema audit below on 2026-06-16 ([#2](https://github.com/a2a-settlement/a2a-settlement/pull/2)).

## Schema audit

AlgoVoi audited the A2A-SE attestation schemas in `schemas/` for structural compatibility with the AlgoVoi compliance envelope format.

| Schema | Title | Required fields | AlgoVoi compatibility |
|--------|-------|----------------|----------------------|
| `escrow-release-attestation.v1.json` | `EscrowReleaseAttestation` | `header`, `settlement`, `amount_paid` | Compatible — `SettlementCore.amount` is integer; `settlement_kind` default `a2a-se` matches envelope |
| `escrow-refund-attestation.v1.json` | `EscrowRefundAttestation` | `header`, `settlement`, `amount_returned` | Compatible — `refund_kind` enum (`full`, `holdback`, `auto_dependent`) maps cleanly to AlgoVoi's refund classification |
| `dispute-resolution-attestation.v1.json` | `DisputeResolutionAttestation` | `header`, `settlement`, `resolution` | Compatible — `resolution` enum (`release`, `refund`) is a strict subset of AlgoVoi's verdict space |

`SettlementCore`, `AttestationHeader`, `PartyRef`, and `CryptographicProof` definitions are shared across all three schemas. All amount fields are integer (no float). `CryptographicProof.payload_hash`, `merkle_root`, and `merkle_leaf_index` are structurally compatible with AlgoVoi's receipt hash fields.

## A2A-SE settlement vector detail

### escrow_double_release

AlgoVoi envelope describes two release attempts two seconds apart (`task_state_completed`, `manual_mediator_release`).  
A2A-SE enforces single terminal transition: escrow status must be `held` or `partially_released` before release. Row-level lock on escrow prevents double payout.

**Exchange check:** `exchange/routes/settlement.py` — `release()` status guard.

### refund_replay

AlgoVoi envelope describes a refund authorization signed for `esc_PRIOR_settled` replayed against `esc_005_rep`.  
A2A-SE does not accept external signed refund authorizations; refunds are requester-authenticated and bound to `escrow_id` in the request body. Replay surfaces as a second refund on an escrow already in `refunded` or `released` state.

**Exchange check:** `exchange/routes/settlement.py` — `refund()` status guard.

## How to reproduce

```bash
cd a2a-settlement
python -m pytest tests/conformance/test_cross_extension_v0.py -v
```

Live rail runs the same code path (deployed from `main`). Behavioural equivalence verified at commit deployed to `exchange.a2a-settlement.org` — no state-mutating conformance calls are made against production.

## Mapping notes for conformance chair

- AlgoVoi `expected_error_code` strings (`RELEASE_IDEMPOTENCY_VIOLATION`, `REFUND_AUTHORIZATION_REPLAY`) are not emitted literally by FastAPI; the rail returns HTTP 400 with human-readable `detail`. A conformance adapter can map status + detail substring to vector verdict.
- `EscrowReleaseAttestation.reputation_attestation_type` defaults to `urn:a2a-settlement:ema-reputation:v1`. AlgoVoi does not currently consume EMA reputation scores, but the field is compatible as an extension point.
- Vectors tagged `settlement_kind: a2a-se` are the subset this rail owns. Dispute-amplification and mediator-grooming vectors need separate gates or mediator integration.
