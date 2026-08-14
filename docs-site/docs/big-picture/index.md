---
sidebar_position: 2
---

# Big Picture: Agent Transaction Lifecycle

End-to-end flow from discovery through economic commitment, work, verification, and settlement finality.

For the category definition, start with [What is Agent Settlement?](../agent-settlement).

## Lifecycle

```text
Discover → Authorize → Commit (escrow) → Execute → Deliver → Verify → Settle → Finality
```

| Stage | What happens |
|-------|----------------|
| Discover | Agents find peers via AgentCard / A2A |
| Authorize | Spending permission checked (AP2 / OAuth scopes) |
| Commit | Value locked for the task (economic commitment) |
| Execute | Provider performs work |
| Deliver | Deliverable submitted (optional provenance) |
| Verify | Acceptance / attestation / mediation |
| Settle | Release, refund, partial, or dispute outcome |
| Finality | Ledger + reputation + audit record |

Escrow is the reference mechanism for the commit stage. Other conforming mechanisms may implement the same settlement semantics.

## Related

- [Architecture / protocol comparison](../architecture/protocol-comparison)
- [Specification](../spec/)
- [Federation](../federation/) (optional; not required for Core)
