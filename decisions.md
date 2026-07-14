# decisions.md

Every delegated or judgment call, briefly justified. This file exists so a real rationale — the
*why* behind a call that isn't visible in the code — isn't re-litigated later.

## How this file is organised

Entries are grouped **by system/topic**, and each carries a **stable slug ID** (e.g.
`D-lifecycle-gates`) instead of a sequential number. Slugs, not numbers, because sequential
numbering rotted: two branches independently minted overlapping `D77`/`D99`/etc., so the old numbers
collided and any external `Dxxx` reference below ~D28 was already ambiguous. A slug names what the
decision is about and never collides. **New entries:** add them under the right system heading with a
descriptive slug; don't renumber anything.

External references to the old `Dxxx` numbers still resolve through the **[Superseded / merged IDs
map](#superseded--merged-ids-map)** at the bottom of this file.

Some entries carry a `// migrate to docs/ticket-types.md when it lands` marker: their substance is
plain-language documentation of how the ticket-type machinery works and will move to that doc once it
exists. They are kept intact here until then so nothing is dropped before it has a home.

---

# Tickets, gates, and the resolution engine

## D-lifecycle-gates — Kickoff plus five worker stages are ordinary gated fields

The ticket lifecycle is `needs_kickoff → needs_success → needs_approach → needs_plan →
needs_implementation → needs_closeout → done`, plus `dropped` from any point. Every non-terminal
state gates exactly one field through the same machinery: gate, proposal, acceptance, scope, event,
visual state. There is no exceptional tail — Implementation and Closeout are ordinary gates like the
rest, and Kickoff is the ordinary *first* field (the human-approved intake context), not a special
compound proposal. `done` is terminal and has no field.

Kickoff is why this matters structurally: a new ticket atomically parks a Kickoff proposal and stays
awaiting approval, so readiness can never dispatch an unapproved intake. The ticket **title** is
separate editable metadata from creation, edited through the ordinary title PATCH independently of any
stage — no title-proposal type is justified (it only ever duplicated the stored title). Direct
operations may jump between *worker* stages but cannot bypass or re-enter Kickoff; workers never jump.

## D-lifecycle-migration — Migration reads approval intent from the legacy gated state, not the control status

`ticket_status = awaiting_approval` is stage-agnostic: it says the ticket is waiting on *some*
field, not that Result/Implementation is the pending one. So the lifecycle migration classifies the
pending field from the legacy *state*: only legacy `in_progress`/`needs_review` rows reconstruct a
Result→Implementation proposal; earlier `needs_success`/`needs_approach`/`needs_plan` rows keep their
own pending proposal. A later-stage row's old Kickoff note migrates as a *settled* Kickoff value with
no proposal, because it's already past Kickoff. This is the smallest status-sensitive exception that
preserves active work instead of silently treating a not-yet-reviewed revision as approved.

The migration convention throughout: recover valid legacy data, reject only corrupt JSON or broken
referential integrity — never edit canonical data to make a migration pass. A live restart proved the
fixture-only tests missed two production realities (see [D-migration-boundary]).

## D-migration-boundary — Repair migrations at the migration boundary, on the real data shape

Two production-only assumptions failed on the first live restart after the lifecycle rename, and the
fix rule generalises: (1) `awaiting_approval` is a Ticket-wide control status, not proof of the
pending field — classify from the legacy state as well; (2) a table rebuild cannot drop `tickets`
under production foreign-key enforcement while `day_tickets` has rows — disable FK enforcement only
across the drop/rename swap, then restore it and run `foreign_key_check`. The migration must accept
the real valid data shape; no canonical data is edited around it. Fixed inline (not as a worker
ticket) because the server could not start and the change is confined to the migration plus its
regression seam. The live migration also holds the exclusive write lock across snapshot→copy→swap so
a concurrent write can't lose data.

## D-single-writer-edit — One canonical writer per transition; a compound edit is atomic

Every `fields.*.value` write goes through the resolution engine and the sole appender
`_apply_decision` — agents file proposals only; the engine is the one door. Two extensions of the
human write surface, both routed through that same door:

- **Human field-value edit.** A human may edit an already-*settled, passed* field value directly
  (`decide_edit_value`, guarded: require_human → validate_body → reject dropped/unset/live-proposal →
  reject a not-yet-passed field). A field is *passed* iff the state it gates strictly precedes the
  current state. This emits its own `field_value_edited` event — never a reused `proposal_accepted`,
  which would make the event log lie.
- **Compound Ticket PATCH is one atomic edit.** When one request edits several Ticket attributes,
  every value and its events commit together or none do; a no-op touches nothing. Per-field events
  still fire, but only for actual changes, in stable order. Chief external-work reconciliation stays a
  separate operation — superficially similar multi-column write, but different authority and meaning.

## D-scope-onward — Scope is one shared "approved until … then stop/propose" contract

A ticket's scope (how far a worker may go before waiting for the human) is `(ceiling, at_cap)`,
edited through one shared picker whose default is "next stage, then stop." The ceiling options offer
the current stage and every later one, never an earlier one, so no control can approve back past where
the ticket already is — one centralized option list feeds both the header scope row and the approval
grant picker so they can't drift. Every gating approval (Kickoff included) uses this same onward-scope
contract. The default-reset logic lives in the shared picker so a previous approval's choice can't leak
into the next same-stage proposal.

## D-field-circles-derived — Field/stage visual state is lifecycle-derived, not value-derived

A ticket field's circle (completed / current / upcoming) is a pure frontend derivation over the
loaded ticket: it ignores whether the field happens to hold text. Completed = the lifecycle has
passed that field; current = it's the active gate; upcoming = not yet reached. The current
awaiting-approval gate is shown as its own state before the generic passed-field check, because the
visual model needs to mark the human approval point even though the field is technically editable.

## D-derived-sprint-item-status — Sprint-item status is derived; the API keeps it read-only

The database stores only item fields and placement. `todo`/`in_progress`/`blocked`/`done` is derived
from child tickets, worker/control status, and blocking links — there is no writable/proposal
lifecycle for sprint-item status. Serializers keep returning `status` as a read field so callers
don't break. Deferred is *placement*, not status (legacy `deferred_next_sprint` → backlog,
`sprint_id = NULL`); legacy `blocked_by` JSON migrated into `links(kind='blocks')`. The universal
`done`/`dropped` bookend states are shared by every ticket type by validation, so status derivation
keys off those bookends, not a coding-specific state table.

## D-blockers-one-model — Blockers are one typed read model and one active relationship

`blocks` is the only explicit Ticket relationship; `tickets.sprint_item_id` stays canonical
membership. `done`/`dropped` sources are cleared; reopening validates active cycles; a source-state
change reports affected targets. Ticket, worker/CLI, Sprint planning, and the read-only Ticket UI all
consume one typed blocker summary — the UI adds no editor or graph, and Sprint-item targets navigate
by selecting the existing Sprint row. Detailed dispositions live in
`orchestration/tickets/t_bqxt44fb-blockers/`.

## D-hard-delete — Permanent ticket deletion compacts Planner history, not Hermes storage

A permanent delete is a deliberate human-only exception to the append-only event rule. The
transaction removes the ticket, placements, links, Panels chat rows, and every prior Planner event
owned by or referring to it, then writes fresh cleanup doorbells and one minimal `ticket_deleted`
audit. It is refused while ticket control or any Panels chat turn is still running (avoids a
completion race against removed rows). The stored Hermes session is outside the approved Panels-record
scope and stays in Hermes storage; once the ticket row is gone, Panels has no route that can resolve
it. The delete writer and `panels ticket delete <id> --yes` command stay, but no delete control is
exposed on standalone or Workspace ticket screens.

## D-external-work-intake — Chief external-work is an explicit atomic ticket operation

Work done outside Panels is reconciled through two explicit `panels chief` commands (reconcile an
aligned ticket, or create a populated one). It is not a new domain object, generic state setter, or
worker power: it reuses canonical ticket fields, notes, scope, status, events, and readiness, and
applies the complete imported state in one `BEGIN IMMEDIATE` transaction after checking proposals,
active control, and chat turns under that lock. `PLAN_ACTOR` is an operational boundary for the
trusted single-user runtime, not an auth credential: ordinary product operations are unattributed
(absence of an actor header is not proof a human acted), worker calls stay attributed and gated, and
external-work routes require the explicit Chief role.

## D-imported-ticket-note — Legacy imported ticket body becomes the ticket user note

Legacy seed ticket body text imports into the ticket-level `user_note`, not any gated field note.
The body is intake context for the ticket as a whole; keeping the field notes empty avoids mixing
preserved context with canonical gated outputs or step-specific direction.

## D-review-ticket-only — Review contains Ticket decisions and running workers

Review is focused on parked Ticket proposals awaiting a human decision and the current running-worker
count. The unused Sprint-item approval branches and overdue digest are deleted: item approvals already
return no entries, and the frontend has no overdue contract or consumer. The current `/api/queues` route,
`queues` resource key, and generic Queue names become Review names in the same replacement; no compatibility
adapter preserves the shallower model.

---

# Worker types (the current implementation calls these ticket types)

## D-worker-type-language — Worker type is the domain name

The required classification chosen when a Ticket is created is its **Worker type**, not its Ticket
type. It names the kind of AI employee assigned to the Ticket and therefore determines the staged
workflow and specialist behavior used to work it. Existing `ticket_type` fields, `ticket_types` modules,
and prose are implementation debt to rename as part of the deepening program; they do not justify a
second domain term or a compatibility alias.

## D-worker-type-immutable — Worker type does not change after Ticket creation

Worker type is immutable once the Ticket is created. `needs_kickoff` is not a safe mutability window:
creation has already built Worker-type-specific fields and scope, and a rejected kickoff can be revised by
a worker or discussed through Chat while the Ticket remains at that Stage, creating durable session and
transcript state. A safe exception would need a new “never used” invariant and a reset protocol for fields,
scope, sessions, transcripts, and pending context. That machinery has not earned its existence. The choice
can change before creation; afterward, correcting it means replacing the Ticket.

## D-stored-stage-is-authoritative — Reading a Ticket's Stage is a direct value read

