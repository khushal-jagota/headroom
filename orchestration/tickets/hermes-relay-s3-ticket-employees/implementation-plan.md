# Hermes relay S3 — ticket employees through the child pool implementation plan

Bound by `orchestration/tickets/hermes-relay-s3-ticket-employees/contract.md` (BINDING; adds
nothing it does not ask for). Builds on landed S1 (`052a968`), S2a, and S2b (in the working
tree under `src/planner/hermes_backend/`, `web/src/`, `chat/`). Decisions bound:
`D-child-per-employee`, `D-chief-first-cutover` (its second half — ticket chat and steps move
TOGETHER), `D-native-turn-concurrency`, `D-only-free-hermes-features`, `D-codex-loop-cap`,
`D-stock-hermes-only`, `D-relay-raw-frame-transport`, plus the runtime decisions this ticket
consumes (`D-automatic-employee-step-eligibility`, `D-runtime-ownership-boundaries`,
`D-runtime-restart-continuation`, `D-gateway-topology`).

**Implementation must not begin until the orchestrator confirms S2b is integrated and verified
green** (contract lines 7-9). This plan is written against S2b's shipped code as of the resume
ground truth + the round-1 Codex fixes (`S2B-OWN-001`..`S2B-ROUTE-001`). Where an assumption
could shift with S2b's confirming round, it is flagged inline as **[S2b-dependent]**.

**The implementer must NEVER run `./verify`** — the parent owns the one canonical verify.
`pytest.skip`/`skipif` is FORBIDDEN repo-wide (verify's preflight skip-scan fails the whole
run); every test runs unconditionally or hard-fails. No real Hermes anywhere — fakes only.
Before any edit round, `pgrep -f scripts/verify.py`; if a verify is running, wait.

---

## 0. Shape of the change (what S3 adds, minimally)

S2b shipped: the pool with adoption + fresh-binding persistence (fail-closed inside the spawn
guard, `S2B-OWN-001`), the `on_stored_session_bound` callback, `rebind_fresh_session`, the
neutral vocabulary + downstream session + WS route, the stateful scripted child, the neutral
pane client, the tri-state meta signal, and the central Chief crossover guard in
`chat/service.py`. S3 generalizes the Chief patterns to **every active ticket employee** and
routes **worker steps** through the pool. It adds exactly six things:

