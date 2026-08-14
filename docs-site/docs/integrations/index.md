# SDKs & Integrations

The A2A Settlement ecosystem includes official SDKs and framework integrations.

## Official SDKs

| Language | Package | Repo |
|----------|---------|------|
| Python | `a2a-settlement` | [a2a-settlement/sdk](https://github.com/a2a-settlement/a2a-settlement/tree/main/sdk) |
| TypeScript/JS | `@a2a-settlement/sdk` | [a2a-settlement/sdk-ts](https://github.com/a2a-settlement/a2a-settlement/tree/main/sdk-ts) |

Both SDKs mirror the same method signatures. See [Python SDK](./python-sdk) and [TypeScript SDK](./typescript-sdk).

## Framework Integrations

| Framework | Package | Repo | Pattern |
|-----------|---------|------|---------|
| **Google ADK** | `adk-a2a-settlement` | [adk-a2a-settlement](https://github.com/a2a-settlement/adk-a2a-settlement) | `to_settled_a2a()` + `SettledRemoteAgent` + tools |
| **CrewAI** | `crewai-a2a-settlement` | [crewai-a2a-settlement](https://github.com/a2a-settlement/crewai-a2a-settlement) | `A2ASettlementClient`, `SettledCrew` (v0.2) |
| **LiteLLM** | `litellm-a2a-settlement` | [litellm-a2a-settlement](https://github.com/a2a-settlement/litellm-a2a-settlement) | CustomLogger hooks (pre/post call) |

## Supporting Services

| Service | Repo | Purpose |
|---------|------|---------|
| **Mediator** | [a2a-settlement-mediator](https://github.com/a2a-settlement/a2a-settlement-mediator) | AI dispute resolution, WORM Merkle pipeline |
| **Auth** | [a2a-settlement-auth](https://github.com/a2a-settlement/a2a-settlement-auth) | OAuth settlement scopes, NIST compliance |