A Ticket stores its current **Stage** directly. Anything that only wants to display, compare, or return
that Stage uses the stored value; it does not consult the Worker type registry, resolve a worker, or accept
a second type parameter. The Worker type's workflow is needed only to interpret relationships or rules
around the Stage, such as its gated field or successor. Existing lifecycle uses of `state`, `TicketState`,
and `ticket.state` are naming debt to address in the deepening program; Ticket control status remains a
separate concept.

// migrate to docs/ticket-types.md when it lands
## D-ticket-types-registry — The type registry is a sibling domain that imports only leaves

The ticket-type machinery lives at `src/planner/ticket_types/` — a first-class sibling domain, split
contracts/logic/registry/coding like every other domain. It imports ONLY the two leaves
`tickets/contracts` + `core/contracts`; nothing imports back (allowlist-tested over the package).
**No-cycle seam:** the validator's reference catalogs (known specialist-skill ids, toolset-profile
ids) are *injected into the constructor*, not imported — specifically to avoid importing `minds/config`
(not a leaf), which would plant a latent cycle once the gateway consumes worker profiles from
`ticket_types`. The engine is definition-driven and string-id-native: generic functions take a
`WorkflowDefinition` and index its tables instead of module-level constants, so heterogeneous
field-sets and non-enum state ids cost nothing. Production ships **coding only**; `probe` is a
test-only synthetic type that proves genericity. **Commit cadence:** each `t_tt*` ticket commits
straight to `main` once its full `./verify` passes, so the DB migration is never stacked on
uncommitted work.

// migrate to docs/ticket-types.md when it lands
## D-ticket-types-no-second-prod-type — No second production type until every coding-default path is threaded

The codec gates on field-*set*, not type-id, so a coding-shaped second type could silently reach the
coding-default engine paths. Deferral of full genericization was safe ONLY because production
registers `coding` alone. The binding rule: no second production type until the coding-default paths
(engine, propose/accept/drop/scope drive path, external-work, direct-scope prefix) are all threaded
with the row's own definition. Migration audit policy is strict-on-missing, lenient-on-extra: it
ignores extra top-level keys (live rows carry a legacy `result` key) but rejects unknown type / bad
state / bad ceiling / missing declared field / malformed slot. The DDL keeps `state DEFAULT
'needs_kickoff'` (a universal bookend) but drops any ceiling default (`needs_success` is coding's, not
type-independent — a raw insert would silently mis-store it).

// migrate to docs/ticket-types.md when it lands
## D-ticket-types-read-models — Read models resolve each row's own definition

The backend read-models (`copy_text`, `board_view`, `queues`, sprint item tickets, sprint in-progress
rollup) resolve each row's own `WorkflowDefinition` via `coding_bridge` instead of assuming coding.
Two things stay intentionally coding-shaped and are correct: `board_view` seeds its default columns
from coding's stage order (novel states append as new columns), and the fields codec's
`definition or coding_definition()` is a fallback every caller overrides. The universal `done`/`dropped`
bookend literals stay in `derive_sprint_item_status` and `_TICKET_CLOSED` because those bookends are
shared by every type by validation.

// migrate to docs/ticket-types.md when it lands
## D-ticket-types-worker-routing — Worker realization is skill-driven, not code-routed

A worker self-routes to its type's specialist skill via `skill_view` (a Hermes core tool), not code
routing. `HERMES_TUI_SKILLS` only *preloads* the base role's text; it does not gate later access, and
`skill_view(name)` loads any home skill on demand. The launched base role `panels-worker` stays
type-agnostic; only the on-demand specialist is per-type (coding → `panels-worker-coding`). Precondition:
don't restrict the worker toolset below one that includes `skill_view`. The base skill is
extract-and-place (the coding-specific stage guidance moved verbatim into `panels-worker-coding`; the
base gained one section: my-ticket → skill_view → the specialist). The live self-routing proof is an
owner run, not a `./verify` gate.

// migrate to docs/ticket-types.md when it lands
## D-ticket-types-new-worker — Job B: the `new_worker` production type, and the global kickoff-ceiling change

The payoff of the machinery: a production-shipped type whose worker designs and lands another worker.
Because the stack is type-driven, the type itself needs no engine/UI change — a definition + a
specialist skill + registration. Owner-designed bespoke lifecycle `needs_kickoff → needs_stages →
needs_thinking → needs_drafting → needs_closeout → done` (novel ids are plain strings like probe; one
thinking stage is the crux, because the value of a worker is the quality of thinking its skill forces).
Ships into `coding_registry()` alongside coding; `panels-worker-new-worker` is a real shipped skill.

**The kickoff-ceiling change is GLOBAL (owner-decided) and was a decoupling, not a one-liner.** The
fresh-ticket start ceiling moves to `needs_kickoff` for *every* type — a fresh ticket is leashed at
kickoff and nothing advances until the human grants scope. But `default_ceiling` was overloaded as
both the fresh-ticket start ceiling AND the "first worker stage" threshold read by the recap-writable
gate, sprint in-progress, and the external-work seed. Moving it naively would make those fire one
stage too early. Fix: add `needs_kickoff` to `ceiling_range` so `default_ceiling` derives to it and it
becomes selectable, and add a distinct `first_worker_stage` view (= second stage; `needs_success` for
coding) that recap + sprint + external-work seed re-point at — preserving their behavior. Stays derived,
no per-type ceiling knob.

Three coding-default stragglers had to be threaded with the row's own definition or a live new_worker
step would crash: `employee_step_runner._next_step_prompt`, `runtime/readiness.is_runnable` (blast
radius was the whole poll — a raise aborts discovery for every ticket), and the external-work seed's
`== first worker stage` assertion. A comprehensive sweep of every machine/registry call on a
non-coding runtime path confirmed those were the only stragglers; all others already thread the
definition or are intentionally coding.

## D-ticket-types-front-doors — The agent front doors carry an explicit type list, maintained by closeout

The base worker skill (`panels-worker`) and the Chief-of-Staff skill (`panels-chief-of-staff`) each carry
an explicit list of the ticket types rather than discovering them from the served manifest. The Chief was
type-blind before this — it reflexively filed non-coding work as a `coding` ticket and had coding stage/
field names hardcoded in its external-work and worker-boundary sections. Keeping the lists current is a
step in a `new_worker` ticket's **closeout** (announce the new type to both front doors), so they don't
drift as future types ship. Explicit-list-plus-closeout-discipline over dynamic discovery because these
are agent skills a human reads as prose, not a fetch surface.

---

# Runtime (readiness loop + employee step runner)

## D-automatic-employee-step-eligibility — One complete automatic-start decision

**Automatic Employee-step eligibility** is the whole answer to whether Planner may automatically start a
Ticket's next Employee step now. It includes today's board membership, empty control status, a non-terminal
Stage, no parked proposal, scope permission, and no active blocker. Discovery and the final transactional
claim use this same concept; the claim remains the race-safe authority. Directly requested revision turns
bypass it. Existing `readiness`, `is_runnable`, `TicketReadinessLoop`, and readiness-doorbell names are
rename debt; `EmployeeStepRunner` remains separate because it owns execution rather than eligibility.

## D-runtime-names — Responsibility names, and direct employee turns go straight to the runner

`SystemA`/`SystemB` are named for what they do: **`TicketReadinessLoop`** discovers runnable tickets
on today's board and wakes work; **`EmployeeStepRunner`** owns one employee turn and its settlement.
The step runner exists with the worker gateway even when automatic dispatch is disabled or another
process owns the polling lock, so explicit employee turns (including Review revision guidance) go
straight to it instead of relaying through the nullable readiness loop — which previously returned
HTTP success while no Hermes turn started. Direct revision reserves a parked in-process handoff before
clearing the proposal and claiming the ticket (commit releases it, failure cancels it), so an HTTP
success is evidence that delivery was accepted without a durable queue. Automatic execution rechecks
today's-board membership before claim, closing the stale-discovery race.

## D-runtime-ownership-boundaries — Worker ownership is checked at prompt and settle boundaries

System-B ownership is not established merely by holding a durable session key. The ticket must still
be at `agent_running_step` at the pre-prompt callback (even when the gateway resumes the same stored
key) and at settlement: error settlement marks `errored` only *while* still `agent_running_step`, so
human takeover or a parked proposal that lands mid-turn stays stronger than late worker cleanup. The
runner must persist a created/resumed `chat_session_key` *before* it submits the prompt, because the
worker can call `panels worker my-ticket` in that same turn and needs its key already queryable. While
running, that step owns the session: human sends return `already_running`, not a parallel session.

## D-readiness-ring — Readiness ringing belongs to domain actions and is best effort

The database and periodic scan remain the truth; a one-method doorbell only shortens the wait after
committed actions that may affect eligibility. Domain action modules (ticket, day-placement,
blocker-link) own the write-then-ring sequence; routes don't poke the readiness loop. Delivery
failures are logged and cannot fail a committed action; a process without the polling lock uses a
no-op doorbell. The conservative ring set includes the three audited omissions (takeover,
blocker-link creation, day removal). There is no event bus, durable queue, or generic mutation module.
Adding a ticket to a day, and editing a settled field value, are both readiness-changing actions that
ring.

## D-readiness-today-board — The board is today's execution set; off-day membership is visible but inert

The readiness loop polls only tickets on today's day, and the Board shows that same set (the filter
lives in the backend board view so API and UI share the contract). A ticket that is otherwise runnable
but on yesterday or no day does nothing — correct runtime scope, but poor visibility, so the ticket
header shows an `auto` chip derived from day, status, blockers, pending proposal, and approval limit.

## D-gateway-topology — One shared persistent gateway child per role; identity is a per-turn CLI lookup

The planner runs ONE persistent shared worker gateway child holding every ticket's durable session
(plus one Chief-configured child), not a child per send or per ticket. Hermes runs many sessions'
turns concurrently in one process and binds the durable `HERMES_SESSION_KEY` per *turn* via
ContextVars, and its per-session `4009 session busy` guard is the sole collision guard — so a
per-ticket process pool would buy nothing. A worker learns which ticket it is by a `panels` CLI
command that reads its per-turn session key and reverse-looks-up the owning ticket; the
`panels-worker` skill prompt tells it to. Chosen over birth-injection (a seed message fades on
compaction) because the session key is always present, so identity is re-derivable every turn. Key
rotation on compaction is handled by re-persisting rotated keys.

---

# Hermes gateway, sessions, and chat delivery