1. **A pool-backed step submitter** (`PoolStepGateway`, new file under `hermes_backend/`) that
   satisfies the EXACT `run_ticket_step(...) -> RunResult` + `SharedGatewayBusy` +
   `interrupt(...)` + `status()` contract the `EmployeeStepRunner` already calls, but submits
   the prompt through the ticket's pool child and settles by OWNING the terminal from the
   `prompt.submit` ACK disposition (§1 — the naive "watch the next terminal" design was unsound;
   the settlement approach is the central fork Collision #A). The runner's body is UNTOUCHED — it
   already speaks that interface, so S3 swaps
   only what `loops.py` injects as the runner's `gateway` when the flag is on (§3).

2. **Per-ticket adoption + fail-closed persistence generalized** (composition + a new call-only
   ticket-session writer): the pool adopts each ticket's persisted `employee_session_id` on
   demand and persists fresh bindings through the existing ticket-session ownership writers,
   fail-closed (§4). S2b's Chief-only `persist_chief_session` / `_read_chief_session_key` become
   per-employee dispatchers that route Chief → `agent_chat_sessions`, ticket → the ticket
   ownership writer.

3. **The composition flag split extended to ticket entities** (`server.py` `_lifespan`,
   `loops.py`): flag-on never starts the legacy worker gateway; the pool owns every ticket
   employee AND the Chief; the two-owner startup assertion extends from "Chief" to "every
   pool-owned employee vs the legacy worker gateway"; startup recovery settles-not-resumes stale
   running ticket steps when pool-owned (§5).

4. **The central crossover guard extended to ticket entities** (`chat/service.py`): S2b's guard
   fires only for `CHIEF_OF_STAFF_ENTITY_ID`; S3 widens the predicate so a pool-owned ticket
   entity's human chat also cannot reach the legacy worker gateway (§5.3). Ticket human chat
   flag-on speaks the neutral relay (the pane cutover, §7), so the legacy ticket chat HTTP path
   must be closed to pool-owned tickets exactly as the Chief's was.

5. **`panels` CLI worker identity reads `PLAN_TICKET_ID`** (`cli/main.py`, `tickets/api.py`,
   `tickets/data.py`): `panels worker my-ticket` resolves from `PLAN_TICKET_ID` in the child's
   spawn env; the `HERMES_UI_SESSION_ID`/`HERMES_SESSION_KEY` reads are retired from the worker
   resolution path; a new by-ticket-id resolution endpoint with the existing ownership
   validation (§6). **A contract-vs-reality COLLISION on the flag-off legacy path is raised here
   (§6.1, Collision #1) — the legacy shared worker child does NOT carry a usable `PLAN_TICKET_ID`.**

6. **The ticket route's chat pane cuts over to the neutral client for pool-owned tickets**
   (`web/src/`): flag-on the neutral pane; legacy flag-off; step-generated turns and approval
   re-prompts stream live into the transcript (§7).

Everything else the contract enumerates (live streaming into a subscribed pane, settlement on
completed and on failed, revision guidance, a mid-turn human send following native queue
semantics, eligibility unchanged) is behavior built on these six + the S1/S2a/S2b surfaces — no
further backend surface.

---

## 1. The step-settlement design — THE CRUX (contract §"Worker steps through the pool")

> **REVISED after Codex plan review (Finding 1, Finding 2, Finding 7, Finding 9).** The first
> draft proposed blocking the runner thread on a per-employee turn observer fed by teed frames,
> justified by an "at-most-one-in-flight-turn per employee" invariant. **That invariant is
> false and the design is unsound** (proof below). This section now states the real constraint
> and presents the settlement design as an OPEN ARCHITECTURAL FORK for main to rule
> (Collision #A). The rest of the plan (§2-§8) is written to work under EITHER branch of that
> fork; the branch changes only §1's internals, not the runner interface or the composition.

### 1.1 What the contract asks

`EmployeeStepRunner` "submits the step prompt as a plain send through the ticket's pool child
and observes settlement from teed lifecycle events (its submitted turn's completed/failed), then
settles Panels state exactly as today." The runner blocks its worker thread on
`self._gateway.run_ticket_step(session_key, entity_id, prompt, on_event, on_session_key, *,
require_existing_session) -> RunResult` and expects `RunResult(status, text, usage, session_key,
error)` with `status in {complete, interrupted, errored}`, streamed events via `on_event(dict)`,
the resolved key via `on_session_key(str)` BEFORE submit, and `SharedGatewayBusy(session_key=...)`
on a busy session (`shared_gateway.py:297-330`, `contracts.py:13-23`). It also calls
`self._gateway.interrupt(session_key, ticket_id, deadline=...)` at shutdown
(`employee_step_runner.py:307-318`) and `self._gateway.status().available`
(`employee_step_runner.py:364-368`). S3 swaps only WHAT `run_ticket_step` runs on — a
`PoolStepGateway` (new, `hermes_backend/pool_step_gateway.py`) that presents this exact interface
and TYPE-imports `RunResult`/`OnEvent`/`SharedGatewayBusy` from `minds/`. The runner body is
untouched.

### 1.2 Why "watch the next terminal on the child" is unsound (Codex Finding 1 — verified, CONFIRMED)

Two verified facts break the naive teed-settlement:

1. **The eligibility active-Chat factor does NOT prove single-turn-per-employee on the relay.** It
   rejects a ticket with a running Panels `chat_turns` row (`automatic_employee_step_eligibility.py:116-121`).
   But a neutral-pane HUMAN send translates directly to native frames and injects `prompt.submit`
   into the relay WITHOUT creating a `chat_turns` row (`neutral_downstream_session.py:86-113` —
   the relay path does not write Panels chat rows; that mirror is a write-behind tee, not an
   admission gate). So an automatic step CAN be claimed while a human Hermes turn is already
   running on the same child. (This is the direct consequence of `D-native-turn-concurrency` +
   the relay's demux-free path: Panels no longer sees "a turn is running" for a relay human send.)

2. **The child's terminals are idless and un-owned on the relay path.** `prompt.submit` returns an
   ACK whose `status` is a DISPOSITION — `streaming` / `queued` / `steered`
   (`sessions/service.py:556-560`). The later `message.complete`/`error` are idless event
   notifications fanned out per-employee (`employee_child_relay.py:367-411`). If the step's ACK is
   `queued` (a human turn was live), the NEXT `message.complete` belongs to the INTERRUPTED
   PREDECESSOR, not the step — a naive observer would wrongly settle the step and orphan its
   queued execution. This is EXACTLY the hazard `D-owned-turn-identity` documents
   (`decisions.md:513-525`): "a `message.complete` identified only by live session id cannot
   safely be treated as the completion of whichever caller is currently draining that session."

The legacy `SharedGateway` avoids this precisely because it does NOT watch the next terminal: it
registers an OWNED consequence before submit, applies the ACK disposition
(`steered` → immediate errored return with NO terminal claimed, `queued` → owns only the next
execution after the predecessor's terminal), and drains ONLY its owned consequence's observations
(`shared_gateway.py:819-900`; the owned-consequence routing is `sessions/service.py:747-1078`).
That owned-turn correlation is the `D-owned-turn-identity` machinery — and it lives in the legacy
`GatewayChild`/`sessions/service.py` demux, NOT on the raw-frame relay path (`D-relay-raw-frame-transport`;
`D-child-per-employee` even notes "the 1,100-line session demultiplexer loses its reason to
exist" under one-session-per-child). **So the pool's raw-frame path currently has NO owned-turn
settlement correlation.** A sound step settlement must supply one.

### 1.3 The fork (Collision #A — for main to rule)

Under one-child-one-session, is a step's terminal unambiguous ENOUGH that a per-employee terminal
watch is correct, or must the step carry an owned-turn identity like the legacy path? The honest
options:

- **Option A1 — accept the disposition, own the terminal via the ACK.** `PoolStepGateway` submits
  `prompt.submit` and reads the ACK DISPOSITION (the ACK is an id-correlated RPC result the pool
  can capture via its existing `_transport_request`/responder seam, `employee_child_pool.py:429-478`):
  - `steered` → return `RunResult("errored", ..., "delivered by steering; no independent
    execution")` immediately, mirroring `shared_gateway.py:852-861` — NO terminal watched.
  - `streaming` → this submission owns the CURRENT execution: watch the next `message.complete`/
    `error` (correct, because a streaming disposition means this turn IS the running one).
  - `queued` → this submission owns only the execution AFTER the predecessor's terminal: SKIP the
    first terminal (the predecessor's), then own the next. This requires counting terminals after
    the ACK, per employee.
  This reconstructs the disposition half of owned-turn identity on the pool path from the ACK
  alone, WITHOUT the full demux. It is sound IFF the child emits terminals in submission order per
  session (Hermes's documented behavior) and there is at most one queued predecessor to skip. It
  needs a per-employee ordered terminal counter fed by the pool frame sink (§1.4).
- **Option A2 — thread an owned turn id through the relay (the full `D-owned-turn-identity`).**
  Submit `prompt.submit` with a versioned owned turn id (as the legacy versioned submission does,
  `decisions.md:513-525`), and correlate `message.complete` by that id. This is the STRONGEST
  correlation but requires Hermes to tag terminal events with the owned turn id AND the relay to
  route by it — which the raw-frame relay does not do today, and which may touch `minds/`/Hermes
  (forbidden / `D-stock-hermes-only`). Likely OUT under stock-Hermes-only.
- **Option A3 — route the step through the legacy owned-consequence path even flag-on.** Keep the
  step on `SharedGateway.run_ticket_step` (which already owns its terminal correctly) and use the
  pool ONLY for chat panes. REJECTED by `D-chief-first-cutover` (a ticket's chat and steps share
  one stored session and MUST move together — two owners of one session otherwise) — this is the
  exact reason the contract moves chat+steps together. Recorded to show it was considered.

**Recommendation: Option A1.** It is sound under one-session-per-child + ordered per-session
terminals, needs no Hermes/`minds/` change (the ACK disposition is already returned;
`sessions/service.py:556`), and reconstructs exactly the disposition-driven ownership the legacy
path uses — without the 1,100-line demux. It correctly handles the human-turn-running case
(`queued` → skip the predecessor's terminal) that broke the naive design, and the `steered` case
(errored, no terminal). **FLAGGED to main as Collision #A — this is the central design fork and
must be ruled before implementation. The plan proceeds on A1; A1's terminal-ownership rule is the
load-bearing logic the orchestrator should spot-check.**

### 1.4 The pool turn-observer seam — OFF the stdout thread (Codex Finding 2 — corrected)

**Finding 2 (verified): the pool's `on_frame` runs on the child's stdout-reader thread**
(`raw_frame_transport.py:127-148`; the pool wakes responders synchronously on that thread,
`employee_child_pool.py:233-245`). The runner's `on_event` uses the runner's SQLite connection
(`employee_step_runner.py:425,577-584`), and SQLite connections are thread-affine
(`db.py:204-214`; the tee explicitly warns cross-thread use raises `ProgrammingError`,
`transcript_mirror_tee.py:7-15`). **So the settlement observer must NOT call `on_event` (or touch
the runner's connection) on the stdout thread, and an observer exception must not kill the reader.**

Corrected design: the pool's third `on_frame` sink does the MINIMUM on the stdout thread — it
folds a native frame into a per-employee ordered settlement state (open/terminal-count/final
payload) under a small lock and sets a `threading.Event` on terminal — and it ENQUEUES streamed
frames into a thread-safe queue. The `PoolStepGateway.run_ticket_step`, running on the RUNNER's
thread (which owns the runner's SQLite connection), DRAINS that queue and calls `on_event` itself
— so `on_event` and every `chat_service.observe_worker_gateway_event` write happen on the runner
thread, never the stdout thread. The stdout-thread sink is pure in-memory state + a queue put; it
is wrapped so an exception is swallowed (never kills the reader), mirroring the tee's
`observe`-never-raises discipline (`transcript_mirror_tee.py:114-124`). This keeps the frame shape
(§1.5) and settlement, but respects thread affinity.

The seam on the pool: `register_turn_submission(employee_entity_id, disposition_owner) -> handle`
/ `unregister`, where the handle exposes: the streamed-frame queue, the settlement `Event`, and
the resolved `RunResult` once terminal. Added to `on_frame` AFTER the existing delivery + responder
wake, snapshotting the employee's submission handle off the pool lock. On `on_dead`, the handle
settles errored (child reset). Additive; `deliver_child_frame`, responders, tee, S1 spawn/shutdown
unchanged.

### 1.5 The `on_event` frame shape is preserved (Codex Finding B — sound, verified)

`observe_worker_gateway_event` consumes `event["type"]` + `event["payload"]`
(`chat/service.py:689-725`); `SharedGateway` emits exactly `{type, session_id, payload}`
(`shared_gateway.py:868-873`). `PoolStepGateway` constructs the SAME shape from a native
`{method:"event", params:{type, payload, ...}}` frame. The shaping is a small pure function in
`pool_step_gateway.py`, unit-tested against captured real native frames (S0 record / `tui_gateway`
source, with cites — lessons-carried constraint). The runner and `chat/service.py` are UNTOUCHED.

### 1.6 `interrupt` needs the LIVE session id + an absolute deadline (Codex Finding 7 — corrected)

**Finding 7 (verified): the runner calls `interrupt(session_key, ticket_id, deadline=<absolute>)`
at shutdown** (`employee_step_runner.py:307-318`), but native `session.interrupt` needs the LIVE
session id (`hermes_frame_translation.py:263-266`), and the pool record exposes only the stored id
(`employee_child_pool.py:52-59`); the pool's live-id map + request method are private and use a
RELATIVE timeout (`employee_child_pool.py:120-123,429-468`). Corrected design: the pool gains a
small `interrupt_live_turn(employee_entity_id, *, deadline) -> None` that resolves the employee's
CURRENT live session id from `_live_session_id_by_employee` (already maintained,
`employee_child_pool.py:123`) and issues `session.interrupt {session_id: <live id>}` on the child
transport via `_transport_request`, converting the absolute deadline to the remaining relative
timeout the transport expects. `PoolStepGateway.interrupt(session_key, entity_id, *, deadline)`
delegates to it (it ignores the stored `session_key` the runner passes and uses the pool's live
id, which is the correct target). If no live id is known (no turn in flight), it is a no-op — the
same effect as interrupting a settled turn. Additive pool method; no `minds/` change.

### 1.7 No whole-step timeout (Codex Finding 9 — corrected)

**Finding 9 (verified): the first draft added settlement on "a bounded step timeout" — unrequested.**
The legacy path drains until a lifecycle terminal with NO whole-step timeout
(`shared_gateway.py:862-900`), and the contract requires settlement "exactly as today"
(§"Worker steps"). **Removed.** `run_ticket_step` blocks on the settlement `Event` until a terminal
(`message.complete`/`error`) or child death — never a step timeout. (The per-RPC transport timeout
on the `prompt.submit` ACK stays — that is the existing `_transport_request` timeout for the ACK,
not a whole-step cap.) A legitimately long employee step is never marked errored by a Panels timer.

### 1.8 Revision guidance and recovery ride the same send path (contract §"Worker steps")

The runner already routes revision guidance and restart recovery through the SAME
`run_ticket_step` call with a different `prompt` and `require_existing_session=True`
(`employee_step_runner.py:459-504, 620-637`). S3 swaps only the gateway, so revision and recovery
flow through `PoolStepGateway` unchanged. Worker-step restart recovery is a RESUME by design
(`D-runtime-restart-continuation`), not a settle — see §4.3 (this resolves the false Collision #5).

---

## 2. Loops composition — inject the pool step gateway when flag-on

### 2.1 What runs today (verified)

`start_background_loops(config, clock, *, shared_gateway: SharedGateway)`
(`loops.py:144`) builds the `EmployeeStepRunner` with `gateway=shared_gateway`
(`loops.py:155-165`) and the discovery loop with that runner. `_lifespan` calls it with the
legacy worker `shared_gateway` (`server.py:291-296`), THEN composes the relay pool AFTER
(`server.py:306-320`). So today the runner is hard-wired to the legacy gateway and the pool is
built later.

### 2.2 The change (minimal, additive) — order the pool before the loops when flag-on

`compose_relay_backend_if_enabled` returns the pool; `start_background_loops` needs the pool to
inject `PoolStepGateway` as the RUNNER's gateway when the flag is on. **Under Collision #4 reading
(a), the legacy worker gateway STILL exists flag-on** (serving day chat / catalog / status /
history), so `start_background_loops` still receives it for those non-ticket surfaces — but the
RUNNER's gateway (the step transport) is the `PoolStepGateway`, NOT the worker gateway. These are
two different injections: the chat/day gateway (installed as `app.state.adapters.gateway`, still
the `EntityRoutingGateway` over the worker gateway) and the runner's step gateway.

- **(a) Compose the pool BEFORE `start_background_loops`; pass the `PoolStepGateway` as the
  runner's step gateway.** Flag-on: `_lifespan` composes the pool first, builds `PoolStepGateway`,
  and passes it as `start_background_loops(..., step_gateway=pool_step_gateway)`. The worker
  gateway is STILL built (Collision #4 (a)) and STILL installed as the chat/day adapter, but the
  runner does NOT use it for steps. The physical reorder is feasible (Codex Finding 3 confirmed the
  reorder is possible; the call signature is the fix).
- **(b) Re-inject after.** Rejected — mutating an already-started runner's gateway is a race.

**Recommended: (a). Codex Finding 3 fix: `start_background_loops` must gain a `step_gateway`
parameter AND still accept `shared_gateway`** (it needs the worker gateway for the chat/day/history
surfaces and to build the runner flag-off). Signature: `start_background_loops(config, clock, *,
shared_gateway, step_gateway=None)`. When `step_gateway` is provided (flag-on), the runner is built
with `gateway=step_gateway`; when `None` (flag-off), the runner is built with
`gateway=shared_gateway` (exactly today). The discovery loop, eligibility, and wake are UNCHANGED
(transport-agnostic, §8). **[Collision #4-dependent]** if main rules reading (b) (worker gateway
absent), `shared_gateway` becomes optional/None flag-on and only the non-ticket surfaces must be
re-homed — a larger change the plan does not assume.

**Consequence for `_lifespan` ordering:** flag-on composes the pool FIRST, builds
`PoolStepGateway`, then calls `start_background_loops(..., shared_gateway=worker_gateway,
step_gateway=pool_step_gateway)`. Flag-off is exactly today's order (worker gateway → loops with
no `step_gateway` → no pool).

### 2.3 The runner gateway type widens to a Protocol (ordinary typing — NOT a collision)

The runner's `__init__` annotates `gateway: SharedGateway` (concrete —
`employee_step_runner.py:138`); it calls only `run_ticket_step`/`interrupt`/`status`. Injecting
`PoolStepGateway` needs that annotation to accept both. **Introduce a `StepGateway` Protocol that
both satisfy structurally and widen the runner's one annotation line to `gateway: StepGateway`.**
No behavior change.

**This is NOT a collision (Codex Finding 10 — the first draft over-flagged it).** The contract
EXPLICITLY changes the runner's injected transport ("worker steps are submitted through [the pool
child]"), and nothing in the contract forbids the one annotation/Protocol adjustment the transport
swap requires — it is ordinary typing work the contract authorizes. The Protocol lives wherever is
cleanest without touching `minds/` behavior: a small `runtime/`-local contracts module (the runner
already lives in `runtime/`, and this is a one-line change to a file the contract names as a
consumer) OR `minds/contracts.py` (a type-only addition, no `minds/` behavior change). **Prefer the
`runtime/`-local Protocol** so `minds/` is untouched entirely. The removed "Collision #2" is
withdrawn.

---

## 3. Per-ticket adoption + fail-closed persistence (generalizing S2b §2, §2.5)

### 3.1 Adoption — the pool reads each ticket's persisted `employee_session_id` on demand

S2b's `adopt_stored_session(employee_entity_id, stored_session_id)` (`employee_child_pool.py:152-162`)
is already generic over employee id (first-write-wins, no-op once a live child exists). S3 does
NOT change it. What changes is WHERE the seed comes from: S2b's composition reads ONLY the Chief
key (`_read_chief_session_key`, `composition.py:95-107`). For tickets, the durable key is
`tickets.employee_session_id` (verified: `chat/service.py` resolves a ticket entity's session by
`SELECT employee_session_id FROM tickets WHERE id = ?`), and there are MANY tickets, spawned on
demand — composition cannot eagerly seed all of them at startup.

**Design: adopt at spawn demand, not at composition.** When `PoolStepGateway.run_ticket_step`
(or a ticket pane attach) triggers `pool.child_for_employee(ticket_entity_id)` for the first
time, the pool must resume the ticket's persisted `employee_session_id` if one exists. Two ways:

- **(a) A composition-injected adoption resolver.** The pool gains an optional
  `stored_session_resolver: Callable[[str], str | None] | None` ctor param: given an employee id
  with no held stored id, the pool calls the resolver (composition-owned, DB-aware) to fetch the
  persisted durable key BEFORE `session.create`, and if it returns a key, resumes it. The
  resolver dispatches: Chief → `agent_chat_sessions.chat_session_key`; ticket → `tickets.
  employee_session_id`. This keeps the pool DB-free (composition owns the read) and generalizes
  S2b's Chief-only adoption to on-demand per-employee adoption.
- **(b) Eagerly adopt every ticket at composition.** Rejected — the set of tickets is unbounded
  and changes; eager adoption fights the pool's spawn-on-demand model.

**Recommended: (a).** In `_create_or_resume_session`, when `self._stored_session_id_by_employee`
has no entry for the employee AND a `stored_session_resolver` is set, call it; a non-empty return
seeds the resume branch, a `None`/empty return proceeds to `session.create` (a never-run ticket
mints fresh, which persistence then binds — §3.2). The resolver runs off the pool lock (it opens
a short-lived DB connection, composition's own-connection discipline). Chief adoption at
composition (S2b) STAYS for the Chief's known-at-boot key; the resolver covers on-demand ticket
adoption AND is the general path.

**[S2b-dependent]** S2b's `_read_chief_session_key` + composition adoption are the template; if
S2b's confirming round moved that read, the resolver's Chief branch re-points at wherever it
lives. The resolver is additive either way.

### 3.2 Fail-closed persistence — through the EXISTING ticket ownership writers

S2b persists the fresh Chief binding via the new call-only `record_agent_session_key(conn,
entity_id, session_key, now)` in `chat/data.py` (`agent_chat_sessions`), inside the pool's
guarded publication (fail-closed, `S2B-OWN-001`). For tickets, the durable binding is
`tickets.employee_session_id`, written ONLY through the resolution-engine-adjacent transactional
writers with compare-and-swap ownership validation (verified:
`claim_running_step_employee_session_id` → `write_employee_session_id_in_transaction`,
`tickets/data.py`, which validates `expected → candidate` and REJECTS a candidate owned by
another ticket, `ORDER BY id`).

**Design: the persistence callback dispatches by employee kind (call-only).** S2b's
`on_stored_session_bound: Callable[[str, str], None]` callback (`employee_child_pool.py:103, 424-427`)
is invoked at first-create and every rebind, INSIDE the spawn guard (fail-closed — a raise tears
down the child). S3's composition-owned callback dispatches:
- Chief → `record_agent_session_key` (S2b, unchanged).
- ticket → a **call-only ticket-session persistence writer** that binds `tickets.
  employee_session_id` to the fresh stored id.

**The key question: which ticket writer, and does its ownership CAS fit a pool bind?** The
existing session writers are step-lifecycle-bound: `claim_running_step_employee_session_id`
REQUIRES `ticket_status is agent_running_step` (verified). A pool spawn/rebind can happen when
the ticket is NOT running a step (e.g. a ticket pane attach, or a `/new` rebind on an idle
ticket). So the step-bound writers do not fit a pool bind outside a running step.

**[COLLISION — no existing ticket-session writer fits a pool bind outside a running step.]** This
mirrors S2b's Collision #4 (no existing writer fit the Chief bind). The narrowest addition: a new
call-only `bind_pool_employee_session_id(conn, ticket_id, *, expected_stored_session_id,
candidate_stored_session_id, now)` in `tickets/data.py` that writes `tickets.employee_session_id`
THROUGH the same `write_employee_session_id_in_transaction` ownership CAS (rejecting a candidate
owned by another ticket — the hardened fail-closed invariant this project just landed), but
WITHOUT the `agent_running_step` status precondition (it is a session binding, not a step claim).
It emits the appropriate session-binding event so the frontend event log / resource catalogue
stays consistent. **This is flagged to main as Collision #3 — see §12.** The recommendation: yes,
in-scope — the ticket analogue of S2b's authorized `record_agent_session_key`.

**The CAS needs BOTH expected AND candidate — the callback signature must widen (Codex Finding 5).**
The canonical `write_employee_session_id_in_transaction` requires `expected → candidate`, and on an
expected-value MISMATCH it can RETAIN the current value rather than raise (verified `tickets/data.py:342-376`);
the runner protects itself by asserting the returned binding EQUALS its candidate
(`employee_step_runner.py:551-568`). So `bind_pool_employee_session_id` MUST (a) receive the OLD
stored id as `expected` (the pool has it: `EmployeeChildRecord.stored_session_id` before a rebind,
or `None` for a first-create), and (b) after the write, ASSERT the returned binding equals the
candidate — raising if not, so a silent-retain becomes a fail-closed error. The pool's
`on_stored_session_bound` callback therefore widens to carry BOTH ids: `Callable[[str, str, str |
None], None]` (employee, new stored id, OLD stored id) — a small additive signature change.

**Fail-closed placement — CORRECTED (Codex Finding 5 — first-create is guarded, REBIND IS NOT).**
Verified: first-create persistence IS inside the spawn cleanup guard
(`employee_child_pool.py:270-301` — a raise tears down + unregisters the child). **Rebind is NOT
guarded** (`employee_child_pool.py:382-414`): it closes the old session, creates a fresh live
session, then calls `_notify_stored_session_bound` with NO teardown guard — if the callback raises,
the child stays alive with an unpublished fresh session and the old durable binding points at a
CLOSED session (a real two-owner-adjacent hazard). The first draft's claim that "the pool's
fail-closed placement is unchanged and already correct" was WRONG for rebind. **The fix is a pool
change in `rebind_fresh_session`: wrap the persistence + in-memory advance so a persistence failure
either (a) re-closes the fresh session and tears the child down (the child then respawns clean on
next demand), or (b) at minimum leaves the DB and in-memory maps CONSISTENT and the child in a
recoverable state.** The existing S2b rebind comment already reasons about consistency on a persist
failure (`employee_child_pool.py:402-408`: "if persistence raises, the in-memory maps still point
at the OLD binding and the DB still holds the OLD key — the two stay CONSISTENT") — but the OLD
LIVE session was already CLOSED, so "the OLD key" now resolves to a dead session on resume. The S3
rebind fix must close the NEW session and leave BOTH the DB key and the child's live binding on a
resumable session, OR tear the child down for a clean respawn. **This is a genuine S2b-adjacent
defect S3 must fix, not inherit** — flagged as part of Wave 2's fail-closed test (§9.2).

### 3.3 The idempotent-adoption interaction with the runner's own session claim (verified subtlety)

The runner ALSO persists the session key during a step: `persist_employee_session_id` calls
`claim_running_step_employee_session_id` with an `expected → candidate` CAS
(`employee_step_runner.py:551-575`). Under S3, the pool has ALREADY adopted/bound the ticket's
stored id (§3.1-3.2) before the runner's `on_session_key` fires. So the candidate the runner
claims EQUALS the already-bound stored id → the CAS is idempotent (`current == candidate` →
accept, verified `write_employee_session_id_in_transaction`). This is consistent, not a
double-write conflict: the pool binds the durable session; the runner's claim confirms the SAME
key under the running-step status. **Test this interaction explicitly** (§9): a pool-owned step's
`on_session_key` claim must be idempotent against the pool's prior bind, not a rejected
ownership change.

---

## 4. Composition split extended to ticket entities (generalizing S2b §1)

### 4.1 Flag-on never starts the legacy worker gateway

S2b's split: flag-on builds no CHIEF gateway (`_build_role_gateways(chief_owned_by_pool=True)` →
`(worker_gateway, None)`, `server.py:107-108`). S3 extends: flag-on builds NO legacy worker
gateway EITHER — the pool owns every ticket employee AND the Chief. The contract: "Flag-on
composition never starts the legacy worker gateway; the pool owns every ticket employee"
(§Ownership handoff).

**[COLLISION #4 — CONFIRMED BROADER than day chat (Codex Finding 3).]** The first draft framed
this as "does day chat break?" and deferred it to a Wave-3 grep. Codex verification shows the
legacy worker gateway has SEVERAL live consumers, so "build no worker gateway flag-on" as written
does NOT produce an executable composition:
- **Day chat** routes through the generic chat lifecycle + generic send route
  (`chat/service.py:64-101`, `chat/api.py:52-75`), and `EntityRoutingGateway` sends every UNMAPPED
  entity to its DEFAULT worker gateway (`shared_gateway.py:66-84`) — so a day chat send reaches the
  worker gateway.
- **`/api/chat/commands`** (the "/" catalog) and **`/api/chat/{entity}/status`** consume the
  installed gateway (`chat/api.py:127-146`).
- **Ticket employee-session history** (`/api/tickets/{id}/employee-session-history`) consumes the
  installed gateway (`tickets/api.py:578-593`).
- The production adapter installed BEFORE startup is a deliberate OFFLINE placeholder until
  `SharedGateway` is installed (`core/adapters/real.py:27-56`) — it cannot cover these calls.

So flag-on cannot simply "build no worker gateway"; SOMETHING must answer these calls. This needs
main's ruling. Options:
- **(a) Keep the legacy worker gateway RUNNING flag-on, but route only day + the non-ticket
  surfaces to it; the pool owns TICKET steps + chat + Chief.** The worker gateway no longer holds
  any TICKET session (tickets are pool-owned, guarded off by §5), so there is no two-owner hazard —
  the gateway serves day chat, the command catalog, and status ONLY. The two-owner assertion then
  asserts NO ticket/Chief session is owned by the legacy gateway (which is guaranteed by the guard
  + the empty entity map for pool-owned entities), NOT that the worker gateway is absent. This is
  the SMALLEST change that keeps the non-ticket surfaces working.
- **(b) Cut day chat + the catalog + status + session-history over to a pool-backed or neutral
  path too.** Large scope creep beyond the contract (which scopes S3 to ticket employees + Chief).
  Rejected unless main explicitly widens scope.
- **(c) Stub the non-ticket surfaces flag-on.** Breaks day chat / the catalog — rejected.

**Recommendation: (a) — the legacy worker gateway STAYS running flag-on to serve day chat, the
command catalog, entity status, and employee-session-history, but holds NO ticket/Chief session
(the crossover guard + empty entity map keep every pool-owned entity off it).** This means the
contract phrase "the legacy worker gateway is never started" must be read as "never OWNS a ticket
employee session" (the two-owner concern), NOT "the process is absent" — because non-ticket
surfaces still need it. **FLAGGED to main as Collision #4: confirm reading (a), or rule the wider
cutover in.** Under (a), `_build_role_gateways` STILL builds the worker gateway flag-on; only the
CHIEF gateway and the ticket ENTITY MAPPINGS are absent, and the guard closes ticket/Chief chat to
it. The two-owner assertion (§4.2) changes accordingly (assert no pool-owned entity is routable,
not that the gateway is None).

### 4.2 The two-owner startup assertion extends to ticket entities

S2b's `_assert_single_chief_owner(relay_backend_enabled, pool, chief_gateway)` asserts
`relay_backend_enabled == (pool is not None) == (chief_gateway is None)`
(`server.py:118-127`). S3 generalizes the CONCEPT: exactly one owner per stored session, for
EVERY pool-owned employee. The assertion reads composition inputs (S2b Collision #2 — `minds/`
routing map is untouchable).

**Under Collision #4 reading (a), the worker gateway STAYS running flag-on** (for day chat /
catalog / status / history), so the assertion CANNOT be "worker_gateway is None." What it must
assert is that NO pool-owned entity (Chief + every ticket) is ROUTABLE to the legacy worker
gateway — i.e. the entity map maps NEITHER the Chief NOR any ticket to a dedicated gateway
(flag-on the entity map is EMPTY, and the crossover guard §5 raises before any pool-owned entity
falls through to the worker-gateway default). The generalized assertion asserts on the composition
inputs that GUARANTEE this: `relay_backend_enabled == (pool is not None) == (chief_gateway is
None) == (entity_gateway_map is empty)`, plus a positive check that the injected step gateway is
the `PoolStepGateway` (not the legacy worker gateway) when flag-on. Rename it
`_assert_single_employee_owner`. Called from both compose branches, as S2b's is.

**[Collision #4-dependent]** If main rules reading (b) (wider cutover, worker gateway absent), the
assertion reverts to "worker_gateway is None." Under (a) it is the empty-entity-map + pool-step-
gateway form above. **[S2b-dependent]** If S2b's confirming round renamed/moved
`_assert_single_chief_owner`, S3 generalizes whatever landed.

### 4.3 Startup recovery settles-not-resumes stale running ticket steps when pool-owned

**Two distinct recovery paths, both must be handled (verified):**
- **Human chat turns:** `_recover_running_human_chat_turns` (`server.py:157-178, 287-290`) runs
  `recover_human_turn` for running HUMAN turns EXCLUDING tickets at `agent_running_step` (the SQL
  filters `tickets.ticket_status <> 'agent_running_step'`). S2b's guard settles-not-resumes a
  stale running CHIEF human turn (`recover_human_turn`'s settle branch). S3 extends that guard to
  a pool-owned TICKET human turn (§5.3 widens the predicate).
- **Worker step turns:** a ticket left at `agent_running_step` across a restart is recovered by
  the `EmployeeStepRunner.recover_running_step` path (`employee_step_runner.py:178-199, 507-531`),
  which resumes the durable session with `_RESTART_RECOVERY_MESSAGE` through `run_ticket_step`.
  Under S3 flag-on, that `run_ticket_step` goes through `PoolStepGateway` — the pool ADOPTS the
  ticket's persisted `employee_session_id` (§3.1) and resumes it (`D-runtime-restart-continuation`
  — resume, not replay). **This IS "settle-not-resume" at the RIGHT layer:** the contract's
  "startup recovery settles-not-resumes stale running ticket turns when pool-owned" applies to
  turns that CANNOT be continued (a stale HUMAN turn against a session the pool now owns), while a
  worker STEP left at `agent_running_step` is DESIGNED to be continued via session resume
  (that is the existing restart-continuation contract, which S3 preserves). The plan must NOT
  conflate them: pool-owned stale HUMAN chat turns settle; worker step recovery resumes.

**Not a collision (withdrawn — Codex Finding 10).** The contract phrase "settles-not-resumes stale
running ticket turns when pool-owned" is unambiguously the HUMAN-chat-turn recovery: the existing
code already splits it — startup human-turn recovery EXCLUDES tickets at `agent_running_step`
(`server.py:157-178`), and running worker steps are explicitly resumed
(`D-runtime-restart-continuation`, `decisions.md:426-433`). Pool-owned stale HUMAN turns settle;
worker-step recovery resumes. No owner clarification needed; the first draft over-flagged it.

---

## 5. The central crossover guard extended to ticket entities (generalizing S2b §1.4)

### 5.1 What S2b built (verified)

`ChatTurnLifecycle.__init__` takes `chief_pool_owned: Callable[[], bool]`
(`server.py:353-359` wires it to `lambda: config.relay_backend_enabled`). A private raising guard
fires at the top of `start_human_turn`/`continue_human_turn`/`pause_active_turn`/
`_answer_pending_clarification` when `chief_pool_owned() and entity_id ==
CHIEF_OF_STAFF_ENTITY_ID`; a settle-not-raise branch at the top of `recover_human_turn` settles a
stale Chief turn.

### 5.2 The change — widen the entity predicate from "is Chief" to "is pool-owned"

Flag-on, EVERY pool-owned employee's human chat HTTP path must not reach the legacy worker
gateway. Ticket human chat flag-on speaks the neutral relay (§7), so the legacy ticket chat HTTP
send must be closed to pool-owned tickets exactly as the Chief's was. The guard predicate widens:
- Rename the injected predicate `chief_pool_owned` → `entity_pool_owned: Callable[[str], bool]`
  (takes the entity id; returns whether that entity is pool-owned flag-on). `create_app` wires it
  to `lambda entity_id: config.relay_backend_enabled and _is_pool_owned_entity(entity_id)`, where
  `_is_pool_owned_entity` is True for the Chief entity AND any ticket entity (`t_*`). Flag-off →
  always False (exactly today).
- The raising guard and the `recover_human_turn` settle branch fire for ANY pool-owned entity,
  not just the Chief.

**Scope note (naming + earning existence).** Day chat and any non-pool-owned entity are
UNAFFECTED — the predicate is False for them. This is the S2b guard with a widened predicate, not
a new mechanism. **[Depends on Collision #4]** if day chat stays on the legacy worker gateway
flag-on, the predicate must correctly EXCLUDE day entities (it does — `_is_pool_owned_entity`
returns True only for Chief + ticket ids).

### 5.3 Preserve the worker-step path (NOT a human turn — verified)

The guard fires only for HUMAN turn entry points (`start_human_turn` etc.). Worker STEP turns go
through `EmployeeStepRunner` → `PoolStepGateway`, NOT through `ChatTurnLifecycle`. So the guard
does not touch the step path. Verify the runner never calls a `ChatTurnLifecycle` human entry
point (it calls `chat_service.start_worker_turn` / `observe_worker_gateway_event` /
`finish_worker_turn` — the worker-turn writers, not the human-turn lifecycle). Confirmed:
`employee_step_runner.py` imports `chat_service` worker functions, not `ChatTurnLifecycle`.

---

## 6. `panels` CLI worker identity reads `PLAN_TICKET_ID` (contract §3)

### 6.1 The change (backend + CLI)

Today `panels worker my-ticket` reads `HERMES_UI_SESSION_ID`/`HERMES_SESSION_KEY` and hits
`/api/tickets/by-live-session/{id}` or `/api/tickets/by-employee-session/{id}`
(`cli/main.py:1313-1337`, verified). The contract (§3): resolve from `PLAN_TICKET_ID` in the
child's spawn env; retire the Hermes-env reads from the worker resolution path; server-side
resolution by ticket id directly, with the existing ownership validation.

**CLI (`cli/main.py`):** `worker_my_ticket` reads `PLAN_TICKET_ID` from env (the same
`_TICKET_ID_ENV = "PLAN_TICKET_ID"` already defined, `cli/main.py:27`); if absent, fail
validation ("not running as a ticket worker"). It GETs a new by-ticket-id resolution route.

**Server (`tickets/api.py` + `tickets/data.py`):** add `GET /api/tickets/{ticket_id}/worker-self`
(a distinct path from the plain `/api/tickets/{ticket_id}` read, so worker resolution carries the
worker-identity ownership validation the plain detail read does not). It resolves the ticket by
id and applies the SAME ownership validation the by-session readers apply — verified: the plain
`read_ticket` has NO ownership validation (`tickets/data.py`), while
`read_ticket_by_employee_session_id` validates one-owner. The new resolution reads the ticket by
id, then confirms the ticket's `employee_session_id` is unambiguously owned (rejecting a corrupt
duplicate), returning the same detail shape (`ticket_detail` + `worker` skill) the existing
by-session route returns.

**Retire the by-live-session and by-employee-session reads from the WORKER path.** The contract
says the Hermes-env reads are "retired from the worker resolution path." The `/by-live-session/`
and `/by-employee-session/` ENDPOINTS may remain if a legacy caller needs them, but the CLI
worker command stops using them. **[Scope]** the contract says "retired from the worker
resolution path" — the plan retires the CLI's USE of them; whether to DELETE the endpoints is S4
(deletion) scope, not S3. State this: S3 stops the CLI reading Hermes env; endpoint deletion is
out of scope.

### 6.2 THE FLAG-OFF COLLISION (Collision #1 — the contract's assumption FAILS, verified)

**The contract (§3, lines 54-56):** "Flag-off ticket employees still run through the legacy
gateway whose children carry the same spawn env the pool sets — the CLI change must hold for both
compositions (state exactly how in the plan; if the legacy children cannot carry it, that is a
collision to flag, not to patch around)."

**Verified reality: the legacy children DO NOT carry a usable `PLAN_TICKET_ID`.** The legacy
worker path is ONE shared `SharedGateway` child (`panels-worker` role) serving EVERY ticket's
durable session (`D-gateway-topology` — "ONE persistent shared worker gateway child holding every
ticket's durable session"). Its spawn env is built ONCE at startup:
`base_env` + `PLAN_ACTOR=worker` + `HERMES_*` (`server.py:99-106`, `shared_gateway.py:_env`) —
**it sets NO `PLAN_TICKET_ID`, and it CANNOT**, because one child serves all tickets, so no single
`PLAN_TICKET_ID` value is correct. Today the legacy worker learns its ticket from the PER-TURN
Hermes session env (`HERMES_UI_SESSION_ID`/`HERMES_SESSION_KEY`, injected per-turn by Hermes's
local terminal shell, NOT spawn env — the exact mechanism `D-stock-hermes-only` describes as the
reason the local patches exist). So a `panels worker my-ticket` that reads ONLY `PLAN_TICKET_ID`
would FAIL on the legacy shared child (no `PLAN_TICKET_ID` in its per-turn subprocess env).

**This is a genuine contract-vs-reality collision. The contract's premise "legacy children carry
the same spawn env the pool sets" is false for `PLAN_TICKET_ID`.** Per the contract's own
instruction, I FLAG it rather than patch around it. Options for main:

- **(a) The CLI change is flag-on-only in practice; flag-off keeps the Hermes-env resolution.**
  `worker_my_ticket` reads `PLAN_TICKET_ID` FIRST (pool children, flag-on); if absent, FALLS BACK
  to the existing `HERMES_UI_SESSION_ID`/`HERMES_SESSION_KEY` resolution (legacy shared child,
  flag-off). Both compositions work: pool children carry `PLAN_TICKET_ID`; legacy children carry
  Hermes session env. This CONTRADICTS the contract's "the Hermes-env reads are retired from the
  worker resolution path" — but the contract ALSO says retiring them is coupled to
  `D-child-per-employee` (the pool topology), and flag-off is still the legacy topology where the
  Hermes-env read is the ONLY working identity. So the retirement is correct FLAG-ON, and the
  fallback is required for flag-off to keep working. **This is the recommendation.**
- **(b) Retire the Hermes-env reads unconditionally; accept that `panels worker my-ticket` breaks
  on the flag-off legacy path.** Rejected — the contract requires "the CLI change must hold for
  BOTH compositions," and flag-off is "exactly today's system" (§Outcome). Breaking the flag-off
  worker identity violates both.
- **(c) Make the legacy shared child carry `PLAN_TICKET_ID`.** Impossible — one child, all
  tickets; there is no single correct value. This is why the contract flags it as a potential
  collision.

**Recommendation: (a) — read `PLAN_TICKET_ID` first, fall back to the Hermes-env resolution when
absent — BUT this NEEDS AN EXPLICIT OWNER OVERRIDE (Codex Finding 6).** The fallback keeps both
compositions working, but it DIRECTLY CONTRADICTS the contract's binding text: Acceptance §3
requires "Hermes-env reads absent from the worker path" and §3 says the reads "are retired from the
worker resolution path" (`contract.md:47-56, 83-84`) — in BOTH compositions. A fallback that KEEPS
the Hermes-env read for flag-off is the OPPOSITE of "absent." The contract itself says to FLAG this
collision, not implement around it. **So the fallback cannot proceed without an explicit owner
override.** The tension is real and unavoidable: the contract asks for BOTH "hold for both
compositions" (§3) AND "Hermes-env reads absent" (§3, Acceptance §3), and on the current
shared-child topology those two cannot both hold (the shared child has no `PLAN_TICKET_ID` and its
ONLY working identity IS the Hermes-env read). Something must give. **FLAGGED to main as Collision
#1 (the primary collision this ticket names) — main must rule ONE of: (a) authorize the flag-off
Hermes-env fallback (override "absent" for the legacy path only, until S3b/S4 deletes it); or rule
that flag-off `panels worker my-ticket` is ACCEPTABLY broken until S3b (the legacy path is being
deleted anyway); or another resolution. The plan does NOT silently patch it and does NOT proceed on
the fallback without the override.**

---

## 7. Ticket pane cutover (contract §"Ticket pane cutover")

### 7.1 The change (frontend) — generalize S2b's Chief neutral pane to tickets

S2b built `ChiefNeutralPane.svelte` + `neutralPane.ts` (the pure-TS neutral client) + the
tri-state `relayChief` capability signal, mounted at `ChiefOfStaffRoute` + `BoardRoute` (Chief
mounts only); `TicketRoute` was explicitly LEFT on the legacy `ChatPanel` (S2b §5.2, "Tickets
MUST stay on the legacy path (S3 moves them)"). S3 moves them:

- **`neutralPane.ts` is already employee-generic** (it takes `employeeEntityId` — verified S2b
  §4.2, "the relay already routes per-employee"; no Chief-specific logic). The ticket pane reuses
  it with the ticket entity id.
- **A ticket neutral pane component.** Options: (a) reuse `ChiefNeutralPane.svelte` directly with
  a ticket entity id (if it is already entity-generic), or (b) a thin `TicketNeutralPane.svelte`
  wrapping the same client. **[Verify S2b's component]** if `ChiefNeutralPane.svelte` hardcodes
  the Chief entity or Chief-only affordances, a sibling ticket component reuses `neutralPane.ts`;
  if it is entity-parameterized, reuse it. Prefer reuse; do not duplicate the client.
- **The capability signal generalizes.** S2b's `relayChief` tri-state (`capabilities.ts`) gates
  the Chief mounts. S3 needs the SAME signal to gate the ticket mount (the flag is one config
  flag — `relay_backend_enabled`). Rename/generalize `relayChief` → a single `relayBackend`
  tri-state (or add a parallel `relayTickets` reading the same meta key). Cleanest: ONE signal
  `relayBackend` (`"unknown"|"enabled"|"disabled"|"error"`) drives BOTH Chief and ticket mounts,
  since one flag governs both. `/api/meta` already exposes `relay_chief_enabled` (S2b); S3 reads
  the SAME key (one flag) — no new meta key. **[Naming]** if the meta key stays `relay_chief_enabled`,
  rename to `relay_backend_enabled` for honesty (it governs the whole backend), OR add a note
  that the one key governs both; recommend renaming since the flag is `relay_backend_enabled`.

- **`TicketRoute.svelte` swaps `ChatPanel` → the ticket neutral pane under the tri-state signal**
  (enabled → neutral pane; disabled → legacy `ChatPanel`; unknown/error → placeholder/retry).
  This is the S2b route-swap pattern applied to `TicketRoute` (which S2b explicitly did NOT
  touch). `BoardRoute`'s ticket branch (the `else if selectedCard` TicketRoute branch S2b left
  untouched) also swaps under the same signal.
- **The `chatGatewayStatus` resource MUST become disabled-branch-only (Codex Finding 4 —
  S2B-ROUTE-001 repeats for the ticket).** `TicketRoute` today EAGERLY opens `chatGatewayStatus`
  for every mount (`TicketRoute.svelte:27-35`), which calls `/api/chat/{ticket}/status`
  (`resourceCatalogue.ts:333-335`) — a legacy-gateway surface. If the swap only changes the
  rendered pane but leaves that resource eager, flag-on would STILL touch the legacy gateway even
  when the neutral pane is selected. The S2b Codex review required exactly this lazy boundary for
  the Chief (`codex-implementation-review.md:49-52`, S2B-ROUTE-001). So `TicketRoute` (and the
  `BoardRoute` ticket branch) must open `chatGatewayStatus` ONLY in the `disabled` branch and
  dispose it otherwise — the neutral pane has its own connection meaning and does not need it.

### 7.2 Step turns stream live into the ticket pane (contract §"Ticket pane cutover")

"Step-generated turns and approval re-prompts appear in the transcript and stream live like any
turn." A step submitted through the pool child emits native frames (`message.start`/`delta`/
`complete`) that the relay fans out to EVERY subscriber of that employee — including a ticket pane
subscribed to the ticket entity. The neutral client folds them in exactly as it folds a human
turn's frames (S2b's 13-kind reducer). So a step turn streams into the ticket pane for free
BECAUSE the pane subscribes to the same employee's frames the step submits to. **No extra frontend
work** — the pane's subscription + the client's fold-in already render any turn on the employee,
whether human-initiated or step-initiated. Approval re-prompts (a step's clarify/approval frames)
render inline via the pane's existing clarify/approval affordances (S2b §4.4). **This is the "chat
IS the worker" preservation the contract names** (§Outcome): the ticket pane subscribes to the
employee; the step's turn IS a turn on that employee; it streams live.

**Verify the subscription is live during a step.** The pane subscribes on attach
(`_attach` synthesizes `{relay:"subscribe"}`, `neutral_downstream_session.py:182-190`); the step
submits on the SAME employee's child; the relay fans out the step's frames to the subscribed pane
(uncorrelated event frames fan out to all subscribers, `employee_child_relay.py:392-411`). A
Playwright test asserts a step turn streams into a subscribed ticket pane (§9, contract Acceptance
§2 + §4).

---

## 8. Eligibility decision preservation (contract §"eligibility decision unchanged")

**The complete automatic-dispatch eligibility decision is UNTOUCHED.** Verified factors
(`automatic_employee_step_eligibility.py`): board membership, active-Chat (no running `chat_turns`
row), non-terminal stage, empty/paired control status, gating field exists, no parked proposal,
scope/at-cap, no blocker. S3 changes NONE of them:
- The discovery loop (`automatic_employee_step_discovery_loop.py`) polls today's board and calls
  `try_run_automatic_step` UNCHANGED — it is transport-agnostic (it calls the RUNNER, which S3
  re-points at a different gateway).
- The transactional claim `claim_automatic_employee_step` (with the same eligibility check under
  the lock) is UNCHANGED — the runner still claims before submitting.
- The active-Chat factor + the runner's `_active_ticket_ids` in-flight guard (dispatch
  bookkeeping — `D-native-turn-concurrency`'s "scheduling state, not conversation policy") are
  UNCHANGED. The mid-step human send follows STOCK Hermes semantics on the relay path (no Panels
  admission gate — the contract's "only chat-side rejection machinery is absent on the relay
  path"), but that is a CHAT-side concern; the eligibility/dispatch bookkeeping is untouched.

**The existing eligibility suites must pass UNCHANGED (lessons-carried constraint; contract
Acceptance §2).** S3 adds NO new eligibility test and modifies NO existing eligibility test — a
diff to those files would be a defect. The plan's step tests exercise the runner's TRANSPORT swap,
not the eligibility decision.

---

## 9. Every acceptance clause mapped to a NAMED test (both flag states)

RED-first (lessons-carried constraint #3 — a behavior test that stays green when the behavior is
deleted is a defect). Fakes only; the STATEFUL scripted child (S2b's `scripted_relay_child.py`)
is extended to drive ticket steps.

### 9.1 Backend unit — `tests/unit/test_pool_step_gateway.py` (new, §1, §2)
- `test_pool_step_gateway_run_submits_and_settles_on_completed` — a scripted child streams
  `message.start` → deltas → `message.complete{status:complete, text}`; assert
  `run_ticket_step` returns `RunResult("complete", text, ...)`, `on_event` saw each streamed
  frame in the `{type,session_id,payload}` shape, and `on_session_key` was called BEFORE submit.
- `test_pool_step_gateway_settles_on_failed` — `error` frame → `RunResult("errored", ..., error)`.
- `test_pool_step_gateway_settles_on_child_reset` — child death mid-turn → `RunResult("errored",
  ..., "child reset...")` (proves mid-step restart surfaces as errored → recovery re-runs).
- `test_pool_step_gateway_busy_raises_shared_gateway_busy` — a busy `prompt.submit` ACK (4009) →
  `SharedGatewayBusy(session_key=...)` so the runner's busy branch runs.
- `test_pool_step_gateway_on_event_frame_shape` — the native→`on_event` shaping is EXACTLY the
  `{type,session_id,payload}` shape `observe_worker_gateway_event` consumes (cite the captured
  real frame).
- `test_pool_step_gateway_turn_observer_unregistered_on_settle` — settled/failed turn leaves no
  dangling observer (dispatch a second step, assert clean state).

### 9.2 Backend unit — `tests/unit/test_pool_ticket_adoption.py` (new, §3)
- `test_ticket_session_adopted_on_first_spawn` — a ticket with a persisted `employee_session_id`
  → first `child_for_employee` resolves the stored id via the resolver and `session.resume`s it
  (not `session.create`). A never-run ticket (NULL) → `session.create`.
- `test_fresh_ticket_binding_persisted_through_ownership_writer` — a fresh ticket bind writes
  `tickets.employee_session_id` through the new call-only `bind_pool_employee_session_id` (the
  ownership CAS), fail-closed inside the spawn guard (a persist failure tears down the child, no
  live-but-unpublished owner — `S2B-OWN-001` generalized).
- `test_pool_bind_rejects_session_owned_by_another_ticket` — the CAS rejects a candidate session
  owned by a different ticket (the hardened fail-closed invariant holds on the pool path).
- `test_runner_session_claim_idempotent_against_pool_bind` (§3.3) — the runner's
  `claim_running_step_employee_session_id` is idempotent when the candidate equals the pool's
  prior bind (not a rejected ownership change).

### 9.3 Backend unit — `tests/unit/test_hermes_backend_ticket_composition.py` (new, §4)
- `test_flag_off_composition_is_todays_wiring` — flag off → legacy worker gateway built, runner
  wired to it, no pool.
- `test_flag_on_no_legacy_worker_gateway_pool_owns_steps` — flag on → NO legacy worker gateway,
  pool composed, runner wired to `PoolStepGateway`.
- `test_assert_single_employee_owner_helper` + `test_lifespan_boot_raises_on_inconsistent_composition`
  — the generalized assertion raises for a hand-built inconsistent triple/quad AND a `_lifespan`
  boot with a deliberately inconsistent composition RAISES (proves it is wired, not a standalone
  bool — the S2B-ACC-001 lesson: make boot actually raise).
- `test_flag_on_ticket_human_chat_ops_rejected` — flag on → each gateway-touching TICKET human
  chat op is rejected by the widened crossover guard before capturing a gateway; a non-pool-owned
  entity (day, if applicable) is unaffected.
- `test_flag_on_stale_running_ticket_human_turn_settled` — a stale running ticket HUMAN turn at
  boot is settled (not resumed); a ticket at `agent_running_step` (a worker step) is left for the
  runner's resume-recovery (NOT settled — §4.3).

### 9.4 CLI unit — `tests/unit/test_worker_cli_identity.py` (new, §6)
- `test_worker_my_ticket_resolves_from_plan_ticket_id` — with `PLAN_TICKET_ID` set, the CLI hits
  the by-ticket-id route and resolves the ticket (mock HTTP).
- `test_worker_my_ticket_falls_back_to_hermes_env_when_no_plan_ticket_id` (Collision #1 (a)) —
  with no `PLAN_TICKET_ID` but Hermes env set, the CLI hits the by-session route (flag-off legacy
  path preserved). **[Depends on Collision #1 ruling]** — if main rules unconditional retirement,
  this test asserts the no-identity failure instead.
- `test_worker_self_endpoint_ownership_validation` — the new by-ticket-id resolution route
  applies the ownership validation (rejects an ambiguously-owned session).

### 9.5 Playwright, both flag states (contract Acceptance §4)

**[COLLISION #B — the automatic-step Playwright test has NO ingress in the current test
composition (Codex Finding 8, verified).]** Relay-enabled test mode composes ONLY pool+relay+
neutral route — NO role gateways, NO loops, NO real runner (`server.py:216-244`); the installed
`TestModeAcceptingEmployeeRevisionRunner` performs NO Hermes work (`testmode.py:45-47`); the test
router exposes only `set-now` (`testmode.py:77-99`). So there is NO way to drive a REAL automatic
step through the pool in the e2e server as composed today. The contract's Acceptance §2 ("a full
automatic step through a scripted pool child — dispatch, live streaming... settlement on completed
and on failed") and §4 ("a step turn streaming live into the ticket pane") REQUIRE such an
ingress. Options for main:
- **(a) Compose the real `EmployeeStepRunner` + `PoolStepGateway` in relay test mode, and add a
  test-only step-trigger route** (a `/api/test/run-step/{ticket_id}` in the test router that calls
  `runner.try_run_automatic_step`) so Playwright can dispatch a real step against the STATEFUL
  scripted child. This mirrors S2b's precedent (S2b added a stateful scripted child + a test-mode
  compose path specifically so Playwright could drive real behavior against scripted frames). The
  discovery loop is NOT needed (the test triggers dispatch directly); only the runner + step
  gateway + a trigger route. This is additive test machinery, gated on `test_mode`.
- **(b) Assert the step-streaming behavior only at the unit/backend level** (§9.1 already proves
  `PoolStepGateway` settlement) and drop the Playwright automatic-step scenario. REJECTED — the
  contract explicitly names a Playwright step-streaming scenario (Acceptance §4).
- **(c) Drive the step frames purely from the scripted child with no runner** (fake the whole
  dispatch). REJECTED — it would NOT exercise the real `EmployeeStepRunner` → `PoolStepGateway` →
  settlement path, so the test would pass without the behavior (the S2B-ACC-001 lesson: a test that
  stays green when the behavior is deleted is a defect).

**Recommendation: (a) — compose the real runner + `PoolStepGateway` in relay test mode and add a
`test_mode`-gated step-trigger route.** This is the same "add the test machinery that lets
Playwright drive the REAL path against scripted frames" S2b established, scoped to steps.
**FLAGGED to main as Collision #B (test-composition addition) — confirm the test machinery
(runner + trigger route in relay test mode) is in-scope, as S2b's scripted-child + compose path
was.** Wave 6 depends on this ruling.

New file `tests/e2e/test_ticket_neutral_pane.py` (flag-on) against the extended scripted child +
the test step-trigger (Collision #B (a)):
- `test_ticket_step_streams_live_into_subscribed_pane` — trigger an automatic step; its turn
  streams token-by-token into a subscribed ticket pane (MutationObserver, S2b's F13 discipline).
- `test_ticket_step_settles_on_completed` and `..._on_failed` — the pane shows the settled turn.
- `test_ticket_chat_send_and_render_flag_on` — the re-anchored ticket-chat scenario on the neutral
  pane.
- `test_ticket_human_send_mid_step_native_queue` — a human send during a running step follows
  stock queue semantics (no synthetic busy, composer never disabled — `D-native-turn-concurrency`).
- `test_ticket_recovery_after_child_reset` — child reset → the pane re-attaches and restores
  history (contract Acceptance §4 "recovery after child reset").
Flag-off: the EXISTING ticket-chat Playwright scenarios pass UNCHANGED on the legacy path (the
`TicketRoute` legacy `ChatPanel` branch) — a required re-anchor point (contract Acceptance §4).

### 9.6 Eligibility suites (contract Acceptance §2 — "existing suites")
The EXISTING `test_automatic_employee_step_eligibility*.py` and discovery-loop suites pass
UNCHANGED — S3 touches neither the eligibility module nor its tests. A diff there is a defect.

---

## 10. RED-first wave ordering

Every test RED first, then the minimal code until green. Waves gated so a later wave builds on a
green earlier one (the S2b cadence that worked).

- **Wave 1 — the pool turn-observer seam + `PoolStepGateway` (§1, §2).** RED `test_pool_step_gateway.py`
  → add `register_turn_observer`/`unregister_turn_observer` + the `on_frame` third sink to the
  pool, build `pool_step_gateway.py` (submit + block-on-observer + native→`on_event` shaping +
  busy/reset settlement) + the `StepGateway` Protocol + the runner annotation widen, until green.
- **Wave 2 — per-ticket adoption + fail-closed persistence (§3).** RED `test_pool_ticket_adoption.py`
  → add the `stored_session_resolver` ctor param + the on-demand resolve in
  `_create_or_resume_session`, the composition-owned resolver + persistence dispatch (Chief vs
  ticket), and the call-only `bind_pool_employee_session_id` ownership-CAS writer in
  `tickets/data.py`, until green.
- **Wave 3 — composition split + generalized assertion + widened guard (§4, §5).** RED
  `test_hermes_backend_ticket_composition.py` → extend `_build_role_gateways`/`_lifespan` for the
  flag-on no-legacy-worker-gateway case, order the pool before the loops + pass `step_gateway`,
  generalize the assertion, widen the crossover guard predicate + the settle-not-resume branch,
  until green. (Wave 3 depends on the Collision #4 verification — day chat — as its first step.)
- **Wave 4 — CLI identity (§6).** RED `test_worker_cli_identity.py` → add the by-ticket-id
  resolution route + ownership validation reader, change `worker_my_ticket` to read
  `PLAN_TICKET_ID` (with the flag-off fallback per Collision #1's ruling), until green.
- **Wave 5 — ticket pane cutover (§7).** RED the frontend mount/capability tests + a ticket-pane
  fold-in test → generalize the capability signal, swap `TicketRoute`/`BoardRoute` ticket branch
  to the neutral pane under the signal, until green. Keep `npm run check` clean and the
  resource-catalogue completeness test green (the pane registers NO resource — S2b's boundary).
- **Wave 6 — Playwright flag-on (`test_ticket_neutral_pane.py`) + flag-off re-anchor.** RED each
  scenario → wire end to end against the extended stateful scripted child; confirm the existing
  ticket-chat flag-off scenarios pass unchanged.

Run the frontend unit suite with `cd web && npm test`, the type check with `cd web && npm run
check`. The implementer NEVER runs `./verify`. No `pytest.skip`/`skipif` anywhere.

---

## 11. Exact file allowlist

The implementer may create/modify EXACTLY these; nothing else. Ignore non-ticket tree noise
(the `initiative_planning` worker-type files, `skills/panels-chief-of-staff/SKILL.md`,
`orchestration/vps-agent-gui-research-2026-07.md`, `.claude/worktrees`).

### Backend — new source
- `src/planner/hermes_backend/pool_step_gateway.py` — `PoolStepGateway` + the native→`on_event`
  shaping (§1). TYPE-imports `RunResult`/`OnEvent`/`SharedGatewayBusy` from `minds/` (no `minds/`
  edit).

### Backend — additive edits to existing `hermes_backend` files
- `src/planner/hermes_backend/employee_child_pool.py` — ADD the per-employee turn-submission seam
  (`register_turn_submission`/`unregister` — a stdout-thread-safe frame sink that enqueues streamed
  frames + folds the disposition-driven settlement, §1.3-§1.4) + the `on_frame` third sink +
  on-dead notify; ADD `interrupt_live_turn(employee_entity_id, *, deadline)` resolving the LIVE
  session id (§1.6); ADD the `stored_session_resolver` ctor param + on-demand resolve in
  `_create_or_resume_session`; WIDEN `on_stored_session_bound` to carry (employee, new stored, OLD
  stored) (§3.2); FIX `rebind_fresh_session` fail-closed persistence (§3.2 — a real defect, not
  inherited). Additive/corrective; S1 spawn/shutdown paths unchanged.
- `src/planner/hermes_backend/composition.py` — generalize the persistence callback (Chief vs
  ticket dispatch, carrying expected+candidate) + the adoption resolver; select the
  pool-before-loops composition for the step gateway. Additive; the S2a tee + S2b Chief adoption
  wiring preserved.
- `src/planner/hermes_backend/scripted_relay_child.py` — extend the stateful child to drive
  ticket-step frames with disposition-bearing `prompt.submit` ACKs (streaming/queued/steered) +
  message.start/delta/complete/error + a child-reset cue (§1.3 — the disposition path must be
  e2e-observable). Additive to S2b's child.

### Backend — named composition/wiring edits (minimal, justified)
- `src/planner/core/server.py` — `_lifespan` flag-on: compose the pool before the loops, build
  `PoolStepGateway`, pass `step_gateway`; keep building the worker gateway for non-ticket surfaces
  (Collision #4 (a)); generalize `_assert_single_chief_owner` → `_assert_single_employee_owner`
  (empty-entity-map + pool-step-gateway form, §4.2); the tri-state meta key (rename/reuse — §7.1);
  **compose the real runner + `PoolStepGateway` in relay TEST mode (Collision #B (a)).**
- `src/planner/core/testmode.py` — ADD a `test_mode`-gated step-trigger route so Playwright can
  dispatch a real automatic step against the scripted child (Collision #B (a)).
- `src/planner/core/loops.py` — `start_background_loops` gains `step_gateway: Any | None = None`
  (KEEPS `shared_gateway`); wires the runner to `step_gateway` flag-on, to `shared_gateway`
  flag-off. The discovery loop / eligibility / wake wiring UNCHANGED.
- `src/planner/chat/service.py` — widen the crossover-guard predicate `chief_pool_owned` →
  `entity_pool_owned(entity_id)` + the settle-not-resume branch, for any pool-owned entity
  (Chief + ticket); day entities are NOT pool-owned → unaffected (Collision #4 (a)).
- `src/planner/runtime/employee_step_runner.py` — ONE-LINE annotation widen `gateway:
  SharedGateway` → `gateway: StepGateway` (ordinary authorized typing, §2.3 — NOT a collision).
- a `runtime/`-local contracts module — the `StepGateway` Protocol (keeps `minds/` untouched).
- `src/planner/tickets/data.py` — ADD the call-only `bind_pool_employee_session_id(conn, ticket_id,
  *, expected_stored_session_id, candidate_stored_session_id, now)` ownership-CAS writer with a
  post-write equality assert (no `agent_running_step` precondition). **[COLLISION #3 — flagged.]**
- `src/planner/tickets/api.py` — ADD the by-ticket-id worker-self resolution route with ownership
  validation (§6).
- `src/planner/cli/main.py` — `worker_my_ticket` reads `PLAN_TICKET_ID` (the flag-off fallback
  ONLY if Collision #1 is ruled that way — else per main's ruling); hits the new route.

### Frontend — new/edited files
- `web/src/components/TicketNeutralPane.svelte` (new, or reuse `ChiefNeutralPane.svelte` if
  entity-generic — §7.1).
- `web/src/lib/capabilities.ts` — generalize `relayChief` → `relayBackend` (or add `relayTickets`
  on the same key) — §7.1.
- `web/src/App.svelte` — read the (possibly renamed) meta key into the generalized signal.
- `web/src/routes/TicketRoute.svelte` — swap `ChatPanel` → the ticket neutral pane under the
  signal.
- `web/src/routes/BoardRoute.svelte` — swap the ticket branch under the signal.
- `web/src/lib/neutralPane.ts` — reuse as-is (entity-generic); edit ONLY if a ticket-specific
  affordance is genuinely required (avoid).

### Tests — new/edited
- new: `tests/unit/test_pool_step_gateway.py`, `tests/unit/test_pool_ticket_adoption.py`,
  `tests/unit/test_hermes_backend_ticket_composition.py`, `tests/unit/test_worker_cli_identity.py`,
  `tests/e2e/test_ticket_neutral_pane.py`, a frontend `web/tests/*.test.mjs` for the ticket pane
  fold-in (added to `web/package.json`'s `test` chain).
- edited: `tests/e2e/conftest.py` — a `relay_tickets` server-factory param + fixture (additive,
  mirroring S2b's `relay_chief`); `src/planner/core/testmode.py` — the step-trigger route
  (Collision #B (a), listed above under wiring edits).

### Hard constraints (RE-VERIFIED against this plan)
- **No file under `src/planner/minds/` changes** — the `StepGateway` Protocol lives in a
  `runtime/`-local contracts module; `pool_step_gateway.py` only TYPE-imports from `minds/`.
- **`src/planner/runtime/` changes only the ONE annotation line** on the runner (ordinary typing,
  §2.3) plus the new `runtime/`-local `StepGateway` Protocol; the eligibility module, the discovery
  loop, and their tests are UNTOUCHED (§8).
- **The existing eligibility + discovery suites pass unchanged** (a diff is a defect).
- **S1/S2a/S2b `hermes_backend` public behavior preserved (additive only)** — the pool's
  one-child-one-session rules, the denylist, and the tee are untouched; the ONE corrective change
  is the rebind fail-closed defect (§3.2, Collision #3).
- **No `core/db.py` schema change** — adoption READS `tickets.employee_session_id`; persistence
  WRITES it through the ownership writer; no new column.
- **Fail-closed persistence (lessons-carried #2)** — first-create is already guarded; **REBIND is
  NOT and must be fixed** (§3.2). The callback body dispatches by employee kind AND carries
  expected+candidate for the ticket CAS.
- **The settlement observer never touches the runner's SQLite connection on the stdout thread**
  (§1.4, Codex Finding 2) — the stdout sink only folds in-memory state + enqueues; `on_event`
  fires on the runner thread.
- **No whole-step timeout** (§1.7, Codex Finding 9) — settlement is by lifecycle terminal or child
  death only.

---

## 12. Collisions raised (flagged to main; NOT patched around)

Genuine contract-vs-reality tensions with verified evidence, surfaced for main's ruling BEFORE
implementation (per the contract: "On contract-vs-reality collisions: STOP and flag with evidence
and options"). This set is CORRECTED after the Codex plan review: two are design FORKS that need a
ruling (#A, #B), three are confirmed real collisions (#1, #3, #4), and two first-draft "collisions"
are WITHDRAWN as not genuine (#2, #5 — see the tail).

### Collision #A — the step-settlement correlation (the CENTRAL fork)
Full analysis in §1.2-§1.3. The first-draft "watch the next per-employee terminal" design is
UNSOUND: the eligibility active-Chat factor does NOT prove single-turn-per-employee on the relay (a
neutral-pane human send injects native frames with no `chat_turns` row), and the child's terminals
are idless/un-owned on the raw-frame relay path (the `D-owned-turn-identity` demux lives only on the
legacy `GatewayChild` path). **Recommendation: Option A1 — `PoolStepGateway` reads the
`prompt.submit` ACK DISPOSITION (`streaming`/`queued`/`steered`, already returned) and owns the
correct terminal from it** (steered → errored no-terminal; streaming → own the current terminal;
queued → skip the predecessor's terminal, own the next), reconstructing the disposition half of
owned-turn identity without the demux. Needs no Hermes/`minds/` change. **Main must rule the
settlement approach before implementation — this is load-bearing and the orchestrator should
spot-check A1's terminal-ownership logic.**

### Collision #B — the automatic-step Playwright test has no ingress in the current test composition
Full analysis in §9.5. Relay test mode composes only pool+relay (no loops, no runner, no step
trigger — `server.py:216-244`, `testmode.py:45-99`), so the contract's required Playwright
"automatic step streams into the pane" (Acceptance §2, §4) cannot be driven. **Recommendation:
Option (a) — compose the real `EmployeeStepRunner` + `PoolStepGateway` in relay test mode and add a
`test_mode`-gated step-trigger route**, exactly the "add test machinery so Playwright drives the
REAL path against scripted frames" S2b established with its scripted child + compose path. **Main
must confirm this test machinery is in-scope** (as S2b's was).

### Collision #1 — the flag-off legacy worker child does NOT carry a usable `PLAN_TICKET_ID`; the fallback CONTRADICTS the contract and needs an owner override
Full analysis in §6.2 (Codex Finding 6). The legacy path is ONE shared `panels-worker` child
serving every ticket (`D-gateway-topology`); its spawn env sets NO `PLAN_TICKET_ID` and CANNOT.
Today's identity rides per-turn Hermes session env. A `PLAN_TICKET_ID`-only CLI breaks flag-off.
The natural fix (read `PLAN_TICKET_ID` first, fall back to Hermes-env) KEEPS the Hermes-env read
for flag-off — which DIRECTLY CONTRADICTS the contract's Acceptance §3 "Hermes-env reads absent from
the worker path (both compositions)." The contract asks for two things that cannot both hold on the
current topology. **Main must rule: authorize the flag-off Hermes-env fallback as an override until
S3b/S4 deletes the legacy path, OR accept flag-off `panels worker my-ticket` as broken until S3b,
OR another resolution.** The plan does not proceed on the fallback without the override.

### Collision #3 — no existing ticket-session writer fits a pool bind outside a running step; the CAS needs expected→candidate
Full analysis in §3.2 (Codex Finding 5). The step-lifecycle writers require `agent_running_step`; a
pool spawn/rebind can bind outside a running step. **Recommendation: the narrowest new call-only
`bind_pool_employee_session_id(conn, ticket_id, *, expected_stored_session_id,
candidate_stored_session_id, now)` in `tickets/data.py`, routed through the existing ownership CAS
(`write_employee_session_id_in_transaction`) with a post-write equality assert** (the CAS can
silently RETAIN on an expected mismatch, so the writer must assert the returned binding equals the
candidate and raise otherwise — fail-closed). The ticket analogue of S2b's authorized
`record_agent_session_key`. **Also a real S2b-adjacent defect to fix (not inherit): rebind
persistence is NOT inside a teardown guard** (`employee_child_pool.py:382-414`) — a failed persist
leaves the child alive with an unpublished fresh session and the old durable key pointing at a
closed session. S3's `rebind_fresh_session` must make that fail-closed.

### Collision #4 — the legacy worker gateway has SEVERAL live consumers (broader than day chat); it cannot simply be "not started" flag-on
Full analysis in §4.1 (Codex Finding 3). Verified consumers: day chat (via the
`EntityRoutingGateway` default), `/api/chat/commands`, `/api/chat/{entity}/status`, and ticket
`employee-session-history` — all consume the installed worker gateway. **Recommendation: reading
(a) — the legacy worker gateway STAYS running flag-on to serve these non-ticket surfaces, but holds
NO ticket/Chief session (the crossover guard + empty entity map keep every pool-owned entity off
it).** The contract phrase "the legacy worker gateway is never started" then reads as "never OWNS a
ticket employee session," not "the process is absent." The two-owner assertion (§4.2) and the loops
wiring (§2.2) are written for reading (a). **Main must confirm reading (a) or rule the wider cutover
(day chat + catalog + status + history to a pool/neutral path) in-scope.**

### WITHDRAWN — first-draft "Collision #2" (runner gateway typing) is NOT a collision
Codex Finding 10: the contract explicitly changes the runner's injected transport, and nothing
forbids the one annotation/Protocol adjustment the swap requires. It is ordinary authorized typing
work (§2.3). The Protocol lives in a `runtime/`-local contracts module so `minds/` is untouched.
Withdrawn.

### WITHDRAWN — first-draft "Collision #5" (settle-not-resume reading) is NOT a collision
Codex Finding 10: the existing code already resolves it — startup human-turn recovery EXCLUDES
tickets at `agent_running_step` (`server.py:157-178`), while running worker steps are explicitly
resumed (`loops.py` recovery, `D-runtime-restart-continuation`, `decisions.md:426-433`). Pool-owned
stale HUMAN turns settle; worker-step recovery resumes. No owner clarification needed. §4.3 states
the resolution directly. Withdrawn.

---

## 13. What earns its existence (PRINCIPLES check)

Every added surface maps to an explicit contract clause: `PoolStepGateway` + the pool turn-observer
seam (Worker steps through the pool); per-ticket adoption + `bind_pool_employee_session_id`
(Ownership handoff — adoption + fail-closed persistence); the composition split + generalized
assertion + widened guard (Ownership handoff — no legacy worker gateway, extended assertion, guard
coverage, settle-not-resume); the CLI `PLAN_TICKET_ID` read + by-ticket-id route (`panels` CLI
identity); the ticket neutral pane + generalized capability signal (Ticket pane cutover). Nothing
speculative: no new eligibility factor, no admission gate (`D-native-turn-concurrency`), no relay
protocol change, no new Hermes-side owned-turn id (A1 uses the existing disposition), no db schema
change, no idle reaping (out of scope), no deletion of legacy machinery (S4). `runtime/` is touched
only for the one annotation line the gateway swap requires plus the `runtime/`-local `StepGateway`
Protocol; `tickets/data.py` only for the narrowest ownership-CAS writer the fail-closed bind needs
(Collision #3); `minds/` is UNTOUCHED. Four open items need main's ruling before implementation:
Collision #A (settlement approach — recommend A1), Collision #B (test ingress — recommend (a)),
Collision #1 (CLI flag-off fallback — needs an owner override), Collision #4 (worker gateway stays
for non-ticket surfaces — recommend reading (a)); Collision #3 is a recommended in-scope addition
mirroring S2b; two first-draft collisions (#2 typing, #5 recovery) are withdrawn as not genuine.
