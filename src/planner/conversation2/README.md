# `conversation2` — the conversation contract

This package is the boundary between Panels' conversation system and the rest of Panels.
It is the contract itself plus a reference implementation of it, so that the new
conversation system and the work that consumes it can be built at the same time against
one agreed shape.

The package is called `conversation2` only because `conversation/` is still occupied by
the layer being replaced. Renaming it is the job of whoever deletes that layer.

## What is in here

- `contracts.py` — the seam. The types, the three floor defaults, and the
  `ConversationSystem` Protocol. **The docstrings in this file are the contract's
  documentation of record**: every ruled sentence lives on the thing it governs. If the
  contract changes, the docstring changes with it.
- `logic/conversation_start_resolution.py` — the pure rule that applies the floor
  defaults to a start request. It lives with the contract, not inside each
  implementation, so every implementation resolves from one source.
- `in_memory_conversation_system.py` — a deterministic in-memory implementation. It is a
  real implementation of the contract, usable as a stand-in wherever a conversation
  system is needed but a child process is not.

## The boundary

Four operations cross it: start a conversation, send text into it, interrupt it, ask
whether it is running. The conversation id is the identity everywhere; the ACP session id
of the backend is internal to the conversation system and appears nowhere here.
Permissions are internal too — they have no method, only rules, and those rules are on
the Protocol's docstring.

## What is deferred

These are ruled to belong to the real build, and are deliberately absent rather than
sketched:

- **The transcript read** — fetching the events after a position, the live tail, and the
  shape of an event record.
- **The database schema** — how a conversation, its events, and its held messages are
  stored.

## Obligations this seam cannot check

The conformance suite proves what it can observe from outside. Two ruled obligations are
the real build's to keep, because no external observer can see them:

- **The conversation's record is written as step one**, before any other creation work.
  The suite asserts the conversation is fully addressable the instant
  `start_conversation` returns and that nothing has reached a backend yet, which is as
  close as an outside observer can get. It deliberately does not forbid an eager backend
  spawn during creation, because the contract does not forbid one.
- **A failing turn gets an error-log line** of the conversation system's own. That is why
  a turn's failure needs no return channel; it is not a surface the seam exposes, and the
  conformance suite does not reach into an implementation's logging to check it.
- **The start request's values are actually used.** Of everything a start request carries,
  the only value conformance can show was honoured is the backend key, and only indirectly:
  a steer is refused on codex and claude and accepted on hermes. The role text, the identity
  environment variables, the model, the reasoning effort, the workspace folder and the
  access posture have no observation path at this seam at all — an implementation that
  threw the role materials away entirely would pass every conformance test. Honouring them
  is the real build's obligation, and proving it belongs to that build's own tests, close
  to where the values are applied. No observation surface has been invented for them here.

## Two vocabularies, on purpose

The fake records what it saw as `InMemoryConversationObservation`. The conformance suite
has its own `RecordedFact`. They look alike and they are not the same thing:

- `InMemoryConversationObservation` belongs to the fake. It is the fake's test
  observation surface. **It is not the deferred event record shape** and nothing should
  be built on it as though it were.
- `RecordedFact` belongs to the conformance suite. Event record shapes are deferred, so
  the suite needs a vocabulary of its own to assert *that* something was recorded without
  fixing *how* an implementation records it. Each implementation's binder maps its own
  recording onto it.

The duplication is intentional. Collapsing them would make one implementation's internal
shape into the contract that was ruled deferred.
