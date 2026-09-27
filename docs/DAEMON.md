---
title: What Daemon is
status: product identity; DEC11–DEC12 ratified by the product owner. Implementation is governed separately.
repository_path: docs/DAEMON.md
governs: product identity, promises and priority order (direction, never implementation status)
---

# What Daemon is

> **Daemon is one personal AI for everything, from a quick question to autonomous work that runs for days. It uses the best models available at any moment, and your context belongs to you rather than to any AI lab.**

Tagline: **One Daemon. Every model. Your context.**

Daemon brings together the AI tooling that should exist but is split up today:

- the everyday breadth of ChatGPT, Claude and Perplexity: chat, search, images, documents, scheduled tasks, reminders, email, calendar and other integrations, and general personal-assistant work;
- the follow-through of autonomous agents in the OpenClaw/Hermes style: finishing tasks, writing and shipping code, working in the cloud or on your computer, and running on their own until the job is done.

Everything starts from **one conversational entry point**. Tasks, artifacts and project views share the same underlying context. The user never has to move context by hand between separate products or modes.

This document says what Daemon is and why. How it's built is covered in the README architecture section. The detailed principles and acceptance criteria are in [DAEMON_VISION.md](DAEMON_VISION.md). Ratified decisions are in [DAEMON_VISION_DECISIONS.md](DAEMON_VISION_DECISIONS.md). Status is in [FEATURE_MATRIX.md](FEATURE_MATRIX.md), and the code wins on what exists.

**Ratification boundary:** DEC11–DEC12 are ratified product decisions. As DEC11 states, "spine before surface" and build order remain **Proposed** sequencing, not ratified scope. DEC12's R-route implementation requires a separate design and approval; until then, runtime routing stays Z-only. Descriptions of target capabilities and retention classes below do not claim they are implemented.

## Why it exists

1. **The labs split one assistant into several products.** Chat, "work" modes, coding agents, remote sessions and browser agents each have their own interface, context and limits. The user ends up doing the routing.
2. **Models leapfrog every few months, and your context stays behind.** Months of work get locked inside one vendor's product, and moving it is painful. It has to be moved again when the lead changes hands. No lab has a reason to fix this, because lock-in is their business model.
3. **No single model family is best at everything.** The best system uses the strongest qualified model for each role at any given time.

Daemon fixes all three. There's one interface, the context stays with the user, and the models are replaceable parts chosen in Daemon's configuration.

## The four promises

| Promise | Meaning | Test |
|---|---|---|
| **One place** | Chat is just chat. When a request needs more, Daemon uses tools, spawns agents or runs long work from the same conversation. The user never picks a mode. | No capability requires the user to switch products or modes, or to move context between them by hand. |
| **Any task** | "Where's the best pizza near me?" and "Build a Zelda-style action game in Rust and tell me when the GitHub link is ready" are both normal requests. Daemon works out what the task needs and chooses how to do it. | The same entry point handles a 2-second answer and a multi-hour autonomous job. The user sees effort in proportion to the task. |
| **Best models** | Daemon is not one model or one model family. Each role (fast answers, orchestration, reasoning, coding, research, extraction, media) goes to the current best model that meets the role's quality, cost and privacy requirements. Routing lives in Daemon's configuration and is updated as the field moves. | Replacing a qualified model behind an existing role takes a configuration change and passing evaluations, with no user-managed migration and no loss of context. Daemon handles any internal conversion or re-indexing explicitly (for example, after an embedding-model change). |
| **Your context** | Memory, conversations, files, projects, tasks and preferences belong to the user in Daemon. They don't depend on any model or provider, and they can be inspected, corrected, exported and imported. | The user can change every model without losing anything. They can bring history in from ChatGPT or Claude and take everything out again. |

## How Daemon handles a request

Daemon (the coordinating agent) looks at each request and chooses the lightest approach that will actually finish it:

| Request | What Daemon does | What the user sees |
|---|---|---|
| "Where's the best pizza near me?" | Fast model, location (if granted) and a places/web search. Answers directly. | An answer in seconds. No visible task interface. |
| "Draft a reply to Sam and put the meeting in my calendar" | Email and calendar integrations inside the user's grants. Asks before sending if the grants require it. | A draft for approval and a confirmed event. |
| "Research X and give me a report by tonight" | Research subagents, a document artifact and a scheduled delivery. | Progress, then a finished document and a notification. |
| "Build a Zelda-style action game in Rust and tell me when the GitHub link is ready" | Plan, a hosted sandbox and repository, coding agents on the best coding model, checkpoints and tests. | Status in the conversation. It continues after the app closes, notifies on completion or when blocked, and returns the link. |

Every accepted request is durably saved and tracked **before** Daemon acknowledges it. Ordinary questions use the same continuity foundation as long-running work, without a visible task interface. Closing the app is not cancellation (DEC04–DEC05). Complexity is Daemon's problem, not the user's.

## The spine: what everything sits on

The capabilities users see are built on five shared layers. The whole product depends on them, so they come first.

