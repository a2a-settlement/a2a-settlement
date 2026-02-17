# A2A Settlement Extension (A2A-SE)

Economic settlement for the Agent2Agent (A2A) protocol.

When two agents discover each other through A2A and one performs work for the other, how does payment happen? This extension adds escrow-based token settlement to A2A's existing task lifecycle using A2A's native extension mechanism. Zero modifications to A2A core.

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

    git clone https://github.com/yourname/a2a-settlement
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
- `legacy/agent-token-exchange/`: original Node prototype retained for reference

## Development

Run tests:

    pytest -q

## License

MIT. See `LICENSE`.

