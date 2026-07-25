# Orchestration rework — package plan

Branch `simplify/orchestration-rework` off local staging e23a36c4. Part of the Panels
simplification program. This package rebuilds the worker-orchestration side against the
new conversation contract (`src/planner/conversation2/contracts.py`). The real
conversation system is built in parallel by a sibling; everything here codes against the
contract and the in-memory fake. Zero Alembic revisions in this package. Nothing under
`src/planner/conversation2/` or `src/planner/conversation/` changes except recorded
mechanical fallout. Commits stay local.

## What exists today (facts the plan builds on)

- The loop path: `AutomaticEmployeeStepDiscoveryLoop` (thread, timer + change-signal
  wake, machine lock) → eligibility check → `EmployeeStepRunner.try_run_automatic_step`
  → `claim_automatic_employee_step` (status flip to `agent` under BEGIN IMMEDIATE with
  transaction-time eligibility re-check) → `employee_step_runs` row →
  `AcpStepGateway.run_ticket_step` (old conversation hub, worker-context
  prepare/acknowledge, watch `tracked.completion`) → settle → resting-status writers.
- Boot: `_settle_stale_employee_steps_after_ticket_handoff` +
  `_recover_running_ticket_steps` (re-drives `agent` tickets with a recovery prompt).
- Revision: `POST /tickets/{id}/return-for-revision` → `reserve_revision` park/release
  choreography on the runner → guidance prompt through the same gateway.
- The permission broker rejects worker-origin asks unless a settlement guard is
  installed; today `composition.py` installs `AcpStepGateway.guard_worker_permission_settlement`.
- Ticket columns that already exist (no migration needed): `employee_backend` (NOT
  NULL), `employee_launch_model`, `employee_launch_reasoning_effort`,
  `employee_session_id`. `pending_worker_context` table + `SqliteWorkerContextService`
  (prepare/acknowledge, revision-guarded deletes).
- Board rows: `/api/board` (async route) → `board_view` → per-card `agent_working`
  (fusion of `ticket_status == agent` OR projection activity) and `agent_reply_state`
  (`ticket_conversation_projections`, acknowledge-on-ticket-open endpoint).
- Live freshness: commit-time contentless change signal → SSE → TanStack invalidation;
  the same signal wakes the loop (`core/loops.py` subscribes `loop.wake`).
- Anti-rename guard tests exist that pin the OLD vocabulary and forbid readiness names
  (in `test_automatic_employee_step_discovery_loop.py` and
  `test_automatic_employee_step_eligibility*.py`); they die with those files.

## The new shape

### 1. Readiness loop with optimistic start (ruled 7a, 7c)

`EmployeeStepRunner`, `AcpStepGateway`, `step_gateway.py`, `employee_step_repository.py`
writers, watch-and-settle, boot recovery, stale-step settlement, and the revision
park/release choreography are deleted. The `employee_step_runs` table is NOT dropped (no
migration here; closeout drops it). `core/db.py` PRE_ALEMBIC fingerprint lists keep it.

New per-ticket flow, owned by the rewritten loop. Thread shell keeps the timer +
change-signal wake + machine lock. The per-ticket flow is a plain async function
(unit-testable against the fake with no thread gymnastics). The poll thread does NOT
wait on sends serially: it schedules one independent task per ready ticket onto the
server's asyncio loop (`run_coroutine_threadsafe`, futures tracked, an in-flight
ticket-id set prevents duplicate scheduling — the claim flip is still the real
arbiter). The contract states no latency bound on delivery fates, so one stalled
backend must not block other tickets or wake processing. Shutdown: stop the poll
thread, then wait for in-flight tasks up to the deadline BEFORE releasing the machine
lock; tasks past the deadline are abandoned (process exit; crash-mismatch semantics
cover them). Nothing waits for turn endings.

1. Readiness check (read-only pre-filter): today's day membership, non-terminal stage,
   not user-owned, `ticket_status == empty`, stage has a gating field, no pending parked
   proposal, ceiling/at-cap, closeout-lane free. The one dead condition:
   `running_exists` (step runs). Closeout-lane occupancy stays status-based. Plus the
   ruled occupancy check (7a: "all checks (readiness, occupancy) BEFORE sending"): if
   the ticket has a conversation link, `ConversationSystem.is_running(link)` must be
   false — an occupied worker is skipped this pass, and queueing remains the success
   path only for collisions that slip past this check.
