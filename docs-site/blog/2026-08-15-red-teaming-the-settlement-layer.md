---
slug: red-teaming-settlement
title: "Red-teaming a settlement layer: three ways agents lie"
authors: [rsmith]
tags: [security, verification]
description: A deliverable that looks correct and a deliverable that is correct are different things. The A2A-SE simulation harness models three archetypes of fabricating agent.
---

Escrow protects you from a counterparty who never delivers. It does nothing about a counterparty who delivers something that merely looks right.

That is the harder problem, and it is the one that decides whether settlement is worth anything. If value releases whenever a provider returns well-formed JSON, then an agent that fabricates well-formed JSON has found a money printer. So the reference implementation ships an adversarial simulation harness whose entire job is producing convincing garbage and seeing what survives verification.

<!-- truncate -->

## The threat is plausibility, not malformedness

Broken output is easy. It fails a schema check, and nobody needs a settlement layer to catch it.

The interesting adversary produces output that passes every structural check and is still fiction. It cites a real domain. Its timestamps are recent and internally consistent. Its content hash is the right length and character set. Its record count matches the metadata that describes the record count. Everything is coherent, and none of it happened.

This is not a hypothetical failure mode for language-model agents; it is their default failure mode. A model asked to retrieve data and unable to retrieve it will frequently produce something shaped exactly like the answer. The settlement layer is the last checkpoint before that becomes a payment.

## Three archetypes

The harness in [`simulation/`](https://github.com/a2a-settlement/a2a-settlement/tree/main/simulation) implements three adversarial agents, each isolating a different way provenance can be false while looking true. All three declare `is_fabricated=True` internally, so scoring knows ground truth; the verifier does not.

**The fake endpoint citer** invents the source outright. It generates a random subdomain like `api.nonexistent-service-abcdef.io` and claims a `GET` against it. This is the easiest case: the endpoint cannot be reached, because it does not exist. It exists in the harness as a control — a verifier that cannot catch this one is not doing anything at all.

**The GitHub fabricator** is more interesting because its citation is *real*. It points at `https://api.github.com/repos/{repo}/commits`, a legitimate, reachable, well-known endpoint. The commits it returns are invented: SHAs are the first twelve hex characters of a hashed UUID, messages are drawn from a list of plausible conventional-commit strings. The content hash is 64 random hex characters — correct in form, meaningless in fact. Reachability checks pass. Only comparing the claimed hash against the actual response catches this.

**The plausible hallucinator** attacks the attestation tier itself. It cites a real API, fabricates the payload, and then claims `attestation_level: "signed"` while supplying `"fake-x-request-id-12345"` as the signature. It is asserting a stronger provenance guarantee than it can back. A verifier that trusts self-reported tiers will rank this *above* an honest agent that modestly declares `self_declared`.

That last archetype is the one that keeps me up. Tiered provenance creates an incentive to overclaim your tier, so tier assertions have to be verified rather than believed. Otherwise the tier system inverts: honesty is penalized.

## Honest agents are half the experiment

The harness also implements three honest agents — a GitHub retriever, a web extractor, and a dataset summarizer — and by default assigns tasks with `honest_ratio=0.5`.

They are not there for balance. They are there because detection rate alone is a worthless metric. A verifier that rejects everything scores 100% detection. The number that matters is what it does to legitimate work, so scoring tracks the full confusion matrix: fabricated and flagged, fabricated and approved, honest and approved, honest and flagged. A false positive is an honest provider who did the work and did not get paid, which is a more expensive failure than it first appears, because it drives good providers off the exchange.

Two further measures come out of each run. **Escrow protected** sums the value held in escrows where fabrication was caught, which converts detection into the only unit that matters to an operator. **Verification latency** records the overhead, because a check nobody can afford to run is a check nobody runs.

Scenarios are plain YAML — currently data retrieval, code review, and document summary — each fixing an attestation tier and an escrow amount, so you can ask how detection changes as the stakes rise.

## No numbers yet, deliberately

The harness is checked in. Published results are not, and I want to be precise about why.

Producing credible numbers requires the mediator's verification pipeline running against live endpoints, and the results are only as meaningful as the scenario set is representative. Three scenario files authored by the same person who wrote the adversaries is not an evaluation; it is a demonstration. Publishing a detection rate from it would be exactly the failure mode this whole post is about — a well-formed number with nothing behind it.

So the harness is offered as methodology rather than evidence, and the invitation is for someone else to run it. The neutral place to do that is the [conformance suite](/docs/conformance/), which is designed so that any settlement rail can be tested without using our exchange.

The claim I will make is narrower: a settlement layer that never verifies deliverables is not settling anything. It is a payment queue with extra steps.
