# `conversation` — the conversation contract

This package is the boundary between Panels' conversation system and the rest of Panels.
It is the contract itself plus a reference implementation of it, so that the new
conversation system and the work that consumes it can be built at the same time against
one agreed shape.

The package is called `conversation` only because `conversation/` is still occupied by
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
- `message_content.py` — the ordered text, image, and file pieces in each message.
- `message_files.py` — the durable bytes that image and file pieces name.
- `file_validation.py` — the accepted document and data formats and their size boundary.

## The boundary

Five operations cross it — start a conversation, send a message into it, interrupt the
running turn, kill its activity outright (stop the turn AND discard the held messages —
what pressing New uses), ask whether it is running — plus one more read: whether a
permission ask is waiting, which exists for the surfaces that tell the owner a
conversation needs them. The conversation id is the identity everywhere; the ACP
session id
of the backend is internal to the conversation system and appears nowhere here.
Permissions are internal too — they have no method, only rules, and those rules are on
the Protocol's docstring.

A message can contain text, images, and attached documents or data files. Panels accepts
PDF, UTF-8 text, Markdown, CSV, TSV, JSON, and JSONL files. One message can carry at most
10 MiB of these files. The server validates every attachment before it stores any file.
It derives each file's media type and byte count rather than trusting browser metadata.
Conversation send routes reject request bodies above 20 MiB before JSON parsing. This
ceiling leaves room for the base64 form of both file and image budgets plus JSON metadata.

The durable message stores a managed file id, the original file name, the canonical media
type, and the byte count. Hermes receives an ACP resource link. Codex and Claude receive
the managed local path as explicit attachment context. Images keep their native backend
routes.

A send may carry a model or reasoning-effort change: from that delivery on, the
conversation runs on the named value. There is no separate set-model operation — the
change rides the message (commit-on-send), a held message applies it when it runs, and a
refused delivery changes nothing. How a backend realizes the change is internal.

Sprint Item supervisors use the same send operation for targeted Worker guidance. The
item-scoped service requires the Ticket's exact current conversation and records the
supervisor agent key as sender. It never creates a Worker conversation. Ticket lifecycle
facts remain outside this send and use their canonical domain actions.

## The real build lives here too

What the contract deferred has since been built, in this same package: the event
record and storage (`events.py`, `storage.py`, one migration), the real system
(`system.py`), the three backend adapters (`backends/`), and the reading side —
events-after-a-position, a live tail, and the backend cards (`api.py`,
`live_tail.py`, `snapshot.py`). The conformance suite runs against the real system
through a process-backed binder in `tests/unit/test_conversation_conformance.py`.
The transcript read and the schema are typed by the real build, not by the
contract — the seam above still deliberately says nothing about them.

## Obligations this seam cannot check

The conformance suite proves what it can observe from outside. Three ruled obligations are
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
  conformance can show the model and reasoning effort were honoured directly — the
  harness reads the backend side's own account of what its session runs on, added with
  the model-change extension. Backend steering support is advertised separately and
  remains off until that adapter proves the shared contract. The role text, the identity environment
  variables, the workspace folder and the access posture have no observation path at this
  seam at all — an implementation that threw the role materials away entirely would still
  pass every conformance test. Honouring them is the real build's obligation, and proving
  it belongs to that build's own tests, close to where the values are applied.

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
