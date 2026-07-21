# ACP-04 Hermes vertical cutover — implementation plan

## Status and boundary

This ticket is ready to implement. The frozen ACP-04 contract is implementable against the settled ACP-01, ACP-02, and ACP-03 public APIs. There is no contract blocker.

The implementation is a vertical cutover, not a second conversation stack. Production will compose one `ConversationComposition`, expose one `/api/conversation` WebSocket, use one registry, broker, permission owner, and `AcpStepGateway`, and stop starting the legacy shared gateway, routing gateway, pool, relay, and chat tee. The three existing conversation locations will mount the same restrained ACP wrapper; this ticket does not redesign the application or add styling.

The narrow internal seams listed below are required because the settled component APIs do not yet carry enough source identity, completion state, or attach state to satisfy the frozen integration contract. They preserve the frozen wire values and existing compatibility APIs. They do not create new owners.

## Plan-review round 1 dispositions

1. **Ingress/deadlock — accepted.** One guarded callback now carries immutable source identity for valid and rejected ingress. It bounded-enqueues into one per-employee sequencer and returns. Owner calls run outside the drain path and transitions close through explicit barriers/watermarks, so publish-back cannot re-enter a held non-reentrant lock.
2. **Worker-first stream/completion — accepted.** `ensure_stream_ready(...)` is mandatory before a tracked worker prompt, even with no browser. Attach state includes capture-finalizing, and exact error settlement precedes collector teardown and queued-successor start.
3. **Permission provenance — accepted.** Worker/browser origin is captured immutably at prompt/permission admission and copied into the pending record. Worker settlement uses that stored provenance and the exact in-transaction binding/Ticket/turn guard; it never infers origin from a later active-handle lookup.
4. **Frozen/current APIs — accepted.** The invented cursor session/browser ID are removed, CAS returns the non-null complete winner, `RunResult` uses the existing constructor, and `require_existing_session` plus `SharedGatewayBusy` retain their exact behavior.
5. **Hermes/lifecycle — accepted.** Composition derives and validates the sibling `hermes` executable from the resolved Python, uses `src/planner/core/loops.py`, and keeps the publisher/writers alive until all reverse publishers have stopped under one deadline.
6. **Persistent rejection/check ledger — accepted.** Same-binding reset preserves persistent protocol rejection state, focused paths/suite names match the tree, and `./verify` is described only as the program's final authoritative gate.

## Allowed files

Implementation is limited to the contract's conversation/runtime/composition surface and its tests:

- `src/planner/core/db.py`
- `src/planner/core/server.py`
- `src/planner/chat/data.py`
- `src/planner/core/loops.py`
- `src/planner/runtime/acp_step_gateway.py` (new)
- `src/planner/runtime/__init__.py`
- `src/planner/conversation/sqlite_binding_repository.py` (new)
- `src/planner/conversation/hub.py` (new; owns the WebSocket adapter unless a small `websocket.py` extraction is demonstrably clearer)
- `src/planner/conversation/composition.py` (new)
- the settled ACP conversation modules whose public integration seams must be extended: registry, turn broker, permission broker, runtime ports/contracts, and package exports
- the Hermes ACP backend definition/factory wiring only where composition needs an injectable official ACP child definition
- `web/src/lib/acp/*` for the transport/controller behavior required by the frozen contract
- one new restrained production wrapper under `web/src/components/`
- the existing Board, Ticket, and Chief Svelte route components at their current mount points
- focused unit, integration, and e2e tests under `tests/`, `web/tests/`, and existing ACP test support/fixtures

Do not change unrelated domain contracts, dependencies, shared visual tokens/CSS, route hierarchy, documentation, memory files, or generated `web/dist` output in this ticket.

## Required internal seams

These are integration gaps, not optional redesign work.

