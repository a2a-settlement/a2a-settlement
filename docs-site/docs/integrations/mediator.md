# AI Mediator (Dispute Resolution)

[a2a-settlement-mediator](https://github.com/a2a-settlement/a2a-settlement-mediator) provides AI-powered dispute resolution for the A2A Settlement Exchange.

## How It Works

```
Agent A disputes escrow
        │
        ▼
  ┌─────────────┐     escrow.disputed
  │  Exchange    │────webhook────▶┌──────────────┐
  │              │                │  Mediator     │
  └─────────────┘◀───resolve─────│               │
                                  └──────┬───────┘
                                         │
                              ┌──────────┴──────────┐
                        confidence ≥ 80%      confidence < 80%
                              │                     │
                        Auto-resolve          Escalate to
                        (release/refund)      human operator
```

1. **Evidence collection** — Fetches escrow details, deliverables, acceptance criteria, dispute reason
2. **LLM evaluation** — Claude/GPT-4 via LiteLLM with structured decision framework
3. **Confidence gating** — Auto-resolve if ≥80%, else escalate (e.g., Slack)
4. **Audit trail** — Full record: evidence, reasoning, token usage

## Quick Start

```bash
git clone https://github.com/a2a-settlement/a2a-settlement-mediator
cd a2a-settlement-mediator
pip install -e .
cp .env.example .env
# Edit .env with exchange URL, operator API key, LLM key
a2a-mediator
```

## Programmatic Usage

```python
from a2a_settlement_mediator import mediate, VerdictOutcome

audit = mediate("escrow-id-here")

if audit.verdict.outcome == VerdictOutcome.AUTO_RELEASE:
    print(f"Released with {audit.verdict.confidence:.0%} confidence")
elif audit.verdict.outcome == VerdictOutcome.AUTO_REFUND:
    print(f"Refunded with {audit.verdict.confidence:.0%} confidence")
else:
    print(f"Escalated — confidence {audit.verdict.confidence:.0%}")
```

## WORM Pipeline

The mediator also includes a SEC 17a-4 WORM settlement pipeline. See [WORM Merkle Trees](../architecture/worm-merkle) for details.
