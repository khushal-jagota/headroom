# Hermes relay S2b — Chief pane cutover implementation plan

Bound by `orchestration/tickets/hermes-relay-s2b-chief-pane/contract.md` (BINDING; adds
nothing it does not ask for). Builds on landed S1 (`052a968`) and on S2a
(`hermes-relay-s2a-neutral-translator`, landing in the working tree under
`src/planner/hermes_backend/`). Decisions bound: `D-chief-first-cutover`,
`D-child-per-employee`, `D-native-turn-concurrency`, `D-only-free-hermes-features`,
`D-relay-raw-frame-transport`, `D-transcript-ownership-open`.

**Implementation must not begin until the orchestrator confirms S2a is integrated and
verified green** (contract). This plan is written against S2a's shipped code.

**The implementer must NEVER run `./verify`** — the parent owns the one canonical verify.
`pytest.skip`/`skipif` is FORBIDDEN repo-wide (verify's preflight skip-scan fails the whole
run); every test runs unconditionally or hard-fails. No real Hermes anywhere — fakes only.

---

## 0. Shape of the change (what S2b adds, minimally)

S2a shipped the entire neutral backend surface the pane speaks: the vocabulary
(`neutral_vocabulary.py`), the translator (`hermes_frame_translation.py`), the per-connection
session (`neutral_downstream_session.py`), the WS route (`relay_neutral_route.py`, registered at
`server.py:369-379`), the bounded-queue policy, and the transcript-mirror tee. S2b does NOT
re-open any of that. S2b adds exactly five things:

1. **Composition flag split + two-owner startup assertion** (`server.py` `_lifespan`,
   `_build_role_gateways`, `composition.py`): when `relay_backend_enabled`, the pool owns the
   Chief and the legacy Chief gateway child is never started; when off, exactly today's wiring.
   A named `_assert_single_chief_owner(...)` helper CALLED from `_lifespan` proves no config
   yields two owners; a test proves both compositions AND that the helper is wired into boot (F3).