1. **One source-aware conversation ingress.** Replace production use of the source-erasing conversation callback with one guarded callback for both `SessionNotification` and `ProtocolUpdateRejectedPayload`. While the exact registry record is known, construct an immutable source token containing employee ID, child generation, and runtime-record identity; after releasing the registry employee gate, invoke a callback that bounded-enqueues `(source, payload)` into that employee's sole hub sequencer and returns immediately. Keep the existing/global callback compatible only for settled component tests. Production always binds the guarded form and never reconstructs source identity from mutable registry state.

2. **Tracked normal-turn completion.** Add a broker-owned `deliver_tracked_normal(...)` path returning an internal completion handle keyed by exact runtime identity, client message ID, and prompt epoch. Its terminal result is exactly `complete`, `interrupted`, or `errored`, with the settled prompt response/error as applicable. Allocate it and invoke a synchronous `before_prompt_started` hook before the prompt task can emit; the hook installs both the exact worker collector and immutable worker provenance. Terminal ordering is fixed: consume the prompt and required capture/compaction; settle an exact-epoch protocol rejection or capture failure as `errored`; settle the handle once; tear down its collector/provenance; only then start an owned queued successor. A stale handle cannot affect that successor. Leave the browser `deliver(...)` receipt API unchanged, and never infer completion from idle activity.

3. **Serialized attach-state query.** Add a narrow broker query that returns the exact phase `idle`, `running`, `cancelling`, `capture-finalizing`, `failed`, or `closed`, plus owned queued-normal-turn state for an exact runtime. `running`, `cancelling`, and `capture-finalizing` all require active replay. Add a read-only pending-permission snapshot to the permission broker. Terminal snapshots continue to come from the settled terminal owner. These APIs expose owner state; the hub must not become a second canonical service owner.

4. **One per-employee transition sequencer and pre-binding capture.** All source ingress, load/replay, action publications, and transition markers enter one bounded FIFO with monotonically assigned admission ordinals. The single drain task owns stream mutation and sequence allocation. First binding, replacement, attach/load, prompt, cancel, permission, and new-conversation are two-phase transitions: the drain records an opening barrier/watermark, starts the registry/broker/permission call outside the drain path and without a hub/registry non-reentrant lock, and that call posts a completion marker. Publish-back items may therefore drain before the completion marker; the transition completes only after all admitted items through its closing watermark are processed. Pre-binding items remain ordered entries in this sequencer and are held as bounded per-source capture state only until the binding transition emits reset and admits them to normal publication; capture flush is not a second ordering domain. `new_conversation` opens capture before registry replacement. A backend replay of the same content is a separate ingress; there is no content deduplication.

5. **Immutable permission provenance and settlement guard.** The tracked hook registers immutable worker provenance keyed by exact runtime identity and prompt epoch; browser delivery registers browser provenance in the same broker-owned prompt context. Source-aware permission admission copies `origin: worker | browser`, runtime identity, prompt epoch, and binding/session coordinates into the pending permission record. Settlement never reclassifies origin from current mutable state. While holding permission ownership immediately before `settling`, a worker-origin request enters a synchronous guard that opens a short SQLite connection, starts `BEGIN IMMEDIATE`, and validates the stored durable binding, exact Ticket mirror and `agent_running_step` status, exact current active worker handle/epoch, and active worker chat-turn session. The broker marks that same pending request `settling` before the transaction commits. There is no await and no connection crosses a thread boundary. Missing/stale worker provenance rejects; only an admission stored as browser-origin may use the non-worker bypass.

6. **One-composition late binding.** Registry construction needs source-aware conversation/permission/death callbacks while the broker needs the registry runtime. `ConversationComposition` therefore creates explicit mandatory bind-once callback slots which fail closed until bound, constructs each owner once, then binds the cycle exactly once. There are no duplicate registries/brokers and no optional production callback paths.

## Phase 1 — durable binding repository and migration

### Database migration

Bump the schema version from 23 to 24 and add the canonical table:

