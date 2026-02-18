# A2A Settlement Extension (A2A-SE)

Economic settlement for the Agent2Agent (A2A) protocol.

When two agents discover each other through A2A and one performs work for the other, how does payment happen? This extension adds escrow-based token settlement to A2A's existing task lifecycle using A2A's native extension mechanism. Zero modifications to A2A core. The exchange is an interface, not a service -- agents can point to any conforming implementation (hosted, self-hosted, or on-chain) via the `exchangeUrl` field in their AgentCard.

```
RequesterAgent  <---- A2A ---->  ProviderAgent
     |                               |
     +--------- HTTPS (A2A-SE) ------+
                 Exchange
          escrow / release / refund
```

Read the proposal: `SPEC.md`.

## Get started in 60 seconds

Clone, install the SDK, run the exchange, run the demo:

    git clone https://github.com/widrss/a2a-settlement
    cd a2a-settlement
    python -m pip install -e ./sdk
    python exchange/app.py &
    python examples/quickstart.py

You should see an escrow created and released, and balances updated.

## Repo structure

- `SPEC.md`: the extension specification (draft v0.1.0)
- `exchange/`: FastAPI + SQLAlchemy settlement exchange (SQLite dev, Postgres prod)
- `sdk/`: pip-installable Python SDK (`pip install a2a-settlement`)
- `examples/`: runnable demos (including A2A SDK integration)
- `docs/`: deeper integration + architecture notes
- Node.js prototype (archived): https://github.com/widrss/a2a-settlement-node-prototype

## How A2A-SE relates to x402

x402 is pay-per-call (an access gate). A2A-SE is task settlement (escrow for work-in-progress). They're complementary layers, not alternatives. An agent can use x402 for discovery gating and A2A-SE for multi-step task payment. See Section 10.1 of the spec for the full hybrid flow.

## Development

Run tests:

    pytest -q

## License

MIT. See `LICENSE`.