2. ONE guarded atomic flip out of `empty` to the stage's departure status — `agent` for
   worker-owned stages, `paired` for paired-owned stages (`resting_ticket_status` of the
   ownership mode maps worker→empty and is NOT this; the flip target is the status the
   ticket occupies while the step is out). The flip re-runs the readiness check under
   BEGIN IMMEDIATE; the DB picks one winner. The flip IS the claim; no claim stamp, no
   step-run row. One canonical writer: `claim_ticket_for_worker_step` (replaces
   `claim_automatic_employee_step`).
3. If the ticket has no conversation link: `worker_resolve` → contract
   `start_conversation` → one Panels transaction writing the ticket's conversation link
   AND last-chosen backend/model columns. Ordered so the ticket never references a
   conversation that does not exist; a crash between creation and the link write leaves
   an orphaned conversation record, which is harmless and accepted (recorded reading of
   the one-transaction ruling under the contract boundary).
4. Compose the opening message in the loop: the step prompt (today's
   `_next_step_prompt` text, worker/paired variants) plus pending worker context
   (prepare via `SqliteWorkerContextService`, text included in the prompt body).
5. `send(conversation_id, text, sender_label="loop", mode=run_when_free)`.
6. Fate handling: `PromptDeliveryStarted` and `PromptDeliveryQueued` are both success —
   acknowledge the pending-context receipts, done (package brief's explicit ruling;
   the residual risk that a held prompt's later dequeue-delivery fails after its
   context rows were acknowledged is recorded, and softened by the revision-guarded
   deletes: a context re-write during the window survives). `PromptDeliveryRefused` →
   guarded revert of the flip back to `empty` (second canonical writer:
   `release_worker_step_claim`) + ONE traced error-log line. The revert guard is a CAS
   on the pair (departure status, `ticket_status_changed_at` captured at the flip) so a
   delayed revert can never erase a later legitimate transition that happens to land on
   the same status (ABA); same-second aliasing is accepted and recorded. Revert scope
   is strict: only a refused fate, or an exception raised BEFORE the send reported a
   success fate. After Started/Queued the delivery is irreversible — an acknowledge
   failure there is traced and never reverts (reverting would re-arm the ticket for a
   duplicate send). A process crash mid-flight leaves the honest status-vs-liveness
   mismatch (`is_running` is the live answer); no recovery machinery.

Nothing watches turn endings. Tickets move only via explicit actions (proposal filing,
approval, take-over — untouched writers). `finish_run_if_still_running_step`,
`release_run_claim_to_empty_if_still_running_step`, `mark_run_errored_if_still_running_step`
die with their only callers.

`running_exists` call sites outside runtime:
- external-work writer (`tickets/data.py:966`): the admission set already excludes
  `agent`, so the status side is covered; the case the old check really guarded is a
  LIVE PAIRED turn (admission permits `paired`). Replacement: the async route checks
  `is_running(link)` before calling the writer and rejects with already_running while
  live. Same route-level guard for `delete_ticket` (whose combined check collapses).
  Racy by nature (check-then-write across the async boundary) — accepted for the
  one-person model and recorded; under the interim fake, old-layer paired turns are
  invisible to the check, correct at swap.
- `return_for_revision` writer: drop the check entirely — the status guard in
  `decide_return_for_revision` (rejects `agent`) covers the worker case, and busyness
  is never an error under the contract (a busy worker queues).

Revision path, rewritten in place (same route, same Review button), ordered
validate → send → commit because flip-first cannot be honestly reverted: the decision
DELETES the pending proposal, so a revert to `awaiting_approval` would leave nothing
to approve. New order: the route validates first (ticket readable, link present,
`decide_return_for_revision` passes on a read snapshot — no link → fail before
anything changes), then sends the guidance (`sender_label="owner"`, run_when_free,
pending context prepared and acknowledged exactly like loop sends), and only on a
success fate runs the writer (decision + flip `awaiting_approval → agent`, minus the
step-run check). Refused → HTTP error carrying the refusal reason, nothing changed,
retry clean. The residual window — send succeeded but the writer then fails (rare DB
error or a concurrent status change) — surfaces as an HTTP error with the proposal
intact; a retry may deliver the guidance twice, which is benign and visible, and
strictly less destructive than proposal loss. `reserve_revision`,
`EmployeeRevisionRunner`, `EmployeeRevisionHandoff`,
`TestModeAcceptingEmployeeRevisionRunner`, and the `app.state.employee_step_runner`
DI die. The test-mode `/api/test/run-step/{ticket_id}` route is remapped to invoke the
new per-ticket start flow against the composed fake (its Playwright callers are e2e,
not run here — semantic drift is a recorded carry-forward for the swap).

