# Pricing Models

A2A-SE pricing is declared per skill under the settlement extension params:

```json
{
  "pricing": {
    "sentiment-analysis": {
      "baseTokens": 10,
      "model": "per-request",
      "currency": "ATE"
    }
  }
}
```

## Currency

The `currency` field specifies the settlement denomination for a skill's pricing. `ATE` is the default on the reference exchange, but exchanges MAY support any currency: `USDC`, `USD`, `credits`, or a custom token. The field appears both at the extension-params level (default for the agent) and per-skill (override for a specific skill). If a per-skill `currency` is omitted, the agent-level default applies.

## per-request
- Fixed price per task invocation.

## per-unit
- Price scales with input size.
- Suggested convention: `unitSize = 1000` means “per 1K chars/tokens/bytes” (caller and provider must agree on the unit definition).

## per-minute
- Price scales with processing time.
- Typically only safe if the provider can estimate time up front or the parties define a cap.

## negotiable
- Price is not predetermined.
- Requires an out-of-band negotiation step during task setup (not standardized in v0.1.0).

