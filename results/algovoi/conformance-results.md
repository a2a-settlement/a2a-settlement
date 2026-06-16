# Cross-Extension Conformance Results (A2A-SE × AlgoVoi v0)

Artefact: `https://api.algovoi.co.uk/.well-known/cross-extension/v0.json`  
Fixture: `tests/conformance/fixtures/cross-extension-v0.json` (schema_version 1.1, revised 2026-06-01)  
Rail: A2A-SE reference exchange (`a2a-settlement` / `exchange.a2a-settlement.org`)  
Tested: 2026-06-16

## Summary

| Vector ID | Attack class | AlgoVoi verdict | A2A-SE behaviour | Result |
|-----------|--------------|-----------------|------------------|--------|
| `cross-ext-v0-escrow-double-release-001` | `escrow_double_release` | BLOCK | Second `POST /v1/exchange/release` on terminal escrow → HTTP 400 `already released` | **PASS** |
| `cross-ext-v0-refund-replay-001` | `refund_replay` | BLOCK | Second `POST /v1/exchange/refund` on terminal escrow → HTTP 400 `already refunded`; refund after release → HTTP 400 | **PASS** |
| `cross-ext-v0-dispute-amplification-001` | `dispute_amplification` | BLOCK | Not mapped — no concurrent-dispute rate gate in A2A-SE | N/A |
| `cross-ext-v0-synthetic-artifact-dispute-001` | `synthetic_artifact_dispute` | REVIEW | Partial — evidence-hash mismatch handled in dispute/evidence path; no `SYNTHETIC_ARTIFACT_DETECTED` signal emitted | N/A |
| `cross-ext-v0-mediator-grooming-001` | `mediator_grooming` | REVIEW | Not mapped — reputation velocity gate not implemented | N/A |

All five artefact JWS signatures verify against AlgoVoi JWKS (`kid: d0481df4cbbda8e8aba86709419884ef`).

## Schema audit

A2A-SE attestation schemas (from `schemas/`) were audited for structural compatibility with the AlgoVoi compliance envelope format.

| Schema | Title | Required fields | AlgoVoi compatibility |
|--------|-------|----------------|----------------------|
| `escrow-release-attestation.v1.json` | `EscrowReleaseAttestation` | `header`, `settlement`, `amount_paid` | Compatible — `SettlementCore.amount` is integer; `settlement_kind` default `a2a-se` matches envelope |
| `escrow-refund-attestation.v1.json` | `EscrowRefundAttestation` | `header`, `settlement`, `amount_returned` | Compatible — `refund_kind` enum (`full`, `holdback`, `auto_dependent`) maps cleanly to our refund classification |
| `dispute-resolution-attestation.v1.json` | `DisputeResolutionAttestation` | `header`, `settlement`, `resolution` | Compatible — `resolution` enum (`release`, `refund`) is a strict subset of our verdict space |

`SettlementCore`, `AttestationHeader`, `PartyRef`, and `CryptographicProof` definitions are shared across all three schemas. All amount fields are integer (no float). `CryptographicProof.payload_hash`, `merkle_root`, and `merkle_leaf_index` are structurally compatible with our receipt hash fields.

## Vector mapping detail

### escrow_double_release

AlgoVoi envelope describes two release attempts for the same escrow two seconds apart (`task_state_completed` + `manual_mediator_release`). A2A-SE enforces single terminal transition: escrow status must be `held` or `partially_released` before release; row-level lock prevents double payout.

**Exchange check:** `exchange/routes/settlement.py` — `release()` status guard.  
**Attestation produced:** `EscrowReleaseAttestation` records the first (successful) release only; the second attempt is rejected before any attestation is issued.

### refund_replay

AlgoVoi envelope describes a refund authorization signed for `esc_PRIOR_settled` replayed against `esc_005_rep`. A2A-SE binds refunds to `escrow_id` in the requester-authenticated request body; replay surfaces as a second refund on an escrow already in `refunded` or `released` state.

**Exchange check:** `exchange/routes/settlement.py` — `refund()` status guard.  
**Attestation produced:** `EscrowRefundAttestation` records the first (successful) refund only; replayed attempt is rejected before attestation.

## How to reproduce

```bash
cd a2a-settlement
python -m pytest tests/conformance/test_cross_extension_v0.py -v
```

Live rail runs the same code path deployed from `main`. No state-mutating conformance calls are made against `exchange.a2a-settlement.org` production.

## Notes

- `EscrowReleaseAttestation.reputation_attestation_type` defaults to `urn:a2a-settlement:ema-reputation:v1`; AlgoVoi does not currently consume EMA reputation scores but the field is compatible as an extension point.
- AlgoVoi `expected_error_code` strings (`RELEASE_IDEMPOTENCY_VIOLATION`, `REFUND_AUTHORIZATION_REPLAY`) are not emitted literally by the FastAPI rail; HTTP 400 + detail substring is the observable signal. A conformance adapter can map `status == 400 and "already released"/"already refunded" in detail` to the vector verdict.
- Dispute-amplification and mediator-grooming require concurrent-dispute and reputation-velocity gates not yet in A2A-SE; these are marked N/A pending gate implementation.
