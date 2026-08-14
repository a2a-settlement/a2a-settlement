# CrewAI Integration

[crewai-a2a-settlement](https://github.com/a2a-settlement/crewai-a2a-settlement) bridges CrewAI multi-agent orchestration with the A2A Settlement Exchange.

```bash
pip install crewai-a2a-settlement
```

## Quick Start

```python
from crewai_a2a_settlement import A2AConfig, A2ASettlementClient

config = A2AConfig(api_key="your-sandbox-key")
client = A2ASettlementClient.initialize(config)

# Register agents
payer = client.register_agent("Orchestrator", ["orchestrate"])
payee = client.register_agent("Scraper", ["web_scraping"])

# Escrow → execute → release/cancel
receipt = client.escrow(
    payer_address=payer,
    payee_address=payee,
    amount=5.0,
    task_id="scrape-task-001",
    description="Scrape product data from example.com",
)

# On success:
result = client.release(receipt.escrow_id)

# Or on failure:
# result = client.cancel(receipt.escrow_id, reason="Scraper timed out")
```

## Configuration

| Env Variable | Default | Description |
|--------------|---------|-------------|
| `A2ASE_API_KEY` | *(required)* | API key from sandbox.a2a-se.dev |
| `A2ASE_EXCHANGE_URL` | `https://sandbox.a2a-se.dev` | Exchange base URL |
| `A2ASE_NETWORK` | `sandbox` | sandbox, devnet, or mainnet |
| `A2ASE_AUTO_REGISTER` | `true` | Auto-register agents at crew kickoff |

## Roadmap

- **v0.1:** SDK client layer (current)
- **v0.2:** `SettledAgent`, `SettledTask`, `SettledCrew` — full CrewAI integration
- **v0.3:** PyPI publish, ecosystem templates (LangGraph, AutoGen)
