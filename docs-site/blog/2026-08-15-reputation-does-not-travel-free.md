---
slug: trust-discount
title: "Reputation does not travel for free"
authors: [rsmith]
tags: [federation, reputation]
description: Importing another exchange's reputation scores at face value imports its incentives too. A2A-SE Federation applies a Trust Discount that starts steep and has to be earned down.
---

An agent that has settled a thousand honest transactions on one exchange arrives at a second exchange as a stranger. That is a real cost — it locks agents to whichever exchange they started on, which is how an interface quietly becomes a platform.

The obvious fix is to let exchanges import each other's reputation scores. The obvious fix is also how you get reputation laundering.

<!-- truncate -->

## Why face value fails

Suppose Exchange B accepts Exchange A's reputation scores as equivalent to its own. Exchange B has now outsourced its trust model to an operator whose incentives it does not control and whose implementation it cannot inspect at runtime.

The attack writes itself. Stand up an exchange. Score your own agents at 1.0. Peer with someone real. Your agents now arrive everywhere pre-trusted, and the cost of manufacturing that trust was a domain name.

Even without malice the numbers are not commensurable. If A computes reputation over a 30-day window and B over 90, or A counts a partial release as success where B counts it as partial failure, then "0.85" from A and "0.85" from B describe different things. Averaging them produces a number that describes nothing.

## Discount rather than reject

[A2A-SE Federation](/docs/federation/) takes the position that foreign reputation is real evidence but weaker evidence, and prices it accordingly. Every peer carries a discount factor, ρ, and imported scores are multiplied by it:

```
effective_reputation = native_reputation × ρ
```

A new peer starts at **ρ = 0.15**. A perfect 1.0 from a brand-new peer enters as 0.15 — better than nothing, nowhere near parity. The ceiling is ρ = 1.0, and local reputation is always weighted at exactly 1.0, so an exchange's own observations are never discounted against an outsider's claims.

Imported evidence is also capped in aggregate. Blended scoring weights local history at 70% and the federated average at 30%, so foreign attestations can move an agent's standing but cannot dominate it. Under the hood, local reputation is still an exponential moving average with λ = 0.1, which means recent behavior on *this* exchange keeps outweighing history imported from elsewhere.

## What earns ρ down

The discount is not a fixed insult; it is a starting position. Exchanges advertise a named discount algorithm during the peering handshake — the default is `urn:a2a:trust:discount:linear-volume-weighted-v1` — along with the initial ρ and its parameters. Naming the algorithm matters, because it means a peer can inspect how it will be judged before agreeing to peer.

The telemetry that drives ρ is deliberately behavioral rather than reputational. Each peer accumulates federation age in days, cross-exchange settled volume, cross-exchange transaction count, and attestation success rate, alongside health signals like 90-day uptime, average attestation latency, and consecutive health-check failures. Policies can set an attestation success floor and a review cadence.

So ρ rises through settled cross-exchange volume that did not go wrong. A peer earns parity the same way an agent earns reputation: by being economically accountable over time, in public, at increasing stakes. There is no way to buy it and no way to declare it.

## The unglamorous parts matter

Two implementation details do most of the actual defensive work.

**Only active peers are accepted.** An attestation from an issuer that is not an active federation peer is rejected outright, with that stated as the reason. There is no anonymous reputation import, which means every imported score is attributable to a named counterparty who staked their own ρ on it.

**Attestations expire.** Imported credentials carry validity windows and an active flag, so foreign reputation decays unless it is refreshed. A score from an exchange that has gone dark stops counting, rather than lingering as permanent unearned credit.

Peering itself is a signed exchange of capability manifests and discount policies with nonce challenges in both directions, so peers cannot be silently impersonated or replayed into a trust relationship.

## Federation is optional, and that is the design

None of this is required to use A2A-SE. Core settlement — commitment, delivery, verification, release or refund, disputes, finality — is complete on a single exchange, and federation is a separate optional layer on top. If you never peer, you never import a foreign score, and nothing above applies to you.

That separation is intentional. A standard that forces every implementer to adopt a cross-organizational trust model in order to hold funds in escrow would deserve to be ignored. The useful sequence is: get settlement right locally, then decide whether portability is worth the discount.

The broader point generalizes past this protocol. Portable reputation is not a data format problem. Any two systems can agree on a JSON shape for "reputation: 0.85." The hard part is agreeing what it cost to earn — and until that is settled, the honest answer is to import it at a haircut and make the sender earn parity.
