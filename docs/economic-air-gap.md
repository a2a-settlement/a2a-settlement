# Economic Air Gap for AI Agents

The Economic Air Gap is a security and settlement layer that sits between an AI agent and the real world. Even if an agent is compromised or hallucinates, the potential damage -- both financial and operational -- is capped at near zero.

## The Three Components

### 1. A2A Settlement Extension (A2A-SE) -- Settlement Semantics

Instead of giving an agent open-ended access to a credit card or API key, it operates on a micro-budget held in a smart escrow. Funds are only released if the task is successfully completed and verified.

- **Escrow lifecycle:** create, release, refund, dispute, auto-expire
- **Spending guards:** rolling-window and velocity limits
- **Automatic kill switch:** escrow TTL with background expiry loop
- **Economic accountability:** reputation EMA, compliance audit trail, Merkle proofs

This component is fully implemented in the core `a2a-settlement` exchange.

### 2. Secret Masking (The "Secret-less" Agent)

Sensitive credentials (GitHub PATs, AWS keys, Slack tokens) are moved out of the agent's reach. The agent never sees the actual key in its environment or context window; it only sees a `secret_id` placeholder (e.g., `sec_github_deploy_abc123`).

- **SecretVault:** Encrypted storage using Fernet (AES-128-CBC + HMAC-SHA256)
- **SecretRegistry:** CRUD for secrets: register, rotate, revoke, list
- **Access control:** Secrets scoped to organizations and optionally to specific agents
- **Audit logging:** Every resolve attempt is logged (who, when, which secret, which escrow)

This component is implemented in `a2a-settlement-auth` as the vault module.

### 3. Security Shim (The Exchange Proxy)

A network-level forward proxy that acts as the gatekeeper. When an agent wants to use a tool (e.g., "Post to Slack"), it sends the request to the shim. The shim:

1. Checks if there is an active, funded escrow for the task
2. Computes the cost (flat fee or per-destination pricing)
3. Resolves the `secret_id` to the real credential via the vault
4. Injects the credential into the outbound request at the last millisecond
5. Forwards to the destination and returns the response
6. Records an audit entry

The shim is **fully optional**. Operators who don't need credential isolation can skip it entirely.

## Why This Matters

### Immunity to Prompt Injection
An attacker can't trick the agent into revealing a key it doesn't have. The agent only holds a `secret_id` placeholder.

### Automatic Kill Switch
If an agent gets stuck in a loop and depletes its escrow, the shim returns HTTP 402 Payment Required and physically cuts off API access.

### Economic Accountability
Every agent action is a contract. No work = no pay = no access. Every credential access, every proxied request, and every settlement produces immutable, timestamped, Merkle-backed records.

## Architecture

```
┌──────────────────┐
│   AI Agent       │  Only has: secret_id placeholders, escrow_id
│                  │  Never has: real credentials
└────────┬─────────┘
         │
         │  POST /shim/proxy {tool_id, escrow_id}
         │  or {destination_url, secret_id, escrow_id}
         ▼
┌──────────────────┐
│  Security Shim   │  1. Check escrow balance (via exchange)
│  (port 3300)     │  2. Resolve secret_id (via vault)
│                  │  3. Inject credential
│                  │  4. Forward request
│                  │  5. Audit log
└────────┬─────────┘
         │
         ▼
┌──────────────────┐     ┌──────────────────┐
│  External APIs   │     │  Secret Vault    │
│  (GitHub, Slack, │     │  (a2a-settlement │
│   AWS, etc.)     │     │   -auth)         │
└──────────────────┘     └──────────────────┘
```

## Two Request Modes

### Full Air Gap (tool_id)
The agent sends `{tool_id: "github-create-issue", escrow_id: "..."}`. The shim resolves the destination URL, HTTP method, and secret from the tool registry. The agent never sees the real URL or credential.

### Developer Escape Hatch (destination_url)
The agent sends `{destination_url: "https://api.github.com/...", secret_id: "sec_...", escrow_id: "..."}`. The shim still injects the credential and gates on escrow, but the agent knows the destination.

## Cost Model

The shim supports pluggable cost models:

- **FlatFeeCostModel:** Every proxied call costs the same (default: 1 credit)
- **PerDestinationCostModel:** Different destinations cost different amounts (e.g., Slack post = 0.5, AWS Lambda = 5.0)
- **Custom:** Subclass `CostModel` for time-based, payload-size, or ML-inferred pricing

## Destination Policy

- **Sandbox mode** (`A2A_SHIM_DESTINATION_MODE=allow`): Default-allow with blocklist. Critical for developer onboarding.
- **Production mode** (`A2A_SHIM_DESTINATION_MODE=deny`): Default-deny with allowlist. Locks down external access.

## Quick Start

```bash
# Start the exchange
python exchange/app.py &

# Start the shim
python -m shim.app &

# Run the three-act demo
python examples/air_gap_demo.py
```

## Related Components

| Component | Repo | Role |
|-----------|------|------|
| Exchange | `a2a-settlement` | Escrow engine, spending guards, reputation |
| Secret Vault | `a2a-settlement-auth` | Encrypted credential storage |
| Security Shim | `a2a-settlement` (`shim/`) | Forward proxy with credential injection |
| MCP Tools | `a2a-settlement-mcp` | MCP interface for Claude, Cursor, LangGraph |
| Mediator | `a2a-settlement-mediator` | AI dispute resolution + WORM compliance |
| Dashboard | `a2a-settlement-dashboard` | Human oversight UI |