```sql
CREATE TABLE conversation_session_bindings (
    employee_id TEXT PRIMARY KEY,
    entity_kind TEXT NOT NULL CHECK (entity_kind IN ('ticket', 'agent')),
    entity_id TEXT NOT NULL,
    acp_session_id TEXT NOT NULL UNIQUE,
    backend_key TEXT NOT NULL,
    binding_generation INTEGER NOT NULL CHECK (binding_generation > 0),
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    UNIQUE (entity_kind, entity_id),
    CHECK (employee_id = entity_id)
)
```

Run `_migrate_conversation_session_bindings(...)` after legacy domain-table migration and before `user_version` is advanced. In one transaction it must:

- classify only an existing Ticket or the exact Chief agent; Days are excluded;
- backfill every non-null Ticket `employee_session_id` and the Chief `chat_session_key` as `backend_key='hermes'`, generation 1;
- retain an already-valid higher generation on restart;
- verify every existing row's entity metadata and exact domain mirror;
- fail migration for a deleted/missing employee, unsupported agent, mirror mismatch, duplicate ACP session, or conflicting backfill instead of silently choosing a row.

Extract a transaction-scoped Chief mirror writer in `src/planner/chat/data.py`; `record_agent_session_key(...)` delegates to it and preserves its canonical product event. Reuse the settled Ticket `write_employee_session_id_in_transaction(...)`, whose current-equals-candidate behavior is intentionally idempotent.

### Repository

Implement a SQLite repository with short-lived connections:

- `resolve(employee_id) -> ConversationSessionBinding | None` validates complete row metadata and the exact Ticket/Chief mirror before returning.
- `compare_and_swap(employee_id, expected, candidate) -> ConversationSessionBinding` uses `BEGIN IMMEDIATE` and always returns the complete non-null winner: the validated actual binding when it differs from `expected`, otherwise the committed candidate. Impossible, missing, or malformed states raise/fail closed rather than returning `None`.
- A first candidate is generation 1; a replacement is exactly current generation + 1. Candidate employee/entity fields must match classification and ACP-04 uses `backend_key='hermes'`.
- Within the same transaction, write the canonical Ticket/Chief mirror first, assert the effective mirror is the candidate session, then insert/update the binding. Commit before returning and reread the committed value.
- A session-uniqueness conflict owned by another employee, an impossible missing actual binding, or any mirror/metadata mismatch fails closed.

The registry publishes a replacement runtime only after this CAS commits. `EmployeeStepRunner.on_session_key` remains a later validation callback, not a binding writer: it runs on the runner's calling thread before the prompt and checks that the Ticket is still `agent_running_step` with the exact current session. The idempotent Ticket mirror writer makes that ordering compatible.

### Phase 1 tests

Add `tests/unit/test_acp_binding_repository.py` covering first insert, stale and successful CAS, generation rules, atomic mirror update, uniqueness conflict, rollback, restart validation, Ticket/Chief backfill, all migration failures, and Day exclusion. Include an ordering test proving committed binding/mirror publication precedes the later runner callback.

## Phase 2 — broker/registry integration seams

Extend the settled owners without changing frozen receipt or update values:

- Wire the one guarded valid-or-rejected conversation ingress into each spawned registry record alongside source-aware permission and child death. The record identity is captured before publication; the callback performs only bounded enqueue and returns after the registry gate is released.
- Add tracked completion allocation/settlement to the broker actor. Normal non-cancel stops settle `complete`; explicit user/worker/shutdown/new-conversation/send-now cancellation settles `interrupted`; child death, exact-epoch protocol rejection, publisher/capture failure, or cancellation timeout settles `errored`. Allocate the handle, exact collector, and immutable provenance before prompt emission. After ordered prompt/capture/compaction settlement, settle once, remove the collector/provenance, and only then release a queued successor. A stale handle can never settle a successor turn.
- Expose the serialized exact-runtime attach phase (`idle | running | cancelling | capture-finalizing | failed | closed`), owned queued-normal-turn state, and permission pending snapshot.
- Copy immutable worker/browser provenance into the pending permission at admission. Invoke the synchronous worker pre-settlement context guard while permission ownership is held, mark that exact pending request `settling` under its `BEGIN IMMEDIATE` validation transaction, then commit without awaiting.

