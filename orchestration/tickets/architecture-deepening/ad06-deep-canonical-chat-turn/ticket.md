# AD06 — Deep canonical Chat turn

## Outcome

Make the surviving server-owned Chat-turn boundary one deep module. A caller supplies one human-turn
request; the module owns human admission, its background execution, session-key ownership, gateway
observations, visible transcript writes, active-turn Pause control, and terminal settlement. The API and
gateway must not coordinate pieces of that lifecycle themselves.

This is an internal concentration ticket. The `/turns` and hosted Chief HTTP contracts, visible Chat state,
message roles and text, activity presentation, image behavior, command behavior, session continuity, and
errors remain materially unchanged. AD05 already deleted alternate ingress. AD06 removes the remaining
shallow coordination inside the sole path.

## Why this exists

The current `start_human_turn` and `_run_human_turn` path is canonical but still shallow:

- the route passes a wide positional parameter list instead of one request to one lifecycle owner;
- `service.py` manually sequences entity resolution, Worker exclusion, managed images, a raw daemon thread,
  session persistence, turn attachment, string-tagged gateway chunks, transcript mutation, and settlement;
- `GatewayAdapter.stream` reports a session key twice—through a callback and a `session` chunk—and the
  callback cannot return the effective key after a first-write race;
- `ChatStreamChunk` is one optional-field bag whose invalid combinations are representable;
- entity session persistence and turn-session attachment are separate transactions, while human admission
  checks Worker ownership outside the transaction that creates the running turn; and
- pause, completion, exhaustion, and failure reach the same durable turn through separate orchestration
  paths whose idempotence is incidental to low-level writers.

The module should expose less and own more. It must not grow a queue, scheduler, recovery journal, new
database status, or generic workflow framework.

## Contract files

Implement against and, where the reviewed plan requires, replace declarations in:

- `src/planner/chat/contracts.py` — human request, durable Chat state, and a closed set of human gateway
  observations;
- `src/planner/core/adapters/base.py` — the one human Chat transport operation;
- `src/planner/chat/service.py` and `src/planner/chat/data.py` — the human-turn owner and its atomic durable
  operations; and
- `src/planner/chat/api.py` — thin HTTP marshalling into the owner; and
- `src/planner/runtime/automatic_employee_step_eligibility.py` — the same complete automatic decision at
  discovery and final claim, including the transient active-Chat exclusion.

The plan must name the one lifecycle owner and its public methods exactly. Prefer a closed typed observation
union over another tag-plus-optional-fields object. Delete `ChatStreamChunk`, the generic `stream` human
method name, duplicate yielded session observations, `_run_human_turn`, and any compatibility aliases once
their callers migrate. Do not preserve the old interface behind forwarding functions.

## Required behavior

### One request and one owner

`ChatTurnRequest` remains the product request value: text, message-or-command mode, and ordered managed-image
references. The owner accepts that value as one argument, validates the domain invariants, records the
visible turn atomically, starts its execution, and returns the same `ChatTurn` response shape. The hosted
Chief endpoint is still only a narrow message shell over this owner.

The owner also handles Pause for the product-visible active turn. That turn may have human or worker origin:
Pause is cross-origin Chat/session control, still settles the visible turn as interrupted, and still does
not change Ticket runtime status or take over Employee delivery/settlement. The owner's public method names
must say this distinction plainly. Reads (`state`, explicit `history`, status, and the command catalogue)
are not lifecycle admission and need not become methods merely for symmetry.

### Atomic admission

For a Ticket, the final Worker-running check and creation of the one running human turn occur under the same
SQLite write lock. A concurrent automatic Employee claim and a human admission cannot both win. The loser
creates no visible message, no running turn, no gateway prompt, and no misleading start event. Day and Chief
admission keep their current resolution/materialization behavior.

An active Panels Chat turn becomes an explicit factor in the one complete
`is_eligible_for_automatic_employee_step` decision. Discovery and final claim both call that same function;
the claim writer does not add a private second check. This deliberately revises AD03's seven-factor lock to
eight factors so the deeper rule stays one rule. Chat completion, error, and Pause retain their current
no-eligibility-wake behavior; SQLite plus the canonical polling timer observe the Ticket after settlement.

Managed-image validation still completes before a visible turn is created. Text-only, text-plus-image, and
image-only requests preserve the exact visible-text/model-text split.

### Causal session ownership

There is one pre-prompt session-binding handshake. When the gateway creates, resumes, recovers, rotates, or
starts `/new`, it presents the candidate durable key to the human-turn owner before prompt delivery. The
owner atomically persists/adopts the effective entity key and attaches that same key to the still-running
turn, then returns the effective key. The gateway must deliver on that returned key; it cannot continue on a
losing candidate while Panels records another winner.

The first-write race emits at most one `chat_session_created` event and the database entity, running turn,
and actual gateway submission agree on the winner. A real later rotation still persists once. Repeated
reports of the same key are idempotent. Exact `/new` still creates one fresh durable session and the next
ordinary message reuses its live handle without resume, recreation, or replay.

