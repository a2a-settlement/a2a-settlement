# A2A-SE Specification

**Version:** v0.11.0  
**Extension URI:** `https://a2a-settlement.org/extensions/settlement/v1`

The A2A Settlement Extension (A2A-SE) defines **settlement semantics** for the A2A task lifecycle. It is designed as a native A2A Extension per Section 4.6 of the A2A specification — **zero modifications to A2A core**.

## Design Principles

- **Non-invasive** — Uses A2A's Extension, metadata, and AgentCard mechanisms
- **Optional** — Agents that don't support settlement ignore it
- **Lifecycle-aligned** — Settlement states map to A2A TaskState transitions
- **Exchange as interface** — Any conforming implementation (hosted, self-hosted, rail-backed) is valid
- **Currency-agnostic** — Abstract token model; exchanges choose supported currencies; blockchain is not required
- **Multi-exchange** — Agents may register on multiple exchanges

## Key Sections

The full normative spec lives in the [a2a-settlement](https://github.com/a2a-settlement/a2a-settlement) repo as [SPEC.md](https://github.com/a2a-settlement/a2a-settlement/blob/main/SPEC.md) (**v0.11.0**). It covers:

- **AgentCard Integration** — Extension declaration, skill-level pricing
- **Task Lifecycle** — Settlement flow mapped to TaskStates
- **Exchange API** — REST endpoints (escrow, release, refund, etc.)
- **Token Model** — ATE, alternative currencies, deposits
- **Security** — Threat model, authentication, transport

## Quick Reference: Settlement States

| A2A TaskState | Settlement Action |
|---------------|-------------------|
| `submitted` / working | Escrow funded / held |
| `completed` | Release (after verification) |
| `failed` / `canceled` | Refund |
| disputed | Mediation → release or refund |

## Related

- [What is Agent Settlement?](../agent-settlement)
- [Conformance](../conformance)
- [Federation](../federation) (optional extension of Core)