Add focused cases to the settled ACP-01/02 test modules for valid and rejected guarded source identity, hook-before-first-update ordering, each completion outcome, collector teardown before queued-successor start, successor invalidation, all exact attach phases, immutable permission provenance, and a deterministic writer-versus-permission race using latches rather than sleeps.

## Phase 3 — conversation hub and publisher

Implement one `ConversationHub` as both the settled conversation publisher and the `/api/conversation` action coordinator.

### Stream model, sequencer, and publication

- Give each employee one bounded ingress/transition FIFO and one drain task. Each admitted source item or transition marker receives an ordinal; only the drain mutates stream state and allocates envelope sequences. A stream identity is the full employee/session/generation tuple.
- The registry's guarded valid-or-rejected callback captures the immutable source token, performs `put_nowait`, and returns after the registry gate has been released. Full queues fail/close the exact source rather than blocking it. Load/replay, prompt output, cancellation output, protocol rejections, and pre-binding capture all enter this same FIFO.
- A transition never awaits a registry, broker, or permission call from the drain task. The drain opens a barrier and starts that owner call separately; re-entrant publications continue through the FIFO; the owner completion posts a closing marker/watermark; and the transition future completes only after the drain has processed every entry through that watermark. No non-reentrant employee/stream lock is held across an owner call or callback.
- Validate every outbound envelope with the frozen Pydantic contracts and serialize once with aliases and `None` excluded. The outer and nested sequence are allocated once by the drain and are identical.
- Append the serialized envelope to the bounded reset buffer before non-blocking fan-out. Preserve exact envelope and byte limits. If a live turn exceeds the byte limit, mark reset replay unavailable for future active attach; existing subscribers continue receiving live envelopes.
- Each browser has a bounded queue and writer task. Publication never awaits a socket. Queue overflow detaches that browser immediately, schedules permission detachment, and closes it as a slow consumer.
- The hub writes no product chat rows or product event-log rows.

Source-aware registry ingress publishes normal ACP updates and unchanged protocol rejections only for the exact bound source. Valid live compaction notifications also inform the broker's existing observation seam. Replay/capture restore does not count as new compaction observation. An exact-source protocol rejection is retained as persistent projected state until a genuinely new binding; a reset for the same binding must replay/preserve it.

The hub may cache the last visible activity/compaction values for replay, but broker phase/queue, pending permissions, and terminal display state are read from their canonical owners. This cache is a browser projection, not canonical service state.

### Attach, reset, and replay

- **Ensure ready:** `ensure_stream_ready(employee_id, binding)` opens a sequenced barrier and is idempotent for an already-ready exact stream. With zero browsers it still establishes the canonical reset epoch, binds and flushes the exact source, performs typed backend load/replay, publishes deterministic owner snapshots, and publishes ready through the normal buffer/publisher path. The worker gateway must await it before tracked delivery.
- **Idle/failed/closed attach:** resolve the durable binding and call `ensure_stream_ready(...)`; if a reset is required, publish reset, typed backend load/replay, deterministic owner snapshots, then ready.
- **Active attach:** for broker phase `running`, `cancelling`, or `capture-finalizing`, privately enqueue the complete current reset buffer to only the new browser, then publish one global ready. Do not replay active output through global publication.
- **Unavailable active replay:** close only the new browser with a stable retry reason and show a recoverable local controller error. Never send a partial or gapped private buffer.
- **Process restart:** for an uninitialized stream, accept only a same-binding cursor as the reset sequence floor; publish reset above that floor. Once a live stream exists, a cursor beyond its current sequence is future and is rejected.
- **Reconnect:** validate employee/session/generation and either replay contiguous buffered history or perform the appropriate canonical reset. A different binding is rejected.
- **Two browsers:** both observe each global envelope once and in sequence; a new active browser's private replay does not duplicate output for the existing browser.