1. **Task runtime.** Accepted requests are durable and tracked. Work continues after the app closes; it can be resumed, cancelled and budgeted, and it can spawn subagents (Stage 1; [DURABLE_REQUEST_DESIGN.md](DURABLE_REQUEST_DESIGN.md)).
2. **Context layer.** Memory (bitemporal, source-linked, correctable), files, artifacts, conversations and projects. It doesn't depend on any model and supports import and export. This is the moat.
3. **Model router.** Roles map to models in config, qualified by evaluations, retention class and price ceilings.
4. **Authority layer.** Permissions, privacy routing, approvals and compute budgets are enforced outside the model. They make autonomy safe to hand over.
5. **Execution environments.** A hosted sandbox by default, then integrations (email, calendar and similar), then connected devices. Each is scoped by the authority layer.

Chat, search, images, voice, documents, reminders, scheduled tasks, integrations, coding, computer use and device work are **capabilities plugged into this spine**. They are not separate products.

## Priority order (use this to settle trade-offs)

**Authority and data policy are hard constraints on every priority below.** When execution can't continue within them, continuity means keeping the request, its progress and a truthful state. It never means carrying on past a revoked permission or an exhausted budget.

1. **Continuity.** Accepted work is never lost or silently dropped.
2. **Authority and data policy.** Daemon never exceeds grants, budgets or the privacy policy, and it fails closed.
3. **Context integrity.** Daemon remembers accurately, keeps provenance, and never traps the user's context.
4. **Simplicity.** One entry point with no modes, and effort proportional to the task.
5. **Breadth.** Every capability, added on the spine and never beside it.

**Spine before surface:** a capability that bypasses the task runtime, the context layer or the authority layer doesn't ship, however impressive it is.

## Model routing and privacy (DEC12)

"Latest model" means *the latest model that has passed Daemon's evaluations and data policy for that role*. It never means an unqualified `/latest` alias. Every route carries a retention class:

- **Z, zero retention: the default for every role.** Open-weight models on ZDR providers, and frontier models on endpoints that are verified as ZDR route by route. Hosting paths such as Bedrock, Vertex, Azure, or an intermediary's ZDR label are candidates to verify. They are not blanket assurances.
- **R, provider-retained: opt-in only.** No training, and bounded, documented retention. This is for models that have no ZDR path, such as newly released models or models whose provider requires retention for safety. The user enables R per model family after seeing the provider and its retention period. It's never used for restricted-project data, and every use appears in the activity record.
- **T, trains on inputs.** Never used.

Daemon takes all reasonable steps to choose the best ZDR host for each role. It keeps its own dated evidence per route instead of relying only on an intermediary's classification. Privacy opt-in is separate from the plan: a paid plan never means less private routing.

Providers receive requests through Daemon, not through the user's personal provider account. Daemon avoids sending account identifiers and keeps identifying metadata and context to a minimum. This reduces direct account linkage. It doesn't guarantee anonymity or prevent correlation through content or traffic patterns. The main protection is sending only the context a request needs. Neither class can be checked independently; both rest on provider attestation.

## What Daemon is not

- **Not a wrapper around one lab.** Any model can be replaced. The user's context and Daemon's orchestration are what stay.
- **Not a chat app with an agent attached, or an agent with a chat box attached.** It's one system that scales its effort to the task.
- **Not a set of modes.** There's no "work mode", "agent mode" or "code product" boundary for the user to manage.
- **Not an orchestration platform or developer middleware.** Routing and subagents are internal machinery. The OpenAI-compatible endpoint is a convenience, not the product.
- **Not local-first by default.** It's hosted by default, and a phone alone is enough. Connected devices, self-hosting and local models are later extensions.

## Honest tensions

- **Scope vs one developer.** The destination is the combined surface of several frontier products. The spine makes that achievable; building features first doesn't. Capabilities arrive in spine order, and each one is finished before the next.
- **Autonomy vs trust.** Daemon acts on its own only inside explicit grants and budgets, and it reports what it did with evidence. Anything irreversible or outside a grant waits for the user.
- **Cost.** Long autonomous jobs on frontier models are expensive. Plans (Free/Pro/Power) buy capacity. They never buy privacy, memory or continuity.

## Build order (Proposed: spine first)

1. **Task runtime:** durable accepted requests (Stage 1).
2. **Context portability:** workspace files, source-linked memory, **import from ChatGPT/Claude exports**, and full export.
3. **Router roles:** role → model config with per-role evaluations, retention classes and a qualification pipeline.
4. **Hosted execution:** a sandbox plus coding agents that deliver repositories and artifacts.
5. **Integrations:** email, calendar, and scheduled and recurring tasks on the task runtime.
6. **Media and voice:** re-enabled through bounded, qualified adapters.
7. **Connected devices and computer use.**

## Read next

- [DAEMON_VISION.md](DAEMON_VISION.md): principles V01–V07, design defaults, acceptance criteria AC01–AC12.
- [DAEMON_VISION_DECISIONS.md](DAEMON_VISION_DECISIONS.md): DEC01–DEC12 and superseded contracts.
- [GLOSSARY.md](GLOSSARY.md): required before you name anything.
