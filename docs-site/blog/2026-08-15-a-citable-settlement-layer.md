---
slug: citable-settlement-layer
title: "Making the settlement layer citable"
authors: [rsmith]
tags: [standards, provenance]
description: Two NIST public comments and the A2A-SE specification now have permanent DOIs. Here is why a category needs citable artifacts, not just a repository.
---

A category does not exist because someone declares it. It exists when other people can cite it.

As of this week, three A2A-SE artifacts have permanent DOIs: two public comments filed with NIST, and the normative specification itself. That last one matters more than it sounds, and this post explains why we bothered.

<!-- truncate -->

## What is now archived

| Artifact | DOI |
|---|---|
| A2A-SE Specification v0.11.0 | [10.5281/zenodo.21953795](https://doi.org/10.5281/zenodo.21953795) |
| NIST CAISI public comment | [10.5281/zenodo.21745191](https://doi.org/10.5281/zenodo.21745191) |
| NIST NCCoE public comment | [10.5281/zenodo.21745274](https://doi.org/10.5281/zenodo.21745274) |

The specification also has a concept DOI, [10.5281/zenodo.21953794](https://doi.org/10.5281/zenodo.21953794), which always resolves to the newest archived release. Cite the version DOI to pin v0.11.0; cite the concept DOI when you mean "the spec, whatever its current version."

The full chronology lives on the [Standards & Provenance](/docs/standards/) page.

## A GitHub URL is not a citation

The specification has been public since 17 February 2026, in commit [`c5ba9aaa`](https://github.com/a2a-settlement/a2a-settlement/commit/c5ba9aaa8bfca489d1f95cd78a695b98988dacc2). Anyone could read it. So what did the deposit actually add?

A repository URL is a *location*, not an *identifier*. It breaks when an org is renamed, a repo is transferred, a branch is rewritten, or a company folds. Worse, it points at a moving target: `main` today is not `main` last March, so a citation to a repository is a citation to nothing in particular. If a standards body or an academic paper wants to reference the settlement semantics we defined, "see this GitHub link" is not a reference they can rely on.

A DOI fixes both problems. It resolves to a specific immutable deposit, held by an institution whose job is outliving us, and it carries a timestamp that is not ours to edit.

That timestamp is the part people underrate. The claim we care about is not "we wrote a spec." It is "the settlement layer was identified as a distinct architectural concern, with concrete semantics, on this date." Priority claims need dates that a third party will vouch for.

## Why the NIST comments came first

Both comments were filed before the spec was archived, and both are grounded in a running implementation rather than a proposal.

The [CAISI comment](https://doi.org/10.5281/zenodo.21745191) responded to the Request for Information on Security Considerations for Artificial Intelligence Agents (Docket NIST-2025-0035). Its argument: the economic settlement layer is a largely unexamined attack surface for agentic AI. When autonomous agents transact across organizational boundaries, you get threats with no clean parallel in traditional software — settlement fraud, reputation poisoning, escrow-timing attacks, cascading multi-agent settlement failures, cross-boundary trust exploitation.

The [NCCoE comment](https://doi.org/10.5281/zenodo.21745274) responded to the concept paper on accelerating adoption of software and AI agent identity and authorization. Its argument is one sentence: identity without economic accountability is incomplete. OAuth, OIDC, and SPIFFE can establish *who this agent is* and *what it may access*. None of them answer *what economic commitments it may make*, or *what happens when it commits and fails to deliver*.

Notice that both comments make the same structural point from different directions, which is the point of filing them separately. Security people arrive at settlement by asking what an agent can do to you. Identity people arrive at it by asking what an agent is allowed to do. Both roads end at an unowned layer.

## The three-layer position, stated once

Agent commerce has at least three separable concerns, and conflating them is the source of most confused architecture:

- **Payments** decide how value moves.
- **Authorization** decides whether an agent may spend.
- **Settlement** decides whether the obligation was satisfied, and what happens to value that was already committed.

A2A-SE is a standard for the third one. It is not a payment rail and does not want to be. It integrates with authorization rather than replacing it. If you want the long version, see [What is Agent Settlement?](/docs/agent-settlement/).

## What this does not mean

An archived specification is not a ratified standard, and a DOI is not an endorsement. Zenodo assigns identifiers; it does not review content. Filing a public comment means NIST received it, not that NIST agreed.

What the deposits do accomplish is narrow and real. The artifacts are permanent, dated, and citable. If the settlement layer becomes contested ground — and it will, because it sits directly between two layers that already have well-funded standards efforts — the record of who specified what, and when, is now held somewhere we cannot quietly revise.

That is worth a DOI.