For a first binding, the sequencer retains already-admitted source entries in bounded source capture until CAS/publication completes, emits reset at sequence 1, binds the source, then releases those same entries in admission order before load/snapshots/ready. For `new_conversation`, open a sequenced barrier and capture watermark, prepare/cancel the broker within its deadline outside the drain, replace the registry binding, update attached subscriptions to the committed successor, emit its sequence-1 reset, release captured entries in order, restore, publish ready, close the watermark, and reopen admission.

### Browser actions

As sequenced two-phase transitions/barriers:

- `prompt`: echo the human message before broker delivery; require the action session to match the attached binding.
- `cancel`, `respond_permission`, and `new_conversation`: delegate to their canonical owners and publish only their settled results.
- Browser action tasks are serialized per socket and cancelled/detached during shutdown.

### Phase 3 tests

Add `tests/unit/test_conversation_hub.py` for exact serialization, one sequence allocation, buffer limits, slow consumer removal, pre-binding ingress ordering, first/reset/restart behavior, every broker attach phase, unavailable replay, reconnect, two-browser fan-out, snapshot ordering, persistent rejection across same-binding reset, source staleness, prompt echo ordering, permission detachment, new-conversation rollover, and shutdown. Include latch-controlled attach/load, prompt, cancel, and protocol-rejection cases where owner calls publish back before returning; each must complete without deadlock and preserve source/admission order. Add a worker-first/no-browser `ensure_stream_ready` case followed by mid-turn attach with a complete reset buffer. Use `asyncio.Event`/latches and in-memory fake sockets; do not use timing sleeps.

## Phase 4 — strict WebSocket adapter

Mount exactly `/api/conversation` and delegate to `app.state.conversation`.

- Accept text JSON only. Reject binary frames, malformed JSON, unknown actions, and invalid frozen action shapes.
- The first action must be `attach`. It establishes exactly one employee per socket.
- The frozen attach fields are `employee_id`, optional `last_seen_binding_generation`, and optional `last_seen_sequence`; the two cursor fields are accepted only together. Resolve the current session from the durable binding, then reject a future sequence or stale/different generation before spawning a child. Do not add a cursor session field.
- Use one receiver and one bounded writer task per socket. Actions run serially in receipt order.
- Keep protocol close reasons stable enough for the ACP transport/controller to distinguish recoverable replay unavailability from terminal validation errors.

Add `tests/unit/test_acp_conversation_websocket.py` for attach-first enforcement, binary/malformed input, cursor validation before spawn, one-employee scope, action ordering, disconnect cleanup, slow-consumer close, and explicit replay-unavailable retry behavior.

## Phase 5 — synchronous `AcpStepGateway`

Implement the `StepGateway` protocol as a synchronous bridge onto the conversation event loop.

- Capture the composition loop and its owner thread ID. Reject gateway calls made from that event-loop thread.
- Preserve `run_ticket_step(..., require_existing_session=True)`. In that mode, before child spawn, load, reset, or prompt, require the supplied stored session to equal the fully validated non-null durable binding; a missing/mismatched binding or load failure must not mint a session. The non-existing-session path may establish the exact durable Hermes binding through the normal CAS, which returns the complete winner.
- Await `hub.ensure_stream_ready(employee_id, binding)` before installing or delivering the first tracked worker prompt. This is mandatory even when no browser has ever attached.
- Run `on_session_key(session_id)` on that original thread before any prompt starts. The event-loop coroutine waits for a bounded acknowledgement; callback failure/timeout aborts without prompting.
- Deliver the worker prompt through `deliver_tracked_normal(...)` with a unique client message ID and install the exact-source/epoch collector plus immutable worker permission provenance in `before_prompt_started`.
- Collect only typed `AgentMessageChunk` text for the worker response. Thought, tool, terminal, permission, plan, and other ACP updates remain UI publications and are not response text. `on_event` is intentionally unused.
- Map tracked completion with the existing dataclass exactly: `RunResult("complete", text, None, session_id, None)`, `RunResult("interrupted", text, None, session_id, None)`, or `RunResult("errored", text, None, session_id, error)`. If broker delivery reports busy, raise `SharedGatewayBusy(session_key=session_id)` rather than returning an errored result.
- Maintain a lock-protected active worker handle map. `interrupt(...)` validates employee, session, binding, and current handle, then submits broker cancellation using the caller's remaining deadline.
- `status(...)` reports only composition/gateway admission and executability; it does not spawn a child.

