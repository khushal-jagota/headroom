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

---

# Ticket types (the type-registry machinery)

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

## D-file-preview-contract — Ticket files are managed content behind one preview contract

Canonical ticket fields, notes, proposals, results, and chat stay SQLite text. Standalone work
products live beside the database under `files/tickets/<ticket_id>/` and are referenced by ordinary
Markdown links; Panels serves only safely resolved regular files. The public frontend seam is
`FilePreviewTarget` + `FilePreview`, so every surface shares one classifier and renderer instead of
per-surface file logic — no upload API, artifact rows, file ids, or per-stage attachment slots. Every
Markdown link routes through `FilePreview`: safe image/video/audio/managed-Markdown render inline; HTML
is a sandboxed preview card (empty-permission sandbox) whose **Open preview** action leads to a
dedicated document-only route where the sandboxed HTML fills the page (a Blob-backed iframe, avoiding
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