## D-canonical-human-chat-ingress — One server-owned turn replaces legacy human Chat paths

Human Chat has one ingress: the server-owned turn already named by `D-live-chat-state`. The legacy
`/send`, `/stream`, and `/command` routes and their parallel service paths may be deleted after repository
callers and tests migrate. They do not remain as compatibility adapters because doing so would preserve
duplicate admission, session persistence, transcript mutation, settlement, and error handling. Exact
`/new` behavior remains part of the canonical turn. This changes only human Chat ingress; Panels transcript
rows remain UI/audit state and worker delivery still occurs only through the Hermes gateway/session path.

The word `stream` names two different things in the old code. The HTTP SSE route and
`planner.chat.service.stream` are duplicate human ingress and are deleted. `GatewayAdapter.stream` is the
single Hermes observation transport already consumed by `_run_human_turn`; it remains and serves both
message and command modes. The synchronous adapter `send` and `run_command` methods and their result types
are parallel paths and are deleted. This distinction keeps one ingress without cutting out the transport
the surviving turn actually owns.

## D-panels-chat-and-employee-history — Visible Chat and employee history are separate truths

**Panels Chat** is the durable product-visible conversation; **Employee session history** is the
authoritative record of what the employee actually received and produced. Employee history remains useful
for continuity, inspection, and deliberate recovery, so its interface is retained. It is never used as a
silent fallback to populate an empty Panels Chat: model prompts can contain hidden revision guidance,
worker context, system/tool content, and native image delivery that are not the visible transcript. Ordinary
restart continuity already uses the stored durable session key and persisted Panels rows. The separation is
the final, finicky ticket in the architecture program and lands in its own isolated commit so restart,
rotation, and recovery behavior are independently reviewable and reversible.

## D-session-ingress — One ordered, gapless session ingress; Panels never guesses Hermes state

Panels has one deep session module per live Hermes session: one ordered event ingress, exact
preservation of Hermes's submit disposition and lifecycle observations, stored/live session rebinding,
and resume-snapshot reconciliation. The physical event capture is child-wide and gapless — the
`GatewayChild` captures all session events into one ordered feed *from construction*, before a
create/resume response can reveal the live session id, and a per-role consumer demultiplexes it into
lightweight live-session records (opening a listener only after the response would retain a loss
window). Panels registers submission intent before writing `prompt.submit`, linearizes per-session
writes only to preserve Hermes's receive order, and never waits for locally inferred idle, retries an
uncertain submit, or grows its own FIFO. When Hermes's observations can't prove a stronger result,
Panels represents an honest unknown/offline outcome rather than inferring one. No general queue is
assumed; Hermes's native busy behavior and one pending slot remain authoritative. Hermes itself is
not modified, and the two role children stay separate.

## D-owned-turn-identity — Completion ownership is causal, via one owned turn id

A Hermes `message.complete` identified only by live session id cannot safely be treated as the
completion of whichever caller is currently draining that session — the old broadcast fan-out let a
new prompt consume an older turn's interrupted completion while the real prompt continued orphaned.
The correction passes the already-durable Panels `ChatTurn.id` into a versioned Hermes turn
submission: Hermes retains it in a non-merging queued envelope, tags every turn-scoped event with it,
gives internal work separate ids, produces exactly one terminal per accepted turn, and interrupts only
the requested id. Panels registers a drain by `(live_session_id, turn_id)` and routes only matching
events to it. A Panels-only session lock was rejected (can't distinguish Hermes-created background
turns); an unbounded FIFO was rejected as unnecessary. Employee settlement follows the same native
delivery disposition: streaming owns its execution, queued input owns only the next execution after
the preceding terminal, steered input uses the existing errored result rather than claiming a later
terminal or inviting a retry.

## D-live-chat-state — Live chat state is Panels-owned; Hermes is transport

Chat accuracy depends on a server-owned `ChatState`, not a browser-local transcript or a lazy
Hermes-history read. `chat_messages` stores the product-visible transcript and `chat_turns` stores the
single active turn per entity (phase, activity label, partial output, session key, error). It is
generic over `entity_id`, so ticket chat and Chief chat share one contract; only gateway routing
differs. Human sends start a background server turn (`POST /api/chat/{entity_id}/turns`); navigation
never owns or cancels the turn. `ChatPanel` reads `GET /api/chat/{entity_id}/state` and polls only
while a turn is active. The step runner records automatic worker steps through the same chat writer,
so worker activity isn't inferred from `ticket_status` or timed history retries. Chief chat status
rides this same active-turn channel rather than a second live-status feed. Remount tests assert the
server `ChatState`, not browser survival.

## D-panels-chat-not-worker-context — A Panels chat row is not worker delivery

Panels `chat_messages`/`chat_turns` are product-visible UI/audit state, not the worker's Hermes
conversation. A normal chat send reaches the worker only because it goes through the gateway/session
path; a chat row, event row, or UI transcript line alone is never delivery. So Review send-back
guidance, approval-edit notices, and any worker guidance must reach the worker through the Hermes
session or the actual worker prompt. Review rejection sends the framed guidance directly to the
ticket's existing Hermes session as the prompt for the already-claimed `agent_running_step` — it does
not write a chat mirror, queue for later, emit `approval_returned`, or change the stage. Clearing the
pending proposal leaves the canonical field unchanged until the worker drafts a revision. (This
supersedes an earlier reading that treated a field note or chat row as the delivery.)

## D-worker-context-subsystem — Pending worker context is a generic keyed delivery subsystem