The worker permission guard consumes the immutable provenance already copied into the pending permission. Under permission ownership and the short SQLite transaction, it proves that provenance still equals the active handle/epoch, durable binding, Ticket status/mirror, and active worker chat-turn session before the pending record becomes `settling`.

Add `tests/unit/test_acp_step_gateway.py` covering strict `require_existing_session` before spawn, callback thread identity, worker-first `ensure_stream_ready`, callback-before-first-update, callback timeout/failure, typed text collection, ignored update types, exact `RunResult` construction, `SharedGatewayBusy`, exact interrupt targeting/deadline, stale successor protection, exact-epoch rejection/capture failure before collector teardown and queued-successor start, status without spawn, and the active-step permission race.

## Phase 6 — one production composition and lifecycle

Create `ConversationComposition` with explicit fields for the hub, registry, broker, permission owner, and step gateway. Construction order is:

1. SQLite repository/employee resolver, with the repository root as the sole workspace root.
2. Hub and fail-closed bind-once callback slots.
3. Permission broker.
4. Hermes ACP backend definition/turn strategy and official SDK child factory.
5. One employee registry.
6. One turn broker.
7. Bind the conversation-ingress/permission/death/runtime callback cycle exactly once.
8. One `AcpStepGateway`.

Production resolves `python = resolve_hermes_python()`, derives `hermes_executable = python.with_name("hermes")`, and validates that it is absolute, executable, and has basename `hermes`. Pass that executable to the settled backend definition so argv is exactly `(hermes_executable, "acp")`. Use `hermes_src_root(python)` only for the Hermes source-root environment, with the planner Hermes home and repository root supplied through their settled fields. Do not compose filesystem or terminal capabilities for Hermes unless the settled backend explicitly declares them.

Expose only `app.state.conversation`. Start this composition before background loops and change `src/planner/core/loops.py` to require/inject its `AcpStepGateway` explicitly into the always-composed `EmployeeStepRunner`; production must not fall through to constructing or defaulting to a legacy shared gateway. Production must not start any legacy `SharedGateway`, entity-routing gateway, pool, relay, or chat tee.

Shutdown uses one absolute deadline and publisher-last dependency order: close external WebSocket/action admission; stop discovery and the runner with the remaining time while worker interrupt remains available; stop the turn broker and reverse services while the hub publisher/browser writers still drain; close registry children; close permission and browser resources; finally stop hub publisher/sequencer/writer tasks. Every stage receives only the deadline's remaining time; no owner gets a fresh timeout.

Provide an explicit `create_app(..., conversation_test_options=...)` injection seam accepted only in test mode. It can supply the scripted official ACP subject/factory, small capacities, deterministic IDs, and audit hooks. Production modules must not import from tests.

Add `tests/unit/test_acp_conversation_composition.py` proving single ownership, bind-once/fail-closed callbacks, startup order, official injected ACP subject, exact app-state exposure, absence of legacy production starts, shared shutdown deadline, and timeout cleanup. Retain existing loop-call compatibility only where old unit fixtures require it; production wiring must assert one gateway.

## Phase 7 — restrained Svelte cutover

Add one production ACP wrapper around the settled `AcpConversationPane`:

- derive `ws:`/`wss:` from the current origin and connect to `/api/conversation`;
- create only client message IDs with `crypto.randomUUID()` through the controller's existing deterministic fallback seam; socket/browser identity stays server-side and no browser ID is added to the frozen attach action;
- instantiate the settled transport/controller with the existing 500 ms retry timing and required timers/date dependencies;
- pass only employee ID and the current route's compact label into the pane.

