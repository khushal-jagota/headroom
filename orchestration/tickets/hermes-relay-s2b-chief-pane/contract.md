# Hermes relay S2b — Chief pane cutover contract

Plan and grounding: `orchestration/hermes-relay-redesign/plan.md`. Decisions bound here:
`D-chief-first-cutover`, `D-child-per-employee`, `D-native-turn-concurrency`,
`D-only-free-hermes-features`, `D-codex-loop-cap`. Builds on landed S1 (`052a968`) and on
S2a (`hermes-relay-s2a-neutral-translator`) — **implementation must not begin until the
orchestrator confirms S2a is integrated and verified green**; planning and plan review
may proceed against S2a's contract and plan.

## Outcome

The Chief of Staff chat pane speaks the neutral vocabulary over the relay: live streamed
text and thinking, tool activity, clarify questions answered inline, tool approvals if
they occur, interrupt, compact, pool-owned new-conversation, history from the durable
session, and the catalog-driven picker where choosing a skill inserts its trigger as
composer text. Ticket chats are untouched (they move in S3). With the backend config off
(production default) nothing changes; the swap ships dark and turns on with the flag.

## Ownership handoff (the crux)

One stored session has exactly one owning process. When the relay backend is enabled,
server composition MUST NOT start the legacy Chief gateway child; the pool owns the Chief
employee. The pool adopts the Chief's existing durable session (the persisted chief
session binding) — legitimate because the legacy owner no longer runs in that
configuration. With the flag off, composition is exactly today's (legacy chief child,
no pool child for the Chief). No configuration may produce two owners; a startup
assertion enforces it and a test proves both compositions.

## Scope

### Backend (small, additive)

- A pool-owned **new-conversation** neutral request for an employee: the pool ends its
  child's current session binding and binds a fresh session (its own `session.create`),
  then the pane receives a fresh-history event. Employee lifecycle, not a command; the
  session-lifecycle denylist stays intact.
- Chief employee wiring: pool spawn for the Chief carries the actor identity (S1's env
  rules); adoption of the persisted chief session key at first attach.
- Test-mode composition: the e2e server composes the relay backend with the injectable
  fake spawn seam (S1's `SpawnFn`) so Playwright drives the real pane against scripted
  child frames. No real Hermes in any test.

### Frontend

- The Chief route's pane speaks the neutral envelope over the S1 relay WS route: attach
  → history render → live events (text delta, thinking, tool activity, question,
  approval, completed, failed, titled, passthrough rendered as a quiet system line,
  child reset → automatic re-attach).
- Composer: never disabled (`D-native-turn-concurrency`) — a mid-turn send is a native
  queue/steer, not an error; no synthetic busy state.
- Clarify questions render inline with an answer affordance; approvals likewise if they
  arrive.
- Compact and new-conversation actions; the picker rendered from the catalog result
  (payload as served), skills inserting their trigger text into the composer; no model
  picker (`D-only-free-hermes-features`).
- Images: the existing upload flow produces references carried on send (S2a vocabulary).
- Browser↔Panels WS reconnect: on drop, reconnect and re-attach (attach's history
  snapshot is the recovery mechanism); the existing top-level connection indicator keeps
  its meaning.
- History renders from the attach snapshot (the durable session) — never from Panels DB.
  The S2a tee keeps mirroring transcript rows; the pane does not read them.

## Existing contracts to preserve

- Ticket chat paths, worker steps, and all legacy chat machinery stay untouched and
  fully working for tickets; the legacy Chief path remains fully working when the flag
  is off.
- No file under `src/planner/minds/` or `src/planner/runtime/` changes; `src/planner/
  chat/` changes only if strictly required for the flag-off/flag-on composition split,
  and then minimally.
- The S1/S2a hermes_backend public behavior is preserved; additive changes only.
- `./verify` stays green throughout.

## Parity and acceptance (all inside `./verify`)

1. Composition: flag off → exactly today's wiring (legacy chief child, no pool chief);
   flag on → pool-owned Chief, legacy chief child never started; the two-owner startup
   assertion; adoption of the persisted chief session key.
2. New-conversation: pool ends and rebinds the session; pane receives fresh history;
   denylist untouched.
3. Playwright (fake-spawn test composition, flag on): the existing Chief chat scenarios
   re-anchored — send/render, history on load, refresh mid-conversation, images,
   interrupt — plus NEW scenarios: live streamed delta rendering (token-by-token
   appearance), thinking indication, a clarify question answered inline, compact, a
   skill picked from the catalog inserting composer text, child reset → re-attach
   recovery.
4. Playwright (flag off): existing Chief scenarios pass unchanged on the legacy path.
5. Unit: the pane's neutral-envelope client logic (attach/re-attach, event fold-in,
   request emission) under the frontend unit suite.

## Out of scope

Ticket-employee cutover, worker steps, deletion of legacy chat machinery (S4), model
picker, transcript-ownership changes, Hermes-side changes, `panels` CLI changes,
production flag flip.
