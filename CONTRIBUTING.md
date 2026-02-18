# Contributing

Thanks for your interest in contributing to `a2a-settlement`.

This repository does two jobs:
- A formal extension proposal (`SPEC.md`) intended for eventual contribution to the A2A ecosystem
- A working reference implementation (exchange service + Python SDK + TypeScript SDK + runnable examples)

## Where to contribute

- **Spec feedback**: open an Issue or (preferably) a Discussion thread with concrete text changes to `SPEC.md`.
- **Bugs in the reference implementation**: open an Issue with reproduction steps.
- **New pricing models or settlement behaviors**: open a Discussion first if it changes the spec or API surface.
- **SDK improvements**: PRs welcome for both the Python SDK (`sdk/`) and TypeScript SDK (`sdk-ts/`).

## How to propose a new feature

1. **Open a GitHub Discussion** with a description of the feature and its motivation.
2. Get feedback from maintainers and the community.
3. If the feature changes the spec or API surface, submit a PR to `SPEC.md` first.
4. Once the spec change is approved, submit an implementation PR with tests.
5. Link the implementation PR to the spec PR.

## Governance

- Decisions are made via GitHub Discussions. Significant changes require at least 2 maintainer approvals.
- Spec changes (`SPEC.md`, `openapi.yaml`) require a Discussion thread before a PR.
- Implementation changes that don't affect the spec can be submitted as direct PRs.
- Breaking changes to the API surface require a minor version bump and a changelog entry.

## Development setup

Recommended: use a clean Python environment (venv/conda).

Install dev dependencies:

    pip install -e ".[exchange,examples,dev]"

## Running the exchange locally

SQLite (fastest local dev, no Docker):

    A2A_EXCHANGE_DATABASE_URL="sqlite:///./a2a_exchange.db" a2a-exchange

Docker Compose (PostgreSQL):

    docker compose up -d

Then:

    curl -s http://127.0.0.1:3000/health

## Running tests

Run all tests:

    pytest -q

Run with coverage:

    pytest --cov=exchange --cov=sdk/a2a_settlement --cov-report=term-missing -q

## TypeScript SDK development

    cd sdk-ts
    npm install
    npm run build

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
- Don't add comments that merely narrate what the code does.
