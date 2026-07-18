# Hermes relay S2a — neutral vocabulary + translator contract

Plan and grounding: `orchestration/hermes-relay-redesign/plan.md`. Decisions bound here:
`D-runtime-neutral-pane-vocabulary`, `D-relay-raw-frame-transport`, `D-chief-first-cutover`,
`D-child-per-employee`, `D-stock-hermes-only` (no Hermes-side change). Built directly on the
landed S1 foundation (`hermes-relay-s1-foundation`, commit `052a968`).

## Outcome

The relay gains a **neutral downstream mode**: browser clients speak one runtime-neutral,
ACP-shaped event vocabulary, and a Hermes translator inside `src/planner/hermes_backend/`
converts the child's native frames into neutral events and neutral requests into native
RPCs. A tee consumer feeds the existing Panels transcript mirror write-behind for
relay-handled turns. No frontend change, no production enablement change (backend stays
off by default), no consumer swap — S2b does the pane.

## Existing contracts to preserve

- No file under `src/planner/minds/`, `src/planner/chat/`, or `src/planner/runtime/`
  changes, with one narrow exception: the mirror tee consumer may CALL existing
  `planner.chat` write functions to append transcript rows — it may not modify them.
- The S1 components' public behavior is preserved: raw verbatim mode remains available
  (tests and the tee depend on it), the session-lifecycle denylist stays enforced in
  both modes, the pool's one-child-one-session rules are untouched.
- `./verify` stays green; every existing test passes unchanged.

## The neutral vocabulary (locked shape-level; exact typed fields are the plan's)

One typed contracts module in `src/planner/hermes_backend/` defines it. Events
(server → pane): turn started · assistant text delta · thinking delta · tool activity
(started / progress / completed, with tool name and bounded preview) · agent question
(clarify: id, prompt text) · tool approval request (id, summary) · turn completed (final
text) · turn failed (error detail) · session titled · history snapshot (on attach: the
durable session's messages) · child reset · catalog result (the served
commands-and-skills catalog, payload as the runtime returns it). Requests (pane →
server): attach to employee · send message (text, optional image references) · answer
question (id, answer) · respond to approval (id, decision) · interrupt · compact ·
list catalog.

Owner-ruled boundary (`D-only-free-hermes-features`): only what Hermes gives for free.
**In:** compact (maps to `session.compress` — also a native `command.dispatch` builtin);
the catalog listing (`commands.catalog`, one read, payload as-is) — the S2b pane renders
its picker from it, and picking a SKILL inserts the skill's trigger into the composer as
ordinary message text (skills are model-side; no execution machinery); new-conversation
is pool-owned and lands in S2b. **Out (cut as nice-to-have):** set-model / model options
(for the record: `prompt.submit` carries no model field — `server.py:8407`; `/model` is
not dispatchable via `command.dispatch`; the real per-session switch is
`config.set {key:"model", value, session_id}`, `server.py:10211+` — recorded, not
built), and any generic mediated command execution. Passthrough stays reserved for
unknown native frames.

Send-while-running: stock `prompt.submit` on a running session QUEUES the prompt (by
default interrupting the live turn) rather than rejecting (`_handle_busy_submit`,
`server.py:8420-8425`). The vocabulary treats a mid-turn send as legal — no synthetic
busy error; the resulting native event sequence flows through translation as-is. The
legacy 4009 mapping to a distinct failure reason stays as a harmless completeness case.

Rules: every event carries the employee identity; unknown native frame kinds map to a
generic passthrough event (nothing is silently dropped — a new Hermes event kind reaches
the pane as opaque-but-visible rather than vanishing); the vocabulary module imports
nothing Hermes-specific (future Claude/Codex translators implement the same contracts).

## The Hermes translator

- Lives in `src/planner/hermes_backend/`; translates between the native frame stream
  (from S1's relay internals) and the neutral vocabulary. Mappings from the S0 record:
  `message.start`→turn started, `message.delta`→text delta, `thinking.delta`/
  `reasoning.delta`→thinking delta, `tool.*`→tool activity, `clarify.request`→agent
  question, `approval.request`→approval request, `message.complete`→turn completed,
  `error`→turn failed, `session.title`→session titled, child death→child reset.
- Neutral requests map to native RPCs: send message→`image.attach`* + `prompt.submit`,
  answer→`clarify.respond`, approval→`approval.respond`, interrupt→`session.interrupt`,
  compact→`session.compress`, list catalog→`commands.catalog` (payload as-is in the
  typed catalog result).
- *Image references are Panels-managed web-relative references (`/files/...`); the
  server side resolves each to a child-openable absolute filesystem path before
  `image.attach` (the same resolution the legacy path performs pre-gateway). A
  reference that does not resolve is a neutral request error; nothing reaches the
  child. Verbatim forwarding of a web-relative reference is a defect.
- History snapshot on attach comes from the pool child's durable session (the
  `session.resume` messages payload, translated) — never from Panels DB.
- A mid-turn send is legal and translates as-is (stock queues, by default interrupting
  the live turn — `D-native-turn-concurrency`); the legacy 4009 code maps to a distinct
  turn-failed reason as a completeness case. The S1 denylist stays enforced underneath
  the neutral mode exactly as in raw mode.

## The transcript mirror tee consumer

A tee observer (the first product consumer of S1's seam) appends completed relay-handled
turns to the existing Panels chat transcript store write-behind — off the conversation
path, failures logged and never surfaced into the stream. Scope: completed/failed turns'
user text and final assistant text, via existing `planner.chat` write functions
(call-only). No durable row marker: the store has no source column and its CHECK
constraints admit no new origin/mode values without a schema change, which is outside
this ticket. Distinguishing mirrored rows is S4's problem, where transcript ownership
lands (`D-transcript-ownership-open`) — for the Chief the cutover moment itself
separates the eras.

## Acceptance tests (named; all run inside `./verify`; fakes only, no real Hermes)

1. Vocabulary contracts: typed shapes round-trip serialization; module imports nothing
   Hermes-specific (mechanical import-graph assertion).
2. Translation events: each native frame kind from the S0 inventory maps to its neutral
   event; unknown native kinds surface as passthrough; employee identity on every event;
   mixed bursts preserve order.
3. Translation requests: each neutral request produces exactly the mapped native RPC
   sequence (including compact→`session.compress` and list-catalog→`commands.catalog`
   with the payload delivered as-is); a mid-turn send is legal and translates as-is
   (queue-not-reject), with the legacy 4009 completeness case still mapped; an
   unknown/cut request kind (e.g. a set-model or run-command envelope) is rejected with
   a neutral error and never reaches the child, and raw `slash.exec`/`cli.exec` from
   downstream stay denied by the S1 denylist.
4. History: attach yields the durable session's translated messages via the pool child.
5. Mirror tee: completed and failed turns append matching transcript rows write-behind;
   a mirror write failure never perturbs the live stream.
6. S1 revisit (trigger fired — real browser consumers arrive here): bounded downstream
   queues with an explicit overload policy, and a stated (tested or explicitly
   re-accepted with rationale) position on final-frame-vs-death ordering.

## Out of scope

Frontend/pane changes, Playwright, production enablement, ticket-employee cutover, worker
steps, `panels` CLI changes, Hermes-side changes, deleting any legacy chat machinery.