### 2. Conversation start wiring — the two resolve functions (ruled)

New module(s) under `src/planner/runtime/`:

- `worker_resolve(ticket, overrides)` — the ruled public shape; settings and workspace
  resolution are internal (injectable seams for tests only). Layering: worker-type
  defaults (managed worker settings `launch_defaults`, file-backed, already
  UI-settable) → ticket's last-chosen columns
  (`employee_backend`/`employee_launch_model`/`employee_launch_reasoning_effort`) →
  explicit overrides. Role materials: the ticket-worker role directive text +
  identity env (`PLAN_ACTOR=worker`, `PLAN_TICKET_ID=<id>`). Compute-only.
- `agent_resolve(overrides)` — chief defaults (managed chief settings, file-backed,
  UI-settable) → overrides. Role materials: chief directive + `PLAN_ACTOR=chief`.
  Compute-only.
- Start action: mint a fresh caller-owned conversation id, `start_conversation`, then
  one Panels transaction writing link + last-chosen columns (new simple writers in
  `tickets/data.py`; the old CAS machinery `write_employee_session_id_in_transaction`
  stays untouched for the old layer until swap).
- Send wrapper for ticket conversations: a send that carries a
  `model_change`/`reasoning_effort_change` and reports `PromptDeliveryStarted` also
  updates the ticket's last-chosen columns (owner-ruled: New must start from whatever
  the conversation currently was). STARTED ONLY, not Queued — the contract is explicit
  that a held change applies only when that prompt later runs and a refused delivery
  changes nothing, so recording at queue time could record a model the conversation
  never adopts. Conservative deviation from the brief's looser "reports success",
  recorded; latent either way (no production caller passes changes in this package —
  the UI does at swap, where queued-change reporting can be revisited).
- New-conversation reset: `kill(old_id)` + clear the ticket's link immediately and
  durably (last-chosen columns are NOT cleared — they are exactly what New starts
  from). No UI caller until swap; built and tested now because it is the ruled New
  semantics of this wiring. The contract gap originally flagged here (interrupt lets
  held prompts run, so New could not fully kill the old worker) was CLOSED mid-package:
  staging commit f6d7d737 added `kill(conversation_id)` — stop the turn AND discard
  held messages, docstring naming New as its caller — merged into this branch as
  0c5a1b44, and the wiring adopted it.

