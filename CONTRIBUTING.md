# Contributing

Thanks for your interest in contributing to `a2a-settlement`.

This repository does two jobs:
- A formal extension proposal (`SPEC.md`) intended for eventual contribution to the A2A ecosystem
- A working reference implementation (exchange service + Python SDK + runnable examples)

## Where to contribute

- Spec feedback: open an Issue or (preferably) a Discussion thread with concrete text changes to `SPEC.md`.
- Bugs in the reference implementation: open an Issue with reproduction steps.
- New pricing models or settlement behaviors: open a Discussion first if it changes the spec or API surface.

## Development setup

Recommended: use a clean Python environment (venv/conda).

Install dev dependencies:

    python -m pip install -e ".[exchange,examples,dev]"

Run tests:

    pytest -q

## Running the exchange locally

SQLite (fastest local dev, no Docker):

    A2A_EXCHANGE_DATABASE_URL="sqlite:///./a2a_exchange.db" a2a-exchange

Then:

    curl -s http://127.0.0.1:3000/health

## Examples

End-to-end demo (A2A comms + escrow settlement):

    python examples/settle_task_end_to_end.py

## Spec changes and compatibility

- This extension is designed to use only A2A-sanctioned extension points (AgentCard extensions + metadata).
- Avoid changes that require modifying A2A core protocol objects or state machine.
- Prefer additive changes (new optional fields, new endpoints) and keep backward compatibility when possible.

## Code style

- Keep code small and readable; this repo is meant to be learned from.
- Avoid introducing heavy dependencies unless they materially improve clarity or correctness.