Mount that wrapper unconditionally at the existing Board Chief conversation location, Ticket conversation rail, and Chief route. Remove legacy chat-resource imports, capability branches, and placeholder chat-status branches made obsolete by those mounts. Preserve current route layout and visual treatment: no new panels, gradients, ornamental cards, token changes, or general ACP redesign.

Update the controller so a higher-sequence reset for the exact same employee/session/generation is a valid replacement base after reconnect even when non-contiguous; stale/equal resets remain ignored and ordinary messages after reset remain contiguous. That same-binding reset preserves persistent protocol-rejection status. A genuinely new binding may clear it. A recoverable sequence gap clears only after a contiguous ready. Extend the transport only enough to expose the stable WebSocket close reason used for replay-unavailable retry.

Add `web/tests/acp-production-mount.test.mjs` and extend the settled `acp-browser-state`, `acp-browser-conformance`, `acp-browser-components`, and `acp-contracts` suites for all three mounts, current-origin URL, client message IDs, 500 ms retry, same-binding reset replacement with persistent rejection retained, stale reset rejection, ready recovery, and replay-unavailable local error. Keep component assertions structural and behavior-based, not pixel/style snapshots.

## Phase 8 — deterministic vertical e2e proof

Add `tests/e2e/test_acp_conversation.py` using the test-mode injected scripted official ACP subject. The fixture may expose an injected audit object/path, but production must communicate with it only through the official ACP child boundary.

The e2e suite must prove:

1. **First binding:** the UI attaches, receives sequence-1 reset/load/ready, sends a prompt through ACP, and the durable binding plus domain mirror agree.
2. **Restart on the same binding:** a fresh server process uses the durable session, accepts the same-binding cursor floor, and resumes monotonically without generation change.
3. **New conversation:** one replacement increments generation, updates the domain mirror atomically, moves both browsers, and rejects stale source updates.
4. **Worker-first stream and mid-turn browser:** with no browser, a worker creates the reset epoch through `ensure_stream_ready` and starts a tracked turn; a browser joining mid-step gets the complete reset-based private replay plus global ready without duplicating output.
5. **Permission race:** only the attached owner may respond, and a worker permission cannot settle after its Ticket/turn/session becomes stale.
6. **Worker prompt path:** the runner callback occurs before the scripted subject receives the prompt; only agent chunks form the returned worker text; interrupt targets the exact active turn; an exact-epoch rejection/capture failure errors completion and tears down its collector before a queued successor starts.
7. **Slow consumer/reset overflow:** a slow browser is detached without blocking publication, and an unavailable active replay produces the explicit recoverable retry result with no partial buffer.
8. **No DB-row shortcut:** the scripted subject audit proves the actual ACP prompt/session was received; inserting product-visible chat/event rows alone never changes its worker context.

Run this against FastAPI/uvicorn plus the Vite proxy/test page without writing `web/dist`. All synchronization uses explicit fixture events or protocol observations, not arbitrary sleeps.

## Acceptance map