Worker awareness is durable state keyed by `(worker_entity_id, context_key)` — not a ticket boolean,
notice queue, event-log convention, or chat row. Producer domains own key names and text; the generic
`worker_context` package owns coalescing storage, deterministic prompt composition, snapshots, and
exact-revision acknowledgement. `SharedGateway` knows only the generic service and appends its prepared
suffix at the actual `prompt.submit` boundary, acknowledging immediately after Hermes accepts the
submit (so a pre-submit failure retains context and an older receipt can't clear a newer revision). The
first key is `ticket_changed`; adding another producer key changes no gateway or runtime code. Internal
context is stripped from direct Hermes history; Panels stores the original visible text.

## D-pause-is-session-control — Pausing is chat/session control, not ticket runtime control

The pause affordance interrupts the visible chat turn and settles `chat_turns` as `interrupted`. It
deliberately does not write `ticket_status`, release/take over the ticket, or ring readiness — a paused
chat turn must not pretend to decide whether the ticket reruns or stays worker-owned. It lives in the
composer send button while a turn is active (the alternate composer action), not in the activity row.
Pause resolves the live session id for the current gateway child from a runtime map (cleared when the
child is replaced/shut down); it uses the live id when available rather than persisting an ephemeral
handle or resuming a session just to interrupt one. Hermes live/durable session-not-found (`4001`/`4007`)
is `not_found`; unrelated RPC/transport failures keep the `gateway_offline` contract.

## D-stale-session-recovery — A rotated/stale session key is recoverable state, not an outage

The durable session key rotates via Hermes auto-compression, so a stored key can fail `session.resume`
with rpc `4007` (session not found) after a gateway restart. The gateway mints a fresh session on
`4007` (for send and command) and the chat service re-persists the new key whenever it differs from the
stored one (logging `chat_session_created`) — chosen over surfacing the raw `4007` to the human because
this is recoverable state. Because keys rotate, worker/session keying is on the stable `ticket_id`,
resolving the current `session_key` from the DB at execution time.

## D-new-session — `/new` is a native Panels session transition

Exact `/new` is handled before ordinary command dispatch by creating and binding one fresh Hermes
session and persisting its key, using the same ordered ingress for the next message. The earlier
`slash.exec` path sent `/new` to a separate noninteractive Hermes CLI worker where the destructive
confirmation can't be answered; it timed out after 45s and couldn't return the new session identity.
Hermes source is unchanged and no general session-command framework is added.

## D-hermes-home-default — The planning database owns the default Hermes-home location

When no explicit `PLAN_HERMES_HOME` is set, startup derives the dedicated Hermes home from the
configured database directory. This came from a live split: the listener served the canonical absolute
database from a git worktree while a relative `data/hermes-home` resolved inside that worktree, so the
fresh session picked an unauthenticated default model and every prompt failed. The derivation
preserves worktree isolation when the DB is worktree-local and session/config identity when the DB is
canonical. Credentials stay operator-owned; startup never copies or links them.

## D-chat-follow — Chat follows only while the reader is near the bottom; upward intent transfers control

The shared chat panel opens at the latest message and follows rendered messages and live output while
the reader is within a small distance of the bottom. **Upward wheel intent stops following
immediately** — distance alone was both the follow state and the intent signal, so frequent activity
renders snapped a small upward movement back to the bottom before it could cross the threshold, which
trapped the reader during expanded activity. Ordinary near-bottom scroll and content growth do not
resume a stopped reader; exact-bottom return, a downward wheel into the near-bottom zone, or the
`Latest` action do. `Latest` is a separate distance signal: it appears whenever the bottom is
meaningfully out of view. One shared component for Ticket and Chief chat. (Supersedes the earlier
scroll-once-on-load and the pure near-bottom-distance rules.) A pending assistant turn renders only the
thinking indicator until real text arrives — the empty-markdown fallback is kept for fields, not chat.

## D-live-activity — Live agent activity is turn-owned, display-safe, and transient

The collapsed `activity_label` gains one ordered child collection for the running turn. A
framework-free normalizer admits only scalar category, short label, lifecycle, stable identity, and
timing from accepted Hermes phase/tool/command events; raw reasoning, arguments, results, and
malformed values never enter storage. Identity-bearing start/end events update one row, idless
consecutive duplicates collapse, the newest 100 are retained, and every settlement deletes the
collection (foreign keys cover chat-turn/ticket deletion) — this is live progressive disclosure, not a
permanent transcript or audit subsystem. The shared `ChatPanel` owns the one disclosure interaction for
Ticket and Chief chat, collapsed by default, and makes timeline renders explicit dependencies of the
pre-growth follow decision.

---

# Managed files, previews, and chat images

## D-managed-markdown-behavior — One owner, unchanged visible behavior

Managed Markdown consolidates safe rendering, preview reconciliation, edit serialization, and teardown
behind one owner. It does not redesign the editor or alter visible behavior: current appearance and
interactions, every preview kind, exact Markdown token round-tripping, stable preview identity during
unrelated Chat updates, recursive-preview limits, and existing safety rules remain fixed. The existing
`FilePreviewTarget` + `FilePreview` seam and the hardened Markdown renderer keep the responsibilities they
already earn; read and edit callers stop coordinating their lifecycle themselves.

## D-file-preview-contract — Ticket files are managed content behind one preview contract

Canonical ticket fields, notes, proposals, results, and chat stay SQLite text. Standalone work
products live beside the database under `files/tickets/<ticket_id>/` and are referenced by ordinary
Markdown links; Panels serves only safely resolved regular files. The public frontend seam is
`FilePreviewTarget` + `FilePreview`, so every surface shares one classifier and renderer instead of
per-surface file logic — no upload API, artifact rows, file ids, or per-stage attachment slots. Every
Markdown link routes through `FilePreview`: safe image/video/audio/managed-Markdown render inline; HTML
is a sandboxed preview card whose shared policy grants only script execution, keeping the document at an
opaque origin without same-origin or other host-page privileges. Its **Open preview** action leads to a
dedicated document-only route where the same sandboxed HTML fills the page (a Blob-backed iframe, avoiding
the failing full-route `srcdoc` paint path, with full-page layout scoped to HTML so other routes are
unchanged); unsupported managed files and external URLs are cards with download/open actions. Editable
Markdown stays one continuous `contenteditable` where each link is an atomic preview island carrying
its exact Markdown token; serialization emits that token and skips generated preview descendants.
Managed Markdown expansion uses a fixed depth and visited bound, and a tokenized max height (in
`tokens.css`) so long previews scroll and short ones keep natural height — a component-level visual
bound, not a truncation contract.

## D-chat-images — Chat images are managed by chat and delivered through Hermes native vision

Uploaded images are stored under `files/chats/<entity_id>/` for every chattable entity (not in ticket
folders, no attachment records). Panels persists an ordinary managed-file Markdown reference as the
visible human message and routes it through the `FilePreview` seam; the backend separately resolves the
same-entity managed file and queues it with Hermes `image.attach` immediately before `prompt.submit` —
a chat row alone is never delivery. Sends are ordered collections (`image_references` at the API,
`image_paths` at the gateway): every reference resolves before a visible turn is created, so one
invalid item prevents both prompt delivery and transcript mutation, and cleanup runs on every failed
admission path (a failed attach submits nothing and best-effort detaches earlier images; a rejected
disposition detaches all attached). Uploads are bounded and validated by required container structure
(not a magic prefix), publication/reads reject symlinked roots, and the composer adds one image
affordance beside `/`, revoking object URLs on removal/success/teardown but retaining previews after a
failed send for retry.

---

# CLI shape

## D-cli-surfaces — The CLI is segmented by day, ticket, and sprint; worker actions have their own home

There are three operating surfaces — things you do on a **day**, to a **ticket**, and to a **sprint** —
and commands group around them, not around a route or authority-mode map. Product commands (`day`,
`ticket`, `sprint`) send headerless human requests; `worker` commands send `X-Plan-Actor`, so actor
classification stays at the HTTP boundary without a mode flag. Approval is the human act, so the CLI
needs no broad human-only control verbs, and code-owned transitions (ticket state/status, runtime
control) stay code-owned, not manual knobs. `set` commands take entity + field + value. Ticket
creation is always a `ticket` command (never a second `sprint create-ticket` surface, though `ticket
create` may carry `--sprint`/`--sprint-item` placement metadata); adding an *existing* ticket to a
sprint item is a `sprint item` operation that sets `sprint_item_id` and clears standalone
`sprint_id`/`project`. The redesign removed the old command homes outright rather than keeping
compatibility aliases (`propose`/`recap`/`note` under `worker`, sprint-item commands under `sprint
item`), and the implemented grammar follows the redesign plan over first-pass convenience.

## D-worker-propose-recap — A worker proposal carries its recap update in one writer

`worker propose` proposes the next gating field; it is not a client-side composition of
`propose/{field}` + `recap`. One backend writer infers the current gating field and updates the recap
in the same transaction. The recap is not a fifth proposal field — a worker may still update it alone —
but every proposal overwrites the recap as part of the same operation. PATCH routes marshal each
recognized field's type before calling a writer, so a wrong-shaped value returns the validation
envelope instead of a SQLite coercion, `not_found`, or 500.

## D-projects-catalog — Projects are data-backed IDs with legacy name compatibility

Projects are a catalog table, not a storage enum. `project_id` is canonical on sprint items,
standalone tickets, and ideas; serializers keep returning `project` as the display name. Write/filter
APIs accept both `project_id` and legacy `project` name but reject mismatched selectors. Parented
tickets keep `project_id = NULL` (the parent item owns the project). Project creation is human-only,
emits `project_created`, and there is intentionally no rename/delete/archive in the first pass.

---

# Frontend architecture and UI

## D-resource-refresh-two-signals — Canonical events plus immediate targeted refresh

The server event stream remains the canonical invalidation source. A successful UI write also keeps an
immediate targeted refresh so the visible result does not wait for event delivery and remains correct when
the socket misses or disconnects. The resource catalogue owns the affected-resource effect; routes do not
repeat free-form key arrays. Both paths invalidate only the exact resource and aggregates affected, never a
whole screen. Review's advance flow awaits one catalogue-owned refresh rather than starting and aborting
duplicate reads.

## D-resource-catalogue-scope — Catalogue cached UI reads, not every request

The resource catalogue contains only server projections the UI caches and refreshes, such as Tickets,
board, Review, sprints, days, Panels Chat, projects, ideas, and Worker-type manifests. Each entry owns its
identity, read location, response type, dependencies, and refresh policy. Mutations, uploads, managed-file
reads, and startup configuration such as `/api/meta` remain ordinary requests outside the catalogue. This
keeps the module deep and focused instead of turning it into a registry of unrelated HTTP operations.

## D-ad08-resource-catalogue-lock — One definition owns cached reads and dependencies

AD08 freezes one 13-entry Resource Catalogue and nine named successful-mutation effects. The generic cache
continues to manage values and request concurrency without knowing Projects, Tickets, events, or mutation
meaning. Routes and components may open typed catalogue entries and select closed effects, but may not
construct keys, cached GET fetchers, invalidation arrays, or a second event vocabulary. Phantom individual
Sprint, Sprint-item, and dated-Day identities are deleted because no production cached read owns them.

The shared `chat_session_created` kind is an intentional temporary exception: until AD09, Employee claim,
human binding, and history remint can all update the Ticket's stored session key, so that event invalidates
exactly matching Ticket plus matching Panels Chat. The other four Chat message/turn events invalidate Chat
only. Ordinary Ticket events do not invalidate Chat.

A name-bearing Project update reaches the six real aggregate projections that embed Project names:
Projects, Board, today's Day, backlog Sprint items, Ideas, and current Sprint. It also reaches opened Ticket
details through a private catalogue-owned process-lifetime identity index: unresolved entries are included
conservatively, while loaded unrelated or projectless Tickets are excluded. The cache engine remains
semantic-free. Project creation, summary-only edits, and synthetic ordinary Project kinds remain
Projects-only. The corrected plan passed fresh independent review with `NO VIOLATIONS` in session
`019f5f81-89a3-7ba2-883b-bb68ce4022b1`.

This catalogue does not weaken the Worker-type boundary. Live creation always requires an explicit Worker
type, with no `coding` inference or default. Only an explicit migration may classify a historical row that
predates stored Worker type as `coding`.

The reviewed implementation is committed at `d2cc125`. The first affected browser run found a duplicate
Review read caused by stale-self-heal racing the new ordered refresh; the delegated correction marks the
decision's refresh as handled before mutation and restores eligibility when the mutation itself fails. The
complete affected browser set then passed 68/68. Independent implementation review session
`019f5f98-5fd1-7610-9cd5-92d2cf2a32a8` found `NO VIOLATIONS` across the product diff, real backend emitters,
served bundle, allowlist, and explicit-Worker-type boundary.

## D-shared-scrollbar-treatment — Native scrollbars, shared CSS, capability-safe hiding

Panels' seven owned overflow surfaces (`.markdown pre`, Markdown `.file-preview`, `.chat-thread`,
`.chat-menu`, `.chat-image-previews`, `.board-workspace-left`, and `.ticket-doc`) share one native
scrollbar treatment in `assets/app.css`. Each reserves a stable gutter. Existing token-backed color
and radius values carry the visual treatment; no custom scrollbar component, JavaScript scroll-state
tracker, or new design token is justified.

Visible native scrollbar styling is the baseline. Transparent-at-rest thumbs exist only inside
`@media (hover: hover) and (pointer: fine) and (forced-colors: none)` and reveal through hover,
`:focus-within`, or active interaction. Forced-colors and non-hover/touch contexts keep
platform-visible behavior. This also repairs the old `.chat-thread` rule, which hid its thumb
unconditionally outside a capability query.

Acceptance coverage uses computed styles and layout invariants on representative vertical and
horizontal surfaces, plus real touch/non-hover and forced-colors Playwright contexts. Forced-colors
coverage checks both rest and interaction states. It does not use screenshots or native-scrollbar
pixel measurements, which vary by OS and headless browser. The plan/implementation agent steps are
collapsed for this trivial CSS ticket; Codex still reviews both the plan and final diff independently.

## D-frontend-reactivity — Events → keyed invalidation → targeted refetch (not reactive queries)

For the Svelte app, `events → keyed invalidation → targeted refetch` was chosen over a Convex-style
reactive-query layer. An independent Codex, given both neutrally, picked this: reactive queries add a
custom runtime (subscription tracking, rerun scheduling, push, reconnect, multi-tab cleanup) *and*
don't remove the dependency-mapping burden for aggregate views (board, queues, current sprint) — they
just relocate the map server-side with more moving parts. The one rot risk (a new event kind that
forgets its invalidation) is contained: the `entity_id`-prefix rule is primary and complete (new kinds
about an existing entity are auto-covered), with a completeness test as the backstop so a missed
mapping fails `./verify`.

When an existing event gains a new aggregate dependency, its mapping and test must gain that resource
key in the same change. In particular, Review's ticket queue follows current-day membership, so
`day_ticket_added` and `day_ticket_removed` invalidate `queues` as well as the ticket and board; this
keeps an already-open Review screen and the shell badge aligned without a reload.