Storage decision (no migration available, and none needed): the ticket's conversation
link IS the existing `employee_session_id` column — under the new system it stores the
caller-owned conversation id. The last-chosen columns ARE the existing three launch
columns; their semantics change from "historical first-session request" to "kept up to
date" per the owner's later ruling (docstring updated; schema untouched). The
`employee_configuration_editable` freeze gate is left untouched (its full condition
also involves stage, status set, and the old binding row; its New-era behavior is a
swap-time concern, not this package's).

Interim collision, accepted and flagged: until swap, the OLD conversation layer also
writes `tickets.employee_session_id` (ACP session ids via the binding repository CAS)
when the browser drives a ticket conversation through the old pane. Whichever wrote
last wins. Consequences both ways are interim-only warts: a loop send aimed at a
stale old-layer id is refused (`no_such_conversation`) → revert + traced line; and an
old-layer overwrite AFTER a new-path link write can leave a fake conversation running
unlinked while the ticket points at the old ACP id (nothing real is lost — the fake
holds no real work, and the swap deletes the old writer). Nothing deployed runs in
this window (program deploys at cutover, after swap).

### 3. Row signals (ruled)

Three read-time signals, each named for whose fact it is, no fusion:

- `agent_working` = `ConversationSystem.is_running(link)` — awaited in the async board
  route for linked cards; false when no link. The old fusion (status/projection) is
  removed from this field.
- `needs_me` = `ConversationSystem.has_pending_permission_ask(link)` — new field, new
  PURE WHITE dot, visually distinct from every other mark state.
- `reply_waiting` = a turn-ended notebook line past the browser-local seen watermark.
  The server-side "latest turn-ended line per conversation" read belongs to the
  sibling's transcript surface and does not exist yet. This package builds the
  browser-local watermark module (localStorage, KEYED BY CONVERSATION ID — sequences
  are per conversation, so a per-ticket key would let an old conversation's high
  watermark suppress a new conversation's replies after New; a fresh conversation id
  has no watermark = 0 = unread, exactly the ruled over-show semantics; the value is
  the last-seen notebook LINE NUMBER — a position, never a clock; mid-turn output
  never makes a thing unread) with unit tests, and keeps the OLD reply machinery
  (`ticket_conversation_projections`, `agent_reply_state`, acknowledge endpoint)
  feeding the reply dot until integration — no gap. The board card gains the ticket's
  conversation id (the link column, already selected for the signal enrichment) so the
  client can key the lookup. SEAM (flagged for integration): when the transcript
  surface lands, board rows gain the latest turn-ended line number, the mark computes
  `reply_waiting = latest > watermark`, the ticket pane writes the watermark, and the
  projection machinery + acknowledge endpoint die.

Mark precedence: `needs_me` (white dot) > `agent_working` (spinner) > reply dot — a
turn waiting on an ask is still running, and the ask is what the owner must act on.
The enrichment lives in the async route; `board_view` stays a pure DB read and adds
the link column to its SELECT and card payload (the frozen board-card key test gains
the enrichment keys). Freshness rides the existing contentless change signal +
refetch; no watch machinery, no status writers for asks. RECORDED INTEGRATION
ASSUMPTION: the real conversation system persists its events in the same Panels
database through the shared connection door, so its commits emit the change signal
and the board refetches; the interim fake makes no DB writes, so fake-only state
changes don't push a refetch — irrelevant in production interim (nothing drives the
fake but the loop, whose claim flip itself commits and signals) and tests drive their
own fetches. Verify the real system's writes ride the signalling connection at swap.
`needs_user` the STATUS is untouched.

Interim visibility, accepted and flagged: `agent_working`/`needs_me` read the interim
fake, which only ever contains loop-created conversations, and a fake turn never ends
on its own — so a loop-started ticket shows the spinner until an explicit action, and
old-pane activity shows no spinner. Honest to the ruled design; real values arrive at
swap; nothing deploys in between.

### 4. Naming pass (ruled map, conservative reading)

Renames across src/ and web/ where the old names name THIS machinery:
- discovery loop → readiness loop (`WorkerStepReadinessLoop`,
  `runtime/worker_step_readiness_loop.py`); eligibility decision → readiness check
  (`runtime/worker_step_readiness.py`, `is_ready_for_worker_step`); the wake is already
  the generic change signal (nothing named "eligibility wake" survives to rename).
- employee / "Automatic Employee" → worker in machinery identifiers, prose, docstrings,
  comments, UI copy, config.yaml comment text, `core/contracts.py` event-kind cleanup
  for kinds only the dead machinery produced.
- resolution engine → proposal resolver: prose/docstring occurrences only (no
  `ResolutionEngine` identifier exists; `tickets/logic/resolution.py` module name may
  become `proposal_resolver.py` if fallout stays contained — implementer judgment,
  recorded).
- `EmployeeStepRunner` and `employee_step_runs` die outright, never renamed.