| Frozen obligation | Implemented by | Named proof |
| --- | --- | --- |
| One composition, one hub/registry/broker/permission owner/gateway; no legacy runtime | Phase 6 composition and server/loop lifecycle | `test_single_conversation_composition_owns_production_runtime`; `test_production_does_not_start_legacy_gateways` |
| Exact durable table, strict backfill, validation, atomic full-value CAS and mirrors | Phase 1 migration/repository | `test_conversation_binding_migration_backfills_exact_mirrors`; `test_binding_cas_updates_mirror_atomically`; migration failure matrix |
| Existing Ticket/exact Chief resolver, repository-root workspace, binding-selected backend | Phases 1 and 6 | `test_employee_resolver_rejects_day_and_unknown_agent`; `test_composition_uses_repository_root_and_binding_backend` |
| Frozen envelopes, exact sequence allocation, bounded replay/fan-out, reset epoch, persistent rejection | Phase 3 hub | `test_publish_allocates_and_serializes_sequence_once`; `test_restart_reset_raises_same_binding_floor`; `test_same_binding_reset_preserves_protocol_rejection`; buffer/slow-consumer cases |
| Idle, active/cancelling/capture-finalizing, restart, reconnect, worker-first, two-browser, and new-conversation semantics | Phases 3 and 8 | `test_worker_first_stream_replays_complete_buffer_to_midturn_browser`; attach-phase matrix; e2e cases 1–4 |
| Strict attach-first WebSocket and serial actions | Phase 4 adapter | `test_websocket_requires_valid_attach_before_spawn`; `test_websocket_actions_are_serial` |
| Synchronous gateway handshake, existing-session rule, exact collector/completion/interrupt | Phases 2 and 5 | `test_session_callback_runs_on_runner_thread_before_prompt`; `test_require_existing_session_rejects_before_spawn`; `test_tracked_failure_tears_down_before_queued_successor`; completion/interrupt matrix; e2e case 6 |
| Immutable permission origin and stale-worker settlement safety | Phases 2, 3, and 5 | `test_permission_admission_copies_worker_provenance`; `test_worker_permission_settlement_loses_to_ticket_transition`; e2e case 5 |
| Source-aware valid/rejected ingress, re-entrant ordering, death/permission, and pre-binding capture | Phases 2 and 3 | `test_valid_and_rejected_ingress_keep_guarded_source_identity`; `test_owner_publish_back_does_not_deadlock_transition`; `test_updates_during_session_new_flush_after_reset_in_order` |
| Three always-ACP mounts with restrained current UI | Phase 7 | `acp-production-mount.test.mjs` three-mount cases |
| Startup before runner, one app-state handle, shared-deadline shutdown | Phase 6 | composition lifecycle tests |
| Test-mode scripted official ACP subject and no test imports in production | Phases 6 and 8 | `test_testmode_injects_scripted_official_acp_subject`; import-boundary assertion; e2e audit |
| Worker guidance reaches the Hermes/ACP session, never merely product DB rows | Phase 8 | e2e case 8 |

## Focused implementation checks

Run these focused checks after ACP-04 implementation. They are not an ACP-04 completeness gate. `./verify` remains the program's one final authoritative gate after all reviewed program work is integrated.

```sh
.venv/bin/ruff check src/planner/conversation src/planner/runtime/acp_step_gateway.py src/planner/core/db.py src/planner/core/server.py src/planner/chat/data.py tests/unit/test_acp_* tests/unit/test_conversation_hub.py tests/e2e/test_acp_conversation.py
.venv/bin/mypy src/planner/conversation src/planner/runtime/acp_step_gateway.py src/planner/core/server.py src/planner/core/loops.py
.venv/bin/pytest -q tests/unit/test_acp_binding_repository.py tests/unit/test_conversation_hub.py tests/unit/test_acp_conversation_websocket.py tests/unit/test_acp_step_gateway.py tests/unit/test_acp_conversation_composition.py tests/unit/test_employee_step_runner.py
.venv/bin/pytest -q tests/unit/test_acp_employee_registry.py tests/unit/test_conversation_turn_broker.py tests/unit/test_conversation_permission_broker.py tests/unit/test_hermes_acp_backend.py
.venv/bin/pytest -q tests/e2e/test_acp_conversation.py
node --test web/tests/acp-contracts.test.mjs web/tests/acp-browser-state.test.mjs web/tests/acp-browser-conformance.test.mjs web/tests/acp-browser-components.test.mjs web/tests/acp-production-mount.test.mjs
npm --prefix web run check
git diff --check -- src/planner web/src tests web/tests
```

Do not run a production frontend build during ticket implementation. Generated assets are handled during reviewed serial integration; the single full `./verify` is reserved for the program's final authoritative gate, not an intermediate ACP-04 run.