## D-svelte-canonical — The Svelte app is canonical; the classic JS path is retired

The Svelte/Vite app is the canonical frontend. FastAPI serves the built `web/dist/index.html` at `/`,
with `/_app` mounting Vite's hashed chunks (a chunk path, not a second UI). The old classic route loop
(`assets/api.js`, `app.js`, `components.js`, `screens-*.js`) is deleted; `assets/tokens.css`,
`app.css`, and `markdown.js` are kept because the Svelte document imports them as shared styling and
hardened markdown infrastructure. Migration was build-then-swap (build in isolation, leave legacy live,
swap once) rather than route-by-route coexistence — a single-user 7-screen app doesn't earn that
machinery.

## D-workspace-route — Workspace is the product route; board stays the backend resource

The user-facing page is Workspace at `#/workspace` (`#/board` kept as an alias). The API route,
resource key, event key, and backend view stay `board` because they describe the execution set the
readiness loop polls; renaming them would be unrelated contract churn. Workspace renders `TicketRoute`
directly in its right pane, keyed by selected ticket id, so the route's id-bound resources recreate on
selection while the standalone `#/ticket/<id>` route is unchanged. The optional `#/workspace/<ticket-id>`
segment is the single selection source of truth; every Workspace variant shares one screen key so
switching tickets doesn't reset the rail's filters/collapsed-groups/hide-done. Once a settled board
proves a routed ticket absent, Workspace replaces the stale entry with `#/workspace`. The left rail
groups by effective project (parented tickets group under their item's project via `group_project_id`
without changing their own `project_id`) and shows all four ticket-stage dots. Hide-done defaults off.
Chief of Staff is embedded in Workspace, not a primary nav tab (the `#/chief` route stays for direct
links).

## D-shared-component-set — One component set; consolidate, don't redesign

The frontend consolidation reduced the component footprint through serial contract-scoped tickets
(disclosure, rows/headers, buttons/pills/selects, one approval surface, scaffold/forms/utils), run
strictly serially because they share `assets/app.css` and the routes. The binding rule was
consolidate-not-redesign: controls that do the same job in deliberately different interaction contexts
stay separate (SegmentedControl vs EnumPill; the review revision box vs the chat composer), and a
shared class carrying one property earns nothing — so the specced `.select` base was dropped when the
board filter (boxed) and scope selects (inline underlined words) proved to share only `cursor:
pointer`; normalizing them would visibly redesign one control. Ticket stages flow through one
`TicketStageSection` composing the shared primitives, and one `ApprovalBlock` is the single approval
surface (gating-pending / proposal / readonly modes); Review uses the same stage section for ticket
approvals, keeping only Skip/Open-Ticket as page-level affordances.

## D-ui-redesign — The UI redesign: serif voice, UI-only, screen shapes locked

The daily/app redesign settled after several owner review passes on the shape rules that survived: the
**functions and screen shapes are locked** (Review is one-thing-at-a-time on its own screen; Workspace
is the only three-panel surface), the target is the **UI, not the IA**, and the identity move is that
the **product speaks in serif** (Newsreader — titles, recaps, proposals, notes, chat) while the machine
stays sans (Inter — nav, pills, buttons, labels). Signal meanings are unchanged from the current app
(spinners are the working signal; a proposed four-colour dot vocabulary was rejected as traffic-lighty).
Depth is rationed to the ask surface; entrance rhythm, keyboard hints, the leash sentence, the
empty-state reward, and ambient presence carried over. The intermediate exploration passes (The Desk,
The Brief, The Review Room, sprint tab/grouping revs, embeds revs) were all superseded by these rulings
and live only in git and the mockups under `orchestration/daily-redesign/` + `orchestration/ui-redesign/`.
A ground rule set during the build: the shipped mockup is the spec and tests follow it — UI tests never
constrain the interface, and `./verify` staying green is the definition of done.

## D-ui-fidelity — The design lane reruns on the owner's complaint surfaces, not only wave boundaries

After the redesign landed, an owner review of the live app against the mockups caught fidelity deltas
that per-wave "FAITHFUL" design reviews had passed (rail width/rhythm, filter-row copy, the ask's
action row rendered as text links instead of boxed pills with serif leaking into machine controls,
review-chamber gaps, key hints not anchored to the viewport bottom). All fixed as CSS-only design
repairs against the mockups. Lesson recorded: a wave verdict is not a substitute for the owner's eye on
the live app; rerun the design lane on the owner's named complaint surfaces.

---

# Sprint planning and rollover (agent-owned skills)

## D-sprint-planning-skill — Sprint planning is one review-first Panels skill, not a cron or rollover path

The legacy file-based `sprint-planning` skill is replaced by a provisioned `panels-sprint-planning`
operating skill. At a sprint boundary it finishes the review first (outcomes, the user's reflection
before agent interpretation, joint discussion, learning, carry-forward candidates), then decides the
next limiting factor, primary bet, supports, pre-mortem, and items. Carry-forward is not automatic;
midpoint reconciliation is a narrower path; daily composition stays owned by rollover. The skill uses
only current Panels commands, resolves an explicit sprint id (so an ended sprint can still be
reviewed), treats item status as derived, and verifies writes by readback. No cron job is added or
removed — there is no sprint-planning scheduler entry.

## D-rollover-skill — Rollover drafts the kickoff; ticket placement waits for agreement

`panels-rollover` is one agent-owned operating skill, not a deterministic server engine. A manual or
scheduled run may write today's likely four-field overview and keep a small list of obvious carryover
candidates in day notes, but it does not add those tickets to today until the user reviews the kickoff
and agrees. The morning job prepares a missing draft; the afternoon job is only a failsafe for the same
missing draft. Because scheduled Hermes sessions inherit `PLAN_ACTOR=worker` while Day overview/note
writes are direct-only, `panels-rollover` may prefix only the scheduled kickoff-draft `panels day set`
commands with `PLAN_ACTOR=chief`; that elevation cannot edit lifecycle state, approve gates, or add
tickets before the user agrees. The repository owns and provisions the skill; the scheduling jobs and
global skill link are deployment state changed only after Implementation approval.

## D-implementer-value — Implementer assignment is one typed Ticket value, not a catalog