2. **Chief session adoption + fresh-binding persistence** (`employee_child_pool.py` +
   `composition.py`): the pool seeds its per-employee stored-session map with the persisted
   `agent_chat_sessions.chat_session_key` so its first spawn `session.resume`s the Chief's durable
   session; a composition-owned persistence callback WRITES the Chief's stored-session binding back
   to `agent_chat_sessions` after the first create AND every rebind, so a restart resumes the
   LATEST session, not a stale/forked one (F5 — **pending main's ruling on scope**, §2.5).
3. **Pool-owned new-conversation** (additive: one new neutral request kind + a pool method +
   session servicing): the pool CLOSES the child's current session and binds a fresh one via its
   own `session.create`, RETURNS the new live+stored ids, and the session bootstraps the RETURNED
   live id directly (not an ambiguous `active_list`) so the pane gets a genuinely EMPTY fresh
   `HistorySnapshotEvent` (F4). The rebind runs through the pool executor so the WS event loop is
   not blocked (F6).
4. **Test-mode relay composition with a stateful fake `SpawnFn`** (`composition.py`, `server.py`,
   a new backend scripted-child module, e2e fixtures): in test mode, gated ONLY on
   `test_mode && relay_backend_enabled` (no extra config flag — F16a), the e2e server composes the
   real pool+relay against a STATEFUL scripted child (stored+live ids + per-session messages that
   survive respawn — F12) whose held turns release on cue, so interrupt / mid-turn / thinking /
   incremental render are genuinely provable (F13). No real Hermes.
5. **The Chief-only neutral pane** (a new `web/src` component with its OWN dedicated composer + a
   pure-TS neutral client module + the two Chief route swaps + a `/api/meta` tri-state signal): a
   live neutral-envelope WS client swapped in at `ChiefOfStaffRoute` and `BoardRoute` ONLY, behind
   a tri-state runtime capability signal that renders NEITHER chat path until meta resolves (F11).
   The pane uses its OWN dedicated composer, NOT `ChatComposer.svelte` (which auto-sends
   skills/commands and carries a disabling busy state — both contract violations, F1);
   `TicketRoute`/`ChatPanel`/`ChatComposer` are untouched.

Everything else the contract enumerates (streaming render, thinking, clarify inline, compact,
skills-only picker, images, interrupt, child-reset re-attach) is pane behavior built on the S2a
events and requests — no further backend surface.

---

## 1. The flag-on / flag-off composition split + the two-owner startup assertion

### 1.1 What runs today (verified)

`create_app._lifespan` (`server.py:169-283`). In production (`elif not config.test_mode:`,
line 197) it ALWAYS builds BOTH role gateways via `_build_role_gateways` (lines 84-109) and
starts them, including the `chief_gateway` (a `SharedGateway`, worker_role
`"panels-chief-of-staff"`, `_start_gateway_if_available(chief_gateway)` at line 232). It wraps
them in `EntityRoutingGateway(shared_gateway, {CHIEF_OF_STAFF_ENTITY_ID: chief_gateway})`
(lines 224-227) and installs it as `app_.state.adapters.gateway` (line 230). Then
`compose_relay_backend_if_enabled` (`composition.py`) builds the pool+relay ONLY when
`config.relay_backend_enabled` (line 38 returns `None` otherwise).

Verified consumers of the two gateways:
- `start_background_loops(config, clock, shared_gateway=shared_gateway)` (`loops.py:144-161`)
  takes the WORKER gateway only — the chief gateway is NOT used by the step loops.
- The chief gateway is reachable ONLY through `EntityRoutingGateway._gateway_for(entity_id)`
  (`shared_gateway.py:77-78`): the sole entity mapped to it is `CHIEF_OF_STAFF_ENTITY_ID`.
  That routing is exercised only by the chat HTTP path (`ChatTurnLifecycle` →
  `gateway_provider()` → `EntityRoutingGateway.run_human_turn`, `service.py:214,265,321,483`).
- `/api/chat/agent_panels_chief_of_staff/*` (generic chat routes) and `/api/messages/chief`
  (`chat/api.py:85-97`) are the HTTP entry points. **`/api/messages/chief` has NO caller in
  `web/src` at all** (verified: no grep hit) — the current Chief pane posts to the generic
  `/api/chat/{entityId}/turns` via `startChatTurn`. The `panels chief` CLI is a different
  surface (ticket reconciliation), not a chat send.

So the chief gateway child exists to serve the Chief chat HTTP path — which the S2b pane
replaces when the flag is on.

### 1.2 The change (minimal, additive)

`SharedGateway.start()` calls `_child_or_spawn()` (`shared_gateway.py:201-202`) — it eagerly
spawns. But `_child_or_spawn` is ALSO called lazily on the first `run_human_turn`/`catalog`/…
(`shared_gateway.py:632-646`). Therefore "never started" is not enough on its own: a stray chat
HTTP hit to the Chief entity would lazily spawn a chief child that resumes the same durable
`chat_session_key` the pool owns — the S0-phase-7 two-owner hazard. The split must make the
Chief entity **unroutable to any live child** while the flag is on, not merely unstarted.

**`_build_role_gateways` gains a `chief_owned_by_pool: bool` parameter** (default `False` — the
flag-off caller passes nothing, exact today's behavior). When `True`:
- It does NOT construct a `chief_gateway` `SharedGateway`. It returns `(worker_gateway, None)`.

**`_lifespan` (the production branch) becomes:**
- `shared_gateway, chief_gateway = _build_role_gateways(..., chief_owned_by_pool=config.relay_backend_enabled)`.
- Build the entity map only when a chief gateway exists:
  `entity_gateways = {CHIEF_OF_STAFF_ENTITY_ID: chief_gateway} if chief_gateway is not None else {}`.
  `chat_gateway = EntityRoutingGateway(shared_gateway, entity_gateways)`.
- `_start_gateway_if_available(chief_gateway)` becomes a no-op when `chief_gateway is None`
  (the helper already guards on `getattr(gateway, "start", None)`; passing `None` is safe — but
  guard the call site with `if chief_gateway is not None` for clarity and to avoid
  `getattr(None, ...)`).

With `chief_gateway is None`, `EntityRoutingGateway._gateway_for(CHIEF)` falls through to the
DEFAULT (worker) gateway. **That is crossover and is forbidden** — so the split must ALSO stop
the Chief entity from reaching the worker gateway. This is the two-owner boundary and is treated
in the startup assertion + the chat-path guard below. (See Collision #1 — the cleanest guard
placement is a genuine tension; the recommendation is stated there and the plan builds on it.)

### 1.3 The two-owner startup assertion — a NAMED helper wired into `_lifespan` (F3)

A single assertion, extracted into a NAMED module-level helper in `server.py`:

    def _assert_single_chief_owner(
        *, relay_backend_enabled: bool, pool: Any, chief_gateway: Any
    ) -> None:
        """Exactly one owner of the Chief's stored session. Raises on any inconsistency."""
        if not (relay_backend_enabled == (pool is not None) == (chief_gateway is None)):
            raise RuntimeError("two-owner hazard: inconsistent Chief ownership composition")

`_lifespan` CALLS `_assert_single_chief_owner(...)` after the pool and gateways are built, before
`yield`, in BOTH the production and (F16) test-mode compose branches — passing the three locally
owned booleans. Because `EntityRoutingGateway._entity_gateways` is private and `minds/` is
UNTOUCHABLE (Collision #2), the assertion reads the COMPOSITION INPUTS (the ground truth for
ownership), not the routing map: `relay_backend_enabled == (pool is not None) == (chief_gateway
is None)`. It fails boot loudly if violated.

The test (§7.3) proves BOTH: (a) the helper itself raises for a hand-built inconsistent triple and
passes for each valid one; AND (b) a `_lifespan` boot with a deliberately inconsistent composition
actually RAISES — proving the helper is genuinely invoked at boot, not merely a standalone bool
check. (F3: the earlier plan only tested the booleans, never that `_lifespan` calls the assertion.)

### 1.4 The chat HTTP path under the flag — ONE central crossover guard (F2)

With the flag on and no chief gateway, EVERY gateway-touching Chief lifecycle op must NOT reach
the worker gateway (crossover, forbidden by `D-child-per-employee`) and must NOT spawn a
contending chief child. A per-endpoint guard on the two send routes is INSUFFICIENT — verified
against `chat/service.py`, the Chief entity captures a gateway in FIVE places:
`start_human_turn` (`service.py:214`), `continue_human_turn` (`service.py:265`),
`pause_active_turn` (`service.py:377`), `_answer_pending_clarification_serially`
(`service.py:425`), and `recover_human_turn` (`service.py:321`).

**The guard is ONE central check in `ChatTurnLifecycle`** (`chat/service.py`). **Round-2 F2 fix:
`ChatTurnLifecycle.__init__` does NOT currently take config (`service.py:163-171`, verified), so
the guard cannot read `app.state.config`.** Inject an explicit ownership predicate instead: add a
ctor param `chief_pool_owned: Callable[[], bool] = lambda: False` (default preserves today's
behavior; `create_app` wires it to `lambda: config.relay_backend_enabled` at
`server.py:294-299`). A private `_reject_if_pool_owned(entity_id)` — for the SEND/continue/pause/
clarify ops — raises a clear `PlannerError` when `chief_pool_owned()` and
`entity_id == CHIEF_OF_STAFF_ENTITY_ID`, invoked at the TOP of each op before
`self._gateway_provider()` is ever called. Ticket (`t_*`) and day chat never hit the branch.

**Plus the stale running-Chief startup recovery.** `_recover_running_human_chat_turns`
(`server.py:139`, called at `server.py:233`) runs BEFORE relay composition (`server.py:257`) and
calls `recover_human_turn` for every running human turn — including a Chief turn left running
across a restart. Under the flag that recovery would capture the worker gateway for the Chief
before the pool even exists. So `recover_human_turn`'s central guard must, for the pool-owned
Chief, SKIP the gateway capture and SETTLE the stale turn (mark it errored/superseded — the pool
now owns the session; the legacy turn cannot be resumed against a live chief child) rather than
resume it. **Round-2 F2 fix: recovery does NOT reuse the raising `_reject_if_pool_owned`** (a raise
cannot itself settle-and-return). Instead `recover_human_turn` checks `chief_pool_owned() and
entity_id == CHIEF_OF_STAFF_ENTITY_ID` at its top and, when true, SETTLES the stale turn (the
existing `settle_chat_turn` path, `service.py:308`, marking it errored/superseded) and returns
`None` — a distinct settle-not-raise branch, not the reject guard. The raising guard is for the
live SEND/continue/pause/clarify ops only.

This is the `src/planner/chat/` change the contract pre-authorizes ("changes only if strictly
required for the flag split, and then minimally"). It is ONE guard method invoked at each of the
six Chief lifecycle entry points — broader than a single endpoint but still a single guard
concept. See Collision #1: whether this guard BREADTH is within the contract's "minimal chat/
change" is **escalated to main** (§10). Tests cover EACH bypass (send, continue, pause, clarify)
and a stale-running-Chief boot (§7.3).

Ticket chat (`t_*`) and day chat are unaffected — they route to the worker/day gateways, never
the chief gateway, in both flag states.

---

## 2. Chief session adoption (the pool reads the persisted durable key at first attach)

### 2.1 Why adoption is needed, and where the key lives (verified)

The Chief's durable session is `agent_chat_sessions.chat_session_key` for entity id
`agent_panels_chief_of_staff` (`CHIEF_OF_STAFF_ENTITY_ID`, `chat/service.py:43`; schema
`db.py:102-107`). It is a Hermes **stored session id** — the same identifier `session.resume
{session_id: key}` accepts (legacy `SharedGateway._resume_or_create` resumes exactly this key,
`shared_gateway.py:668-679`, and `session.create` returns it as `stored_session_id`,
`shared_gateway.py:697`). So it is directly resume-able by the pool's own
`_create_or_resume_session`.

The pool's `_create_or_resume_session` (`employee_child_pool.py:287-313`) checks ONLY its
in-memory `_stored_session_id_by_employee`; on first spawn that map is empty, so it issues
`session.create` (`RELAY_SESSION_SOURCE="panels-relay"`) and gets a BRAND-NEW stored id —
NOT the Chief's durable session. Without adoption the pool-owned Chief would start a fresh empty
history, and the durable transcript would fork. Adoption = seed the map with the persisted key
BEFORE the first spawn so `_create_or_resume_session` takes its `session.resume` branch.

### 2.2 The additive mechanism (belongs in the pool; seeded from composition)

`session.resume` is on the S1 `SESSION_LIFECYCLE_DENYLIST` for DOWNSTREAM connections
(`employee_child_relay.py:45`) but the POOL is the sole legitimate issuer (it already issues
`session.create`/`session.resume` at spawn). Adoption reuses that exact seam — it does not add a
new native RPC, only pre-populates the map.

**Add a small public method to `EmployeeChildPool`:**

    def adopt_stored_session(self, employee_entity_id: str, stored_session_id: str) -> None:
        """Seed the durable stored-session binding for an employee BEFORE its first spawn,
        so the first _create_or_resume_session RESUMES it instead of creating fresh.
        Idempotent; a no-op once the employee already has a bound child (a live binding
        wins — never rebind a running child underneath itself)."""

Semantics (all under `_lock`, no blocking call):
- If `employee_entity_id` already has a live record in `_records`, do nothing (the child is
  already bound; adoption is a spawn-time concern only).
- Else set `self._stored_session_id_by_employee[employee_entity_id] = stored_session_id` only if
  not already present (first-write-wins; a later real `session.create` id must not be clobbered).

This is minimal and additive: it does not change `_create_or_resume_session`, the resume branch,
or any S1 behavior. `_create_or_resume_session` then naturally issues `session.resume
{session_id: <adopted key>}` on the first Chief spawn and returns `resumed or held`
(`employee_child_pool.py:292-300`).

### 2.3 Where the seed is read and applied (composition, not the pool)

The pool must not import `planner.chat` (layering) and must not open its own DB read at
construction. So **composition reads the durable key and calls `adopt_stored_session`.** In
`compose_relay_backend_if_enabled` (`composition.py`), after building the pool and only when the
flag is on, read `agent_chat_sessions.chat_session_key` for `CHIEF_OF_STAFF_ENTITY_ID` from the
DB (`db_path` is already passed in for the tee) and, if non-empty, call
`pool.adopt_stored_session(CHIEF_OF_STAFF_ENTITY_ID, key)`. If the row is absent or the key is
NULL (the Chief has never chatted), adoption is skipped and the first spawn creates fresh — the
correct behavior for a never-used Chief.

The read is a single `SELECT chat_session_key FROM agent_chat_sessions WHERE id = ?` on a
short-lived connection composition opens and closes (mirrors the tee's own-connection
discipline). It is a read of an existing column — NO db.py schema change. Composition already
imports `CHIEF_OF_STAFF_ENTITY_ID` transitively via the pool; import it explicitly from
`planner.chat.service` (call-only, reads a constant — no chat behavior change).

Evidence this belongs in composition, not the pool: the pool is deliberately DB-free and
chat-free (its only `planner.chat` import is the `CHIEF_OF_STAFF_ENTITY_ID` constant,
`employee_child_pool.py:21`); composition is already the DB-aware seam (it constructs the tee
with `db_path`). Putting the read in composition keeps the pool a pure process manager and keeps
the one durable-store read at the one composition site.

### 2.4 Actor identity at spawn (already correct — no change)

The contract says the Chief spawn carries S1's actor identity. `_env_for_employee`
(`employee_child_pool.py:274-285`) already sets `PLAN_ACTOR=chief` and omits `PLAN_TICKET_ID`
for `CHIEF_OF_STAFF_ENTITY_ID` (proven by `test_pool_spawns_chief_child_with_actor_and_no_ticket_id`,
pool test). No change; the plan only adds the durable-key adoption.

### 2.5 Fresh-binding persistence (F5 — pending main's ruling on scope)

**The defect.** Nothing under `hermes_backend/` writes `agent_chat_sessions.chat_session_key`
back. Verified: the pool's initial `session.create` (`employee_child_pool.py:301-313`) and any
`/new` rebind (§3) update ONLY the in-memory `_stored_session_id_by_employee` map; the tee writes
transcript rows, never the session binding. So after the Chief's FIRST pool-created session (a
never-chatted Chief), or after any new-conversation rebind, a server restart re-reads the STALE
persisted key (or NULL) via adoption (§2.3) and resumes the wrong session — the durable transcript
forks. Adoption alone (read-only) does not close this; the write half is missing.

**The recommended design.** A composition-owned persistence callback, symmetric to the adoption
READ that already lives in composition (§2.3):

- Composition constructs a small `persist_chief_session(stored_session_id: str) -> None` closure
  that opens a short-lived connection (mirroring the adoption read's own-connection discipline).
  **Round-2 F5 fix — do NOT emit a raw column `UPDATE`.** It must (a) handle the ABSENT row (the
  `agent_chat_sessions` row is created lazily via `INSERT OR IGNORE` in `resolve_chattable_entity`,
  `chat/service.py:92-94` — so `persist_chief_session` first `INSERT OR IGNORE`s the Chief row,
  then updates), (b) set `updated_at` (the column is NOT NULL / carries timestamp semantics like
  the existing writer `chat/data.py:960`), and (c) append the `chat_session_created` event so the
  frontend event log / resource catalogue stays consistent (the same event
  `bind_human_turn_session` emits, `chat/data.py:966`). The cleanest form is a NEW small call-only
  helper in `chat/data.py` (`record_agent_session_key(conn, entity_id, session_key, now)`) that the
  pool-persistence closure calls — this keeps the timestamp/event semantics inside the chat-owned
  writer rather than duplicating them in composition. It writes the EXISTING column — no `db.py`
  schema change. (Whether ADDING this call-only helper to `chat/data.py` is within the authorized
  `chat/` surface is part of Collision #4's escalation.)
- The callback is INJECTED into the pool (a new optional ctor param
  `on_stored_session_bound: Callable[[str, str], None] | None = None`, keyed by employee id) and
  the pool invokes it — off the lock, before reporting success — at the two moments the Chief's
  stored id changes: (a) the first `session.create` in `_create_or_resume_session`
  (`employee_child_pool.py:301-313`), and (b) every `rebind_fresh_session` (§3.2). It is a no-op
  for non-Chief employees (the callback filters, or composition only wires it for the Chief) and
  a no-op when `None` (unit/gate tests that omit it compose exactly as S1 did).
- The write happens BEFORE new-conversation success is reported to the pane, so a crash between
  create and pane-render still leaves the durable binding correct.

**Test.** After a first-create (or a rebind), recreate the pool / re-run composition against the
SAME file DB; assert adoption now reads the LATEST persisted key and the first spawn `session.
resume`s it (not `session.create`). Uses a migrated temp FILE db (F15 — in-memory is invisible to
composition's separate connection, `db.py:204`).

**Scope note (escalated).** This WRITES a `chat/`-owned table (`agent_chat_sessions`) from
`hermes_backend`/composition. The READ already lives in composition (§2.3); the write is
symmetric. Whether this write is within the contract's authorized `chat/` surface (it does not
touch `chat/` code — it writes a table `chat/` owns) is **one of TWO items escalated to main**
(§10, Collision #4). Written here as the recommended design, marked pending main's ruling on scope.

---

## 3. Pool-owned new-conversation (pane trigger → pool rebind → fresh-history event)

### 3.1 Why it is not a translator-forwarded request (verified)

The shipped neutral vocabulary has EXACTLY seven request kinds (attach, send_message,
answer_question, respond_to_approval, interrupt, compact, list_catalog —
`neutral_vocabulary.py:57-64`) and NO new-conversation kind. New-conversation is
`session.create` bound afresh, which is:
- on the S1 downstream denylist (a downstream translator cannot issue it), and
- a Hermes-free feature the owner placed IN scope but explicitly "pool-owned" (`D-only-free-hermes-features`,
  decisions.md:1507-1510: "new is the pool's own `session.create` (S2b, pool-owned)").

So new-conversation is an EMPLOYEE LIFECYCLE operation the POOL services — not a native frame the
translator forwards. This requires an additive seam. The contract lists new-conversation under
"Backend (small, additive)" and under "Frontend: compact and new-conversation actions," so the
additive vocabulary/pool seam is contract-demanded.

### 3.2 The additive seam (one new neutral request kind + one pool method)

**Vocabulary (additive, `neutral_vocabulary.py`):** add
`NeutralRequestKind.new_conversation = "new_conversation"` and a frozen dataclass
`NewConversationRequest(employee_entity_id: str)`, registered in `_REQUEST_BY_KIND` /
`_KIND_BY_REQUEST`, the `_REQUEST_TYPES` isinstance tuple, and the `NeutralRequest` union. It
round-trips like every other request. It names a lifecycle intent, not a native command, so it
does NOT weaken the denylist.

**Pool (additive, `employee_child_pool.py`):** add
`rebind_fresh_session(employee_entity_id: str, old_live_session_id: str) -> tuple[str, str]`
returning `(new_live_session_id, new_stored_session_id)`:
- **The old LIVE id is supplied by the caller (the session), NOT read from the pool (round-2 F4
  fix).** Verified: `EmployeeChildRecord` carries only `stored_session_id`, not the live id
  (`employee_child_pool.py:52-59`), and `_create_or_resume_session` discards the live id
  (`employee_child_pool.py:300`). The pool therefore has no source for the old live id. But the
  NeutralDownstreamSession ALREADY holds the live id it bootstrapped (`self._session_id`,
  `neutral_downstream_session.py:44,227`) — so the session passes it in. (Adopting the live id into
  `EmployeeChildRecord` was considered and rejected: it would touch S1's spawn/resume path to
  capture and store the live id, widening the change; passing it from the session that already has
  it is smaller and additive.)
- **Per-employee rebind serialization (round-2 F4 fix).** Concurrent `new_conversation` requests
  must not each create a live session. The pool serializes rebind per employee under a small
  per-employee lock (or reuses the `_init_slots` latch pattern): a second concurrent rebind for the
  same employee waits for the first, then observes the already-fresh binding and is a no-op (its
  `old_live_session_id` no longer matches the current live id — it returns the current fresh ids
  without re-closing/re-creating).
- Off the lock, on the child's transport, via the SAME `_transport_request` path
  `_create_or_resume_session` uses:
  1. **CLOSE the old live session first** — `session.close {session_id: old_live_session_id}`.
     Without closing, `active_list` returns `[old, new]` and `_sole_session_id` takes `sessions[0]`
     (`neutral_downstream_session.py:295-303`, verified) = the OLD session. **`session.close` IS on
     `SESSION_LIFECYCLE_DENYLIST` (`employee_child_relay.py:49`) — but the denylist gates only
     DOWNSTREAM-injected frames through `handle_downstream_message`; the pool issues it via
     `_transport_request` → `transport.enqueue_frame` DIRECTLY, never touching the downstream seam
     (verified round-2: `session.close` is a valid RPC the child answers and the pool bypass is
     confirmed).**
  2. `session.create {source: RELAY_SESSION_SOURCE, cols: SESSION_COLS}` — capture BOTH the new
     live `session_id` and the new `stored_session_id` from the result.
- Under `_lock`, replace `_stored_session_id_by_employee[employee_entity_id]` and update the live
  `EmployeeChildRecord.stored_session_id`.
- Invoke the F5 persistence callback with the new stored id (before returning).
- **RETURN `(new_live_session_id, new_stored_session_id)`** so the session bootstraps the returned
  live id directly.

**Pool-issued close/create responses must not fan out to OTHER panes (round-2 F4 note).** The pool
issues these via `_transport_request` (`next_child_request_id` + a `_PoolSessionResponder`), NOT a
downstream `PendingForward`. `deliver_child_frame` fans an int-id response to subscribers ONLY when
it is uncorrelated (no `pending_by_child_request_id` entry —
`employee_child_relay.py:367-411`, verified); a pool RPC response has an int id but no downstream
forward entry, so it WOULD be fanned out as an uncorrelated frame and reach other subscribed panes
as a passthrough row. This is the SAME path S1's spawn-time `session.create`/`session.resume`
already take, so it is a pre-existing property, not one this design introduces — BUT it is newly
reachable at runtime (spawn happens before any pane subscribes; a `/new` rebind happens while panes
are attached). The plan REQUIRES the implementer to confirm the pool RPC id space is excluded from
fan-out (or that the translator drops these ids), and adds a test: a `/new` rebind must NOT deliver
a stray passthrough row to a SECOND subscribed pane. If exclusion needs an S1 relay change (it is
S1 relay behavior), that is a collision to raise — flagged for the implementer, not silently
patched.

### 3.3 The session services it; the pane gets EMPTY fresh history (F4, F6)

**`NeutralDownstreamSession` (additive branch in `handle_neutral_request`):** on a
`NewConversationRequest` the session does NOT call `neutral_request_to_native_frames` (no native
mapping). Instead:

- **(F6) Run the rebind OFF the WS event loop.** `rebind_fresh_session` blocks on a
  `threading.Event` up to the request timeout (`_transport_request`,
  `employee_child_pool.py:331-345`, verified) and is called from the WS receive task
  (`relay_neutral_route.py:89-94`). Calling it inline would block the asyncio loop. So the session
  awaits it through the pool's executor, passing the OLD live id it already holds
  (`self._session_id`): `new_live_id, _new_stored = await loop.run_in_executor(pool.init_executor,
  pool.rebind_fresh_session, employee_entity_id, self._require_session_id())`. (`pool.init_executor`
  exists and is the pool's own bounded executor — confirmed round-2 as appropriate for a blocking
  transport request. If `self._session_id` is None — no prior attach — the session rejects the
  `NewConversationRequest` with a neutral error rather than rebinding.)
- **(F4) Bootstrap the RETURNED live id directly.** Set `self._session_id = new_live_id` from the
  return value — do NOT re-issue `session.active_list` (which could still resolve ambiguously). Then
  issue `session.history {session_id: new_live_id}` → `HistorySnapshotEvent`, which for the
  just-created session carries an EMPTY `messages` tuple.
- **Clear ephemeral turn state** the same way `history_snapshot` does on the client (§4.2): the
  fresh empty snapshot is authoritative; the pane drops any in-flight turn/thinking/tools/pending
  question/approval on receiving it.

**How the session reaches the pool:** the route injects the pool into the session.
`relay_neutral_route.py` already has `pool_provider` in scope (`server.py:377`); pass it into
`NeutralDownstreamSession(..., pool_provider=...)` (a new optional ctor param, default `None`). The
session calls `pool_provider()` for the rebind. When `None`, a `NewConversationRequest` answers
with a neutral `TurnFailedEvent(agent_error, "new-conversation unavailable")` — the same
closed-surface posture the session already uses for unrecognized kinds
(`reject_unrecognized_request_kind`). Unit tests inject a fake pool.

**Pane trigger:** a "New conversation" action emits
`{"neutral":"request","kind":"new_conversation","employee_entity_id":"agent_panels_chief_of_staff"}`.
The pane clears its local turn state on send and rebuilds from the incoming (empty)
`HistorySnapshotEvent`, exactly as on attach.

**F4 test.** After servicing a `NewConversationRequest`: exactly ONE live session remains
(assert the old id was closed on the transport), and the pane's `HistorySnapshotEvent` carries
EMPTY `messages`. **F6 test.** A held rebind RPC (the fake pool blocks on its
`_transport_request`) keeps the writer loop / event loop responsive — another frame drains while
the rebind is in flight.

### 3.4 Denylist stays intact

No downstream connection can send a native `session.create`; the neutral surface still exposes no
request that maps to `session.create` at the child. `new_conversation` is serviced by the POOL
(the legitimate lifecycle owner), never forwarded as a native frame. The S1
`SESSION_LIFECYCLE_DENYLIST` and its tests are untouched. A unit test asserts that servicing a
`NewConversationRequest` injects NO `{relay:"request", frame:{method:"session.create"}}` envelope
through `handle_downstream_message` (the pool's own transport request bypasses the downstream
seam).

---

## 4. The pane's neutral-envelope client (frontend)

### 4.1 The reactivity boundary (verified against the resource-catalogue test)

The neutral pane is a live WS client — NOT a keyed-invalidation resource. The frontend
completeness test (`web/tests/resource-catalogue.test.mjs:59-76`) scans every `.ts`/`.svelte`
file and FAILS if any file other than `resourceCatalogue.ts`/`resources.svelte.ts` calls
`resource(` or imports the cache engine. Therefore the neutral pane MUST NOT register a resource,
must NOT import `resources.svelte`/`resourceCatalogue`, and must NOT read `panelsChat`/
`chat_messages`. History comes only from the attach `HistorySnapshotEvent`. This keeps the
resource catalogue complete (the test passes) because the pane adds no fake resource.

The top-level event WS (`ws.ts`, `/api/events`) keeps its exact meaning — it drives the resource
cache and the top-level connection indicator (`connectionStatus`). The neutral pane runs a
SEPARATE WebSocket to `/api/relay/neutral` with its OWN reconnect+re-attach lifecycle. The two
are independent: the neutral pane does not touch `connectionStatus`, and the event WS does not
carry neutral frames. State this boundary in the component doc comment.

### 4.2 Pure-TS client module (testable in Node, mirrors `ws.ts`)

The frontend unit suite is plain Node (`package.json` `test`: `node tests/*.test.mjs`), NOT
vitest — each test compiles a pure `.ts` module with `typescript` and drives it with injected
fakes (see `ws.ts` ↔ `tests/ws-connection.test.mjs`: a `FakeWebSocket`, fake timers, injected
deps). So the pane's neutral logic MUST live in a **pure TS module** with no Svelte/DOM deps,
exactly like `ws.ts`.

**New file `web/src/lib/neutralPane.ts`** — a framework-free neutral-envelope client:
- `createNeutralPaneClient({ url, employeeEntityId, socketFactory, now, setTimeout,
  clearTimeout, onState })` returns a handle `{ attach(), send(text, imageRefs), answer(requestId,
  answer), respondApproval(requestId, decision, applyToAll), interrupt(), compact(), listCatalog(),
  newConversation(), dispose() }`.
- Holds the **rendered turn state** as a plain object (not Svelte runes): an ordered transcript
  of settled messages (from the last `HistorySnapshotEvent`), the in-flight turn (accumulating
  `AssistantTextDeltaEvent.text`), a thinking buffer (`ThinkingDeltaEvent`), the current tool
  activity (`ToolActivityEvent` by `tool_id`+phase), a pending question (`AgentQuestionEvent`),
  a pending approval (`ToolApprovalRequestEvent`), the last session title, the latest catalog
  payload (parsed from `CatalogResultEvent.payload_json`), and quiet system lines from
  `PassthroughEvent`. It calls `onState(snapshot)` after each fold-in so the Svelte wrapper can
  render.
- **Fold-in law — the EXHAUSTIVE 13-kind reducer** (the unit-tested core; the 13 kinds are
  enumerated against `neutral_vocabulary.py:41-54`). Each neutral event mutates state
  deterministically:
  1. `turn_started` — open a turn (fresh in-flight text buffer).
  2. `assistant_text_delta` — append `text` to the open turn's text (token-by-token). If no turn
     is open (a delta before `turn_started`), open one implicitly.
  3. `thinking_delta` — append to the thinking buffer.
  4. `tool_activity` — upsert by `(tool_id, phase)`.
  5. `agent_question` — set the pending question (`request_id`, `prompt_text`, `choices`). (F9:
     previously omitted.)
  6. `tool_approval_request` — set the pending approval (`request_id`, `summary`). (F9:
     previously omitted.)
  7. `turn_completed` — close the open turn into the settled transcript with `final_text`; CLEAR
     in-flight text + thinking + tool activity + any pending question + any pending approval for
     that turn (round-2 F9: completion clears pending question/approval too — a completed turn
     leaves no live question/approval).
  8. `turn_failed` — close the open turn with a failure line keyed on `reason`; CLEAR the same
     turn state INCLUDING pending question + pending approval. **Idle case (F9):** a `turn_failed`
     with NO open turn (e.g. compact's 4009 arrives when nothing is running) appends a standalone
     failure line and clears any pending question/approval — it must not crash or corrupt state.
  9. `session_titled` — set the title.
  10. `history_snapshot` — REPLACE the settled transcript AND CLEAR ALL EPHEMERAL TURN STATE:
      in-flight text, thinking buffer, tool activity, pending question, pending approval, and any
      optimistic human line (F9 — attach/re-attach/new-conversation all deliver it; a
      settled-only replace would leave stale in-flight text/thinking/tools/question/approval after
      recovery). Authoritative.
  11. `child_reset` — clear the in-flight turn AND pending question + pending approval (round-2 F9),
      and trigger re-attach (§4.3).
  12. `catalog_result` — store the parsed `payload_json` for the picker.
  13. `passthrough` — append a quiet system line.
  Answering a question (`answer`) clears the pending question; responding to an approval
  (`respondApproval`) clears the pending approval — both locally, immediately on send (the
  authoritative snapshot on the next attach reconciles).
- **No defensive wrong-employee filter (F16b).** The relay already routes per-employee (the
  socket only ever receives its own employee's frames — the relay's per-employee subscriber
  invariant); a client-side id filter would DUPLICATE that invariant and could MASK a server
  routing defect. The client does NOT filter on `employee_entity_id`.
- **Optimistic human message on send (F8).** S2a emits NO user-message event (verified:
  `hermes_frame_translation.py` maps assistant-lifecycle frames only). So `send(text, imageRefs)`
  APPENDS an optimistic human transcript entry immediately (with image presentation from
  `imageRefs`), BEFORE the assistant stream arrives. The next authoritative `HistorySnapshotEvent`
  (on attach / re-attach / new-conversation) REPLACES the whole settled transcript, superseding the
  optimistic entry (no duplication). Unit-test both: the optimistic line renders on send, and a
  following `history_snapshot` replaces it.
- **Catalog requested on picker OPEN (F10).** S2a does NO attach-time catalog fetch (verified:
  `neutral_downstream_session.py:104` attach sends only `active_list` + `history`). So the pane
  sends `list_catalog` when the picker OPENS, and renders SKILLS from the resulting
  `catalog_result` payload. `listCatalog()` emits exactly
  `{"neutral":"request","kind":"list_catalog","employee_entity_id":...}`.
- **Request emission**: each pane action serializes the matching neutral request wire object
  (`{"neutral":"request","kind":...,"employee_entity_id":...,...}`) and sends it. `send` carries
  `image_refs`. `newConversation` emits the new kind (§3). The module owns the wire shapes as
  typed constants mirroring `neutral_vocabulary.py` (a small hand-kept mirror — the vocabulary is
  the locked contract; a comment points at the Python source of truth).
- **Composer never disabled** (`D-native-turn-concurrency`): the client exposes no "busy" flag
  that gates sends. A mid-turn `send` just emits `prompt.submit`; the resulting native sequence
  (stock queue/interrupt) flows back as events and folds in. No synthetic busy state anywhere.

### 4.3 Attach / re-attach on WS drop

- On construct/`attach()`: open the socket via `socketFactory(url)`; on open, send
  `AttachToEmployeeRequest`. The server's attach path (S2a) subscribes, bootstraps the live
  session id, and returns a `HistorySnapshotEvent` — the client renders history from it.
- On socket close/error: schedule a reconnect with capped backoff (mirror `ws.ts`
  `INITIAL_RETRY_MS`/`MAX_RETRY_MS`); on reconnect, re-open and re-send `AttachToEmployeeRequest`
  — the fresh `HistorySnapshotEvent` is the recovery mechanism (the S2a/S1 death-recovery model;
  a mid-turn delta lost across the drop is superseded by the re-synced durable transcript).
- On `child_reset` event (child died + respawned server-side): drop the in-flight turn and
  re-attach (re-send attach) to re-sync history — the "child reset → automatic re-attach"
  behavior the contract names. This is distinct from a socket drop but recovers the same way.
- `dispose()` stops reconnects and closes the socket (called from the Svelte `onDestroy`).

### 4.4 The Svelte wrapper component + its DEDICATED composer (F1)

**New file `web/src/components/ChiefNeutralPane.svelte`** — a thin view over `neutralPane.ts`:
it instantiates the client with the real `WebSocket`/`window` deps and a `$state` snapshot updated
in `onState`; renders the transcript (including the optimistic human line, F8), the live streaming
turn, a thinking indicator, and its OWN dedicated composer (below). It does NOT touch
`ChatPanel.svelte` or `ChatComposer.svelte`.

**Why a dedicated composer, NOT `ChatComposer` reuse (F1 — verified).** `ChatComposer.svelte` is
incompatible on two counts the contract forbids:
- **It auto-sends skills AND commands.** `choose(item)` calls `void send(item.name)` for both
  skill and command kinds (`ChatComposer.svelte:268-273`, verified). The contract (line 52-54)
  authorizes ONLY skills inserting their trigger text into the composer — commands are NOT
  runnable, and a skill pick INSERTS (never auto-sends).
- **It has a disabling busy/disabled state.** `busy`/`submitDisabled`/`disabled` gate submission
  (`ChatComposer.svelte:17-18, 261, 375, 422`, verified), violating `D-native-turn-concurrency`
  (composer never disabled).

**The dedicated composer's shape** (lives in the neutral pane's own files, never touching
ChatPanel/ChatComposer/TicketRoute):
- a plain `<textarea>` draft + a **send/interrupt** action (send emits `send_message` with the
  draft + image refs; interrupt emits `interrupt`) — NEVER disabled, no busy state; a mid-turn
  send just emits another `send_message` (§4.2, `D-native-turn-concurrency`);
- a **skills-only picker menu** built from the `catalog_result` payload — it renders ONLY skills
  (not commands, not `/model`); choosing a skill INSERTS its trigger text into the draft (no
  auto-send); opening the menu sends `list_catalog` (F10);
- a **clarify affordance** — when a pending question is set, render the question + choices +
  free-text answer, emitting `answer` (clears the pending question locally, §4.2);
- an **approval affordance** — when a pending approval is set, render approve/deny (+ apply-to-all),
  emitting `respondApproval`;
- **image attach/preview reusing the existing `chatImages` helper** (`web/src/lib/chatImages.js`:
  `createPendingChatImages`, `pendingChatImageFiles`, `removePendingChatImage`,
  `revokePendingChatImages`, `clearSentPendingChatImages` — verified pure module, already consumed
  by ChatComposer) plus `uploadChatImage` (`web/src/lib/api.ts:134`, unchanged) to upload and carry
  the returned reference in `send`'s `image_refs`;
- **compact** and **new-conversation** actions in the composer/header (emit `compact` /
  `newConversation`).

**The picker renders SKILLS ONLY** from the `CatalogResult` payload-as-is (`payload_json` parsed):
the pane parses the native `commands.catalog` shape itself (it does NOT call the Python
`_build_catalog`) and lists only skill entries. Picking a skill inserts its trigger text as
ordinary draft text — no execution machinery, no `/model`, no runnable commands
(`D-only-free-hermes-features`).

### 4.5 Frontend unit tests (the contract's required unit coverage)

**Runner:** `cd web && npm test` (which runs `node tests/*.test.mjs`). The new suite is added to
the `package.json` `test` script chain: `node tests/neutral-pane.test.mjs`. It compiles
`src/lib/neutralPane.ts` with `typescript` (exactly as `tests/ws-connection.test.mjs` compiles
`ws.ts`) and drives it with a `FakeWebSocket` + fake timers + a captured `onState`.

**New file `web/tests/neutral-pane.test.mjs`** — covers the contract's three named areas:
- **attach/re-attach**: `attach()` sends an `attach_to_employee` request on open; feeding a
  `history_snapshot` renders the transcript; a socket close schedules a reconnect and, on the new
  socket's open, re-sends `attach_to_employee`; a second `history_snapshot` REPLACES the
  transcript (recovery). A `child_reset` event triggers a re-attach send and clears the in-flight
  turn.
- **event fold-in**: the EXHAUSTIVE 13-kind reducer (§4.2): `turn_started` + three
  `assistant_text_delta` accumulate token-by-token; `turn_completed` closes with `final_text` and
  clears in-flight state; `thinking_delta` accumulates separately; `tool_activity` upserts by
  tool_id/phase; `agent_question` sets the pending question; `tool_approval_request` sets the
  pending approval; `session_titled` sets the title; `catalog_result` parses the payload;
  `passthrough` appends a quiet system line; `turn_failed` closes the open turn with the mapped
  reason; an IDLE `turn_failed` (no open turn — compact's 4009) appends a standalone failure line
  without corrupting state (F9); `history_snapshot` REPLACES the settled transcript AND clears ALL
  ephemeral state — in-flight text, thinking, tools, pending question, pending approval, optimistic
  human line (F9). Assert the exact rendered snapshot after each.
- **optimistic human message (F8)**: `send("hi", [])` appends a human transcript line
  immediately; a following `history_snapshot` REPLACES it (no duplicate).
- **request emission**: `send("hi", ["ref1"])` emits exactly
  `{"neutral":"request","kind":"send_message","employee_entity_id":"agent_panels_chief_of_staff","text":"hi","image_refs":["ref1"]}`;
  `answer`, `respondApproval` (with `apply_to_all`), `interrupt`, `compact`, `listCatalog`,
  `newConversation` each emit their exact wire object. `listCatalog` fires on picker OPEN (F10).
  Assert a mid-turn `send` emits the same send request with NO busy gating
  (composer-never-disabled). NO wrong-employee filter is applied (F16b).

---

## 5. The `/api/meta` capability signal + the two Chief mount points

### 5.1 The signal — TRI-STATE (F11)

Add ONE additive key to `/api/meta` (`server.py:327-334`): `"relay_chief_enabled":
config.relay_backend_enabled`. Minimal — derives from the one config flag.

But `/api/meta` is fetched in `App.svelte`'s `onMount` (`App.svelte:98`, verified) AFTER child
routes mount. A boolean default of `false` would transiently mount the LEGACY `ChatPanel` on a
flag-ON boot (and a meta failure would leave it there) — the wrong pane. So the capability is
**TRI-STATE**: `"unknown" | "enabled" | "disabled"`.

`web/src/lib/capabilities.ts` exports `relayChief = writable<"unknown"|"enabled"|"disabled"|"error">(
"unknown")`, set to `enabled`/`disabled` only when `App.svelte`'s meta fetch RESOLVES with the key.
**Round-2 F11 fix — a meta FAILURE does NOT resolve to `disabled`** (that would mount the legacy
pane, the exact wrong-pane outcome the tri-state exists to prevent): it resolves to `error`, and
the Chief routes render an ERROR/retry placeholder — NEITHER chat path — with a retry that re-fetches
meta. Only a successful meta with `relay_chief_enabled === false` yields `disabled` (legacy).
`capabilities.ts` is a plain store — no resource cache, so the completeness test is unaffected. The
two Chief routes render: **NEITHER path while `unknown` or `error`** (spinner / error+retry), the
neutral pane only when `enabled`, legacy `ChatPanel` only when `disabled`.

The neutral pane exposes a **readiness marker** (a `data-neutral-ready` attribute) only AFTER its
initial `HistorySnapshotEvent` has rendered; the flag-on Playwright waits for that marker before
asserting (so it never races the pre-history mount).

### 5.2 The two mount points choose (ChiefOfStaffRoute + BoardRoute ONLY)

`ChatPanel.svelte` is shared by THREE routes: `ChiefOfStaffRoute`, `BoardRoute` (both Chief), and
`TicketRoute` (a ticket). Tickets MUST stay on the legacy path (S3 moves them). So:
- **`ChiefOfStaffRoute.svelte`**: on `relayChief === "enabled"` render `ChiefNeutralPane`
  (entityId `agent_panels_chief_of_staff`); on `"disabled"` render exactly today's `ChatPanel`;
  on `"unknown"` render NEITHER (a small placeholder) until meta resolves (F11). The
  `chatGatewayStatus` resource read stays for the `disabled` branch only (guarded so it is not
  opened in the neutral branch — the neutral pane has its own connection meaning and does not
  need `chatGatewayStatus`).
- **`BoardRoute.svelte`**: the right-pane `rightPaneMode === "chief"` block (lines 263-270)
  chooses `ChiefNeutralPane` / `ChatPanel` / placeholder under the same tri-state signal; the
  `else if selectedCard` TicketRoute branch is UNTOUCHED. `chiefChatStatus` is used only by the
  `disabled` branch.
- **`TicketRoute.svelte`**: UNTOUCHED — always `ChatPanel`. Not in the allowlist.

`disabled` → both Chief mounts render exactly today's `ChatPanel` (legacy path); the existing
Chief scenarios pass unchanged. `enabled` → both Chief mounts render the neutral pane over
`/api/relay/neutral`. `unknown` → neither, until meta resolves.

---

## 6. Test-mode relay composition with a fake `SpawnFn` (the e2e seam)

### 6.1 The problem (verified)

In `config.test_mode` the ENTIRE `elif not config.test_mode:` branch is skipped
(`server.py:197`) — today no gateways, no loops, and NO relay pool compose in test mode. The e2e
server is a real `panels serve` subprocess with `PLAN_TEST_MODE=1` (`tests/e2e/conftest.py`),
using the echo gateway (`EchoGatewayAdapter`) on the LEGACY path. The existing Chief e2e
(`test_chief_of_staff.py`) expects `echo: <text>` from the echo gateway. So to drive the REAL
neutral pane against streamed child frames, S2b needs a test-mode path that composes the
pool+relay with a fake `SpawnFn` that emits scripted native frames — WITHOUT real Hermes.

### 6.2 The scripted child — a STATEFUL, NON-BLOCKING session model (F12, F13)

**New backend file `src/planner/hermes_backend/scripted_relay_child.py`** — a
`ScriptedRelayChild(ChildProcess)` + `scripted_relay_spawn(argv, env) -> ChildProcess` `SpawnFn`.
Both `RawFrameChildTransport` and `GatewayChild` spawn via the SAME `SpawnFn`
(`minds/gateway.py:43-54`); `RawFrameChildTransport` reads via `read_stdout`, so a `ChildProcess`
impl is a valid pool spawn seam (proven: the pool tests inject `FakeGateway.spawn`).

**Stateful session model (F12).** Static method→reply scripts cannot serve
history/refresh/new-conversation/reset. The fake keeps a SHARED session store (a module-level or
spawn-closure-shared dict) that SURVIVES child respawn, keyed by stored session id, holding
`{live_id, stored_id, messages: [...]}` per session:
- `session.create` → mint a fresh `(live_id, stored_id)`, EMPTY messages; return both ids.
- `session.resume {session_id}` → look up by stored id; return `resumed` + that session's
  messages (so the adoption e2e asserts prior messages render). If seeded (below), the Chief's
  key resolves to seeded messages.
- `session.active_list` → return the child's currently-live session(s) accurately — after a
  new-conversation close+create, exactly ONE live entry (the new one). This makes the F4 fix
  observable end-to-end.
- `session.history {session_id}` → that session's messages (updated on completed prompts).
- `session.close {session_id}` → mark that session not-live (removes it from `active_list`).
- `commands.catalog` → a small fixed payload: one category + one SKILL (so the skills-only picker
  is deterministic; NO runnable command entry that the pane could mis-render).
- `session.compress` → ACK; on a cue, a `4009` error (to exercise the surfaced-failure path).
- `session.interrupt` / `clarify.respond` / `approval.respond` / `image.attach` → ACK. **The
  `image.attach` ACK must VALIDATE the resolved path (F7):** it asserts the `path` param is a
  real, openable filesystem path, NOT a logical `/files/chats/...` reference — an ACK-anything
  fake would let the images scenario pass FALSELY. (This depends on the F7 ruling, §10.)
- On completed `prompt.submit`, APPEND the human + assistant messages to that session's store so a
  subsequent `session.history` (refresh) returns them.

**Non-blocking held-turn state machine (F13).** `RawFrameChildTransport` has ONE serial writer
calling `ChildProcess.send` (`raw_frame_transport.py:157`, verified) — a blocking `send` would
DEADLOCK `clarify.respond`, and synchronous full-stream completion would leave NO running turn to
interrupt/steer (and a partial-prefix assertion could pass AFTER the full string already arrived).
So `send` NEVER blocks: it parses the frame, enqueues the ACK + any immediate frames, and for a
prompt it opens a HELD turn whose stream is released on a cue:
- On `prompt.submit {text}`: ACK, emit `message.start`, a `thinking.delta` (so the thinking
  indicator shows), then stream `message.delta` frames tokenizing an echo of `text` ONE TOKEN AT
  A TIME, HELD OPEN between tokens, then `message.complete {text, status:"complete"}`. Emit
  `session.title` once. **Round-2 F13 — the release-gate control mechanism (cross-process,
  specified).** The e2e server is a separate subprocess, so the release cue cannot be an in-process
  Python handle. The scripted child releases each held beat when the NEXT frame it is waiting for
  arrives on its OWN stdin — i.e. the stream is DRIVEN BY THE PANE's own subsequent requests, and
  for the pure-timing beats the child uses a SMALL fixed inter-token delay from an isolated config
  constant (a scripted-child tunable, not `time.sleep` in the transport writer — the child's own
  reader thread paces its output queue). Playwright asserts intermediate state via a pre-installed
  `MutationObserver` capturing the growing text (so a partial prefix is proven to have existed even
  though the full string later arrives), NOT a bare partial-prefix wait. The child is deterministic:
  its output is a pure function of (input frames, the fixed pacing constant).
- CLARIFY cue (e.g. `text` contains `"ask me"`): emit `clarify.request {request_id, question,
  choices}` and HOLD; complete only AFTER the `clarify.respond` arrives (proving inline answer
  releases the held turn; the non-blocking model means `clarify.respond` is not deadlocked).
- INTERRUPT: while a turn is held, a `session.interrupt` releases it as `message.complete
  {status:"interrupted"}` → a neutral `TurnFailed(interrupted)` (proving interrupt acts on a
  RUNNING turn, not a finished one).
- RESET cue: emit child death → the relay synthesizes `child_reset`; the next spawn shares the
  session store (respawn survival), so re-attach restores history.

Frames ride the same `read_stdout` thread-safe queue model `FakeGateway` uses. Deterministic and
sleep-free; Playwright waits on DOM (a pre-installed `MutationObserver` or the explicit release
gate proves intermediate text/thinking, F13), not wall-clock.

This child lives in `hermes_backend/` (allowlist-eligible, additive) — NOT under `minds/`
(untouchable) — and TYPE-imports only `ChildProcess`/`JsonDict` from `minds/gateway`. Its only
caller is the test-mode composition path.

### 6.3 The test-mode composition path (NO extra flag — F16a)

`compose_relay_backend_if_enabled` already accepts an injectable `spawn: SpawnFn = spawn_popen`
(`composition.py:34`). The e2e composition selects `scripted_relay_spawn`. **No new config flag.**
The earlier plan's `relay_test_scripted_child` is DROPPED (F16a): `test_mode &&
relay_backend_enabled` ALREADY uniquely selects the fake test composition — no production path
composes the relay in test mode (the whole `elif not config.test_mode:` branch is skipped,
`server.py:197`, verified), so there is nothing to disambiguate. The scripted spawn is gated
purely on `test_mode`.

Wiring:
- In `_lifespan`, when `config.test_mode and config.relay_backend_enabled`, compose the relay
  backend in the test-mode branch, passing `spawn=scripted_relay_spawn`. The production branch
  keeps `spawn=spawn_popen`. In test mode we build ONLY the pool+relay+neutral route (and call
  `_assert_single_chief_owner`, §1.3) — NOT the role gateways or loops.
- The neutral WS route (`server.py:369-379`) is already registered unconditionally and gates on
  `pool_provider()`/`relay` being non-None. With the scripted-child pool composed, it is live.

`config.py` gains NO new field. (F16a removes the earlier `relay_test_scripted_child` /
`PLAN_RELAY_TEST_SCRIPTED_CHILD` addition entirely.)

### 6.4 Flag-off e2e is unchanged

The existing Chief e2e (`test_chief_of_staff.py`) runs with `relay_backend_enabled` UNSET (default
`server` fixture) → the legacy echo path → `echo: <text>` still asserts, unmodified. The flag-on
e2e uses a NEW server fixture that sets ONLY `PLAN_RELAY_BACKEND_ENABLED=1` (test_mode is already
on for the e2e subprocess) — no second flag. It ALSO seeds a known Chief `chat_session_key` via
conftest's `seed_db` (the harness DB starts empty, `conftest.py:68-96`, F12) so adoption/history
have a durable session to resume.

---

## 7. Every parity + new scenario mapped to a NAMED Playwright test (both flag states)

The `server_factory` fixture (`tests/e2e/conftest.py`) gains a `relay_chief: bool = False`
parameter that, when true, sets ONLY `PLAN_RELAY_BACKEND_ENABLED=1` in the subprocess env (F16a —
no second flag) AND seeds the Chief `chat_session_key` via `seed_db` (F12). A `relay_chief_server`
fixture returns `server_factory(relay_chief=True)`. This is the ONLY conftest change and is
additive.

### 7.1 Flag-ON Playwright — new file `tests/e2e/test_chief_neutral_pane.py`

Each test opens `#/chief` (and one opens `#/workspace` to prove the BoardRoute mount) against
`relay_chief_server` and drives the REAL neutral pane against the scripted child.

Re-anchored existing scenarios (contract §3):
- `test_neutral_chief_send_and_render` — fill + send; assert the user line and the streamed
  assistant echo render (`data-chat-msg`).
- `test_neutral_chief_history_on_load` — the scripted child's `session.resume`/`session.history`
  returns seeded messages; on attach the pane renders them from the `HistorySnapshotEvent`
  (proves history from durable session, not Panels DB).
- `test_neutral_chief_refresh_mid_conversation` — send, then `page.reload()`; after re-attach
  the transcript is restored from the fresh `HistorySnapshotEvent`.
- `test_neutral_chief_images` — attach an image via the existing upload flow, send; assert the
  reference is carried AND the scripted child VALIDATES the resolved path (F7 — an ACK-anything
  fake would pass falsely). **This scenario depends on main's F7 ruling** (§10): if S2a forwards
  the managed `/files/chats/...` reference verbatim, `image.attach` gets a non-openable path and
  the scripted validator fails — surfacing the S2a gap rather than masking it.
- `test_neutral_chief_interrupt` — send a HELD long stream, click interrupt; assert the running
  turn closes as interrupted (F13 — interrupt acts on a RUNNING turn) and the composer stays
  enabled (no synthetic busy).

New scenarios (contract §3):
- `test_neutral_chief_streamed_delta_token_by_token` — assert the assistant text APPEARS
  incrementally via a pre-installed `MutationObserver` / the scripted release gate (F13 — a
  partial-prefix wait alone can pass after the full string lands).
- `test_neutral_chief_thinking_indication` — assert the thinking indicator shows during the turn
  (driven by the scripted `thinking.delta`).
- `test_neutral_chief_clarify_answered_inline` — type the clarify cue; assert the inline question +
  choices render; answer inline; assert the HELD turn completes AFTER `clarify.respond` (F13 — no
  deadlock).
- `test_neutral_chief_compact` — click compact; assert the OUTGOING `session.compress`/compact
  request is sent AND the ABSENCE of a failure event (F14 — S2a emits NOTHING on a successful
  compact ACK; do not assert an invented confirmation). **Round-2 F14 — the absence assertion needs
  a BARRIER**: after compact, drive a following observable action (e.g. a `list_catalog` whose
  `catalog_result` renders, or a send that streams) and assert THAT lands with no failure line
  in between — so "absence of failure" is checked AFTER the compact ACK has been processed, not
  before. A SECOND variant drives the scripted 4009 and asserts it surfaces as a `TurnFailed`
  failure line (native rejection not masked).
- `test_neutral_chief_skill_picker_inserts_composer_text` — OPEN the picker (assert it sent
  `list_catalog`, F10), which renders SKILLS ONLY (F1); choose the skill; assert its trigger text
  is INSERTED into the composer draft (no auto-send, no model picker, no runnable command).
- `test_neutral_chief_child_reset_reattach_recovery` — the scripted child emits child death →
  `child_reset`; assert the pane clears the in-flight turn and re-attaches, restoring history.
- `test_neutral_chief_new_conversation` — click new-conversation; assert the pool closes+rebinds
  (scripted `session.close` then `session.create`), exactly ONE live session remains, and the pane
  shows EMPTY history (F4).
- `test_neutral_chief_board_route_mount` — same send/render but opened at `#/workspace` (the
  BoardRoute right-pane chief mount), proving the second mount point uses the neutral pane.
- `test_neutral_chief_composer_never_disabled_mid_turn` — send while a turn is streaming; assert
  the composer stays enabled and the second send is accepted (no busy state,
  `D-native-turn-concurrency`).

### 7.2 Flag-OFF Playwright — existing scenarios pass unchanged

The existing `tests/e2e/test_chief_of_staff.py` tests
(`test_chief_of_staff_route_nav_and_chat`,
`test_workspace_defaults_to_chief_chat_and_ticket_selection_restores`,
`test_workspace_ticket_route_restores_on_load_refresh_and_history`,
`test_legacy_board_route_renders_workspace`) run with NEITHER flag set (default `server`
fixture, echo gateway) and are NOT modified — they prove the flag-off Chief legacy path is
intact (`echo: <text>` still asserted). This is the contract's "Playwright (flag off): existing
Chief scenarios pass unchanged on the legacy path."

### 7.3 Backend composition tests (contract §1, §2 — pytest, fakes only)

New file `tests/unit/test_hermes_backend_chief_composition.py`:
- `test_flag_off_composition_is_todays_wiring` — flag off → a chief gateway IS built + mapped, NO
  pool composed.
- `test_flag_on_composition_pool_owns_chief_no_legacy_child` — flag on → NO chief gateway, pool
  composed, Chief not routable to a dedicated gateway.
- `test_assert_single_chief_owner_helper` — the NAMED `_assert_single_chief_owner` raises for a
  hand-built inconsistent triple and passes for each valid one (F3, unit of the helper).
- `test_lifespan_boot_raises_on_inconsistent_composition` — a `_lifespan` boot with a deliberately
  inconsistent composition actually RAISES, proving the helper is WIRED into boot, not a standalone
  bool (F3).
- `test_chief_session_adoption_seeds_stored_key` — seed the Chief key in a MIGRATED TEMP FILE db
  (F15 — in-memory is invisible to composition's separate connection, `db.py:204`); compose flag-on
  with a fake spawn; assert the first Chief spawn issues `session.resume {session_id: <that key>}`.
  A NULL/absent-key case asserts `session.create`.
- `test_fresh_binding_persisted_and_readopted` (F5 — pending ruling) — after a first-create (or a
  rebind), recreate the pool / re-run composition against the SAME file db; assert adoption reads
  the LATEST persisted key and resumes it.
- `test_flag_on_chief_lifecycle_ops_rejected` (F2) — with the flag on, EACH gateway-touching Chief
  op (`start_human_turn`, `continue_human_turn`, `pause_active_turn`,
  `_answer_pending_clarification`) is rejected by the central guard before capturing a gateway;
  ticket/day entities are unaffected.
- `test_flag_on_stale_running_chief_recovery_is_settled` (F2) — a stale running Chief turn present
  at boot is SETTLED (not resumed) by `recover_human_turn`'s guard before relay composition; no
  gateway is captured for the Chief.

New file `tests/unit/test_hermes_backend_new_conversation.py`:
- `test_new_conversation_request_round_trips` — the additive kind round-trips.
- `test_new_conversation_closes_old_then_creates_and_returns_ids` (F4) — servicing calls
  `pool.rebind_fresh_session`, which issues `session.close {old live id}` THEN `session.create` on
  the child transport, returns the new `(live, stored)` ids, and asserts NO `{relay:"request",
  frame:{method:"session.create"|"session.close"}}` envelope is injected through
  `handle_downstream_message` (denylist untouched — the pool bypasses the downstream seam).
- `test_new_conversation_emits_empty_history_and_one_live_session` (F4) — after rebind exactly ONE
  live session remains and the `HistorySnapshotEvent` has EMPTY `messages`, bootstrapped from the
  RETURNED live id (not `active_list`).
- `test_new_conversation_runs_off_event_loop` (F6) — a held rebind RPC keeps the writer/event loop
  responsive (the rebind runs through `pool.init_executor`, awaited).
- `test_new_conversation_persists_fresh_binding` (F5 — pending ruling) — the rebind invokes the
  persistence callback with the new stored id before success is reported.
- `test_new_conversation_without_pool_answers_neutral_error` — no `pool_provider` → a
  `TurnFailedEvent(agent_error, ...)`, no native frame.

---

## 8. RED-first ordering (unit + Playwright)

Every test is written RED first, then the minimal code until green. Order across the stack:

1. **Vocabulary + new-conversation seam (Python unit):** RED `test_new_conversation_request_round_trips`
   → add the `NewConversationRequest`/kind to `neutral_vocabulary.py` until green. Then RED the
   pool-rebind + session-servicing tests (`test_hermes_backend_new_conversation.py`) → add
   `rebind_fresh_session` + the session branch + `pool_provider` injection until green.
2. **Chief adoption (Python unit):** RED `test_chief_session_adoption_seeds_stored_key` → add
   `adopt_stored_session` + the composition read until green.
3. **Composition split + assertion + guard (Python unit):** RED the
   `test_hermes_backend_chief_composition.py` tests (flag-off/flag-on wiring, the NAMED
   `_assert_single_chief_owner` helper + a boot-raises test proving it is wired, adoption on a
   FILE db, the F2 per-op guard + stale-recovery settle) → add the `_build_role_gateways`
   `chief_owned_by_pool` param, the `_lifespan` split, the named assertion helper CALLED from
   `_lifespan`, and the central `chat/service.py` crossover guard until green.
4. **Test-mode scripted-child composition (Python):** RED a small unit test that composing in
   test mode with `test_mode && relay_backend_enabled` (NO extra flag — F16a) yields a live pool
   whose STATEFUL scripted child answers `session.create`/`resume`/`active_list`/`history`/
   `close`/`prompt.submit` and survives respawn (drives `scripted_relay_child.py` + the test-mode
   compose path) → build the scripted child + the compose path until green.
5. **Frontend neutral client (Node unit):** RED `web/tests/neutral-pane.test.mjs` (attach/re-attach,
   the EXHAUSTIVE 13-kind fold-in incl. optimistic human line + idle turn_failed, request emission
   incl. list_catalog on picker open) → build `web/src/lib/neutralPane.ts` until green.
6. **Frontend capability + mount tests BEFORE the UI (F15):** RED the mount/capability assertions
   (tri-state renders neither path while `unknown`; flag-on mounts the neutral pane; flag-off mounts
   legacy) and the dedicated-composer send test → THEN build `capabilities.ts`, `ChiefNeutralPane.
   svelte` + its dedicated composer, the two route swaps, and the meta signal until green. Keep
   `npm run check` clean and the resource-catalogue completeness test green (the pane registers NO
   resource).
7. **Playwright flag-on (`test_chief_neutral_pane.py`):** RED each scenario → wire end to end
   against the STATEFUL scripted child until green.
8. **Playwright flag-off:** confirm `test_chief_of_staff.py` still passes unchanged.

Note on invocation for the implementer: run the frontend unit suite with `cd web && npm test`
and the type check with `cd web && npm run check`. The implementer NEVER runs `./verify` — the
parent owns the one canonical verify run. No `pytest.skip`/`skipif` anywhere (verify's skip-scan
fails the run); every test runs unconditionally.

---

## 9. Exact file allowlist

The implementer may create/modify EXACTLY these; nothing else.

### Backend — new source (all under `src/planner/hermes_backend/`)
- `src/planner/hermes_backend/scripted_relay_child.py` — the `ScriptedRelayChild(ChildProcess)`
  + `scripted_relay_spawn` `SpawnFn` for test-mode composition (§6).

### Backend — additive edits to existing S1/S2a `hermes_backend` files (public behavior preserved)
- `src/planner/hermes_backend/neutral_vocabulary.py` — ADD `NewConversationRequest` +
  `NeutralRequestKind.new_conversation` and its wire dispatch (additive; every existing shape and
  round-trip unchanged).
- `src/planner/hermes_backend/neutral_downstream_session.py` — ADD the `NewConversationRequest`
  branch + an optional `pool_provider` ctor param (additive; existing request handling unchanged).
- `src/planner/hermes_backend/employee_child_pool.py` — ADD `adopt_stored_session`,
  `rebind_fresh_session` (returns `(live, stored)` ids, CLOSES the old session, invokes the F5
  persistence callback), and an `on_stored_session_bound` ctor param (F5). Additive;
  `_create_or_resume_session` invokes the persistence callback on first create; all S1
  spawn/shutdown paths unchanged.
- `src/planner/hermes_backend/relay_neutral_route.py` — pass `pool_provider` into the
  `NeutralDownstreamSession` construction (additive; the route's accept/gate/writer/receive
  loops unchanged).
- `src/planner/hermes_backend/composition.py` — read the durable Chief key and call
  `adopt_stored_session` when flag-on; construct the F5 `persist_chief_session` callback and inject
  it into the pool; select `scripted_relay_spawn` when `test_mode` (F16a — no flag; the caller
  passes it in test mode). Additive; the S2a tee wiring unchanged.

### Backend — named composition/wiring edits (minimal, justified)
- `src/planner/core/server.py` — `_build_role_gateways` gains `chief_owned_by_pool`; `_lifespan`
  performs the flag split, CALLS the NAMED `_assert_single_chief_owner` helper, and composes the
  relay in the test-mode branch when `test_mode && relay_backend_enabled` with
  `spawn=scripted_relay_spawn` (F16a); `/api/meta` adds `relay_chief_enabled`.
- `src/planner/chat/service.py` — the central `_reject_if_pool_owned` guard invoked at every
  gateway-touching Chief lifecycle op (send, continue, pause, clarify) + the stale-running-Chief
  recovery settle (F2). Keyed on `relay_backend_enabled`. (F15: resolved to `service.py` ONLY — NOT
  `api.py`.) **Guard BREADTH pending main's ruling** (Collision #1). (NO `config.py` change — F16a
  drops the extra flag; `relay_backend_enabled` already exists.)

### Frontend — new files (under `web/src`)
- `web/src/lib/neutralPane.ts` — the pure-TS neutral-envelope client (§4).
- `web/src/lib/capabilities.ts` — the tri-state `relayChief` boot signal store (§5, F11).
- `web/src/components/ChiefNeutralPane.svelte` — the Chief-only neutral pane view + its DEDICATED
  composer (NO ChatComposer reuse — F1) (§4).

### Frontend — edited files
- `web/src/App.svelte` — read `relay_chief_enabled` from `/api/meta` and set the tri-state
  capability store to `enabled`/`disabled` once meta resolves (F11; additive to the existing meta
  fetch).
- `web/src/routes/ChiefOfStaffRoute.svelte` — swap `ChatPanel` → `ChiefNeutralPane` under the
  signal (Chief mount #1).
- `web/src/routes/BoardRoute.svelte` — swap `ChatPanel` → `ChiefNeutralPane` under the signal in
  the `rightPaneMode === "chief"` block ONLY (Chief mount #2).
- **NOT edited:** `web/src/components/ChatPanel.svelte`, `web/src/routes/TicketRoute.svelte`,
  `web/src/lib/resources.svelte.ts`, `web/src/lib/resourceCatalogue.ts` (the pane registers no
  resource).

### Tests — new files
- `tests/unit/test_hermes_backend_chief_composition.py` (§7.3)
- `tests/unit/test_hermes_backend_new_conversation.py` (§7.3)
- a small test-mode-compose unit test (may live in the composition test above, §8 step 4)
- `tests/e2e/test_chief_neutral_pane.py` (§7.1)
- `web/tests/neutral-pane.test.mjs` (§4.5), added to `web/package.json`'s `test` script chain.

### Tests — edited files
- `tests/e2e/conftest.py` — the `relay_chief` `server_factory` param + `relay_chief_server`
  fixture (additive).
- `web/package.json` — add `node tests/neutral-pane.test.mjs` to the `test` script.

### Hard constraints (S2a-inherited, RE-VERIFIED against this plan)
- **No file under `src/planner/minds/` changes** — `scripted_relay_child.py` lives in
  `hermes_backend/` and only TYPE-imports `ChildProcess`/`JsonDict` from `minds/gateway`.
  `shared_gateway.py` is READ-ONLY (the two-owner assertion reads composition inputs, not the
  routing map — see Collision #2).
- **No file under `src/planner/runtime/` changes.**
- **`src/planner/chat/` changes only if strictly required for the flag split, and minimally** —
  one central `_reject_if_pool_owned` guard in `service.py` covering all Chief lifecycle ops +
  the stale-recovery settle (Collision #1, **guard breadth pending main's ruling**). The tee's
  call-only `planner.chat.data` usage from S2a is unchanged.
- **F5 persistence write** — composition WRITES `agent_chat_sessions.chat_session_key` (a
  `chat/`-owned table) after first-create and every rebind. It does NOT touch `chat/` code — it
  writes a table `chat/` owns, symmetric to the adoption READ already in composition. **Pending
  main's ruling on scope** (Collision #4).
- **S1/S2a `hermes_backend` public behavior preserved (additive only)** — no S1 relay/transport/
  tee behavior changes; the pool's one-child-one-session rules and the `SESSION_LIFECYCLE_DENYLIST`
  are untouched (the pool issues `session.close`/`session.create` on its OWN transport, never
  through the downstream seam the denylist gates).
- **No `src/planner/core/db.py` schema change** — adoption READS and F5 persistence WRITES the
  existing `agent_chat_sessions.chat_session_key` column; new-conversation adds no column.
- **No S1/S2a test file modified** (their tests are the regression guard).

---

## 10. Collisions raised (for the orchestrator's ruling)

These are genuine contract-vs-reality tensions surfaced with verified evidence. Do NOT treat the
recommendations as decided — they go to the orchestrator.

### Collision #1 — the flag-on chief chat HTTP path cannot both "still exist for tickets/legacy" AND "never reach a live chief child" without a `chat/` guard

**Evidence.** The Chief chat HTTP path is `ChatTurnLifecycle.start_human_turn` →
`gateway_provider()` → `EntityRoutingGateway.run_human_turn` (`service.py:214,483`;
`shared_gateway.py:108-138`). `EntityRoutingGateway._gateway_for(entity_id)` returns the mapped
gateway or the DEFAULT (worker) gateway (`shared_gateway.py:77-78`). With the flag on, if we do
not build a chief gateway, the Chief entity falls through to the WORKER gateway — a crossover the
`D-child-per-employee` design forbids (two sessions in one process is the exact leak). If instead
we keep building a chief gateway, `SharedGateway._child_or_spawn` spawns lazily on the first HTTP
hit (`shared_gateway.py:632-646`) and resumes the same durable `chat_session_key` the pool now
owns — the S0-phase-7 two-owner hazard. The contract says the path "must still exist for
tickets/legacy but the Chief pane no longer uses it," AND "the chief chat HTTP send must not
reach a live chief child that would contend with the pool." Verified: `/api/messages/chief` and
`/api/chat/agent_panels_chief_of_staff/*` have NO `web/src` caller once the pane is swapped;
tickets use `t_*` entities that route to the worker gateway, never the chief gateway.

**Options.** (a) A minimal guard: when `relay_backend_enabled`, the chat HTTP entry points reject
a send addressed to `CHIEF_OF_STAFF_ENTITY_ID` (the Chief is pool-owned; use the neutral relay).
This is a one-line-ish `chat/` change the contract pre-authorizes ("`chat/` changes only if
strictly required for the flag split, and then minimally"). Ticket/day chat is unaffected. (b)
Route the Chief entity to a NO-OP gateway that raises on any bind — more machinery, same effect,
and it still lives partly in `minds/` (forbidden) or needs a new adapter. (c) Do nothing and rely
on "the pane doesn't call it" — REJECTED: a stray/legacy POST would still spawn a contending
child and corrupt the durable session; the contract explicitly forbids two owners.

**Recommendation: (a).** It is the smallest honest change, is contract-pre-authorized, and keeps
the path existing (it returns a clear error rather than 404) for any legacy caller while
guaranteeing no contending child. The guard reads a config flag the chat layer already has access
to via `app.state.config` (the meta endpoint reads `config.relay_backend_enabled` the same way).
Needs a ruling because it touches `chat/`, and the contract's "must still exist for
tickets/legacy" could be read as "must still FUNCTION for the Chief" — which is impossible
without two owners.

**BREADTH note (F2 — the guard is broader than one endpoint).** Codex correctly showed a
two-endpoint guard is insufficient: the Chief captures a gateway in FIVE `chat/service.py` ops
(`start_human_turn`, `continue_human_turn`, `pause_active_turn`,
`_answer_pending_clarification_serially`, `recover_human_turn`), and `_recover_running_human_chat_turns`
(`server.py:233`) runs BEFORE relay composition (`server.py:257`) so a stale running-Chief turn
would capture the worker gateway at boot. So the guard is ONE central `_reject_if_pool_owned`
invoked at all six entry points, plus a settle-not-resume for the stale-recovery case. This is
consistent (one guard concept) but broader than a single endpoint check — **escalated to main:
is this breadth within the contract's "minimal `chat/` change"?** Recommendation: YES (a narrower
guard leaves real crossover paths open).

### Collision #2 — the two-owner startup assertion wants to read the routing map, which lives in untouchable `minds/`

**Evidence.** The cleanest assertion would observe the installed `EntityRoutingGateway` to prove
the Chief entity is/ isn't routable to a dedicated gateway. But `_entity_gateways` is private in
`shared_gateway.py` (`shared_gateway.py:66-78`), and `src/planner/minds/` is UNTOUCHABLE
(S2a-inherited hard constraint) — adding a public accessor is a `minds/` change.

**Options.** (a) Assert on the COMPOSITION INPUTS in `_lifespan` instead — the three booleans
`config.relay_backend_enabled`, `chief_gateway is None`, and `pool is not None` are all local to
`_lifespan` and fully determine ownership; the assertion is `relay_backend_enabled == (pool is
not None) == (chief_gateway is None)`. No `minds/` change. (b) Add a read-only accessor to
`EntityRoutingGateway` — forbidden (`minds/`). (c) Assert by attempting a routing lookup and
checking identity — still needs a `minds/` accessor or reaches private state.

**Recommendation: (a).** The composition inputs are the ground truth for ownership and are
locally owned; asserting on them is strictly stronger than introspecting the map (it catches a
mis-wired map before it is even built). This is the plan's chosen form (§1.3) and needs no
`minds/` change. Flagged only because the "read the routing map" reading is the more literal one
and the orchestrator may prefer it — which would require widening the allowlist to `minds/`.

### Collision #3 (F7) — RESOLVED in shipped S2a; NOT escalated

**Round-1 raised this as a blocker; the round-2 re-check RETRACTS it.** Verified against shipped
S2a: `NeutralDownstreamSession.handle_neutral_request` already resolves each managed
web-relative image ref to a child-openable ABSOLUTE path via `_resolve_image_refs` BEFORE building
the native `image.attach` plan (`neutral_downstream_session.py:89-92`, comment: "Resolve each
managed web-relative image ref to a child-openable ABSOLUTE path BEFORE building the native plan
... defect #7 / contract lines 75-79"; on any failure the WHOLE send is rejected with a neutral
error). So the translator does NOT forward the `/files/chats/...` reference verbatim — S2a already
handles it. The round-1 reading (that `hermes_frame_translation.py:226` forwards `path: ref`
verbatim) missed the session-level resolution step that runs before translation. **No S2b work, no
S2a fix, no escalation.** The images e2e still asserts the reference is carried and the child opens
a real path (the scripted child validates the path is absolute/openable — a genuine assertion, not
a false-pass), but this is straightforward against the working S2a resolution.

### Collision #4 — persisting the fresh Chief binding writes a `chat/`-owned table (F5 — escalated)

**Evidence.** Nothing under `hermes_backend/` writes `agent_chat_sessions.chat_session_key` back;
the initial pool `session.create` and any `/new` rebind are memory-only (§2.5). So after first use
or `/new`, a restart re-adopts a STALE key and forks the durable session. The F5 fix WRITES
`agent_chat_sessions.chat_session_key` (a `chat/`-owned table) from composition after first-create
and every rebind. It does NOT touch `chat/` code — it writes a table `chat/` owns, symmetric to the
adoption READ already in composition (§2.3). **Ruling needed:** is this write in-scope for S2b (the
necessary other half of "adoption of the persisted chief session key"), or should it live behind a
`chat/`-owned function? Recommendation: keep it in composition (symmetric, one DB seam, no schema
change). Marked pending main's ruling.

---

## 11. Summary of what earns its existence (PRINCIPLES check)

Every added surface maps to an explicit contract clause: the composition split + named assertion
(Ownership handoff / Parity §1); adoption + fresh-binding persistence (Chief employee wiring);
`new_conversation` kind + pool close/rebind (Backend small-additive new-conversation); the stateful
scripted child + test-mode compose (Test-mode composition); `neutralPane.ts` +
`ChiefNeutralPane.svelte` (with its dedicated composer) + the two route swaps + the tri-state meta
signal (Frontend); the unit + Playwright tests (Parity §3-5). Nothing speculative: no model picker
(`D-only-free-hermes-features`), no busy state (`D-native-turn-concurrency`), no runnable commands
in the picker (skills only), no defensive wrong-employee filter (F16b), no extra test config flag
(F16a), no new resource, no db schema change, no `minds/`/`runtime/` change. `chat/` is touched only
for the central crossover guard the flag split strictly requires (breadth escalated, Collision #1);
the F5 persistence write touches a `chat/`-owned table from composition (escalated, Collision #4).