Exact `/new` is not an ordinary compare-and-set race. The owner carries a one-use force-fresh intent for its
first binding: that fresh candidate replaces any current entity key, attaches to the running turn, and is the
session the gateway actually uses. Later recovery/rotation reports return to ordinary causal compare-and-set
rules. No other message or command can request force-fresh binding.

### Closed observations and one settlement door

The gateway yields only valid typed observations for display-safe activity, output deltas, and completion.
Session binding is the handshake above, not a second observation. Completion says the final text and whether
its visible role is assistant or system without unrelated empty fields. Unsupported or exhausted transport
output settles as one honest error; it is never silently ignored.

Only the Chat-turn owner interprets these human gateway observations. Activity, partial output, final output, transcript
messages, and events retain their existing values and ordering. Completion, Pause, gateway failure, stream
exhaustion, and a late callback all use one idempotent terminal transition: the first settlement wins,
activity entries are cleared once, the assistant/system message is appended at most once, and a later
terminal cannot overwrite or duplicate it. A paused partial reply remains visible exactly as today.

### Worker boundary remains separate

This ticket does not make Panels Chat rows into Employee context and does not route automatic or revision
Employee prompts through the human-turn owner. `EmployeeStepRunner`, `run_ticket_step`, Worker-context
acknowledgement, Worker Chat projection, and Ticket settlement retain their current ownership. Shared low-
level durable Chat writers may remain where they genuinely serve both projections, but no human session or
prompt abstraction is generalized around the Worker path.

The sole cross-origin operation is `pause_active_turn`: it interrupts and settles the visible Chat turn but
does not settle or release the Ticket. The Employee runner remains responsible for the later Ticket outcome.

AD09 owns the later removal of silent Hermes-history fallback. Do not change state/history merging here.

## RED-first acceptance tests

Add a focused `tests/unit/test_human_chat_turn.py` (or one equivalently exact file chosen in the reviewed
plan) and migrate existing tests to the new public seam. Prove:

1. `/turns` and `/messages/chief` pass one `ChatTurnRequest` to the sole lifecycle owner; static checks find
   no alternate human admission, raw thread spawn, old chunk bag, old gateway `stream`, `_run_human_turn`, or
   compatibility wrapper.
2. The closed observation variants cannot express session-plus-token, activity-plus-completion, or another
   mixed state; fake, offline, routed, real, and Shared gateways implement only the new transport contract.
3. A deterministic Ticket human-admission/Employee-claim race has exactly one winner and the losing side has
   none of its downstream durable or gateway effects.
   The same new active-Chat factor is proven through both discovery and final claim, with no claim-local rule.
4. First-key loss, repeated same-key reports, stale-key recovery, genuine rotation, and command-mode rotation
   leave the entity, turn, and gateway submission on the same effective key with exact event counts.
5. Message, command, activity, delta, completion, busy/offline, empty transport, pause/completion race, and
   late-observation cases preserve the settled `ChatState`, message roles/text, and exactly-once events.
6. Literal `/new`, including a concurrent entity-key change before binding, always persists and uses its
   fresh candidate; `/new` with arguments, next-message live-handle reuse, Worker-running rejection, direct-
   only authorization, unknown entities, and Chief/Day/Ticket routing remain exact.
7. Text-plus-image and image-only delivery preserve ordered managed paths, model cues, visible Markdown,
   rejection-before-admission, retry state, and transcript restoration.
8. Automatic and revision Employee delivery tests prove they do not call the human owner and retain their
   existing session, Worker-context, Chat projection, Ticket settlement, and eligibility-wake behavior.
9. A worker-origin visible turn retains exact Pause behavior: interrupt, `interrupted` Chat settlement,
   partial output preservation, unchanged Ticket status, and no eligibility wake.

Run the complete existing unit and browser image suites, live Chat-state browser suite, affected command
and session tests, and employee separation tests as preservation evidence. Do not edit a preservation test
merely to accommodate a changed visible result.

## Documentation

Update `docs/chat.md` and synchronized `docs/systems.md` / `docs/systems.html` to describe the one deep human
turn, its causal session binding, and the separate Employee path in plain language. Keep the explicit warning
that a Panels transcript row is not worker delivery.

## Explicit exclusions

- No HTTP, JSON, database-schema, frontend, or visual redesign.
- No local prompt queue, retry, timeout policy, new thread registry, recovery daemon, or new status.
- No managed-Markdown or resource-catalogue work from AD07–AD08.
- No silent-history/fallback change from AD09.
- No compatibility alias for the old chunk, gateway method, callback contract, or runner function.

## Plan requirements

The delegated plan must inventory every production and test implementation of `GatewayAdapter`, every
`ChatStreamChunk` constructor/consumer, every direct human writer, the server/API composition seam, session-
key event assertions, image delivery, `/new`, pause, and Employee separation. It must define a bounded
changed-path allowlist, exact typed declarations, atomic transaction boundaries, RED order, focused commands,
and deletion/static guards. Any need for a schema change or visible behavior change returns to the
orchestrator before implementation.
