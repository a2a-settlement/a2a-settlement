# Safeguarding Caps Appendix (draft)

**Status:** first cut for co-author review — @chopmob-cloud (AlgoVoi), @msaleme  
**Origin:** [a2aproject/A2A#1576](https://github.com/a2aproject/A2A/discussions/1576) — dispute-as-DoS thread  
**Applies to:** A2A-SE v0.11.x; intended as a normative appendix once reviewed

## Problem

Escrow-first settlement creates a liquidity-freeze attack surface. If any party can flag an escrow for mediation, a malicious or compromised agent can lock provider funds at scale: the per-attack cost is sub-dollar, the cumulative provider-side cost is unbounded. msaleme flagged this in #1576; AlgoVoi's Agent Trust Bench carries it as the `dispute_amplification` category.

Caps do not prevent the attack. They bound the blast radius per identity so the attack stops being economical. That is the whole design goal: a single bad actor should be able to freeze a known, small, pre-declared maximum — not "everything they can reach."

## Layering

Caps live in the trust/authorization overlay, not the rail. The rail moves funds mechanically (`release` / `refund`); the overlay decides whether a new escrow or dispute is allowed to exist given the identity's current exposure. This keeps the cap regime composable with any escrow design — an operator can adopt this appendix without adopting A2A-SE's exchange, and vice versa.

AlgoVoi ships this in production under UK MLR 2017 alignment. Their numbers are the reference point throughout: **£100 per mandate, £300 per account, max 3 active mandates per human principal.** Nothing in this appendix requires those specific values — they are one regulator-aligned instantiation.

## Cap taxonomy

Three caps, enforced at escrow creation and dispute filing:

### 1. Per-escrow cap (`max_escrow_amount`)

Upper bound on the value of any single escrow an identity may open. Reference value: £100 (AlgoVoi per-mandate cap).

Enforcement point: `POST /exchange/escrow` — reject with `ESCROW_CAP_EXCEEDED` if `amount > max_escrow_amount` for the requesting identity's tier.

### 2. Aggregate exposure cap (`max_active_exposure`)

Upper bound on the *sum* of all non-terminal escrows (status `held`, `evidence_pending`, `disputed`) attributable to one identity. Reference value: £300.

This is the cap that actually bounds dispute-DoS: an attacker who disputes everything they touch can freeze at most `max_active_exposure` per identity, regardless of how many escrows they fragment it into.

Enforcement point: `POST /exchange/escrow` — reject with `EXPOSURE_CAP_EXCEEDED` if existing non-terminal exposure plus the new amount exceeds the cap.

### 3. Concurrency cap (`max_active_escrows`)

Upper bound on the *count* of non-terminal escrows per identity. Reference value: 3 (AlgoVoi max-3 active mandates).

Value caps alone leave a griefing path: many tiny escrows, each individually disputed, each consuming mediator attention. The concurrency cap closes it. Mediator time is the scarce resource the amount caps don't protect.

Enforcement point: `POST /exchange/escrow` — reject with `CONCURRENCY_CAP_EXCEEDED`.

## Identity binding

Caps are only as strong as the identity they bind to. A cap per API key is no cap at all — registration is free.

- Caps MUST bind to the strongest identity tier the operator supports: human principal (KYC-verified) > verified developer > bare account.
- Operators SHOULD scale cap values by tier. Bare accounts get the floor values; verified identities can earn higher tiers.
- Where a human principal operates multiple agents, exposure aggregates across all of them (this is what AlgoVoi's "max-3 mandates per human" does).
- Sybil pressure migrates to the identity layer by design. That is the correct place for it — settlement caps should not try to solve identity.

Settlement-derived reputation (`GET /reputation/{agent_id}`) can inform tier promotion, but reputation MUST NOT raise caps on its own: msaleme's EMA-manipulation scenario (ring of sock-puppets builds score, cashes out on one large fraudulent task) is exactly a cap-raise attack. Tier promotion needs identity verification, not score thresholds.

## Dispute-side controls

Caps bound exposure; these bound the dispute path itself. Both came out of msaleme's #1576 analysis:

### Cost to dispute

Filing a dispute requires staking a percentage of the disputed escrow amount (suggested default: 10%, floor of one currency unit). Stake is returned if the dispute resolves in the filer's favor, forfeited to the counterparty otherwise. This makes mass-disputing economically self-limiting without pricing out legitimate disputes — a party with a real grievance expects to recover the stake.

### Time-bounded mediation fallback

A disputed escrow MUST NOT freeze funds indefinitely. If mediation does not rule within `mediation_timeout_seconds` (suggested default: 7 days), the escrow auto-resolves to its pre-dispute trajectory: release if the task had reached a terminal `COMPLETED` state, refund otherwise. This removes "freeze forever" as an attack outcome even when the mediator itself is the bottleneck or the target (the `mediator_grooming` pattern).

## Declaration surface

Operators declare their cap regime in the Agent Card extension block, alongside the rail finality declaration chopmob-cloud proposed in #1576:

```json
"a2a-se": {
  "rail": "a2a-se-exchange",
  "safeguarding": {
    "regime": "uk-mlr-2017",
    "currency": "GBP",
    "max_escrow_amount": "100.00",
    "max_active_exposure": "300.00",
    "max_active_escrows": 3,
    "dispute_stake_pct": 10,
    "mediation_timeout_seconds": 604800
  }
}
```

`regime` is informational (which regulatory alignment, if any, the values derive from). The numeric fields are what counterparties consume: a client agent deciding whether to open a £500 escrow can see *before creating it* that the operator will reject it, and can split or renegotiate instead of failing at runtime.

Operators that enforce no caps MUST omit the `safeguarding` block rather than declaring null values — absence is a signal counterparties can price in.

## Verification

A declared cap that doesn't fire under pressure is worse than no cap — it's a false safety signal. Cap enforcement belongs in the adversarial conformance track ([settlement-conformance](https://github.com/a2a-settlement/settlement-conformance)) as its own vector class:

- `cap_bypass_fragmentation` — open N escrows each below `max_escrow_amount` whose sum exceeds `max_active_exposure`; expect `EXPOSURE_CAP_EXCEEDED` on the crossing escrow.
- `cap_bypass_concurrency` — exceed `max_active_escrows` with minimal-value escrows; expect `CONCURRENCY_CAP_EXCEEDED`.
- `dispute_amplification` (existing AlgoVoi vector) — mass-dispute under caps; expected outcome shifts from BLOCK-at-rail to bounded-exposure-plus-stake-forfeiture.
- `stake_griefing` — file disputes with stakes against a victim to force mediator load; concurrency cap plus stake forfeiture should make this self-limiting. Needs a vector definition.

These vector definitions are the natural follow-up contribution to the conformance repo once this appendix stabilizes.

## What this appendix does not do

- It does not mandate specific cap values. The £100/£300/3 set is regulator-aligned prior art, not a requirement.
- It does not solve identity. Caps inherit the strength of the identity layer beneath them.
- It does not replace mediator integrity work. `mediator_grooming` and `synthetic_artifact_dispute` need mediator-side defenses; caps only bound what a compromised mediation path can cost.
- It does not cover cascade-refund semantics in multi-agent pipelines (msaleme's fourth point in #1576). That interacts with caps — a cascade refund can momentarily spike an identity's active exposure — but it needs its own treatment.

## Open questions for co-authors

1. **Currency normalization.** Caps in GBP, escrows in USDC — whose FX rate, sampled when? Cap evaluation at escrow-creation time with the operator's declared rate seems right, but it should be explicit.
2. **Tier schedule.** Should the appendix define named tiers (e.g. `floor` / `verified` / `institutional`) with multipliers, or leave tiering entirely operator-defined and only standardize the declaration shape?
3. **Stake custody.** Does the dispute stake go into the same escrow rail (recursive, clean) or a separate operator account (simpler, less trustless)? Leaning recursive.
4. **Per-counterparty caps.** Worth a fourth cap bounding exposure between a single requester/provider pair? Catches bilateral wash patterns that aggregate caps miss, at the cost of more state.
