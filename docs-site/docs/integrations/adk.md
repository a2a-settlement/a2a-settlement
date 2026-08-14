# Google ADK Integration

[adk-a2a-settlement](https://github.com/a2a-settlement/adk-a2a-settlement) adds escrow-based settlement to Google's Agent Development Kit (ADK).

```bash
pip install adk-a2a-settlement
```

## Provider (Exposing a Settled Agent)

```python
from google.adk.agents import Agent
from adk_a2a_settlement import SettlementConfig, to_settled_a2a

analyst = Agent(
    name="sentiment_analyst",
    model="gemini-2.5-flash",
    instruction="Analyze the sentiment of the given text.",
    description="Sentiment analysis agent",
)

app = to_settled_a2a(
    agent=analyst,
    config=SettlementConfig(),
    pricing={"sentiment-analysis": {"baseTokens": 100, "model": "per-request"}},
)

# Run: uvicorn provider:app --port 8001
```

## Requester (Consuming a Settled Agent)

```python
from adk_a2a_settlement import SettledRemoteAgent, SettlementConfig

analyst = SettledRemoteAgent(
    name="analyst",
    description="Remote sentiment analysis agent",
    agent_card="http://localhost:8001/.well-known/agent.json",
    config=SettlementConfig(),
)

escrow = analyst.create_escrow(task_id="task-001", task_type="sentiment-analysis")

# Use analyst.remote_agent in your orchestrator
# After task completes:
analyst.release(task_id="task-001")  # or analyst.refund("task-001", reason="...")
```

## Settlement Tools

Give your ADK agent direct settlement capabilities:

```python
from adk_a2a_settlement.tools import create_settlement_tools

agent = Agent(
    name="settlement_manager",
    tools=create_settlement_tools(SettlementConfig()),
)
```

Available tools: `check_balance`, `create_escrow`, `release_escrow`, `refund_escrow`, `dispute_escrow`, `lookup_agent`, `get_escrow_status`.
