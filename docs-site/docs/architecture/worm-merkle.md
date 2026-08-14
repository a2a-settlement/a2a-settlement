# WORM Merkle Trees

The [a2a-settlement-mediator](https://github.com/a2a-settlement/a2a-settlement-mediator) includes a **SEC 17a-4 WORM** (Write-Once-Read-Many) settlement pipeline for compliance-sensitive deployments.

## What It Does

Bridges "Natural Language Intent" (CrewAI transcripts) with "Cryptographic Execution" (AP2 mandates):

```
CrewAI transcript (hashed)
+ AP2 mandates
        │
        ▼
┌───────────────┐  1. ARBITRATION     LLM evaluates mandate compliance
│  Orchestrator │  2. ATTESTATION     Build immutable payload + SHA-256 seal
│               │  3. TIMESTAMPING    RFC 3161 timestamp from TSA
│               │  4. MERKLE APPEND   Append to append-only Merkle Tree
│               │  5. VERIFY          Mathematical proof verification
│               │  6. PROOF           Assemble cryptographic proof bundle
└───────┬───────┘
        │
        ▼
┌───────────────┐
│  Gatekeeper   │  Mandate released ONLY after Merkle leaf confirmed
│               │  Recovery: re-emit for confirmed-but-unexecuted leaves
└───────────────┘
```

## Merkle Tree Specification

- **Algorithm:** SHA-256 binary Merkle Tree (domain-separated hashing)
- **Leaf nodes:** `H(0x00 || data)` — prevents second-preimage attacks
- **Internal nodes:** `H(0x01 || left || right)`
- **Structure:** RFC 6962 §2.1 style — unbalanced binary tree, deterministic given leaf count
- **Semantics:** Append-only (WORM) — leaves never removed or mutated
- **Concurrency:** Thread-safe via lock

Third-party verification requires only: leaf data, sibling path, root hash at insertion time. No tree access needed.

## Gatekeeper Recovery

If Merkle append succeeds but the network fails before mandate release:

1. Pipeline records confirmed settlements in recovery ledger
2. Execution engine polls `GET /settlements/pending`
3. Re-emits mandates for confirmed-but-unexecuted leaves
4. Acknowledges via `POST /settlements/ack` after execution

This prevents:
- **Phantom settlements** — payment without audit trail
- **Orphaned audit leaves** — confirmed leaf with no execution

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| POST | `/settle` | Full pipeline: arbitration → timestamp → Merkle → proof |
| GET | `/settlements` | List recent settlement results |
| GET | `/merkle` | Current Merkle tree state (size + root hash) |
| GET | `/settlements/pending` | Confirmed settlements awaiting execution |
| POST | `/settlements/ack` | Acknowledge mandate execution |
| GET | `/schemas` | Versioned JSON Schema for attestation models |

## Attestation Schema

All attestation payloads carry `payload_version` / `schema_version`. Versioned JSON Schemas at `GET /schemas` let external auditors validate proofs without mediator source code.
