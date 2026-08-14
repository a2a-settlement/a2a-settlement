---
sidebar_position: 1
---

# AP2 vs A2A-SE

**AP2 authorizes the agent. A2A-SE ensures the agent actually did the work before the money settles.**

| | AP2 | A2A-SE |
|---|-----|--------|
| Primary question | May the agent spend? | Was the obligation satisfied? |
| Layer | Authorization | Settlement |
| Escrow / hold during work | Not the focus | Core |
| Disputes / reputation / finality | — | Yes |

Use AP2 (or OAuth settlement scopes) to bound spend. Use A2A-SE to commit, verify, and settle. Full stack comparison: [x402 vs AP2 vs A2A-SE](../architecture/protocol-comparison).
