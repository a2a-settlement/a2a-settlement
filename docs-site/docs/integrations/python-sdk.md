# Python SDK

```bash
pip install -e ./sdk
# or from PyPI (when published): pip install a2a-settlement
```

## Quick Start

```python
from a2a_settlement import SettlementExchangeClient
from a2a_settlement.agentcard import build_settlement_extension
from a2a_settlement.metadata import build_settlement_metadata

client = SettlementExchangeClient(base_url="http://localhost:3000", api_key="ate_...")

# Create escrow
escrow = client.create_escrow(provider_id="...", amount=10, task_type="sentiment-analysis")

# Build metadata for A2A message
meta = build_settlement_metadata(
    escrow_id=escrow["escrow_id"],
    amount=escrow["amount"],
    exchange_url="http://localhost:3000",
)

# Release on completion
client.release_escrow(escrow_id=escrow["escrow_id"])
```

## AgentCard Extension

```python
from a2a_settlement.agentcard import build_settlement_extension

ext = build_settlement_extension(
    exchange_urls=["https://exchange.a2a-settlement.org/api/v1"],
    account_ids={"https://exchange.a2a-settlement.org/api/v1": "my-uuid"},
    pricing={
        "sentiment-analysis": {
            "baseTokens": 10,
            "model": "per-request",
            "currency": "ATE",
        }
    },
    reputation=0.87,
)
# Add ext to your AgentCard capabilities.extensions array
```

## Provider Verification

When receiving a message, verify the escrow before starting work:

```python
block = message.metadata.get("a2a-se", {})
if block.get("escrowId"):
    escrow = client.get_escrow(block["escrowId"])
    if escrow["status"] != "held" or escrow["provider_id"] != my_account_id:
        raise ValueError("Invalid escrow")
```