`t_mkkvq9qz` stores only a nullable `implementer` with four wire values (`khushal`, `panels_worker`,
`hermes_codex`, `hermes_claude`). The ordinary Ticket edit transaction owns validation, events, and
worker-context invalidation; the existing metadata-row `EnumPill` owns editing. No implementer table,
registry, account/capability system, or model router. Assignment edits are inert — the only control
consequence is at the accepted Plan transition into `needs_implementation`, where `khushal` selects the
existing `user_takeover` status while agent values and NULL keep the employee path (applies to direct
and auto-accepted Plans so a settling planning worker can't race into human-owned implementation). The
field is direct-only on ordinary PATCH; route-selection guidance lives in the worker skill, not backend
data.

## D-worker-artifact-guidance — Ticket-owned artifact guidance lives in role skills, not runtime prompts

The managed-file model stays in the general `panels` orientation, practical artifact judgment stays in
`panels-worker`, and the Chief has only enough awareness to preserve the expectation while shaping
frontend work. Frontend HTML is a strong planning default, not a ticket gate; when HTML *is* the design
exploration, it is the work product and needs no duplicate precursor. This keeps canonical gated fields
concise and changes no worker stage or runtime prompt.

## D-framing-employee-scope — The per-ticket agent is an "employee" with a "scope"

The per-ticket agent is framed as an **employee** taking on the task, and its **scope** is how far it
may go without approval (the grant/ceiling). The owner's mental model is managing a worker, not
operating a "mind"; employee/scope reads as plain management language in UI copy. This is a UI/copy
framing — the underlying code identifiers were not renamed by this decision.

---

# Documentation and orchestration hygiene

## D-architecture-review-scope — Advance all six candidates to implementation

The owner selected all six deepening candidates and confirmed the shared model through a one-decision-at-a-
time design interview. They land as independently reviewable stages in dependency order; Employee session
history is deliberately last and isolated. No implementation stage may weaken the deletion decisions or
preserve rejected terminology through compatibility aliases.

## D-architecture-review-exploration — Split the read-only architecture scan by system

The 2026-07-13 deepening review used three non-writing exploration lanes: Tickets/Worker types/runtime,
Hermes sessions/chat/worker context, and planning domains/frontend. These areas had distinct call and test
surfaces, so they could be inspected in parallel without file overlap. The orchestrator owned the
cross-system deletion test, decision-conflict check, candidate ranking, and temporary HTML report.

## D-architecture-review-ranking — Rank Worker workflow interpretation first

The deepening review ranks Worker workflow interpretation above the other five candidates. It sits in the
correctness core, each new Worker type multiplies its leverage, and three real call sites already missed the
row's Worker type, including one path that could abort a whole eligibility poll. The implementation must
concentrate Worker-type resolution and lifecycle knowledge behind the existing sibling-domain seam.

## D-architecture-program-isolation — Build the deepening program on one external worktree

The architecture-deepening stages share public contracts and must land serially, so they use branch
`codex/architecture-deepening` in the external worktree
`/Users/khushaljagota/.hermes/planning-v2-worktrees/architecture-deepening`. Keeping the worktree outside
the source checkout avoids presenting the entire child checkout as an untracked change on `main`. Each
stage still receives its own reviewed commit; AD09 remains a deliberately isolated final commit.

## D-architecture-program-merge — Merge only the completed verified program

The owner authorized merging the architecture-deepening branch back to `main` after the program is done.
No partial stage is merged: AD01 through AD09 each land as reviewed branch checkpoints, the complete branch
receives the final canonical verification, and only then is it merged serially into `main` and verified in
the integrated checkout. This authorization does not include pushing or opening a pull request.

## D-architecture-final-review-boundary — Review the complete program as one integrated diff

The final independent review covers `b7ca44a..191f14a`, all nine contract locks, migrations v18–v20,
generated assets, API and cache deletion seams, and the cross-stage Worker/Chat/Employee boundaries. The
generated Svelte bundle contains an intentional whitespace-character table that makes `git diff --check`
report trailing whitespace inside generated output; this is not hand-edited or treated as a product
violation. Source hygiene and the served bundle remain part of `./verify`. The read-only review session
`019f5fcf-cfa9-73d0-8741-27e7f687900a` reports `NO VIOLATIONS`.

## D-ad09-explicit-employee-history — Hermes history is explicit and Panels state stays Panels-owned

AD09 gives the Ticket's durable Hermes identity the exact name `employee_session_id`. Panels Chat reads only
its own durable visible messages and active turn; it never fills an empty transcript from Hermes or rotates
an Employee id as a state-read side effect. Hermes history remains authoritative for what the Employee
actually received and produced, including internal context and after restart, but a caller must request it
through the explicit Ticket-only Employee-history route. No UI history pane or copied transcript is added.

The retained standalone seed importer is live Python, not the terminal database migration. It therefore
requires an explicit Worker type on the command and every programmatic/parser call, and passes that exact id
through definition-driven field and Stage validation. It may not choose `coding`. The terminal lock-held
Ticket migration remains the sole place allowed to assign `coding` automatically to a genuinely historical
row that predates stored Worker type. This live-boundary ruling supersedes the seed carveout recorded in
`D-ad02-ingress-and-seed-authority` without changing its migration translation rules.

The implementation review also made the deletion consequence explicit: a historical Ticket
`chat_session_created` cannot be treated as an ordinary Ticket event after AD09. Migration rewrites stored
instances, and a synthetic live instance maps to no frontend resource. Only the new
`employee_session_changed` refreshes Ticket detail. Real Day/agent Chat session and message/turn events
remain Chat-only. A browser regression now proves retained Hermes history cannot manufacture visible Panels
rows after those rows are deleted.

## D-ad02-definition-owns-workflow — A resolved Worker-type definition interprets its workflow

AD02 replaces the shallow definition/views/registry-forwarder/bridge stack with one immutable
`WorkerTypeDefinition` that owns pure Stage and field interpretation. A boundary resolves the Ticket's
stored Worker type once and threads that definition into semantic rules. Direct Stage and stored-field
reads remain direct. Coding lifecycle enums and tables, optional-definition defaults, and compatibility
imports are deleted because each would preserve a second authority or a hidden coding path.

The no-default rule also applies to storage. Canonical SQLite `tickets.fields` becomes required with no
default; sanctioned creation builds it from the required Worker-type definition. The existing coding-
shaped JSON default is removed through a v19 forward migration that rewrites recognized old tables under
the proven lock-held migration envelope and preserves every stored field value. An omitted field map must
fail rather than silently produce a coding-shaped row for another Worker type.

This does not forbid migrations from translating historical data. A recognized old Ticket row that
predates a stored Worker type is backfilled as `coding`, because coding is the workflow that created that
shape. That classification exists only inside the migration. No Python signature, request boundary, or
canonical SQLite column supplies `coding` when live input omits Worker type.

## D-ad02-ingress-and-seed-authority — Every field set comes from the resolved definition

AD02's first independent plan review found two remaining coding-shaped edges. The historical seed importer
may translate its three known coding values, but it builds the complete slot map from the resolved coding
definition rather than hardcoding six field keys. Chief's external-work CLI retains its named coding
conveniences and adds repeatable `--field-file FIELD=PATH` input so another Worker type can carry its own
fields. Duplicate keys fail locally; the API remains the validity authority. The same correction removes
the proposed renamed guard: Ticket-position validation lives on `WorkerTypeDefinition` after explicit
registry lookup.

## D-ad02-contract-lock — The reviewed deep Worker-type boundary is fixed before rewiring

AD02's corrected plan passed independent re-review with no violations. The frozen behavior-bearing
definition, narrow registry, explicit production/test configuration, required semantic parameters,
definition-derived seed and creation fields, generic Chief field transport, and schema-v19 no-default
migration are locked in `ad02-deep-worker-workflow/contract-lock.md`. Implementation may fill in and
rewire these declarations but may not add compatibility aliases, implicit definitions, parallel tables,
or a broader registry surface.

## D-ad02-seed-test-allowlist — The seed authority proof lives with seed behavior

The reviewed AD02 plan requires a behavior test that installs a coding definition with one extra field
and proves the legacy importer derives the complete slot map from that definition. The natural canonical
owner is `tests/unit/test_seed.py`, which the initial bounded allowlist omitted. That single test path is
added rather than hiding seed behavior in an unrelated allowed module. No production scope or contract
changes.

## D-ad02-support-wording-allowlist — Shared test support uses the live Worker-type model

`tests/support/__init__.py` is added to AD02's allowlist for a docstring-only correction. Its old
“ticket type” term and “production stays coding-only” statement are both false after the already-locked
Worker-type model and shipped `new_worker` definition. No executable test support or behavior changes.

## D-ad02-chief-fixed-keys — Generic Worker fields cannot replace fixed request structure

Chief's `--field-file` transport carries Worker-type-owned fields, not the request envelope. Reconcile
therefore rejects collisions with its fixed `stage`, `kickoff_note`, and `recap` keys; create additionally
rejects its title, Worker type, placement, and metadata keys. Rejection happens before file reads or HTTP
delivery. The reserved set stays command-specific so a create-only name on reconcile still reaches the API,
where the resolved Worker-type definition remains the authority on whether it is a declared field.

The corrected AD02 implementation passed independent read-only re-review with `NO VIOLATIONS` after this
repair. That review also confirmed the explicit definition boundary, registry-free stored reads, v19
historical-only backfill, deleted compatibility stack, and bounded changed-path set.

## D-ad03-one-complete-eligibility-decision — Discovery and claim ask the same whole question

AD03 names the rule `is_eligible_for_automatic_employee_step`. It includes current planning-day
membership and empty Ticket control status as well as the Stage, gated field, parked proposal, scope, and
blocker checks. Discovery may use today's membership only to bound its candidate query, but may not repeat
status, Stage, proposal, scope, or blocker rules in SQL. Every candidate still goes through the complete
function. The runner resolves today's planning day inside the final claim transaction and calls that same
function, preserving the planning-boundary and stale-discovery guarantees.

The runtime responsibilities become `AutomaticEmployeeStepDiscoveryLoop`, `EmployeeStepRunner`, and the
best-effort `AutomaticEmployeeStepEligibilityWake`. The wake operation is `wake()`, not a readiness
`ring()`. `tickets.data.claim_automatic_employee_step` requires the eligibility callback and has no
optional guard or bypass. Direct revision remains a different, explicitly requested runner path and does
not use automatic eligibility. These names are long because each says what the thing actually does; no
old-name facade or compatibility alias is justified.

AD03's first independent plan review found one remaining live wording edge: `config.yaml` called the
dispatch switch “Ticket readiness polling” while the plan excluded configuration. The corrected scope
allows only that line-9 comment and includes it in the vocabulary lock; config keys and behavior stay
unchanged. Wake-specific test doubles and aliases also take eligibility-wake names rather than preserving
the rejected Doorbell metaphor in generic helper names. Corrected-plan re-review reports
`NO VIOLATIONS`, and `ad03-automatic-employee-step-eligibility/contract-lock.md` freezes the interfaces.

## D-ad03-stale-gate-regression — Prove every eligibility factor at the final side-effect boundary

Independent AD03 implementation review found no production-path split, but correctly identified that the
runner's stale-discovery regression covered only six of the complete decision's seven factors. A valid
registered Worker-type definition normally guarantees that a non-terminal Stage has a gated field, yet the
complete eligibility contract deliberately treats that as its own factor and the acceptance language says
*any* factor becoming false must prevent every downstream side effect. The finding is accepted. The
already-allowed runner test file gains the smallest test-only definition/seam that makes that factor false
at final claim, without changing production code or weakening the requirement that discovery and claim use
the exact same function.

AD03 follows the existing implementation-scope rule from `D-ad01-review-scope-isolation`: required
`PROGRESS.md`, `decisions.md`, and review evidence are orchestrator outputs, not delegated product paths.
They are committed as an isolated review/memory checkpoint before the corrected product-diff re-review.

## D-ad04-review-is-one-ticket-read-model — Review exposes only parked Ticket decisions and running workers

AD04 replaces the generic Queue interface with one Ticket-specific Review read model. Its public payload is
exactly `ticket_decisions` plus the global `running_worker_count`; each decision is exactly a Ticket id,
gated field, title, and waiting timestamp. Sprint-item approvals, overdue calculation, generic entity
dispatch, `/api/queues`, the `queues` resource, and all compatibility aliases are deleted in the same
replacement. The stored Stage is read directly; the Ticket's explicit stored Worker type is resolved only
to interpret which field that Stage gates.

Event and mutation invalidation follow actual Review dependencies as far as the existing event contract can
express them. Ticket chat/session/turn, note, recap, value, scope, link, metadata, and Sprint-item changes do
not refresh Review. Current-day membership does; a known off-day membership change does not. Cold-start
event handling uses the existing `includeTodayAlias` option only while the cached day id is unknown. Ticket
creation's companion proposal/status events conservatively refresh Review because those payloads carry no
day-membership snapshot; adding an event distinction solely to avoid that bounded refetch has not earned its
existence.

## D-ad04-ticket-only-types-and-worker-names — Review source carries the concrete model

The initial implementation review found that `ReviewRoute.svelte` still fetched a Ticket detail as
`AnyRecord` and named the `running_worker_count` formatter `runningAgentsText`. Both findings are accepted.
The route is no longer a generic entity dispatcher, so retaining the generic record type would preserve the
deleted abstraction in the source contract. The count is explicitly a Worker count, so its helper uses
Worker terminology. The visible `agent` / `agents in progress` wording is preserved because AD04 changes the
model and interface names, not that product copy.

The generated Vite JavaScript contains the same trailing whitespace inside Svelte's compiled whitespace-
character table as the previously checked-in bundle. The source and every non-generated path pass
`git diff --check`; the production build deterministically reproduces the generated byte. The bundle is
committed exactly as generated, because trimming it after the build would make the served artifact differ
from the reviewed source build.

The first three plan reviews found five real gaps: the file-preview Review selector, overbroad Ticket event
mapping, Sprint-item mutation invalidation, the shared Ticket mutation list, and current-day gating. A final
review exposed the creation-companion contradiction. All are accepted in the corrected plan; the final
read-only re-review reports `NO VIOLATIONS`. The exact read, response, UI, invalidation, deletion, and
preservation boundaries are frozen in `ad04-ticket-only-review/contract-lock.md`.

## D-ad05-image-preservation-is-focused-evidence — Existing image suites stay unchanged and run in full

AD05 changes no image contract, but image admission and delivery pass through the canonical human turn that
the ticket concentrates. The independent plan review therefore correctly required the existing unit and
browser image suites as focused evidence rather than waiting for the later full gate. Both files remain
outside the implementation allowlist: they are preservation contracts, not migration targets, and each runs
in full without a keyword filter.

## D-ad05-contract-lock — One human Chat admission and one gateway observation transport

The corrected AD05 plan passed independent re-review with no violations. The public `/turns` and hosted
Chief shells, sole `start_human_turn` admission service, exact surviving `GatewayAdapter.stream` signature,
six-field `ChatStreamChunk`, deleted synchronous results/methods, stream-only fake behavior, literal `/new`
transition, browser-helper deletion, static guards, and bounded path set are frozen in
`ad05-canonical-human-chat-ingress/contract-lock.md`. Implementation may rewire callers to those declarations
but may not invent compatibility shapes or deepen AD06/AD09 concerns.

## D-ad05-lost-first-write-winner — Panels attaches the key that won persistence

AD05 implementation exposed a pre-existing mismatch in the canonical turn: during a concurrent first
session-key write, `_persist_key` could correctly adopt the database winner while the running Panels turn
was still attached to the losing key announced by this stream. Duplicate session or done observations from
that stream could then retain or overwrite the loser. The correction remembers the announced key, attaches
the effective persisted winner, and substitutes that winner only for duplicate observations carrying the
same losing announcement. A later different key remains a genuine session rotation, and the worker-running
guard still runs on every report.

This narrow deviation from the plan's “turn internals unchanged except glue” is accepted because the AD05
contract explicitly requires preservation of the lost-first-write race. Leaving the mismatch would make the
canonical path less correct after deleting the parallel paths. The implementation review examined this
branch specifically and reported `NO VIOLATIONS`; deeper turn concentration remains AD06.

## D-ad06-active-chat-is-automatic-ineligibility — One complete decision includes active Chat

AD06 closes the race where human admission and automatic Employee claim could both commit. An active Panels
Chat turn is therefore the eighth factor in the sole
`is_eligible_for_automatic_employee_step` function, consumed unchanged by discovery and final claim. It is
not a claim-local mutex: that would violate AD03's central achievement by making discovery and claim ask
different questions. This explicitly supersedes AD03's seven-factor enumeration and nothing else in its
definition/claim contract.

Chat settlement retains its existing no-wake behavior, including Pause. The SQLite state and canonical
polling timer remain authoritative and observe eligibility after the turn settles. Adding an immediate wake
would change the established meaning of Pause and is not required for correctness.

## D-ad06-pause-is-visible-turn-control — Pause crosses origin, delivery does not

The deep owner is `ChatTurnLifecycle`, with exact public operations `start_human_turn` and
`pause_active_turn`. The first owns only human admission/execution. The second is deliberately cross-origin:
the product Pause affordance can interrupt a human- or worker-origin visible Chat turn. It settles only the
Panels Chat turn, preserves partial output, does not write Ticket status, and does not wake automatic
eligibility. Employee prompt delivery and eventual Ticket settlement remain with `EmployeeStepRunner`.

## D-ad06-new-forces-fresh-session — Literal /new wins its first binding

Ordinary session binding compares the execution's expected key and adopts a concurrent database winner, so
Panels and Hermes cannot diverge. Exact `/new` has different meaning: its first created candidate must become
the new durable session. The owner carries one private, one-use force-fresh intent for that first binding;
the candidate replaces any current key, attaches to the running turn, and is the live session used. After
that binding, normal compare-and-set recovery and rotation rules resume. No other input may force a key.

## D-ad07-managed-markdown-owner — One owner holds Markdown DOM lifetime

AD07 keeps the existing continuous Markdown `contenteditable` and rendered file previews, but concentrates
their mechanics in one `managedMarkdown.ts` surface owner. `MarkdownBlock` remains a read-only product
wrapper; `InlineEdit` remains the product interaction and save-state wrapper; `FilePreview` remains the
target-specific preview owner. None of those boundaries duplicates rendering, serialization, observer, or
preview mount lifetime.

Read-only identity is keyed by every render input value, not Svelte update frequency. Editable identity uses
both source and dirty state: pristine same-source updates do nothing, while dirty same-source updates restore
generated DOM. Moved/reinserted preview islands stay alive when final DOM containment says they remain in the
host; true deletion unmounts them. Failed saves retain a wrapper-owned pending value so retry does not depend
on the surface remaining dirty after a repaint. This is lifecycle concentration only: no visible editor,
renderer, preview-kind, backend, resource-cache, AD08, or AD09 change is accepted.

## D-ad01-one-locked-ticket-migration — One terminal rebuild migrates every old Ticket schema

AD01 replaces the sequential Ticket lifecycle, kickoff, type, and vocabulary rebuild path with one
terminal v18 rebuild. That rebuild recognizes every old Ticket-table shape already supported, performs
the same legacy lifecycle and kickoff transformations, renames Worker type and Stage, and rewrites affected
events while one write lock is held across snapshot, copy, and swap. Repairing only the final rename would
leave older live databases exposed to the existing pre-lock snapshot windows; repairing three separate
rebuilds would retain unnecessary migration machinery and duplicate the safety envelope.

## D-ad01-contract-lock — The reviewed vocabulary is fixed before consumer rewiring

AD01's contract skeleton uses required `worker_type` and stored `stage`, coding-only `CodingStage`
tables, `stage_changed`, the Worker-type manifest, and matching frontend names before implementation
rewires consumers. Internal `WorkflowDefinition.type_id` and the `planner.ticket_types` package stay for
AD02, but `TransitionHook.old_stage`/`new_stage` use Stage language now because they are contract fields
that directly name Ticket workflow positions. The implementation may complete the SQLite migration body;
it may not change the locked declarations or add compatibility aliases.

## D-ad01-fixture-allowlist — Direct creation fixtures may supply the required Worker type

AD01's implementation allowlist also includes `tests/unit/test_authctx_routes.py` and
`tests/unit/test_trusted_ingress.py`, plus legacy HTTP creation fixtures in
`tests/unit/test_projects.py` and `tests/unit/test_worker_my_ticket.py`. The first pair calls
`tickets.data.create_ticket` directly, so the locked removal of its implicit Worker-type default
requires the fixture-only argument `worker_type="coding"`; the second pair changes only the old create
key to `worker_type`. These are mechanical contract-consumer updates: the project, security,
trusted-ingress, and worker-command behaviours under test stay unchanged, and no production scope is
added.

## D-ad01-stage-filter-is-direct — Listing by Stage needs no Worker type

The Ticket-list Stage filter compares directly with the stored `tickets.stage` string. The old
list-only type parameter merely validated or disambiguated a lifecycle value; it did not filter rows.
That boundary contradicts the owner's ruling that a Stage read should return or compare what is already
stored without requiring Worker-type interpretation. AD01 therefore deletes the list `worker_type`
query and CLI flag instead of renaming the old disambiguation parameter. Ticket creation still requires
Worker type, and operations that interpret workflow meaning still resolve it.

## D-ad01-approval-stage-prop — The shared Ticket approval prop uses Stage language

`web/src/components/ApprovalBlock.svelte` is added to AD01's bounded frontend allowlist so its
Ticket-lifecycle prop can be renamed `newState` → `newStage` together with
`ScopePairPicker.svelte`. `TicketStageSection` is the component's only caller, so retaining the old
prop as a destructuring alias would be rejected compatibility vocabulary, not a generic UI state.

## D-ad01-generic-ticket-read — A stored Ticket row decodes without Worker-type resolution

`src/planner/tickets/logic/fields_codec.py` is added to AD01's bounded backend allowlist. Its
no-definition decode path reads every stored field slot without choosing a workflow definition, allowing
`_row_to_ticket` to return the stored `worker_type`, `stage`, ceiling, and fields without a Registry call.
Definition-supplied decoding remains strict for boot audit and semantic interpretation, while write doors
continue to validate the Ticket's Worker type, Stage, ceiling, and affected fields before persistence.
The obsolete read-door tests that expected a Registry error from plain row loading are corrected; the
startup audit and write-door rejection tests remain.

## D-ad01-delete-retired-state-helper — Do not rename an unused duplicate blocker helper

The static deletion scan found `sprints.logic.blockers`, an exported but uncalled legacy helper whose
entire interface is `ticket_states` plus coding-only terminal values. Current reads already use
`core.links.blocker_summary`. AD01 deletes the helper and its unused package export instead of preserving
rejected State vocabulary through a rename. `days/data.py` is also in scope for one comment correction
from “ticket state untouched” to “Ticket untouched”; neither change alters runtime behaviour.

## D-ad01-review-scope-isolation — Program memory is separate from the bounded implementation

The AD01 implementation diff may contain only its reviewed allowlist. `CONTEXT.md`, `PROGRESS.md`, and
`decisions.md` are still required orchestrator-owned domain and current-cycle records, so they are kept in
a separate architecture-program commit rather than folded into the delegated AD01 implementation commit.
This accepts the independent review's path-scope finding without pretending the live memory files are
implementation files or deleting decisions the operating model requires us to retain.

## D-ad01-served-frontend-bundle — Generated dist is part of the public replacement

FastAPI serves the checked-in `web/dist` entrypoint and hashed JavaScript, not the Svelte source tree.
AD01 therefore keeps the production build output generated from its reviewed source changes. The output
paths are added to the bounded allowlist only as build artifacts: they are produced by the existing Vite
build, never hand-edited. Reverting them after a successful build would leave the deleted
`/api/ticket-types` route and `ticket_type`/`state` response contract live in the product.

## D-ad01-verify-chat-scroll-fixture — Synchronize the unrelated flaky scroll fixture separately

The post-AD01 canonical gate exposed a pre-existing race in the echo half of the chat-follow e2e test:
the fixture assigned `scrollTop = 0` and immediately sent a message without dispatching `scroll`, so
under full-suite load the transcript render could snapshot `following=true` before the browser delivered
its asynchronous native event and snap back to the bottom. The product component and relevant test
sequence are unchanged by AD01. Ten natural focused runs passed; suppressing native delivery reproduced
the exact failure 3/3, while a synchronous event passed 3/3 target-reaching controls. The fixture now
dispatches the same event as the stronger cases beside it. This one-line, behavior-neutral stabilization
is a trivial collapsed ticket and an isolated commit before AD01, not part of the architecture contract.

## D-docs-are-map-plus-audit — The systems doc is a map plus boundary audit, honest about actual boundaries

`docs/systems.md` names the whole-system architecture and current boundary friction in one place, while
`docs/README.md` stays a short map to subsystem docs. The friction list is system-level only. Crucially
the map describes *actual* boundaries, not ideal ones: a read-only review caught places where the first
draft described a cleaner boundary than the code has (canonical writer functions, not "all domain data
files"; `SharedGateway` installed at startup but spawning its child lazily; the Board sharing today's
membership with the readiness loop, which narrows further). The HTML companion (`systems.html`) is a
reading surface (one system-spine, question-based orientation, disclosure rows), not a landing page.

## D-orchestration-hygiene — Orchestration keeps structure and current intent, not historical exhaust

`orchestration/` keeps current design intent, active plans, and each ticket's scope file, while old
generated reviews, raw Codex outputs, smoke scripts, reports, dogfood logs, and verify transcripts are
deleted — they were process evidence for completed work, not source of truth. Live guidance stays in
`CLAUDE.md`, `PRINCIPLES.md`, `PROGRESS.md`, this file, `docs/`, and the retained redesign
plans/mockups. Assistant guidance files (`AGENTS.md`, `CLAUDE.md`) stay maintained top-level docs, kept
current by inline correction, not turned into stale pointers or large duplicated architecture docs.

## D-green-wave-commits — Verified-green work commits straight to main as it lands

The redesign/build practice: each ticket or wave commits straight to `main` (or its worktree branch)
once its full `./verify` passes, so per-ticket checkpoints exist and later work is never stacked on
uncommitted state. Owner-delegated. Frontend-consolidation and ticket-types builds both followed this.

---

# Original build (`SPEC.md`-era) — retained rationale

These entries predate the current design intent (`SPEC.md` was retired). Most of the original build log
— audit rounds, verify-run bookkeeping, per-stage integration notes — was process exhaust and has been
dropped; git carries it. What remains here is the small set of original decisions still referenced from
outside this file, or still load-bearing.

## D-run-commits-local — Local commits during the run, never pushed (original build)

The unattended goal run committed locally at integration points (git worktrees can only see committed
state) and never pushed — superseding the global no-auto-commit preference for that run only. Retained
because the green-wave commit practice descends from it.

## D-orchestration-location — Orchestration records live in `orchestration/` (original build)

Named "orchestration" to avoid colliding with the product's own ticket entity; per-ticket pipeline
evidence lives under `orchestration/tickets/`. Still the live convention.

## D-snapshot-contradiction — The §12 snapshot self-contradiction (owner-accepted, not code-resolvable)

`SPEC.md`'s §12 called `migration/source-snapshot/` a frozen capture of the real data while §12/§18.3
pinned exact counts the original capture didn't match, with the migration test fence-locked to the
counts. This could not be resolved in code (SPEC was unmodifiable; the live data that would arbitrate
couldn't be read), so it recurred as an audit finding across several draws. **Owner ruling:** keep the
reconciled snapshot as-is (real capture, minimally reconciled to the spec's counts) and accept the
contradiction explicitly; do not revert (that fails the test forever) and do not keep looping the audit
for a clean pass. Both snapshot versions are preserved in git; `migration/README.md` (which references
this as D3/D21) records the full provenance. Retained because it's an owner impasse ruling and
`migration/README.md` points at it.

## D-component-inventory — The original 17-component UI inventory (superseded by the current component set)

The original build recorded a binding 17-component inventory (app shell, panel, markdown block, field
editor, meta chips, entity row, proposal card, grant-pair picker, plan tree, chat panel, state/grant
controls, create form, event log, run history, review card, error line) before writing any component.
Several `orchestration/tickets/T14–T17` records still cite it as "D11". The live component set is now
the shared-component set (see [D-shared-component-set]); this entry is retained only as the anchor for
those external `D11` references.

## D-orchestrator-tiering — Per-ticket orchestrator sub-agents with model tiering (owner directive)

The owner directed that each ticket be dispatched to a per-ticket orchestrator sub-agent (Fable) that
runs the plan → review → implement → review pipeline internally, while the top-level agent only
decomposes, integrates serially, runs `./verify`, and spot-checks load-bearing code. Fable for
orchestrators and load-bearing-groundwork planners; Opus for implementers everywhere. Retained because
it's the standing operating model, still in force.

---

# Superseded / merged IDs map

External references (`orchestration/`, `docs/`, tests, `src/`) still cite the old `Dxxx` numbers. The
file had two colliding number runs, so several old numbers meant two different things; the "content"
column disambiguates. Find the old number here to reach its current slug (or its disposition).

| Old ID | What it was about | Now |
|---|---|---|
| D1 | Local commits during the run, never pushed | [D-run-commits-local] |
| D2 | Orchestration records live in `orchestration/` | [D-orchestration-location] |
| D3 | §12 snapshot amended to the pinned ground truth | [D-snapshot-contradiction] (see `migration/README.md`) |
| D10 | T01/T02 integration reviews | Dropped (verify-run bookkeeping); `links.py` D10 comment = a different §3.6 cycle rule, unaffected |
| D11 | The 17-component UI inventory | [D-component-inventory] |
| D12 | Post-first-write test consolidation / link not_found | Dropped (test bookkeeping); `links.py` D12 comment is its own inline note |
| D21 | §12 snapshot reframed (audit round 4) | [D-snapshot-contradiction] |
| D29 (early) | Sprint-item derived status | [D-derived-sprint-item-status] |
| D31 / D34 / D36 (early) | Send-back / chat rows are not worker context | [D-panels-chat-not-worker-context] |
| D32 (early) | Legacy imported body → user note | [D-imported-ticket-note] |
| D33 (early) | Workspace mounts TicketRoute by id | [D-workspace-route] |
| D35 (early) / D45 | Chat scroll behavior | [D-chat-follow] |
| D38 | Ticket hard deletion | [D-hard-delete] |
| D39 | System A/B → responsibility names | [D-runtime-names] |
| D40 | Compound Ticket edit is atomic | [D-single-writer-edit] |
| D41 | Ticket files behind one preview contract | [D-file-preview-contract] |
| D42 | Every Markdown link has one preview component | [D-file-preview-contract] |
| D43 | Pause resolves durable keys via live identity | [D-pause-is-session-control] |
| D44 | Permanent delete stays outside normal UI | [D-hard-delete] |
| D46 | Chat images via Hermes native vision | [D-chat-images] |
| D47 | Pending worker context subsystem | [D-worker-context-subsystem] |
| D48 | Chief external-work intake | [D-external-work-intake] |
| D49 | Direct revision reserves the runner | [D-runtime-names] |
| D50 | Readiness ringing is best-effort | [D-readiness-ring] |
| D51 | Ordinary Ticket PATCH is one atomic edit | [D-single-writer-edit] |
| D54 | Ticket-owned artifact guidance in skills | [D-worker-artifact-guidance] |
| D55 | Implementation/Closeout are ordinary gates | [D-lifecycle-gates] |
| D108 / D109 (kickoff) | Kickoff is an ordinary gated field; title stays editable metadata | [D-lifecycle-gates] |
| D56 | Migration reads approval from legacy state | [D-lifecycle-migration] |
| D57 | Sprint planning is a review-first skill | [D-sprint-planning-skill] |
| D65 | Live chat state is Panels-owned | [D-live-chat-state] |
| D66 | Remount tests assert server state | [D-live-chat-state] |
| D68 / D70 / D73 | Workspace route / grouping / selection | [D-workspace-route] |
| D69 | Chief embedded, not a nav tab | [D-workspace-route] |
| D72 | Pausing is session control | [D-pause-is-session-control] |
| D74 / D75 | Owned turn identity through Hermes completion | [D-owned-turn-identity] |
| D76 (frontend) | Frontend consolidation program | [D-shared-component-set] |
| D76 (Hermes) | Ordered session ingress | [D-session-ingress] |
| D77 (frontend) | The `.select` class is dropped | [D-shared-component-set] (cited from `docs/frontend.md`) |
| D77 (Hermes) | Keep the correction Panels-only | [D-session-ingress] |
| D78–D80 (Hermes) | Ingress child-wide, settlement, boundary | [D-session-ingress], [D-owned-turn-identity] |
| D78–D95 (design) | Daily/UI redesign passes | [D-ui-redesign], [D-ui-fidelity] |
| D88 / D89 | UI-redesign program / UI-is-spec ruling | [D-ui-redesign] |
| D93 | Repair the live lifecycle migration | [D-migration-boundary] |
| D96 | `/new` is a native session transition | [D-new-session] |
| D97 | Planning DB owns the Hermes-home default | [D-hermes-home-default] |
| D98 (implementer) | Implementer assignment is one value | [D-implementer-value] |
| D99 (implementer closeout) | No merge/deploy/follow-up | Dropped (closeout bookkeeping) |
| D98 (live activity) | Live agent activity | [D-live-activity] |
| D100 / D101 | Rollover drafts the kickoff | [D-rollover-skill] |
| D102 (ticket-types) | Registry location, no-cycle seam, cadence | [D-ticket-types-registry], [D-green-wave-commits] |
| D103 (ticket-types) | Migration rulings / no 2nd prod type | [D-ticket-types-no-second-prod-type] |
| D104 (ticket-types) | Read-model delegated calls | [D-ticket-types-read-models] |
| D105 (ticket-types) | Worker realization, skill-driven | [D-ticket-types-worker-routing] |
| D115 | Job B: `new_worker` + global kickoff ceiling | [D-ticket-types-new-worker] |
| D102–D114 (closeout) | Per-ticket closeout/integration bookkeeping | Dropped (git carries commits + integration facts) |
| D4–D9, D13–D28 (original) | CLI location, test-mode clock, seed tensions, audit rounds, verify-run notes, ticket-redesign inventories | Dropped (original-build process exhaust; git carries it) |
| D58–D64 (segments) | Review/ticket UI segment cleanups | Folded into [D-shared-component-set] / [D-workspace-route] |