FROZEN (each with reason, enforced by the sweep gate's allowlist):
- DB table/column names and every SQL literal touching them (`employee_session_id`,
  `employee_backend`, `employee_launch_*`, `employee_conversations`,
  `employee_configuration_catalog_cache`, `pending_worker_context.worker_entity_id`
  etc.) — column renames need migrations; this package ships none.
- HTTP wire field names bound to those columns (`employee_backend`,
  `employee_launch_model`, `employee_launch_reasoning_effort`, `employee_session_id`,
  `employee_backends`, `employee_id`, the `/api/employee-configuration-*` routes) and
  the Python/TS identifiers that ARE those wire names — splitting code names from
  storage/wire names would create two vocabularies for one thing. Flagged as the
  package's named vocabulary debt; the closeout migration slot can retire it.
- Managed settings JSON file keys (renaming breaks existing on-disk settings).
- `src/planner/conversation/**` and its web twins (`web/src/lib/acp/**`,
  `web/src/components/acp/**`, `AcpConversation.svelte`) — out of scope, dies at swap.
- Ownership-mode/status/turn vocabulary that happens to reuse the words (traps recorded
  by the statuses package).
- `skills/**` — parked system, untouched.
- CLI flag `--employee-backend` and `ticket set employee-backend` field: kept (they
  post the frozen wire field; renaming the UX while the wire stays is the two-vocabulary
  trap again). Recorded as the same vocabulary debt.

The old anti-rename guard tests (which pin old vocabulary and forbid the readiness
names, including a byte-exact config.yaml line and docs content) die with the files that
carry them. Lesson applied: the new naming-sweep completeness gate is a RUN-AND-SHOW
script, not a committed forever-test — permanent vocabulary guard tests are exactly what
rotted here.

Docs (kept-current rule, targeted): the orchestration-owned pages
(`docs/employee-runtime.md` → worker-orchestration equivalent, mentions in
`docs/systems.md`, `docs/tickets-and-gates.md`, the workspace-mark section of
`docs/frontend.md`) are rewritten to describe the new machinery; the root
`AGENTS.md`/`CLAUDE.md` paragraphs describing the runtime split are updated; the
conversation-pane docs stay untouched (sibling's, dies at swap).

### 5. Interim composition

At startup (production and test mode alike) the server composes
`InMemoryConversationSystem` as the ConversationSystem stand-in
(`app.state.conversation_system`), clearly marked as the interim stand-in the real
system replaces at the program's swap step. All access is serialized on the server's
asyncio event loop (async routes await directly; the loop thread goes through
`run_coroutine_threadsafe`). `start_background_loops` takes the system + loop handle.
The old `ConversationComposition` keeps serving the browser pane and WebSocket,
unchanged except: `step_gateway` construction and its permission-guard wiring are
removed. Mechanical fallout, recorded: without a settlement guard the broker REJECTS
worker-origin asks, so composition installs a permissive interim guard (mark settling,
return True) with a comment naming it interim-until-swap — the ruled death of the
ticket-state cross-check without editing broker internals that die at swap anyway.

## Tickets (four, serial, one worktree)

- **A — start wiring**: resolve functions, start action, send wrapper (last-chosen
  update rule), New reset, link/last-chosen writers in `tickets/data.py`; unit tests
  against the fake. No deletions. Gates: new tests + focused
  `pytest tests/unit/test_conversation_start_*` + ruff/mypy on touched files.
- **B — loop rewrite and deletions**: new readiness loop + readiness check, claim/revert
  writers, revision path rewrite, interim composition, permissive interim guard,
  test-route remap, all deletions (runner, gateways, step-repo, recovery, stale
  settle, testmode revision stub, runtime contracts), test rewrites/deletions. Gates:
  new loop tests (claim-flip atomicity with two concurrent claimers on one SQLite
  file; delayed-release-after-ABA does not fire; occupancy skip; revert-on-refusal;
  queued-counts-as-success; pending-context acknowledged only after success and never
  reverted after success; revision path success/refusal/no-link), full unit suite,
  ruff, mypy.
- **C — row signals**: board enrichment (`agent_working` re-sourced, `needs_me` added),
  mark precedence + white dot CSS, watermark module + tests, board-view/e2e-adjacent
  unit test updates. Gates: row-signal tests against the fake, web check/tests/build,
  unit suite.
- **D — naming pass + docs + sweep gate**: the rename tiers above, docs updates, sweep
  script run with output as evidence. Gates: sweep output, full unit suite, ruff, mypy,
  web check/tests/build.

Each ticket: Opus implementer; steps 2–5 of the per-ticket pipeline collapsed to
implement + my inspection for A and C (small, contract-scoped), full plan review
already covered by this package plan's codex review; B and D get my line-level
spot-check of the load-bearing writers and the sweep allowlist. One codex review of
this plan before dispatch; one codex review of the combined diff before final gates.

## Final gates (package)

`ruff check .`; `mypy src/ tests/typing/`; `pytest tests/unit` (full); `npm --prefix
web run check`, web unit tests, `npm --prefix web run build`; the named new test
groups; the naming-sweep script output. No `./verify` (reserved for program end).
Playwright e2e is NOT run here; the board-indicator e2e group will need remapping at
swap/integration (carry-forward, same posture the statuses package recorded).

## Delegated choices (recorded)

1. Conversation link storage = existing `employee_session_id` column; last-chosen =
   existing three launch columns with re-ruled semantics. No migration, no new columns.
2. Flip targets: worker-owned → `agent`, paired-owned → `paired` ("stage's resting
   status" in the ruling = the status occupied while the step is out, not
   `resting_ticket_status()`'s after-step value).
3. Revision path kept and rewired, ordered validate → send → commit (flip-first would
   destroy the pending proposal on revert; duplicate-guidance on a rare post-send
   writer failure is the accepted, benign residue). Pending context included +
   acknowledged like loop sends.
4. `running_exists` guards → route-level `is_running(link)` pre-checks for external
   reconcile and delete (the live-paired case), dropped for return-for-revision
   (status decision + queue semantics cover it).
5. Sender labels: `"loop"` for readiness-loop sends, `"owner"` for revision guidance.
6. Loop stays a thread; per-ticket flows are independent asyncio tasks on the server
   loop (in-flight set; no serial waiting; bounded drain before lock release at stop);
   fake composed in test mode too.
7. Permissive interim settlement guard at composition rather than editing the broker.
8. Mark precedence needs_me > agent_working > reply dot.
9. Naming freeze set as listed; sweep gate is a script run, not a committed test.
10. Interim collision on `employee_session_id` and interim signal visibility accepted
    and flagged (nothing deploys before swap).
11. Occupancy pre-check `is_running(link)` restored to the loop's pre-flip checks
    (7a); queued remains success only for slipped collisions.
12. Claim revert guarded by CAS on (departure status, `ticket_status_changed_at`);
    revert scope limited to refusal or pre-success exceptions.
13. Last-chosen columns update on `PromptDeliveryStarted` only, never Queued
    (contract: a held change applies only when the prompt runs).
14. Reply watermark keyed by conversation id, not ticket id.
15. `/api/test/run-step` remapped to the new per-ticket start flow.

## Codex plan-review dispositions (gpt-5.6-sol, round 1)

Blockers: B2 (occupancy check) ACCEPTED → choice 11. B3 (ack on queued) REFUTED AS
RULED — the package brief states "acknowledged only after the send reports started or
queued"; residual dequeue-loss risk recorded in §1 step 6. B4 (queued model changes)
ACCEPTED → choice 13. B5 (New vs held prompts) ACCEPTED AS CONTRACT GAP, then CLOSED
mid-package by the sibling's `kill` extension (staging f6d7d737; wiring adopted it). B6 (paired guards) ACCEPTED → choice 4. B7 (revision revert loses
proposal) ACCEPTED → choice 3. B8 (catch-all revert crosses delivery boundary)
ACCEPTED → choice 12. B9 (editable-again claim) ACCEPTED — factual overstatement
removed. B1 (start not atomic with link) REFUTED AS RULED — the package brief
explicitly records the orphan-row reading of the one-transaction ruling under the
contract boundary; the crash-between-link-and-send case lands in ruled 7c mismatch
territory (owner recovers manually).

Majors: M1 (ABA release) ACCEPTED → choice 12 + a delayed-release test. M2
(head-of-line blocking) ACCEPTED → choice 6. M3 (lock release with sends in flight)
ACCEPTED → choice 6 (bounded drain). M4 (freshness bridge) ACCEPTED AS RECORDED
ASSUMPTION → §3, verify at swap. M5 (test route) ACCEPTED → choice 15. M6 (collision
orphans a live conversation) ACCEPTED — wording strengthened in §2, interim-only. M7
(watermark key) ACCEPTED → choice 14.

Minors: all three ACCEPTED (external-work description corrected; board link source +
pure-view boundary specified; resolve signatures restored to the ruled
two-function shape).
