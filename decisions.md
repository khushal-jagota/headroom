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

## D-isolated-runtime-acp-integration — Keep ACP and adapt environment seams

The isolated-runtime commit is integrated against the current ACP-era main tree. Deleted
`planner.minds` modules are not restored: the opt-in Hermes smoke launches each non-production
instance through the official conversation ACP child factory, with the instance Hermes home and
`HOME` in the scrubbed subprocess environment. Deleted chat-managed-file storage is not restored:
the fake fixture writes both representative files under the current ticket-managed file root.
This keeps the environment feature while preserving the current conversation and file ownership
boundaries.

The official environment Hermes smoke carries credential-file names as CLI metadata into its
subprocess, never credential values. The child validates those names again and passes them through
the shared confined-environment builder via an opt-in definition extension. The production Hermes
catalog continues to use only `HERMES_INHERITED_ENVIRONMENT_NAMES`, preserving its scrubbed-env
boundary and avoiding any legacy `planner.minds` compatibility layer.

## D-isolated-runtime-review-corrections — Resolve launch inputs before scrubbing and verify durable smoke sessions

The ordinary environment runner explicitly resolves the operator-selected Hermes Python path from
the caller's ambient mapping before replacing `HOME`, then writes only that value as the
contract-owned `PLAN_HERMES_PYTHON`; arbitrary ambient `PLAN_*` values remain excluded. The
official ACP smoke captures typed agent-message text and validates both `end_turn` and the exact
response requested by its prompt. It closes the first child and loads the returned session id in
a fresh child against the same isolated home before printing `stored=`, so authentication or
model refusal cannot masquerade as success and persistence is proven. Smoke sessions are
unrelated durable sessions belonging to those homes, not temporary sessions to delete.

## D-ticket-projection-v29-after-main-v28 — Keep migration numbers forward-only

`main` already consumes schema version 28 for retiring the Learning default project. The Ticket
conversation projection therefore uses the next forward version, v29, with its own shape validation,
dispatch, and migration tests. The existing v25 through v28 migrations keep their established behavior.

# Workspace

## D-ticket-projection-cutover-lock-and-permission-compensation — Serialize replacement publication with Ticket facts

Compaction and requested-cancel recovery acquire the existing per-Ticket projection lock only around their final
sequencer enqueue and stream cutover. Preparation, broker settlement, backend I/O, and replay construction stay
outside that lock. This gives an old-binding projection publication one indivisible validation/write/envelope
window against replacement, while Chief and other non-Ticket streams remain unchanged. A permission-request
projection fact is cleared with `record_permission(False)` if its envelope publication raises; that compensation
does not clear the separate response-attention fact.

## D-workspace-dot-is-one-derived-ticket-result — Keep ACP facts durable and the visual decision centralized

Workspace owns one pure classifier per Ticket. It accepts factual Ticket status/proposal facts and a minimal
durable ACP projection (latest activity, completed response awaiting the user, and pending permission), then
returns exactly `exceptional`, `active`, `needs_attention`, or `quiet` with fixed precedence. ACP and Ticket
writers do not choose visual states, and the Svelte Workspace route maps only that result to the existing
StageMark vocabulary. The projection uses ordinary `t_*` event IDs so the existing resource catalogue
invalidates `ticket:<id>`, Board, and current Sprint without a client canonical store or per-row conversation
subscription. SQLite writes run in short `to_thread` transactions; a new conversation clears stale facts.

The accepted semantics require admitted prompts to become active before the later ACP activity envelope, and
permission waiting must remain attention even while the durable Ticket status says `agent_running_step`. A
completed idle turn remains attention until the next admitted prompt or explicit reset. Initial absence of ACP
facts falls back to Ticket facts, preserving quiet initial load and truthful status-only behavior.

The projection treats connecting and loading as active activity facts without changing the response fact. An
idle fact creates response attention only when the previous fact was thinking, working, or compacting; a new
admitted turn clears that response fact. Permission is cleared only by the published permission outcome. Hub
Ticket publications synchronously validate the exact current employee and binding while holding the existing
per-Ticket projection lock, await the SQLite projection write through `asyncio.to_thread`, and only then enqueue
the ACP envelope as the final awaited operation. This applies to activity, accepted delivery receipts, permission
requests, and permission outcomes; non-Ticket paths remain direct. A stale publication queued behind New
Conversation therefore fails validation before writing, while a publication that wins the lock writes first and is
then cleared by the successful replacement reset.

The implementation-review P2 about restoring done/paired/takeover field marks is refuted by the approved
ticket: the single Workspace dot is deliberately the derived Ticket result, not a field-stage status mark.
Field-stage marks elsewhere, grouping, navigation, and interactions remain unchanged, so no Workspace status
mapping is restored.

## D-workspace-three-level-grouping — Derive hierarchy from the existing board and manifest

The production Workspace left rail groups each ticket as project → worker type → its current stage.
Only populated groups render. Project names, worker labels, stage labels/order, ticket running state,
and activity ordering already exist in the board response and worker-type manifest, so this is a
client-side derivation and visual hierarchy change with no new backend field. Ticket rows keep only
their title and the existing running mark because worker and stage are carried by structure. Plan and
implementation review are collapsed to one frontend ticket because the API contracts are unchanged
and the change is confined to one route, its CSS, and focused e2e assertions.

Independent review widened the test-only boundary to the pre-existing paired-work Workspace assertion:
that assertion encoded the superseded ticket-level stage/marker presentation and must now prove the
same paired ticket is under its stage group with no non-running row mark. The production boundary is
unchanged. The review also restored the approved prototype's exact 40px project title and 44px project
separation instead of weakening that owner-chosen hierarchy to the nearest existing type/space tokens.

The canonical gate found one additional cross-suite E31 reload snapshot that asserted the superseded
pending-proposal dot. This is an integration-only test repair: reload still proves the same ticket title
and canonical stage survive, but now does so through the Implementation group nesting and absence of a
non-running row mark. No production scope was widened.

Owner correction supersedes the earlier "running mark only" interpretation. Stage grouping removes the
duplicated stage label from each row, but the existing condition circle remains because it communicates
whether the ticket is waiting, running, needs approval, is paired, errored, or complete. A ticket row is
therefore title plus one condition mark, with no worker/stage byline.

The final nested hierarchy uses normal-case worker-type headers and compact uppercase stage labels. Worker
identity should read as the content heading inside its card; stage is repeated navigational metadata and
therefore uses the smaller tag treatment.

# Tickets, gates, and the resolution engine

## D-blockers-derived-intake-and-presentation — Keep one relationship and derive its effects

Both Ticket-creation paths accept `blocked_by_ticket_ids` and add those existing Ticket
sources through the canonical `blocks` writer inside the creator's one transaction. This
keeps endpoint, duplicate, and cycle validation plus link events in one engine; the action
wakes eligibility only after that transaction returns successfully.

Blocked is presentation and scheduling, never stored Ticket position. Kickoff wins first;
after Kickoff, an active incoming blocker selects a synthetic quiet Workspace section while
the card retains its real Stage. Ticket detail projects only active direct incoming rows and
removes them through the existing link-delete route. Reverse and cleared rows remain
available to internal link readers that need canonical relationship facts, but are not a
Ticket-detail or copied-Ticket presentation.
Blocker removal retains visible `Remove` text but uses the blocker title in its accessible
name so each control is distinguishable.

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

## D-stage-ownership — Stage ownership and worker scope are separate controls

Each non-terminal Worker-type Stage declares whether work is normally **worker-owned**,
**user-owned**, or **paired**; a Ticket may override that declaration for a particular Stage. The
effective ownership determines the Ticket's inactive control state: worker work is ready for automatic
eligibility and user work rests in `user_takeover`. A newly eligible paired Stage receives exactly one
automatic opening worker turn in durable Ticket Chat, then rests in `paired_work` while ordinary user
messages continue the same Employee session. A later paired Stage reuses that session but still receives
its own one opening turn. A paired worker's real field proposal always parks for approval; paired work is
not a renamed proposal or a second dispatch loop.

The same discovery loop owns paired openings. Entering worker- or paired-owned work writes `empty`; a
successful paired opening settles to `paired_work`, which is not redispatched after its Stage-entry event
has a later worker-step event. Existing silently parked paired Stages use that event history for one
compatibility opening without adding a new persisted flag. A transient busy gateway releases the claim to
`empty` but waits for the periodic poll instead of immediately waking into a retry loop.

## D-worker-live-session-identity — A worker resolves its Ticket from the live gateway session

`panels worker my-ticket` prefers Hermes' per-window live-session identity and asks the Panels-owned
shared gateway to map that identity back to the durable Employee session. The process-level
`HERMES_SESSION_KEY` remains only a fallback for older single-session surfaces. Live identity is
therefore authoritative whenever Hermes supplies it; zero or multiple owners fail rather than guess.

The restart trace proved Panels resumed the right durable conversation, but Hermes' shared local
terminal shell snapshot restored another conversation's `HERMES_SESSION_*` values after current
ContextVars were injected. Hermes now restores every mapped per-turn session variable around each
foreground command, including when the snapshot contains functions named `export` or `unset`. Panels
hardens its own durable boundary: the canonical transactional writer rejects a candidate session
owned by another Ticket, including force-fresh human bindings, and direct durable reads fetch every
owner deterministically and reject impossible duplicates with sorted Ticket ids. Same-Ticket
idempotence remains only while ownership is unambiguous; an already-corrupt duplicate fails before
the idempotent return. Compare-and-swap still adopts a unique current winner, but rejects an ambiguous
winner before returning it to a caller. Production data had zero
duplicates, so a schema migration would add machinery without repairing any live row and was not
added; the existing `BEGIN IMMEDIATE` callers serialize the ownership gate. The supported restart path
resumed the original durable conversation and `panels worker my-ticket` resolved the correct Ticket.

Ownership does not absorb `(ceiling, at_cap)`. Scope still limits autonomous continuation. The stored
`at_cap=propose` contract remains unchanged and is displayed as **Continue**; **Stop** is the explicit
Ticket override. User-completed work returns through Chief external-work reconciliation, which preserves
that explicit Stop instead of imposing one. The old human `khushal` implementer route is represented by a
coding Implementation ownership override. Tickets carry no separate execution-route selector; the Worker
type chooses the specialist skill.

## D-stage-ownership-explicit-overrides — Override-map identity, not effective mode, defines no-op writes

An ownership write is a no-op only when the explicit `stage_ownership_overrides` map would be identical.
Setting an explicit override that matches the Stage default still persists the map and emits an ownership
event; clearing an explicit override still persists the removal. The event records both the previous and
new effective modes so paired-opening compatibility can distinguish real transitions into paired from
same-effective paired events. Future-Stage override writes never change the current Ticket status. On the
current Stage, only a real effective-mode change uses entered-Stage status; same-effective explicit writes
preserve the current status.

## D-stage-ownership-integration-repair — Tests pin ownership, not retired transition hooks

The stage-ownership integration repair translated old `implementer=khushal` and transition-hook tests
to explicit ownership contracts: coding legacy `khushal` migrates to
`stage_ownership_overrides["needs_implementation"] = "user"`, probe uses declared mixed defaults, and
the v22 migration discards the retired agent-route values. Plain Ticket reads now
require the Worker-type registry because Ticket detail exposes default/effective ownership; the old
registry-free read assertion was retired. Chief reconciliation is allowed from inactive owner states
(`user_takeover` and `paired_work`) because user-completed work returns through Chief, while active
worker/proposal/chat states still block it. Run settlement derives the resting owner state only when it
actually changes, so no-op status events are not emitted.

A delayed post-removal review found that old route-change audit events and generated worker prompts could
still expose retired route values. Schema v23 deletes legacy `ticket_updated` rows for `execution_route`
or `implementer`, redacts the generated route clause from stored worker-step prompts, and filters the same
generated clause when reading authoritative Hermes history. The obsolete private v21 route-writing
migration was removed; v20 and existing v21/v22 databases now migrate directly to the route-free shape.

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

## D-hosted-trusted-ingress — Tailscale Serve is the first hosted boundary

The sole hosted Panels instance stays bound to VPS loopback and is exposed first through Tailscale
Serve. A matching `Tailscale-User-Login` selects the remote-human path and removes every caller-supplied
`X-Plan-Actor`; headerless same-host traffic keeps the existing trusted runtime provenance. Tailscale
ACLs admit only the owner's non-tagged devices, while wrong or duplicate identity headers and wrong or
duplicate browser Origins fail closed across HTTP, files, and WebSockets. No-Origin HTTP clients remain
valid, and this decision adds no generic request, body, rate, or connection limits. External tools use
the ordinary `POST /api/messages/chief` contract, which reuses the canonical asynchronous Chief turn.
Provider-specific parsing stays in the server shell so a future clientless gateway can replace
Tailscale without redesigning Panels or Chief intake.

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

## D-new-worker-understanding — Understanding is the paired first working Stage

`new_worker` now enters `needs_understanding` immediately after Kickoff. It is paired by default:
ordinary Ticket Chat continues the durable Employee session, automatic discovery does not claim it,
and an Understanding proposal always parks for approval. Its specialist asks a small purposeful core
set, follows up only for material gaps, and stops once purpose/outcome, hard judgment, and the material
constraints/examples/boundaries are sufficient to design the lifecycle. Approval then enters the
unchanged Stages → Thinking → Drafting → Closeout → Done sequence.

Understanding deliberately becomes `new_worker.first_worker_stage()`. External-work prefixes and
sprint-stage thresholds therefore move with that meaning instead of keeping `needs_stages` as a hidden
special case. Existing Tickets at Stages or later are never rewound; an idempotent startup migration
adds only an empty `understanding` field slot to old `new_worker` rows before the strict registry audit.
Deterministic tests assert the shipped specialist protocol and separately prove real session delivery,
paired readiness, and proposal parking; they do not pretend to prove a model's judgment.

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

## D-server-lifecycle-owner — The foreground serve command owns controlled replacement

`panels serve` is the stable process owner; the FastAPI application and Employee runtime
run in one replaceable child. `panels restart` can ask that owner for a replacement but
cannot supply a PID, worktree, interpreter, environment, or launch command. The owner
acknowledges first, lets the existing application shutdown settle through its one current
deadline, waits for that exact child to exit, and only then starts the next generation
from the launch state captured by `serve`.

Workers finish durable pre-restart writes before using the command. They never inspect
or signal the server process and never start `panels serve` from a Ticket worktree. When
the controlled command is unavailable, the worker reports the operator action and stops.
This is an operational safety seam, not an OS security sandbox; same-user hostile-process
isolation remains out of scope.

## D-isolated-runtime-environments — One contract, separate mutable instances, host account for live

`live`, stable `staging`, and disposable `preview-<id>` use the ordinary `panels serve` runtime through
one repository-owned environment contract. Every instance has its own database, managed files, Hermes
home/session state, port, control socket, locks, logs, credentials reference, and repository/worktree.
Staging and previews share only one immutable versioned synthetic fixture definition; each materializes
its own mutable database and files, and reset rebuilds only that instance. Live never receives fake data
and is ultimately protected from `panels-worker` by the operator-owned `panels-live` Linux account.

Launch starts from a scrubbed allowlist rather than overlaying the invoking shell. Runtime provider
credentials come only from the prepared instance's validated file; Tailscale setup authority is not an
application runtime credential. `panels environment run` requires a caller-provided repository root,
checks it against the prepared allowed roots, and uses that exact root for exec, so a manifest cannot
self-authorize a different worktree. Destructive operations are non-live only and hold the same
port-scoped lifecycle lease through the full mutation.

The repository checks path/resource collisions, fake-state independence, and three concurrent real
server processes locally. Checked-in systemd/account assets are render/install inputs, not proof that
Linux ownership exists: VPS enforcement remains false until an operator installs and verifies the
accounts, permissions, credentials, units, and ingress on the target host.

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

## D-runtime-restart-continuation — Restart recovery resumes sessions instead of replaying prompts

Restart recovery is a continuation problem, not a retry queue. A ticket left at
`agent_running_step` resumes its stored Hermes session with a fresh recovery message that tells the
worker to inspect the canonical ticket and existing conversation, continue unfinished work, avoid
repeating completed actions, and file the current proposal. Panels never resends the original worker
prompt, never creates a replacement session for this path, and never parses worker prose into ticket
state. Ordinary chat recovery uses the same stored-session rule but excludes worker-owned ticket ids.
Shutdown uses one configured monotonic deadline; unfinished ticket work remains `agent_running_step`
so the next startup can continue it.

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

## D-chat-terminal-outcomes — Failed and interrupted turns stay in the Panels transcript

`chat_turns` remains the canonical owner of terminal status, partial output, error, and session
identity. Chat state projects failed and interrupted turns beside the durable messages for display;
it does not duplicate their output or status into a second canonical message record. The shared Chat
panel labels failure and interruption distinctly and never exposes the stored internal error text.

A human terminal turn can be continued only when it is the latest turn, is still bound to the entity's
current Hermes session, no turn or Ticket Employee step is active, and no prior continuation has claimed
it. The new turn records the exact predecessor and sends a continuation instruction through the existing
session; it never replays the original prompt or falls back to a new session. Worker-origin outcomes remain
visible but have no human continuation action. Day state uses the same backend contract, while the UI stays
limited to the currently mounted Ticket and Chief Chat surfaces.

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

## D-managed-markdown-gfm-pipeline — Standards parsing replaces the handwritten subset

Panels renders Markdown through one Vite-owned unified/remark/rehype GFM pipeline instead of extending
the line-oriented classic asset. Raw HTML remains inert because executable HTML belongs in managed HTML
artifacts; a strict sanitizer admits only renderer-authored semantic output and the exact-link-token
metadata required by managed previews. `managedMarkdown.ts` remains the sole DOM owner. Once a rendered
surface is edited, reverse rehype/remark conversion emits canonical GFM while collision-proof temporary
placeholders preserve atomic preview and rendered-image tokens, definitions for surviving references
remain attached to their rendered surface, and generated preview descendants stay excluded. Deleted
references do not leave stale definitions. The migration removes every old global/static renderer seam
and retains the complete existing editing, retry, paste, Escape, move/delete, teardown,
recursive-preview, and self-link contract.

## D-file-preview-contract — Ticket files are managed content behind one preview contract

Canonical ticket fields, notes, proposals, results, and chat stay SQLite text. Standalone work
products live beside the database under `files/tickets/<ticket_id>/` and are referenced by ordinary
Markdown links; Panels serves only safely resolved regular files. The public frontend seam is
`FilePreviewTarget` + `FilePreview`, so every surface shares one classifier and renderer instead of
per-surface file logic — no upload API, artifact rows, file ids, or per-stage attachment slots. Every
Markdown link routes through `FilePreview`: safe image/video/audio/managed-Markdown render inline; HTML
is a sandboxed preview card whose shared policy grants only script execution, keeping the document at an
opaque origin without same-origin or other host-page privileges. The dedicated preview route treats HTML
and Markdown as document kinds: HTML fills the page in the same Blob-backed sandboxed iframe, while
Markdown fills the page through the shared `MarkdownBlock` renderer with its managed links, nested-preview
bounds, and security behavior intact. Embedded Markdown keeps its bounded inline presentation; unsupported
managed files and external URLs remain cards with download/open actions. Editable Markdown stays one
continuous `contenteditable` where each link is an atomic preview island carrying
its exact Markdown token; serialization emits that token and skips generated preview descendants.
Managed Markdown expansion uses a fixed depth and visited bound, and a tokenized max height (in
`tokens.css`) so long previews scroll and short ones keep natural height — a component-level visual
bound, not a truncation contract.

## D-managed-html-document-base — Preview assets resolve through the browser, not a new file transport

Fetched managed HTML is prepared once with an absolute base pointing at the HTML file's own managed URL,
and both the embedded `srcdoc` and full-page Blob lifecycles consume that same document. This preserves the
existing `allow-scripts`-only opaque-origin sandbox and lifecycle cleanup while letting the browser resolve
relative and root-relative same-tree assets. A proxy or per-attribute URL rewriting would duplicate the
managed-file boundary and miss URL-bearing HTML/CSS forms, so neither is added. Implementation was delegated
to Codex in an isolated ticket branch and independently reviewed; no visual planning artifact was created
because this changes resource resolution without changing layout or interaction design.

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

## D-event-websocket-owns-connection-health — One channel owns liveness and recovery

The existing event WebSocket is the sole browser-to-server health authority. Quiet server periods use
an empty event-batch heartbeat (`events: []` with the unchanged cursor); the client confirms health
only from a valid frame, keeps cursor replay and indefinite capped backoff, and exposes
Connected/Reconnecting/Offline as a reactive shell status. Recovery performs one catalogue-owned
refresh of currently subscribed identities in addition to ordinary event-key invalidation, so stale
active resources are repaired without coupling the socket to cache internals. Connection health stays
visually and semantically separate from worker presence; both remain visible in the narrow shell.

## D-slate-blue-brand-accent — Soft Steel is the single brand accent

The owner selected Option A — Soft Steel from the four in-context slate-blue comparisons. Panels keeps its
warm-dark surfaces and routes the selected family through the existing four shared brand tokens: bright
`#9aadd2`, surface `#222a38`, text `#dce6f8`, and ink `#111318`. Done green and error red remain semantic
tokens rather than being recoloured as brand treatment. This is a token change, not a component redesign.

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
switching tickets doesn't reset the rail's collapsed groups or hide-done choice. Once a settled board
proves a routed ticket absent, Workspace replaces the stale entry with `#/workspace`. The left rail
groups by effective project (parented tickets group under their item's project via `group_project_id`
without changing their own `project_id`) and shows all four ticket-stage dots. Hide-done defaults on.
Chief of Staff is embedded in Workspace, not a primary nav tab (the `#/chief` route stays for direct
links).

## D-workspace-row-byline — Workspace rows show worker type and current stage together

Workspace ticket rows use the owner-selected stacked-byline treatment: the title stays on the first
line; the registered Worker-type label and current Stage label stay together on the left of a quiet
second line; the existing `StageMark` sits alone at that line's far-right edge. Active rows read their
Stage label directly from the board card's gating-field label. Terminal rows use the stored terminal
state label (`Done`/`Dropped`) while retaining the existing closeout StageMark fallback, so display
text does not distort marker behavior.

The board payload remains unchanged. Workspace reuses the cached Worker-type manifest for registered
type labels and keeps StageMark state driven only by the existing board card fields. This is a
Workspace-specific row shape rather than a new generic ListRow capability; grouping, filters,
selection, and navigation remain unchanged.

## D-workspace-ticket-order — Served Worker-type and Stage order precede activity

Within each existing Workspace project category, rows follow the served Worker-type manifest order,
then that type's own served Stage order, then `activity_at` newest first. The prior board sequence is
the final deterministic fallback only. Workspace treats both the board and Worker-type manifest as
required resources before rendering rows, so it never briefly presents the retired activity-first
order while the manifest is loading. Category grouping and order, hide-done behavior, collapse state, row
content, selection, navigation, and presentation do not change.

## D-workspace-hide-done-only — Hide done is Workspace's only visibility filter

A fresh app session starts Workspace with **Hide done** on, so its normal today scope emphasizes active
work. Turning it off reveals done tickets, and the existing app-level state preserves that choice while
navigating between screens; no new browser or server persistence is added. Workspace otherwise shows every
Ticket status. The status selector, its local state, and its filtering branch are removed rather than
replaced with a Stage selector or another visibility control, because fixed statuses do not usefully span
multiple Worker types and Stage lifecycles.

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

## D-architecture-restart-integration — Preserve one restart deadline through the renamed runtime

Current main's restart-continuation work is later than AD03 and requires one absolute shutdown deadline
across discovery, the Employee runner, gateways, and child cleanup. The integrated
`AutomaticEmployeeStepDiscoveryLoop.stop` therefore accepts that deadline. This narrowly supersedes the
original AD03 no-argument stop signature; it does not change Automatic Employee-step eligibility, candidate
discovery, atomic claim, wake behavior, polling-lock ownership, or ordinary startup ordering. Restart
recovery uses the architecture owners: strict existing-session resume, `employee_session_id`, canonical
first-wins Chat settlement, and the canonical Ticket transition writers. If a stranded Employee step has no
stored Employee identity, the existing worker turn and still-running Ticket are both errored with no
replacement turn and no gateway call.

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

# Hermes integration restructure (2026-07-17)

## D-hermes-relay-architecture — Panels relays the native conversation instead of translating it

Owner decision after a full structural investigation (2026-07-17). Panels' server stays the single
holder of the Hermes connection (multi-tab, VPS hosting, auth all ride it), but it becomes a
transparent relay instead of a translator: conversation frames pass through to the browser in
flight, and Panels only tees the few facts it needs (turn settled/failed, session ids, optional
audit copy). The DB leaves the conversation's path — no more store → invalidate → refetch → 500 ms
poll, no per-feature rebuild of what Hermes already ships (streaming, clarify, approvals, model
picker, reconnect/resume). Grounding: `tui_gateway` is Hermes's official programmatic surface
(stdio and WebSocket, identical wire format, served by `hermes serve`; Nous's desktop app and
dashboard consume it through the shared client `apps/shared/src/json-rpc-gateway.ts`). Panels'
stdio-child embedding plus rebuilt client was the outlier integration and the direct cause of the
owner's three named pains. Staged plan S0–S5; S0 is a live protocol spike before any product code.

## D-runtime-neutral-pane-vocabulary — The chat pane speaks a runtime-neutral, ACP-shaped protocol

Owner constraint: workers may soon be Hermes, Codex CLI, or Claude CLI — "CLI agents with a GUI and
skills." So the pane must not speak Hermes's wire format. The relay normalizes each runtime's
native stream into one neutral event vocabulary (turn started, text delta, thinking, tool call,
agent-asks-user, needs-approval, turn done/failed), shaped on ACP — the protocol all three runtimes
already have adapters for. Backend connections stay native per runtime (Hermes via the gateway WS,
keeping model options/command catalog richness); only the Hermes translator is built now. This
normalization is a stateless in-flight frame mapping — the structural sin being removed was the DB
in the path, not translation itself.

## D-stock-hermes-only — Panels runs on stock Hermes; local patches are to be undone

Owner ruling (2026-07-17): Hermes updates will erase local modifications, so Panels must not
rely on them. The three local terminal-session-isolation commits (merged `047ba8298`) exist
because worker identity rides `HERMES_UI_SESSION_ID`/`HERMES_SESSION_KEY` subprocess env, and
stock Hermes intentionally collapses all sessions' local shells into one shared "default"
environment (`tools/terminal_tool.py::_resolve_container_task_id`) whose persistent snapshot can
carry one session's identity exports into another's subprocess. Stock offers no per-session
isolation knob for local shells (isolation triggers only on `env_type`/image overrides,
registered in-process). Consequence: retire env-derived worker identity (see
`D-panels-issued-worker-identity`), then revert the local Hermes commits to stock. Sequencing:
revert only after the replacement identity lands, or live workers break.

## D-child-per-employee — One child process per employee; identity is the child's spawn env

Owner decision (2026-07-17), superseding `D-panels-issued-worker-identity` (prompt-carried
credentials — never built) and the shared-`hermes serve` upstream in the relay plan's original
S1. Each employee (each active ticket, plus the Chief) gets its own child process holding
exactly one session. Identity becomes the child's spawn environment: Panels sets the ticket id
and actor when it spawns the child; every shell the child runs inherits them; `panels` reads
them. Crossover is impossible by construction — the leak needed two sessions in one process —
so this is safe on fully stock Hermes and works identically for future Claude CLI / Codex CLI
employees, where the ticket's runtime choice simply selects which binary Panels spawns behind a
thin adapter. Further effects: the 1,100-line session demultiplexer loses its reason to exist
(one stream per child), worker crashes are isolated per ticket, and this matches Hermes's own
first-party kanban swarm (process per task), not a multiplexed backend. Accepted costs:
in-flight turns die with a Panels restart (today's behavior; recovery machinery exists and
shrinks) and N resident processes instead of one.

Reaping policy: none in v1. Children spawn on demand and live until their ticket closes or the
server shuts down — today's behavior at per-ticket granularity. Prompt caching gives no reason
to keep children alive (caches are provider-side, keyed on conversation prefix and short TTLs;
Claude/Codex are per-invocation processes by design), and a respawn costs seconds of agent
rebuild plus a fresh shell environment (accumulated exports/cwd reset — the one real state
loss). Idle reaping is a memory-pressure feature, added only if resident children actually
hurt.

## D-relay-raw-frame-transport — The relay speaks raw stdio frames; the pool owns session binding

Orchestrator ruling on the S1 impasse (2026-07-17), Codex-review-driven. `GatewayChild` is a
typed JSON-RPC client — it rebuilds requests under its own ids, unwraps responses, and consumes
`gateway.ready` — so it cannot carry frames verbatim, which the relay contract requires. The
relay therefore reads/writes raw newline-delimited frames directly on the child's stdio via the
existing `planner.minds.gateway` spawn seam (`SpawnFn`/`ChildProcess`/`spawn_popen`), preserving
byte-level payloads and child emission order; `GatewayChild` stays untouched for the legacy role
children. Companion ruling: downstreams talk to an employee, never to session lifecycle — the
relay rejects session-binding methods (`session.create/resume/close/delete/activate` + any
further binding-capable verbs the implementation plan identifies) with an error frame. Denylist,
not allowlist, so every other native method keeps flowing for free; the set is re-checked when
the Hermes checkout advances.

## D-s1-concurrency-scope — S1 fixes contract-relevant concurrency bugs; deep edges are trigger-bound limitations

Orchestrator ruling during S1 plan review round 3 (2026-07-17). Genuine plan bugs are fixed
(positional shutdown call, in-flight init publishing after close, death between resolution and
registration, unmatched-response fan-out, mechanical denylist-completeness test), plus one cheap
structural hardening (a dedicated bounded init/shutdown executor, which also removes executor
starvation). Two residual robustness edges are accepted as documented S1 limitations, each bound
to the S2 trigger — they must be re-examined when the backend gains real browser consumers, not
before: (1) a failed-write death signal can overtake a child's final already-emitted stdout frame
(death semantics are child-reset + respawn; downstream re-syncs from durable session state, so
lossless ordering across death is not a contract promise); (2) unbounded relay queues (test-only
consumers in S1; a slow real downstream changes the calculus at S2). Review-loop termination:
one confirming Codex round after the fixes; findings that are genuine contract violations always
block, but new beyond-contract robustness edges join the trigger-bound limitations list instead
of extending the loop.

## D-only-free-hermes-features — If Hermes doesn't give it for free, leave it alone

Owner directive (2026-07-18): no overbuilding around the runtime. Panels exposes what Hermes
hands over and builds nothing speculative on top. Sharpened the same day: the NECESSARY
integration is exactly the two things the owner named at the start — programmatic user-message
send and skills attached as defaults (plus what the product already visibly does today).
Model selection, command menus, and any other capability beyond that are nice-to-haves, never
necessary, and never drive contract scope or delay. The free/not-free line, applied by the
owner the same day: **compact, new, and a skill picker are IN** because Hermes gives each for
free — compact is `session.compress` (also a native `command.dispatch` builtin), new is the
pool's own `session.create` (S2b, pool-owned), and the skill picker is the free
`commands.catalog` listing rendered by the pane with skill selection inserting the trigger as
ordinary message text (skills are model-side; zero execution machinery). **Set-model /
model-options and generic mediated command execution stay OUT** — the switch isn't free (the
real seam, session-scoped `config.set`, is recorded in the S2a contract for any future want;
nothing built), and generic execution grows machinery. The S1 denylist blocks the escape
hatches regardless.

## D-codex-loop-cap — One Codex review round per artifact, two at most

Owner directive (2026-07-18, for speed, "for now"): each pipeline artifact (plan,
implementation diff) gets ONE Codex review round, with at most one confirming round when the
first drove material fixes. Findings from a round are still addressed or refuted before
proceeding — the cap bounds rounds, not the fixing of what a round finds. Beyond-contract
robustness edges go straight to the trigger-bound limitations list (same discipline as
`D-s1-concurrency-scope`) instead of earning further rounds. Owner clarification
(2026-07-18): Codex reviews live inside the ticket agent's pipeline ONLY — the top-level
orchestrator never adds its own Codex round at integration; its part is the serial merge, a
personal spot-check of load-bearing code, and the one canonical `./verify`.

## D-native-turn-concurrency — No Panels-invented busy semantics; Hermes's defaults govern mid-turn sends

Owner ruling (2026-07-18): "busy guard doesn't need to be a thing, we don't make up our own
functionality." Stock Hermes queues a mid-turn `prompt.submit` (by default interrupting the
live turn — `_handle_busy_submit`); steering/interrupting/queuing are the runtime's own
semantics and Panels adopts them as-is. Consequences: the S2b pane never disables the composer
or renders a synthetic busy state; S3 does not rebuild a chat-vs-step admission gate — the
legacy one-busy-guard model is superseded for relay surfaces. What remains Panels' own is
dispatch bookkeeping only: the discovery loop still tracks whether an automatic step is in
flight for eligibility, which is scheduling state, not conversation policy.

## D-s3-collision-rulings — Settlement by ACK disposition; transitional CLI fallback; per-session ownership invariant

Orchestrator rulings on S3's plan-review collisions (2026-07-18), recorded in the amended S3
contract: (1) step settlement correlates by the `prompt.submit` ACK disposition
(streaming/queued/steered) — owned-turn identity reconstructed from what stock Hermes returns,
no demux, with all three variants plus an interleaved human send tested; (2) the `panels` CLI
reads `PLAN_TICKET_ID` first with an explicitly TRANSITIONAL Hermes-env fallback for the
flag-off legacy shared child (which cannot carry per-ticket spawn env) — the fallback dies with
the legacy worker path at S4, and de-patching (S3b) may proceed once flag-on is the operating
mode since the patches only protected the shared-child topology; (3) flag-on the legacy worker
gateway keeps running for its non-ticket consumers (day chat, command catalog, status,
session-history) but owns no pool-owned entity's session — the two-owner invariant is per
stored session, not per process; (4) relay test mode composes the real runner + pool step
gateway with a test-gated step-trigger route (S2b's established test-machinery pattern); (5) a
narrowest call-only `bind_pool_employee_session_id` writer routed through the ownership CAS.

## D-relay-flag-rename-debt — `relay_chief_enabled`/`relayChief` are misnamed once tickets ride them

Recorded during S3 closeout (2026-07-19). S2b shipped the capability meta key
`relay_chief_enabled` and the frontend `relayChief` signal; S3 reuses both to govern ticket
panes too, so the "chief" name now lies about scope. Kept as-is inside S3 (one flag, minimal
churn into shipped S2b callers mid-ticket — the resume orchestrator's call, accepted). Owed: a
small dedicated cleanup ticket renaming to backend-scoped names (`relay_backend_enabled`-style)
per name-for-exactly-what-it-is; natural moment is S4's deletion sweep.

## D-chief-first-cutover — The Chief moves to the relay first; ticket chat and steps move together

Orchestrator sequencing ruling cutting S2 (2026-07-18). A ticket's human chat and its worker
steps share ONE stored employee session, and a stored session must have exactly one owning
process (the store does not lock across processes — S0 phase 7). Swapping ticket chat to the
pool while steps still run on the legacy role children would give one session two owners. The
Chief of Staff has no automatic steps, so the S2 cutover targets the Chief only; ticket
employees move chat + steps TOGETHER in S3. Two consequences bound into S2: (1) the neutral
vocabulary and its Hermes translator land as their own backend ticket before the pane ticket
(the pane builds against a locked vocabulary); (2) during the transition the relay's tee feeds
the existing Panels chat-transcript mirror for relay-handled turns (write-behind, off the
conversation path), so the product's durable transcript keeps growing and the
`D-transcript-ownership-open` decision stays genuinely open for S4 rather than being
front-run.

## D-panels-issued-worker-identity — SUPERSEDED by D-child-per-employee

Prompt-carried per-ticket credentials were decided (2026-07-17) while the plan's upstream was
one shared `hermes serve` backend, where ambient env could not be trusted. The same day the
owner chose one child per employee, which makes spawn env the simpler, runtime-agnostic
identity carrier. Never implemented; retained for the reasoning about why ambient Hermes
session env (`HERMES_UI_SESSION_ID`/`HERMES_SESSION_KEY`) is unreliable in any shared-process
topology on stock Hermes.

## D-transcript-ownership-open — Conversation history source of truth is an open owner decision

Under the relay, Hermes's session store holds the conversation and Panels can read it back on
demand. Recommendation on the table: Panels keeps only a thin audit copy written from the tee so
ticket pages and reviews render history without a live backend. The alternative (keep today's full
mirror) preserves more of `chat/data.py`. Owner has not yet ruled; lands with stage S4.

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

## D-shutdown-zero-budget-diagnosis — Treat the reported router timeout as a repairable shutdown bug

The reported `Hermes session ingress router did not terminate within 0.0s` is not evidence that the
router is stuck. A production-lifespan reproduction and a healthy-router differential prove that an
already-expired absolute deadline causes the router thread to be checked before it can consume its close
sentinel; 0.0001 seconds is sufficient for clean shutdown in the same harness. The active Employee turn's
durable timestamps show the configured 30-second budget was consumed before gateway cleanup, while the
Ticket and visible turn retained the intended restart-recoverable state.

This is still a product bug, not a message to suppress without restoring the lifecycle contract. The
restart-recovery plan requires bounded drain, interruption of remaining owned sessions, then shutdown of
both gateways with the remaining shared-deadline budget. Current `EmployeeStepRunner.stop` only waits to
the deadline, and `EntityRoutingGateway.shutdown` stops iterating after the first gateway raises. Any fix
must preserve one total bound, distinguish a zero-budget healthy router from a genuinely stuck router,
perform the missing interruption step, continue cleanup across both role gateways, and add an exact
production-lifespan regression. This diagnostic cycle does not authorize or include that runtime change.

The owner subsequently authorized the repair. Ticket `t_s0q8d2ln` owns the exact production-lifespan,
Employee-runner, routed-gateway, and session-manager seams. It adds no new lifecycle type or config: the
existing Employee-session interrupt door, one absolute deadline, and current restart-recoverable Ticket
state are sufficient. Planning and implementation remain separate sub-agent turns because deadline math
and canonical shutdown settlement are load-bearing rather than trivial.

The first Codex plan review found that the ordinary Chat interrupt signature's fixed request timeout
could outlive the shutdown deadline. The repair therefore adds an optional keyword-only absolute deadline
only to the concrete `SharedGateway.interrupt`; `GatewayAdapter` and existing callers do not change. The
shutdown path uses only an already-live owned session and never spawns or resumes. The same review required
an explicit concurrent-stop regression; the existing `_stopping` transition elects the sole interrupt
snapshot owner, so no second lifecycle flag is needed.

The corrected-plan review found that runner activity also includes a parked revision reservation. Shutdown
therefore interrupts only an active running worker turn whose bound id matches the Ticket's durable
`employee_session_id`; a parked reservation has no such turn. This is session-control identity, not worker
context. Direct concrete-gateway tests prove the deadline path neither spawns nor resumes, and the routed
cleanup test makes both gateways fail so preserving the first error is observable rather than assumed.
The final fresh Codex plan review reports `NO VIOLATIONS`; implementation may now proceed within the
ticket's fixed file boundary.

Implementation review exposed two first-wins races and both findings were accepted. When no drain
time remains, the runner must settle the matched visible worker turn synchronously because its daemon
thread can lose the race to process exit. That settlement alone is insufficient: a later gateway
`complete` result may advance canonical Ticket state. Ticket completion is therefore conditional on
that worker actually winning the visible turn as `complete`; a shutdown-winning `interrupted` turn
keeps the Ticket and Employee session recoverable. Both failures were reproduced before correction,
and the final fresh Codex implementation review reports `NO VIOLATIONS`.

The two-axis review found that the one-deadline contract also applies inside interrupt lock admission
and every child-process cleanup wait. Both are accepted as contract gaps, not follow-up hardening.
The ticket file boundary is expanded only to `minds/gateway.py` so `GatewayChild` can consume the
existing absolute deadline across process wait, forced-kill wait, and reader joins. The concrete
deadline-aware interrupt will likewise include command-lock acquisition in its one budget. Ordinary
Pause remains distinct from shutdown: if an externally interrupted visible turn races with a late
gateway completion, its Ticket is marked errored; only service shutdown preserves it for recovery.

The review's baseline-GREEN note for missing-id and parked-reservation tests is not a TDD violation.
Those are preservation guards for behavior that existed before this ticket, while every new positive
shutdown behavior was observed RED. Inventing a temporary defect solely to make preservation guards
fail would add no evidence. Documentation cleanup is accepted: `employee-runtime.md` owns the plain-
language shutdown explanation and `systems.md` keeps only a handoff.

The final spec re-review correctly rejected exposing the absolute deadline on the publicly re-exported
`LiveSession.interrupt` method. Only the ticket-authorized concrete `SharedGateway.interrupt` gains the
optional deadline; it reaches the manager through a private Live-session seam. The public timeout-only
signature stays exact, while both timeout and shutdown paths still bound command-lock admission and
reply wait. The Employee runtime doc now correctly names the worker and Chief-of-Staff connections.

The closing spec review found the remaining shared-deadline leak in shutdown's SQLite work. The
runner's normal five-second busy timeout cannot apply after the absolute deadline expires. Shutdown
connections and individual DB operations will use at most the remaining time and become non-blocking
at zero. A lock failure logs and leaves the canonical running Ticket/session for startup recovery;
it must not skip gateway cleanup or extend shutdown. `core/db.py` joins the ticket only so its existing
`busy_timeout_ms` parameter also bounds initial SQLite connection and journal-mode setup.

The first full gate invocation used the shared `.venv`'s editable install and therefore imported the
other worktree's older `/Users/khushaljagota/.hermes/planning-v2/src`. The tracebacks and missing
contract fields prove the 39 unit and five e2e failures were an invalid mixed-source run, not failures
of this branch. The failed transcript is retained. The material correction is to run the same
canonical `./verify` with `PYTHONPATH` explicitly pinned to this worktree's `src`; an import probe
confirms that resolves `/private/tmp/panels-t_f9ue37gz-closeout/src/planner/__init__.py`.

The corrected-source canonical gate then exposed an acceptance-fixture contention contradiction.
The real-process fake emitted 5,000 `message.delta` frames; by shutdown, Panels had written 2,241
update events and accumulated 51,520 output characters, and the server log reported `database is
locked` during shutdown settlement. That fixture was meant to prove one active partial reply settles
as interrupted, not to manufacture SQLite writer contention at the zero-budget boundary. The
separate locked-database regression already proves that deliberate contention returns within the
deadline and preserves durable recovery state, and it remains unchanged. The minimal correction is
therefore one nonempty `message.delta`, the same terminal `interrupted` assertion, and an explicit
assertion that no shutdown-settlement SQLite lock error was logged. No production change follows
from this fixture correction; the final canonical `./verify` still remains.

## D-compact-worker-settings — Editable Worker settings are a managed overlay, not structure

Worker identity, Stage order, gated fields, terminality, and specialist-skill identity remain in the
immutable Python registry. One managed Worker-settings source owns only existing Stage ownership
defaults and the canonical editable specialist-skill description/body. Ticket rows persist the default
captured on current-Stage entry before any global default can be changed; explicit per-Ticket overrides
remain authoritative. The UI reads composed Worker detail through dedicated `workers` resources while
`/api/worker-types` stays the structural lifecycle manifest. Edited specialist skills are materialized
into the planner Hermes home without resetting or rewriting existing Employee session ids.

The work is isolated because the main worktree contains unrelated Hermes-relay changes. Implementation
uses serial contract-bounded agent passes and read-only Codex review; only the final settled tree runs
canonical `./verify`. A failed event transaction restores the prior managed file and live skill while
the per-Worker lock is still held. Candidate files remain for repair, and the browser keeps attempted
edits visible instead of pretending a failed write succeeded.

## D-exploration-worker — Exploration extracts the transferable problem before it produces work

`exploration` is a first-class Worker type for premises that are not yet understood well enough to
become implementation Tickets. Its lifecycle is Kickoff → paired Understanding → Research Plan →
Research → paired Answer → Follow-up → Closeout → Done. Understanding and Answer are paired because
they depend on user intent and judgment; evidence work remains worker-owned. Research includes source
discovery, selection, provenance, and synthesis, so there is deliberately no separate Corpus Stage.
The specialist teaches the worker to remove product and implementation nouns, identify the underlying
mechanism, search adjacent domains under their own vocabulary, and state both what transfers and where
an analogy stops. Follow-up may be Tickets, files, record changes, another exploration, or nothing;
Closeout applies only the approved consequences and never hides downstream implementation.

The production landing used isolated branch `ticket/t_8vfx962r-exploration-worker` because `main` had
unrelated local changes. The registry, provisioned specialist, Worker/Chief front doors, tests, and live
docs landed together; no frontend type table or new core contract was added. Review corrections were
small integration repairs applied directly by the orchestrator after one RED validation-payload test.
The first three onboarding Tickets use the existing durable-personas, Vylo-onboarding, and Panels-VPS
sprint items. No existing sprint item honestly owns the user-only existing-Worker management page, so
that Ticket stays loose in the current Panels sprint rather than forcing it into an unrelated item.
All four were added to today under the standing creation default, but remain at grounded Kickoff
proposals awaiting approval; Closeout did not begin or monitor any exploration.

## D-closeout-lanes-use-ticket-state — Closeout serialization adds one fact to normal eligibility

Owner direction (2026-07-18): Closeout is not a second scheduler or claim system. The normal
Automatic Employee-step eligibility decision still determines whether a Ticket may run. A Closeout
adds one fact: another Ticket at Closeout with the same effective project and Worker type occupies
the lane whenever its existing `ticket_status` is not `empty`. `empty` means waiting; a stopped
empty Ticket is ineligible but does not block another waiter. Parented Tickets use the sprint item's
project. Tickets with no effective project share one explicit projectless lane per Worker type.
Discovery chooses the oldest eligible waiter by `updated_at`, then Ticket id, and the existing
`BEGIN IMMEDIATE` claim repeats the complete decision. Existing running, approval, error, takeover,
paired-work, recovery, and Done state is sufficient; no queue table, lease, claim row, migration,
staging-PR queue, or Integration Worker is added.

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
# Workspace left-panel exploration (2026-07-19)

- Owner direction: project header → worker header → tickets, ordered from earliest to latest stage
  inside every project/worker block. Ticket rows show only title, stage, and the running signal.
  This is design research until the owner approves the rendered hierarchy for implementation.
- Current visual comparison: (A) project → worker → tickets, using project size/space instead of a
  project divider and a divider only after each worker group; stage remains on ticket rows. (B)
  project → worker → non-empty stage → tickets; stage is absent from ticket rows because it is now
  the group header. All labels remain flush to one left edge — hierarchy is typographic, not inset.
  Every level is collapsible. Awaiting owner choice between the two nesting depths.
- Owner selected B. At rest chevrons are hidden; hovering a grouping header reveals its chevron at
  the right edge. Worker group boundaries use a larger gutter and a thicker divider; stage groups
  use thin internal dividers. This is a mockup decision awaiting implementation approval.
- The selected direction now has an interactive standalone fake-data mockup. Collapse is supported
  independently at project, worker, and stage levels; ticket selection is local-only mockup behavior.
- Worker type is the card boundary: each worker is a transparent, bordered card, not a filled surface.
  Project remains a large typographic heading outside cards; its worker and stage headings have a
  larger scale than the previous compact design.
- Cards use subtle low-contrast borders. Worker names are uppercase; stage names are normal-cased,
  larger headers than their ticket titles.
- Stage headers use 21px but retain their original muted contrast so they read as a grouping level,
  not ticket metadata.

---

# ACP conversation presentation (2026-07-21)

## D-acp-unmatched-tool-updates-are-quiet — Separate valid protocol events from visible transcript content

A structurally valid `tool_call_update` without a matching pending tool call is incomplete transcript
context, not unsupported agent content. The browser keeps the raw envelope available to transport
history but does not create visible message, timeline, or unsupported-content state. Matched updates
still patch their tool call, and recognized unsupported updates remain visible.

## D-acp-task-strip-active-only — A stored plan is visible only while work remains

The ACP plan snapshot remains durable conversation state, but its task pill represents current work.
The pill therefore requires both an active turn and at least one `pending` or `in_progress` entry; an
all-completed snapshot stays stored but does not resurface in a later turn. This keeps the correction
at the rendering boundary instead of mutating protocol state or adding backend cleanup. The mounted
browser regression proves both sides with a mixed plan and an all-completed plan across an idle-to-active
cycle. The production change is one derived predicate with direct browser proof, so the trivial ticket's
implementation and review work are collapsed without a separate reviewer.

# ACP migration (2026-07-19)

## D-acp-single-conversation-path — ACP replaces relay and legacy after proof

ACP is the destination rather than a third coexistence mode. The unfinished shared-registry P2 is
cancelled: it would spend implementation effort improving a path this program must delete. P1's
employee-keyed ownership primitive remains an input. ACP cuts over the live Chief, Ticket, and step
paths directly; the dead legacy code stays physically present only until the Hermes ACP path passes
computer-use proof, then is removed in one classified deletion ticket. This supersedes
`D-hermes-relay-architecture` as the end state.

## D-acp-server-client-browser-envelope — ACP ends at the Panels server

The Panels server is the ACP Client and uses the official Python SDK over stdio. The browser is a
thin viewer/controller over one Panels websocket. That wire preserves ACP `SessionUpdate` types and
adds only routing, sequence, connection/activity, delivery receipt/queue, compaction, permission,
and optimistic-human-echo events. It does not introduce a neutral translation vocabulary. This
resolves `D-transcript-ownership-open`: the agent's typed `session/load` replay is the conversation
source; the browser holds live view state; canonical product data remains server-owned separately.

## D-acp-visible-programmatic-prompts — Show every Panels-supplied prompt in the transcript

Panels must not hide its own delivery context. At the broker's ACP admission point, the
exact prompt sent by Automatic Employee work is published as a typed
\`programmatic_prompt\` envelope with source \`worker\`. The one-shot employee role prefix
is published with source \`role\` when it is added to a prompt. The browser renders both
as visible system messages in sequence. This closes the production Hermes gap where a
programmatic prompt appeared only after hard refresh because Hermes replayed it but did
not emit a live user-message chunk. The ACP session remains the durable source for
reload replay; the new envelope is a live visibility event, not a second transcript.

## D-acp-ordered-ingress — One consumer serializes each employee/session update stream

The ACP SDK may invoke client callbacks concurrently even though frames arrive in order. Each
callback therefore validates and enqueues only; one employee/session consumer applies the reducer
and broadcasts in sequence. Browser refresh resets a stream generation and forwards typed load
replay before readiness. Unknown or partial typed replay becomes a visible error, never assistant
text. This is the load-bearing fix for refresh-time reasoning spill.

## D-acp-explicit-turn-delivery — Panels owns Steer, Send Now, and Queue semantics

`D-native-turn-concurrency` is superseded by the owner brief. Queue is a server-owned FIFO delivered
only after the active ACP prompt settles. Send Now cancels and settles the active prompt, emits an
interruption boundary, then starts the new prompt. Steer is exposed only when a backend declares a
real steering strategy (Hermes maps to its native command); unsupported backends do not silently
reinterpret it. Every action has a visible receipt and queue snapshot. A Panels transcript/database
row is never treated as delivery to the worker. Queue and Send Now are therefore common broker
capabilities, not backend flags; agent-advertised `/queue` and `/steer` slash commands remain catalog
entries and do not define the separate composer controls.

## D-acp-compaction-normalizer — Compaction is a visible Panels event

ACP v1 has no compaction update, so each backend definition may supply a narrow observation strategy
that produces generic `compacting`, `compacted`, or `failed` events with an inspectable summary. For
Hermes, an explicit `/compact` enters compacting before the prompt and performs a controlled typed
load capture afterward; automatic compression is detected from session provenance and uses the same
capture. The capture is not rebroadcast as duplicate history. The generic turn broker never checks
backend names. This intentionally supersedes the compaction part of `D-only-free-hermes-features`.

## D-acp-permissions-are-transient — ACP permission is not a ticket proposal

An ACP reverse permission call is transient conversation state tied to one tool call and one exact
agent-supplied option. Panels reuses the gate system's clear approval language, not its canonical
ticket writer or `ApprovalBlock` state machine. The request preserves the agent's exact ordered ACP
options and has a five-minute configured deadline. It is broadcast to every browser attached to the
owning employee; the first valid response from a still-attached browser wins. One tab closing does
not settle the request while another can answer, no attached browser rejects immediately, and the
last attached browser leaving cancels it. Timeout, cancellation, child death, new conversation, and
shutdown cancel or deny the outstanding request exactly once.

## D-acp-commands-come-from-the-session — The palette mirrors typed ACP updates only

The slash palette is per-session state from ACP `available_commands_update`. It does not call the
legacy commands API, inspect private skills methods, or hard-code a backend catalog. Stop and New
conversation remain explicit Panels lifecycle controls outside that catalog. Compact, steer, and
queue are shown only when the live session/backend advertises the corresponding operation; visible
delivery controls are driven by typed capability state, not by recognizing command strings.

## D-acp-browser-types-are-pinned-source — Vendor only the framework-free donor core

The server pins `agent-client-protocol==0.11.0`. The browser vendors only the framework-free
`acp-components/core` types/reducer source at exact revision
`525a9d83c5ace577ac0417bf82bf983da4042663`, alongside its upstream license and provenance. Panels
does not install the stale npm package, adopt its React UI, or fork the ACP unions into an
independently drifting vocabulary. acp-ui remains a behavior reference, never a visual donor.

## D-acp-program-review — Freeze after two independent review rounds

The first read-only Codex review found five program blockers; all were accepted and resolved in the
plan: step-runner CAS/settlement/recovery, all ten conformance probes, typed command provenance,
exact permission state, and a mandatory independently reviewed legacy-chat ownership inventory.
The second review reported no unresolved blockers and `READY`. ACP-00 may now freeze contracts; later
implementation agents may not change their shapes without returning an explicit correction to the
orchestrator.

## D-acp-ticket-reviews-use-subagents — Independent reviewers need not be the Codex CLI

Live owner direction after the program review allows independent sub-agents to review ticket plans
and implementation diffs. Each review still runs as isolated work by an agent other than the author,
records its full findings and dispositions, and keeps the two-round cap. The already-completed
program-level Codex CLI review stands; future ticket reviews use sub-agents unless the owner changes
direction again.

Review depth is risk-based, not ritual: one focused plan review and one focused implementation review
is the default. A second round happens only when the first leaves a concrete blocker unresolved or a
material correction changes the load-bearing design. Trivial mechanical follow-ups are dispositioned
and checked by the orchestrator without commissioning another broad review.

ACP-00 exercised that rule: one implementation review found real wire-validation and conformance-
evidence blockers. The implementation was corrected, then the same reviewer performed only a narrow
finding-by-finding check and returned `READY`. This is the intended exception to the one-round
default, not a precedent for automatic second broad reviews.

## D-acp-reference-design-boundary — Zed legibility, Panels presentation, acp-ui behavior only

The owner explicitly rejected acp-ui as a visual target. Zed is the observed affordance bar for
collapsed thinking, compact tools/diffs, persistent status, queue/interruption, compaction, commands,
and permission clarity. Panels keeps its Svelte stack, existing tokens, restrained cardless layout,
markdown/images/previews, and right-rail geometry. The component port adopts state and interaction
logic without widening this migration into a product redesign.

## D-acp-backends-are-definitions — Register only conformant agent binaries

Generic ACP runtime and UI code contain no backend-name conditionals. A backend definition supplies
argv, explicit environment, reverse-service needs, and only the strategies ACP does not standardize.
Hermes proves the path first, then Codex and Claude pass the shared conformance suite. Gemini 0.51.0
is gated because inspected `session/load` can respond before replay completes; Panels will surface
that failure and withhold registration rather than invent a quiet-period heuristic.

## D-acp-hermes-readonly — Remove dependency on patches, never mutate the checkout

The implementer brief's strict boundary wins: no writes and no git commands in
`~/.hermes/hermes-agent`. Panels will make the local legacy patches irrelevant by deleting the
non-ACP paths, but physically reverting those patches is an external/manual action outside this
repository's authorized migration.

## D-acp-verify-at-checked-in-relay-default — Live runtime toggles do not redefine the test baseline

The owner's uncommitted `relay_backend_enabled: true` is an operational setting for the live relay
path; the file itself records that the repository default remains false for tests. Because existing
unit helpers and e2e server processes read the working-copy YAML, a normal `./verify` under that live
toggle rejects or reroutes the legacy-chat cases by design. ACP ticket integration therefore verifies
with that one value temporarily at its checked-in false default, then restores the exact live true
value immediately. Both the contaminated run and the clean run are recorded. This preserves owner
runtime state without weakening, skipping, or rewriting tests.

## D-acp-child-generation-is-not-binding-generation — Process recovery preserves conversation identity

ACP-01 separates the in-memory child generation from the durable binding generation. Every process
spawn advances child generation and guards callbacks/death, but a crash followed by `session/load`
keeps the same ACP session ID and binding generation. Binding generation starts at one and advances
only when a deliberately new ACP session or backend is persisted. This prevents a routine child
restart from looking like a new conversation while still making late callbacks harmless.

## D-acp-load-means-replay-consumed — The load response alone is not readiness

The official SDK may schedule notification callbacks concurrently, so ACP-01 cannot announce load
readiness merely because `session/load` returned. The production child uses the SDK stream observer
only to count/order update frames before the response, then waits until those updates reach the typed
enqueue callback and pass through the one ordered consumer. It does not decode raw payloads, add a
quiet-period heuristic, or create a second transport. This closes the refresh race without weakening
ACP type ownership.

## D-acp-browser-gaps-fail-closed — The transcript never guesses across missing wire state

The ACP browser controller admits exact next-sequence events for the attached identity. Duplicate,
stale, wrong-identity, and out-of-order envelopes cannot change transcript state; a forward gap sets
a persistent visible connection error and reconnects from the last admitted generation/sequence.
Binding generation advances only through a matching reset envelope. Unknown or partial updates stay
protocol errors rather than assistant text. This gives refresh/reconnect a deterministic recovery
path without silently inventing a transcript.

## D-acp-terminal-state-is-a-panels-envelope — Reverse terminal output stays typed tool state

ACP `ToolCallContent::Terminal` carries only a terminal ID. The accumulated output, truncation, and
exit status are returned to the agent through client-owned reverse terminal calls, so forwarding ACP
session updates alone cannot make terminal tools legible. ACP-00 is explicitly corrected before
ACP-02/03 implementation: an eleventh `terminal_state` envelope wraps the terminal ID, an
`active | released` lifecycle, and the exact SDK `TerminalOutputResponse`. The terminal owner emits
and replays bounded snapshots. Panels does not copy the SDK exit-status union, add browser terminal
JSON-RPC, or flatten terminal output into assistant text.

## D-acp-child-death-rejects-the-turn-queue — A reloaded process never inherits stale queued intent

If an ACP child dies, the turn broker interrupts the active prompt and rejects every queued prompt in
FIFO order, then publishes an empty queue. It does not auto-resume those messages after loading the
durable session into a new process. This sacrifices automatic recovery of pending human intent to
make duplicate execution impossible when the exact point of agent failure is unknown; the visible
receipts let the user resubmit deliberately.

## D-acp-reverse-services-are-session-confined — Agent callbacks do not inherit ambient authority

Filesystem and terminal reverse calls accept only the exact employee binding/session and declared
workspace roots. Filesystem paths resolve through symlinks before I/O. Terminals use argv execution,
an injected base environment rather than ambient server variables, bounded UTF-8-safe output, and
employee/session-scoped handles. Services are advertised only when completely composed. This keeps a
binary swap from silently widening file, environment, or process authority.

## D-acp-00a-review-disposition — Close the proof gap without another broad round

ACP-00a's independent implementation review confirmed the production wire and found only that the
terminal-specific test mutated sequence but not every inherited common envelope field. The
orchestrator added the complete parameterized identity/generation/sequence proof and reran the
focused checks. Because the finding required no production or design change, a second broad review
would add no useful evidence.

## D-acp-steer-capability-is-connection-state — The browser never guesses delivery support

ACP-03 planning exposed that the browser wire could show a rejected Steer receipt but could not make
unsupported Steer unavailable in advance. ACP-00b adds one required `supportsSteer` boolean to the
existing connection payload, sourced later from the selected backend definition. It does not add a
capability event or dictionary. Queue and Send Now remain common broker behavior, and neither backend
name, connection detail, nor an advertised `/steer` slash command is accepted as capability evidence.

## D-acp-observer-reserves-typed-ingress — Invalid replay cannot disappear inside the SDK router

The pinned SDK observes raw frames before routing, but validates notifications before the typed client
callback and suppresses a notification validation exception. A count-only load barrier can therefore
wait forever on a malformed replay frame. ACP-01 explicitly narrows the observer exception: for each
raw `session/update` it reserves an ordered bounded slot and runs the exact SDK model validation only
to record a canonical matching fingerprint or fill the slot with the frozen protocol-rejection
payload. Valid content still enters only through the SDK typed callback. The consumer processes slots
in wire order, so a partial/future update becomes visible and load completion remains deterministic
without a quiet-period heuristic or a hand-written protocol parser.

## D-acp-generation-publication-quiesces-old-sinks — A visible child generation has no older writer

A generation check before an awaited sink is insufficient: an old callback can pass, suspend, and
commit after a replacement is published. Each employee therefore has a publication/update gate.
Admitted sink calls hold it through their awaited mutation; publishing N+1 waits for admitted N work
to leave and advances under the same gate. The short global registry lock is never held across the
sink. This makes the generation boundary a real writer boundary rather than a preflight check.

## D-acp-browser-metadata-is-nontranscript-state — Stable session metadata is ordinary typed state

The pinned ACP union's `current_mode_update`, `config_option_update`, and `session_info_update` are
normal session updates, and Hermes emits them during ordinary use. ACP-03 stores them as typed
non-transcript state. They never create assistant prose, an unsupported-content row, or a protocol
error. Genuinely unknown or partial protocol values still fail closed; unstable plan-delta variants
may remain visibly unsupported until their own contract exists.

## D-acp-browser-recovery-is-proven-by-ready — Only a contiguous replacement epoch clears gaps

A browser-side gap or invalid-envelope error remains visible through reconnect and replay. It clears
only when a `connection: ready` envelope is admitted at the exact next sequence on the replacement
socket epoch. A late ready from the failed epoch and a replay that opens another gap cannot clear it.
`protocol_update_rejected` is persistent agent-protocol state and is never cleared by browser
recovery.

## D-acp-browser-snapshot-is-an-immutable-projection — Svelte cannot mutate reducer ownership

Copy-on-write reducer transitions are insufficient if consumers receive donor arrays or its mutable
tool map. ACP-03 keeps mutable donor compatibility internal and publishes a cached recursively
readonly/frozen projection, including a frozen keyed record for pending tools. A compile-time
boundary proof and a runtime cast-and-mutate test protect both the current controller state and later
snapshots.

## D-acp-03-plan-review-disposition — Four bounded corrections need no second broad review

ACP-03's independent plan review found ordinary metadata misclassified as an error, one missing
composer capability prop, an absent recovery transition, and an incompletely immutable public
snapshot. The contract and plan now state each correction explicitly and add focused proof. The same
review confirmed the transcript reducer, sequence rules, terminal state, Svelte inventory, and
restrained Panels visual direction were sound and explicitly recommended no second broad round after
the dispositions. Implementation may proceed with one independent diff review as the normal gate.

## D-acp-ingress-retirement-is-explicit — Child shutdown cannot wait on a callback that cannot arrive

An observer-reserved valid update can remain unfulfilled if the SDK child is closing or canceled
before its typed callback runs. Ordered ingress therefore retires explicitly: it stops acceptance,
drains the already-fulfilled head prefix, marks an unfulfillable reservation fatal, wakes load
waiters, and terminates. Forced/error cancellation never performs an unbounded drain. A downstream
sink exception uses the same generation-fatal path instead of leaving a dead consumer task and a
pending load barrier. This preserves accepted-prefix evidence while making shutdown bounded.

## D-acp-cancellation-cause-freezes-successor — Closing paths cannot accidentally advance the FIFO

The turn actor records a cancellation cause and its successor policy before any permission or ACP
cancel await. User cancel may advance one queued item; Send Now starts only its accepted submission;
new conversation, shutdown, child failure, and timeout reject the entire FIFO and start nothing.
Cancel timeout starts before the owned cancel-send task, so a blocked transport send cannot prevent
the reject-all disposition.

## D-acp-capture-replaces-the-child-generation — Private replay requires a traffic-isolated source

ACP notifications do not identify which `session/load` caused them, so diverting a time window on a
live child cannot distinguish replay from an unrelated delayed update. Compaction capture therefore
quiesces and retires exact generation N after prompt settlement, then privately loads the unchanged
durable binding on fresh generation N+1 whose only operation is that load. N updates remain ordinary
until retirement; N+1 replay is private through the response-consumption barrier; later N+1 updates
become ordinary before publication. This is generation isolation, not a quiet-period heuristic.

## D-acp-permission-open-is-a-turn-actor-command — Terminal causes win over late reverse callbacks

Each child permission callback carries captured employee/record/generation identity into the turn
actor. Permission open serializes with cancel, death, new conversation, and shutdown. Those terminal
causes tombstone the epoch before settlement, so a callback arriving later returns ACP cancelled
without publishing. The broker-owned future is shielded from SDK handler-task cancellation; only the
permission owner settles it.

## D-acp-filesystem-io-is-descriptor-backed — Confinement applies to the object actually opened

Resolving a path and reopening its string leaves a symlink-swap race. Reverse filesystem I/O resolves
safe internal symlinks to a canonical in-root target, traverses that canonical relative path from an
opened root descriptor with no-follow semantics, and reads/writes only the final verified descriptor.
Dangling final symlinks are distinguished from missing names; new writes use exclusive no-follow
creation. Path replacement can fail or select the original confined target, but cannot escape.

## D-acp-planned-retirement-is-not-child-death — Capture replacement suppresses only its exact close

Before compaction capture closes generation N, the registry installs a token containing employee,
binding generation, child generation, record identity, and capture transaction. Only a matching
intentional-close callback consumes it and settles the private retirement future; a non-null error or
mismatched token still enters unexpected-child-death cleanup. This prevents a successful capture
transition from failing its own actor without masking real death.

## D-acp-hermes-summary-is-structural-not-private-provenance — Replay supports all pinned placements

ACP replay omits Hermes' `_compressed_summary` metadata. The strategy therefore recognizes the exact
pinned standalone or merged summary structure across both user and assistant chunks, and requires
exactly one candidate while a typed broker boundary is being finalized. Standalone user/assistant and
merged user/assistant tail forms are valid; zero or multiple candidates fail visibly. Text syntax is
not misrepresented as private provenance.

## D-acp-01-correction-check-closes-the-runtime-ticket — Seven findings resolved without reopening settled scope

ACP-01's original author corrected only the seven independent-review findings, expanded the named
lifecycle evidence, and reran the ticket's focused gates. A different reviewer checked those seven
findings one by one and returned `READY`; it did not reopen areas already confirmed sound. This is the
owner-requested bounded review model, so ACP-01 is the settled runtime prerequisite for ACP-02 and
does not receive another broad review.

## D-acp-03-correction-check-closes-the-unmounted-pane — Runtime component proof replaces source-only evidence

ACP-03's original author corrected only the eight findings from its independent implementation
review. The final proof now drives the real websocket transport/controller/reducer subject and mounts
the actual Svelte pane in Chromium, including its accessibility live regions and every contracted
control. A different reviewer checked those eight findings one by one and returned `READY` without
reopening the already-sound restrained visual scope. ACP-03 is settled and ACP-04 alone owns route
mounting and production composition.

## D-acp-binding-row-mirrors-product-session-fields — Durable ACP identity is complete and worker checks stay valid

ACP needs backend key and binding generation in addition to session ID, so ACP-04 adds one complete
`conversation_session_bindings` row per employee. The current Ticket `employee_session_id` and Chief
agent session key remain atomic mirrors until ACP-06 classifies their retained product uses. A single
transaction updates both through the canonical product writer; mismatch fails closed. This avoids
encoding metadata into an opaque ACP session ID while preserving EmployeeStepRunner's established
session-ownership checks. The current Chief writer owns its own transaction, so ACP-04 may extract
one in-transaction helper in `chat/data.py` and make the existing public writer delegate to it; this
is the only way to join the binding-row and mirror write atomically without creating a second writer.

## D-acp-active-attach-uses-the-typed-reset-buffer — Refresh must not issue load during a live prompt

An idle attach performs the canonical reset + `session/load` + ready sequence. A new browser attaching
while a prompt is active receives the complete typed buffer from the current reset epoch instead,
because issuing `session/load` concurrently with the active prompt is not a proven ACP operation. The
buffer is bounded and never silently partial: an unavailable buffer yields a visible retry until the
next idle load. Existing attached browsers continue receiving every live typed event.

## D-acp-same-binding-reset-rebases-a-restarted-stream — Process restart cannot reuse a stale sequence base

Binding generation deliberately survives child and server restart, while an in-memory envelope
sequence does not. ACP-04 therefore lets an exact same-entity/session/binding reset with a sequence
greater than the browser cursor establish a replacement base even if it is non-contiguous. The server
raises that reset above the browser-provided sequence floor; ready and all later ordinary events must
again be contiguous. This preserves durable session identity without a database write per streamed
token or a false binding-generation increment.

## D-acp-step-callback-runs-on-the-runner-thread — SQLite ownership survives the async bridge

`EmployeeStepRunner`'s `on_session_key` callback closes over its caller-thread SQLite connection. The
event-loop-owned ACP hub cannot invoke it directly. `AcpStepGateway` uses one explicit caller-thread
handshake around `run_coroutine_threadsafe`: the candidate session is handed back, the runner thread
executes and acknowledges its existing CAS/turn attachment, and only then may the broker invoke the
ACP prompt. Lost claims therefore deliver nothing and no SQLite connection crosses threads.

## D-acp-reverse-state-retires-with-its-child-generation — Capture cannot carry N's authority into N+1

Permission requests and live terminals are owned by the exact employee, binding, record identity,
prompt epoch, and child generation that created them. The turn actor therefore uses one generation
cleanup path for child failure, cancel timeout, compaction capture, new conversation, and shutdown.
Compaction settles generation N's reverse state before it retires N and publishes N+1; a terminal or
permission callback from N cannot survive as authority in the replacement generation.

## D-acp-runtime-publication-failure-stops-delivery — Missing visible state is a runtime failure

A failed activity, receipt, permission, compaction, or terminal publication is not downgraded to an
ordinary rejection while the employee continues. The owning generation closes admission, cancels
owned work, settles reverse state, and starts no queued successor. This keeps the browser's typed
state and actual agent execution from diverging after a publisher failure.

## D-acp-delivery-captures-the-submitted-record — Accepted N work never rereads the actor's current handle

Same-binding child refresh may publish N+1 while an older actor command is suspended in event
publication. Runtime adoption is therefore an actor command, and every delivery retains the exact
submitted runtime handle through acceptance and lease acquisition. If that record is no longer live,
the delivery fails stale; it is never redirected to whichever generation is current when the await
resumes.

## D-acp-permission-outcome-commits-before-activity — Visible selection cannot be revoked by status failure

Publishing the permission outcome commits the exact ACP response. Restoring conversation activity is
a later visible transition: its failure is generation-fatal, but it cannot rewrite a selected result
to cancelled after the browser already saw the selection. Publication-failure notification must also
be non-reentrant with actor-owned settlement so the failure path cannot cancel and await itself.

## D-acp-deadline-has-no-cancellation-grace — Cleanup reports at the caller's absolute boundary

The configured shutdown deadline is the complete budget, including forced cancellation and result
collection. After it expires, cleanup may synchronously detach ownership and kill processes, but it
does not await a task that suppresses cancellation. Unfinished employees or terminal IDs are reported
immediately; late task completion is consumed without extending shutdown's wall-clock contract.

## D-acp-02-correction-check-closes-the-broker-ticket — Five load-bearing findings are settled once

The original ACP-02 author corrected exactly the independent review's runtime-redirection,
closed-runner admission, hard-deadline cleanup, permission-commit ordering, and frozen-type findings.
The same reviewer then performed one narrow finding-by-finding check, independently reran the 11
named regressions, and returned `READY` without reopening areas already confirmed sound. Together
with the 214-test affected acceptance gate and the orchestrator's source spot-check, this closes
ACP-02 under the owner's bounded-review rule. ACP-04 may plan against these APIs without another
broad review or an intermediate canonical `./verify`.

## D-acp-04-uses-one-reentrant-safe-sequencer — Owner calls and their publications cannot share a held lock

ACP-04 admits every valid or rejected child update with immutable employee/generation/record source
identity into one bounded per-employee FIFO. Its drain alone mutates browser stream state. Attach,
load, prompt, cancel, permission, and replacement calls execute outside that drain and close through
explicit markers/watermarks, so an owner can publish back before returning without waiting on its
own held transition lock. Pre-binding capture is a state inside the same admission order, not a
second replay channel.

## D-acp-worker-first-demand-owns-a-stream-epoch — A browser is not required to make worker output replayable

Before an automatic Employee prompt, the ACP step gateway requires the hub to establish the exact
binding's reset/load/snapshot/ready epoch even when no browser is attached. The worker collector and
permission provenance are then installed before prompt emission. A browser joining mid-step can
therefore receive the same complete typed reset buffer as any other active attach, and exact-epoch
rejection or capture failure settles the worker result before its collector is removed or a queued
successor begins.

## D-acp-permission-origin-is-admission-state — Stale worker permission cannot become an ordinary browser request

Permission admission copies immutable worker/browser origin together with exact runtime, prompt,
binding, and session identity into the pending record. Settlement never reclassifies from the later
active map. A worker-origin option can win only while one synchronous `BEGIN IMMEDIATE` guard proves
the durable binding, Ticket mirror and running status, exact active worker epoch, and worker-turn
session before that same pending record becomes settling.

## D-acp-remainder-keeps-the-established-bar — The measured runtime justifies its proof surface

After measuring about 13,175 production additions and 15,763 test/support/fixture lines before the
ACP-04 vertical e2e, the owner clarified that the migration is substantial enough to continue the
established process rather than artificially compress it. The bounded-review rule remains: one focused
review is normal and another occurs only for a concrete unresolved correction. ACP-04 completes all
named gateway and official-SDK vertical proofs before dogfood; later backend definitions reuse the
shared conformance harness, and Gemini remains a bounded registration gate rather than receiving
timing heuristics.

## D-acp-04-final-correction-is-proof-only — Close the retained finding at the real boundary

The bounded correction check left no production defect open; it required three exact scenarios to
move from unit evidence into Phase 8. They now run through the production composition and official
ACP child in `tests/e2e/test_acp_conversation.py`: stale source after two-browser replacement,
gateway interrupt plus rejection/capture queue ordering, and active slow-consumer/reset-overflow
behavior. The orchestrator will spot-check those named assertions instead of opening another broad
review round, matching the owner's one-round-by-default direction.

## D-acp-null-load-is-a-cutover-transition — Backend not-found is not transport failure

Real Hermes dogfood disproved the ACP-04 backfill assumption: legacy `planner-chat` session IDs are
valid Hermes history but are not loadable by Hermes's ACP-only session manager. Panels will keep
load exceptions fail-closed, but treat a successful JSON-null `session/load` as the backend's exact
not-found result. It creates one new session on the initialized child, CAS-publishes the exact next
binding generation, and visibly tells the user that the previous conversation was unavailable.
Panels does not mutate Hermes's read-only checkout or couple itself to Hermes's private state DB
schema. Controlled compaction load remains identity-preserving and therefore fails on null.

## D-acp-cutover-does-not-carry-legacy-session-compatibility — Start clean once, then delete the bridge

This owner decision supersedes `D-acp-null-load-is-a-cutover-transition` before that correction was
implemented. The null load observed in real dogfood came only from asking the new Hermes ACP adapter
to restore a legacy `planner-chat` session. Panels will not gain permanent remint-on-null machinery
for that one-time state. ACP-05 uses the existing **New conversation** action to create a clean ACP
binding and proves the product there. After proof, ACP-06 deliberately replaces all remaining legacy
bindings with fresh ACP sessions as part of deleting the legacy transport. The old Hermes history is
not a runtime compatibility contract.

## D-acp-missing-message-ids-close-on-terminal-settlement — The broker owns fallback turn boundaries

Hermes ACP may omit message IDs from live agent chunks, and Panels intentionally publishes a queued
human echo before that prompt is delivered. A user-role boundary can therefore arrive before the
predecessor's final chunk and cannot reliably split responses. The browser closes only its active
missing-ID agent group on terminal activity (`idle`, `interrupted`, or `failed`) and on an interrupted
delivery receipt for Send Now. The focused correction passed one independent review with no findings;
the persistent latest delivery receipt remains intentional.

## D-acp-compaction-persists-through-official-fork — Capture compacted memory before changing children

Real Hermes dogfood proved that `/compact` can update only the live agent's memory while leaving its
old durable session unchanged. Panels will use the agent-advertised official ACP `session/fork` method
on the exact leased child, privately load and normalize that fork, then CAS the durable binding to the
fork at generation N+1. This preserves the backend's own typed history and summary without modifying
the read-only Hermes checkout or inventing a private persistence adapter. The normal winner keeps the
same child generation; browser reset/replay/ready and queued-intent retargeting complete before the
compaction is called successful. Missing fork capability or invalid capture stays visibly failed.

## D-acp-requested-cancel-exception-is-interruption — Successful cancellation owns prompt unwind

After exact ACP cancel delivery and permission cancellation succeed, an exception from the cancelled
prompt await is part of the already-requested user, Send Now, New conversation, or shutdown
interruption. It is not a second independent child failure. The broker captures the cause before
reading the completed prompt task and continues through the existing cause-specific settlement.
No-cause/child-failure exceptions and failed/late cancellation remain fail-closed. Focused Stop,
Send Now, and control regressions plus one independent review settled this classification.

## D-acp-websocket-writer-owns-iteration-waits — Reload cleanup is local task ownership

Each websocket writer iteration owns its queue and closure waits until both are finished. A `finally`
block cancels unfinished waits and gathers both on delivery, subscription closure, send failure, or
external writer cancellation. This preserves disconnect and close semantics while preventing browser
reloads from abandoning asyncio tasks. The orchestrator made this tiny direct dogfood repair, recorded
its red/green proof, and one independent review returned `READY`.

## D-acp-capture-has-one-actor-owned-deadline — A binding transition cannot strand the product

The broker actor creates one absolute compaction-transition deadline before hub admission closes and
passes that exact value through fork, private load, normalization, CAS/recovery, actor rekey, and hub
commit/abort. No owner creates a fresh budget or shields work past it. Expiry releases the registry
reservation and every hub waiter exactly once; pre-CAS exact-N restoration fits only in the remaining
budget, while unprovable restoration or any post-CAS incomplete transition is generation-fatal. This
accepted the sole focused plan-review blocker; the narrow check returned `READY`.

Generation-fatal after the durable CAS means the exact replacement runtime record is invalidated and
its child is closed without rolling the durable N+1 binding back to N. A later fresh demand may load
that durable successor into a new child generation; the incomplete child/browser epoch is never
treated as reusable.

## D-acp-compaction-admission-closes-after-owner-settlement — Commit and abort stage; the broker releases

Hub commit owns N+1 reset/replay/ready/queued publication and recoverable abort selects the unchanged
N stream, but neither operation releases blocked attach/action work. The broker is the only normal
completion owner: after it publishes the normalized boundary, settles the tracked turn, publishes
idle, and advances FIFO, it calls the hub's bounded completion seam with the exact settled runtime.
Any registry outcome that has invalidated or cannot prove the exact runtime is explicitly
generation-fatal instead: the hub transition, actor, tracked turn, and queued intent fail, and no false
idle or successor is published.

## D-acp-backend-definition-means-functional-worker — Codex and Claude must run real Ticket work

“Thin backend definition” describes the backend-specific implementation delta, not a reduced product
role. Codex and Claude each must be operably assignable as the backend for a Panels employee and must
prove a real Ticket conversation plus one actual Automatic Employee step through the shared
`EmployeeStepRunner`, with the same durable session, tools, permission, status, and typed transcript
system used by Hermes. A definition module, conformance-only fixture, or test-only composition swap is
not completion. One supported configuration/binding assignment seam is required; this decision does
not add a backend-specific UI or mandate a polished visual selector.

## D-ticket-kickoff-selects-employee-backend — Worker-type default, Ticket override, then freeze

Each Worker type declares the default ACP employee backend its new Tickets typically use. Ticket
creation copies that exact default into a stored generic `employee_backend` value, and Kickoff shows
it already selected while allowing an override from the currently registered Hermes, Codex, and
Claude Code definitions. Kickoff approval freezes the choice; the same value owns the Ticket's
durable binding, human Ticket chat, and every Automatic Employee step. This is distinct from the
Worker profile's optional model name: Claude Code and Codex are agent backends, not model IDs. The
control remains a small generic selector in the existing Kickoff experience, not a backend-specific
redesign. Existing post-Kickoff Tickets retain Hermes during the one-time cutover unless explicitly
created for backend dogfood.

## D-acp-gemini-out-of-scope — This migration delivers Hermes, Codex, and Claude Code only

The owner explicitly removed Gemini from the delivery scope. ACP-09 performs no implementation,
registration, conformance gate, dogfood, configuration, or product documentation. Historical source
research may remain as research evidence, but it is not a product commitment.

## D-codex-acp-1-1-4-remains-unregistered — A definition is exposed only after real qualification

The locally available official `@agentclientprotocol/codex-acp@1.1.4` does not meet Panels' frozen
worker contract: it has no ACP fork handler for the controlled capture transaction, exposes no
inspectable compaction summary, and replays stored plans as assistant text rather than the live typed
plan form. The installed Codex CLI exposes app-server but no ACP command, and wiring that provider
protocol directly would create a second transport. Codex therefore remains a qualification-gated
backend plan, not a selectable definition, until a later exact official ACP pin passes the real
wrapper gate. This is not permission to weaken the shared capture or typed-replay contract.

## D-claude-acp-declares-only-real-reverse-services — Internal tools need not be client reverse calls

`@agentclientprotocol/claude-agent-acp@0.59.0` is a viable functional-worker candidate with
`filesystem=False`, `terminal=False`, and `permission=True`. Claude Code's internal Bash execution
still crosses as visible typed tool output; the adapter's absence of standard filesystem/terminal
reverse requests is not generic ACP non-conformance and is not a dispatch blocker. Production
registration remains gated on one initialize-only pre-advertisement capability probe and the full
human Chat plus Automatic Employee dogfood. Claude-specific compaction status text is control input
to one exact-binding-generation state machine; Panels' typed compaction boundary remains the only
visible compaction lane.

## D-ticket-backend-remains-editable-through-unaccepted-kickoff — Fresh approval is still pristine

Ordinary Ticket creation immediately stores `needs_kickoff`, creates the Kickoff proposal, and sets
`ticket_status` to `awaiting_approval`. Requiring `ticket_status == empty` would therefore render the
owner-requested preselected Kickoff backend control read-only from its first frame. The canonical
writer instead accepts either `awaiting_approval` or `empty` only while the Ticket remains at
`needs_kickoff` with no employee session and no ACP binding. Any other status, an advanced stage, or
the first session/binding freezes the value. This preserves existing Ticket status semantics and
places the choice at the actual product boundary: before Kickoff is accepted or employee work starts.

## D-acp-cancel-detection-timeout-is-not-the-recovery-deadline — Fail fast, then recover once

ACP-02's short cancellation-settlement timeout remains the detector for an ACP cancel or permission
cancellation that does not finish. Winning that timeout fails the quarantine and generation and never
enters same-session recovery. The longer requested-cancel deadline is installed before cancel only so
the exact-source quarantine has a hard bound; it becomes the sole recovery transaction deadline after
cancel and permission cancellation succeeded and the prompt then unwound exceptionally. Registry
retirement, fresh child load, hub replacement, and final settlement neither create nor extend another
recovery budget. Equating the two timeouts would either weaken ACP-02's fail-fast contract or leave no
real budget for the replacement it is meant to protect.

The requested-cancel timeout itself does not mint a cleanup budget. It reuses the deadline stored on
the active actor from quarantine begin through exact retirement. Registry retirement bounds employee-
gate acquisition, child close, and planned-death settlement by that same timestamp; any error,
cancellation, or expiry removes the exact record and detaches best-effort child close before returning.
Exceptional New Conversation and shutdown use their caller deadline in the same way, and retirement
failure is returned to the close owner rather than allowing a new session to select the old child.

## D-acp-requested-cancel-recovery-replaces-the-process-not-the-session — Same binding, fresh child

Once exact Stop or Send Now cancellation and permission settlement succeed, an exceptional prompt
unwind makes that child process indeterminate but does not invalidate the user's durable conversation.
Panels quarantines the old source before cancel, retires that exact lease, privately loads the same ACP
session into a fresh child generation, and releases browser/successor/FIFO admission only after one
same-binding reset/replay/ready commit. Binding generation and ACP session ID stay exact; child
generation, child object, and record identity must all change. A normal cancelled response instead
flushes quarantined ingress and continues on generation N. New conversation and shutdown keep their
separate close ownership and never turn exceptional unwind into same-session recovery.

## D-acp-step-gateway-delivers-pending-worker-context — Preserve automatic-work prompt parity

Eligibility, Ticket claiming, worker prompt construction, proposal/status settlement, and the
`EmployeeStepRunner` remain unchanged by ACP. The old `SharedGateway` happened to own one additional
automatic-work obligation: prepare pending worker context into the actual model prompt and acknowledge
its receipts only after prompt admission. That obligation moves to `AcpStepGateway` before the legacy
gateway is deleted. It is transport delivery parity, not a second context store or a redesigned
automatic-work path.

## D-pristine-ticket-observation-does-not-bind — Selection precedes employee demand

Opening a fresh Kickoff Ticket is observation, not employee demand. Its existing conversation rail
renders without attaching, so the preselected worker backend can actually be changed before a session
exists. The first explicit prompt retains the submitted content, attaches, and sends it once after
ready; advancing Kickoff restores ordinary eager attach. Chief, Board, and started Tickets remain
eager. This is a small controller admission boundary, not an unbound server session or a UI redesign.

## D-employee-backend-and-worker-registry-are-one-configured-pair — One authority reaches automatic work

The registered ACP backend catalog and the Worker-type registry validated against it are one immutable
configured pair. Production constructs the pair once; app composition, startup audit, manifests,
Ticket writers, bindings, discovery, and `EmployeeStepRunner` read that exact authority. Tests replace
and restore the pair atomically. This prevents a backend appearing selectable in the app while the
module-configured registry used by automatic work still sees a different catalog.

## D-send-now-recovery-replays-its-captured-human-boundary — Reset must not erase the successor

Requested-cancel recovery resets the browser after the successor's original optimistic/human echo was
published, so that pre-reset echo cannot serve as the settled transcript boundary. For Send Now only,
the recovery commit re-emits the already-captured successor client message and prompt through the
existing typed `human_echo` seam after reset/replay/ready/queue and before successor start. This keeps
the old agent interruption, successor user prompt, and successor answer distinct without a browser
heuristic or new wire event. The correction is small and its root cause has a deterministic red/green
harness, so the separate ticket-planning and plan-review stages are collapsed; implementation still
receives one focused post-diff review before dogfood.

## D-compaction-has-an-emergency-deadlock-breaker-and-exact-failure — A shutdown timeout is not UX

Compaction keeps a hard deadline only so an unresponsive ACP backend cannot hold the employee gate and
all dependent admission forever. It no longer borrows the 10-second service-shutdown budget: one named
five-minute compaction-capture budget covers the complete official fork/load/CAS/browser transaction;
compaction taking a couple of minutes is normal, and this limit exists only as an extreme deadlock
breaker. Expiry diagnostics distinguish a binding that stayed on N, committed N+1, or could not be
resolved before the one budget expired and must be read authoritatively on fresh attach. Panels never
prints a false disposition and never extends the transaction with a hidden second budget.
Concrete backend, protocol, persistence, or publication errors do not wait for that breaker and are
not replaced by generic text: the failed boundary and server log retain the exact phase, exception
type, and underlying message immediately. A backend-raised `TimeoutError` is still a backend error;
only Panels' own wait timer winning means the five-minute budget expired.
Expiry still retires an uncertain child and preserves the authoritative durable binding, but Panels
must surface the exact failed phase, configured elapsed budget, and binding disposition instead of the
generic `Conversation runtime generation failed during compaction`. A later fresh attach is ordinary
recovery from the known durable binding, not a hidden compaction retry or a second transaction budget.

## D-acp-compaction-handoff-is-session-aware-and-cross-process — Responses are not quiet barriers

ACP session notifications are asynchronous and session-labelled. Panels must route private capture by
the notification's exact session ID and use the request ID only to close the corresponding response
barrier; a valid update between lifecycle requests is not a callback mismatch. The employee lifecycle
reservation is distinct from the short publication/update gate, so no ACP or repository I/O may wait
while ordinary ordered ingress is blocked from that gate.

Hermes compaction still uses official `session/fork`, but the source child never privately reloads its
own fork. A fresh unpublished child loads and validates the fork, proving it is visible across processes
rather than merely present in Hermes' in-memory SessionManager. Durable CAS then publishes that fresh
child as the N+1 runtime and retires the source. Exact-source updates received before publication are
quarantined and drained in wire order during the browser reset/replay/ready transition. This supersedes
the earlier same-child and unchanged-child-generation compaction clauses; it does not change the
five-minute emergency breaker or authorize Hermes changes.

## D-acp-compaction-replay-persists-provenance-not-summary — Reload reproduces one typed boundary

Hermes's compacted summary marker is worker context, not an assistant transcript message. Every
controlled load therefore passes its complete raw replay through the selected backend strategy exactly
once at the hub: Hermes reuses its pinned structural summary parser and replaces the one summary item at
that exact position with Panels `context_compaction` events. The browser never parses backend text, and
the successful broker path does not publish a second completion after the hub commit.

The summary text remains owned by Hermes and is never copied into SQLite. Panels persists only the
ordered broker boundary IDs and `explicit | automatic` triggers atomically with the successor binding,
because neither value can be reconstructed faithfully from the replay after restart. This is a
no-version-bump amendment to the still-unlanded v24 binding schema; ACP-06's direct v25 cutover also owns
the pre-column upgrade case. A binding contains only the boundaries settled by its own capture. A later
compaction replaces that tuple because its new summary has compacted the earlier transcript; appending
old IDs would falsely attach the newest summary to older boundaries and recreate duplicate UI.

## D-observed-small-ui-fixes-use-spot-check-and-dogfood — Review effort follows change risk

When a defect has already been observed in the real UI and its correction is small, root source
spot-checking plus the deterministic regression and real Panels dogfood are sufficient. It does not
receive another full independent review merely because it followed an earlier ticket review. Larger
combined changes still receive their one normal focused review; concrete findings are corrected, but
there is no automatic second broad round. This owner ruling keeps review proportional without weakening
the live product gate.

## D-compaction-is-an-opaque-lifecycle — Start, finish, or exact failure; never context

The owner never needs or wants to read compaction context. Panels therefore exposes only one stable
typed lifecycle boundary: compacting when work starts, compacted when the backend/session transition
finishes, or failed with the exact reason. Backend summary/context text is private worker state and is
absent from Python, wire, browser, persistence, and UI contracts. There is no disclosure control.

Hermes's synchronous `/compact` command legitimately succeeds without a summary marker when a short
conversation is retained unchanged; the observed clean dogfood case was `2 -> 2`. Successful controlled
backend completion and durable session continuity are authoritative. Replay classification suppresses
one recognized private marker when supplied; a marker-free replay stays intact and receives the durable
completion boundary after its ordinary items. Malformed/multiple marker-like private content and real
protocol rejection still fail closed rather than risk leaking worker context.

This supersedes `D-acp-compaction-replay-persists-provenance-not-summary` only where that decision made
parsing/presence of a summary the completion gate or supplied summary text to the UI. Boundary
ID/trigger provenance remains because it is the small durable fact needed to reproduce lifecycle after
reload. The Hermes fork stays only because its current same-ID persistence can lose the in-memory
compacted history; it is a Hermes durability adapter, not a summary extractor and not a requirement for
other ACP backends. Compaction support is an optional backend capability, not a general worker-
registration gate.

## D-acp06-one-way-deletion-is-authorized — The conditional gates are now evidence, not blockers

ACP-06 may begin. The five named worker-context delivery proofs are green in the settled 49-test
gateway/composition/official-SDK slice. Actual Panels then completed the full ACP-05 gate, including
Hermes permission, Stop, Send Now, Ticket and automatic work, visible compaction start, durable Chief
generation 12 -> 13, content-free completion after hard reload and server restart, and a later prompt in
the same replacement session. The ownership classification's four closure corrections are accepted.

The authorization is exactly the reviewed one-way cutover: no compatibility mode, old-session import,
legacy fallback, Gemini work, or Hermes source change. Runtime/schema and frontend may implement in
parallel because their product files are disjoint; root integrates them serially, builds once, performs
one focused implementation review, and reserves canonical `./verify` for ACP-10.

## D-compaction-contract-follows-backend-lifecycle-signals — Fork and summary are not conformance

The primary-source audit in `orchestration/acp-migration/compaction-primary-source-audit.md` confirms
that stable ACP v1 defines neither a compaction method nor a summary contract. Commands are ordinary
prompts and session updates may arrive asynchronously until the prompt's terminal response. Panels
therefore normalizes each backend's actual start/completion/failure signals into the same content-free
lifecycle and keeps consuming the ordered stream while the command is active.

Codex supplies namespaced `contextCompaction` start/completion metadata on the same thread. Claude's
ACP adapter supplies exact `Compacting...`, `Compacting completed.`, or `Compacting failed: <reason>`
signals on the same session. Neither needs or supplies client-readable compacted context. A
300-second emergency breaker reports only that Panels stopped waiting; it does not fabricate a backend
failure. Hermes alone retains its post-compaction fork/rebind because current Hermes same-ID persistence
can otherwise lose the in-memory compacted history. That workaround is not a worker-registration
capability and cannot gate Codex or Claude.

## D-codex-stored-plan-prose-is-a-pinned-presentation-limit — Functionality wins without a shim

The official Codex ACP 1.1.4 adapter preserves the durable thread and typed user, assistant,
reasoning, tool, diff, and terminal replay, but represents one stored plan as ordinary assistant prose
while the same live plan was typed. That is a reload-presentation defect in the upstream adapter, not
a failure of worker identity, prompt delivery, permissions, cancellation, or Automatic Employee work.
It does not justify keeping Codex unavailable.

Panels will pin and assert the exact upstream behavior, then require the real common runtime and
human/automatic continuity probes before registration. It will not parse the `Plan:` prose, read Codex
private files, or add a backend-specific transcript repair. Other backends retain their typed replay
requirements; this is one named qualification fact for the exact Codex pin, not a global weakening.

## D-acp06-live-cutover-follows-one-ready-review — Correct then migrate once

ACP-06 used one integrated implementation review. Its four concrete P1 findings were corrected in
the same bounded round and checked narrowly; a second broad review would not add useful evidence.
Only after the written verdict became READY did the live schema-24 database migrate once to v25.
The approved cutover deleted old bindings and legacy Chat state, preserved only Employee-step
correctness history, and required fresh generation-1 Chief and Ticket ACP sessions. Actual Computer
Use against `127.0.0.1:8767` then proved the restrained Panels UI, exact Chief reply, hard-reload
replay, and a matching Ticket binding/session mirror.

The next activation stage is parallel only where ownership is disjoint: the generic selector may
implement while Codex and Claude perform read-only, no-model package/runtime qualification. Backend
product source and the shared production registration tuple still wait for the selector's settled
catalog seam, so parallel preparation cannot invent or race that authority.

ACP-07's exact test allowlist omitted two retained constructor callers in
`tests/e2e/test_new_worker_public_flow.py` and `tests/support/acp_runtime_subject.py`. The ticket must
remove the now-forbidden separately configured definition/factory fields, and retaining compatibility
constructors would contradict the paired-authority contract. The implementer may therefore make only
the mechanical pair/catalog construction edits in those files with no behavior or assertion change;
this is caller closure, not product scope.

The same closure rule covers two frontend files omitted from the exact list:
`web/src/lib/acp/productionConversation.ts` must propagate the reviewed deferred-first-attach option,
and `web/tests/acp-production-mount.test.mjs` must keep its exact wrapper proof current with that
signature. Both are necessary consequences of the named controller/component boundary; neither may
change eager behavior or widen the UI. Removing the retired registry-only public exports from
`src/planner/worker_types/__init__.py` is likewise part of deleting the forbidden compatibility seam.

## D-acp07-selector-settles-before-backend-activation — One authority, then parallel definitions

The generic selector is the settled authority before provider activation. One immutable configured
pair owns the ordered Employee-backend catalog and the Worker-type registry validated against that
exact catalog object. Ticket creation, the pristine-Kickoff writer, binding CAS, human conversation,
and Automatic Employee work all consume it; provider definitions do not add another allowlist or UI.

The shared Node dependency directory is canonically `agent_backends/`. Root creates its combined exact
Codex-and-Claude manifest and lock once. After that, provider-specific definition/strategy modules and
focused tests may implement in parallel in disjoint files, while the shared production catalog tuple,
generic composition seams, docs, and real dogfood are integrated serially. This preserves useful
parallelism without letting two backend tickets race the same package lock or registration authority.

## D-acp07-provider-compaction-is-in-place — Preserve the backend's real session lifecycle

Hermes keeps its fork/private-load/CAS/rebind capture only because its persisted same-session state was
proven insufficient. Codex and Claude expose compaction lifecycle on their existing ACP session, so a
generation-bound strategy may instead implement one optional `capture_compaction_in_place` hook after
the exact runtime lease is acquired. The generic wrapper owns the actor's existing absolute deadline
and cancellation; a Panels timer expiry is the exact nonfatal `backend observation` phase with the
durable binding stayed, while a backend-raised timeout remains that backend's concrete failure.

An in-place terminal result creates no capture transition, binding generation, reset/replay, actor
rekey, or queued-message retarget. This is intentionally the smallest provider-neutral seam and keeps
all Hermes behavior unchanged. Startup validation is orthogonal: materialized backend registrations
may carry one optional ordered async preflight, awaited before app state, loops, or routes become
available. Claude uses it for one initialize-only transient child; Codex and ordinary worker children
remain lazy.

## D-acp-claude-private-summary-is-provider-replay-metadata — Suppress the exact synthetic chunk

Pinned `claude-agent-acp` 0.60.0 reads Claude Code's `isCompactSummary` JSONL row but drops that
marker when it replays the row as an ordinary ACP `user_message_chunk`. The message is not a human
turn: it is Claude's private generated compaction context and includes implementation detail such as
the source transcript path. Showing it violates Panels' content-free compaction contract.

Panels handles this only in `ClaudeAcpTurnStrategy.classify_replay`. An exact match for the pinned
Claude summary template is replaced by one terminal content-free `ContextCompaction`; ordinary and
nearby user messages remain untouched. The generic ACP browser wire and transcript reducer do not
learn Claude's private format, and the adapter is not forked. This keeps the workaround narrow,
testable against the exact installed provider, and removable when the upstream adapter preserves a
typed compaction boundary.

## D-claude-requested-cancel-requires-fresh-child — Trust the exact-generation boundary

Real Claude Send Now dogfood proved that a terminal `session/prompt` response is not sufficient
evidence that the pinned adapter has stopped emitting the cancelled turn: the old final answer arrived
after the successor completed. ACP updates have no prompt identity, so Panels cannot distinguish that
late text from valid successor output by inspecting the message.

Claude therefore declares one backend capability requiring the already-built requested-cancel
fresh-child recovery after every Stop or Send Now, including a normal terminal cancel response. The
generic broker reads the capability; it does not branch on the backend name. Hermes and Codex retain
their current terminal-response reuse path. There is no grace timer, output parser, public wire change,
or provider fork; the old exact source stays quarantined until it is retired and the same durable ACP
session is privately loaded into a fresh child generation.

## D-acp10-final-review-is-one-bounded-settled-tree-pass — Close findings, not ceremony

ACP-10 receives one independent review after the provider matrix, evidence, qualification, legacy
proof, docs, and the Claude requested-cancel correction have all settled. The reviewer must inspect
the named load-bearing seams and report concrete P0/P1 requirement violations or evidence
contradictions, not refactoring or polish suggestions. The resulting review is `READY` with zero
unresolved findings and is accepted in full. No second broad round is useful or required; the tree
may advance directly to the no-writer freeze and sole canonical verification.

## D-acp10-failed-frozen-snapshot-reopens-only-the-failed-gates — Retain, correct, re-audit

ACP-10's first frozen verification attempt is a real failed snapshot and remains retained with exit 1,
`VERIFY: FAIL`, its full log/digest, and an unchanged input manifest. It is not hidden, retried, or
called canonical success. The failures are bounded: four Ruff sites and 14 stale test fixtures or
manifest assertions that do not carry the already-delivered `employee_backend` contract.

The tree therefore leaves the freeze only for those exact corrections. Each disjoint test group may
receive a sub-agent and focused checks; product behavior, ACP evidence, provider dogfood, and the
settled-tree review remain valid unless a correction crosses their boundary. After focused evidence
and root disposition, a fresh no-writer snapshot is required before deciding on another full gate.

## D-acp10-fresh-snapshot-may-receive-one-clean-canonical-gate — Failure remains evidence

The bounded post-failure correction is complete: exact implicated Ruff is clean, the exact 10-file
unit slice passes 76/76, root found no behavior or assertion weakening, and the failed snapshot's
input manifest stayed unchanged during its run. The failed log is preserved under an attempt-specific
name and remains a first-class result in `verification-report.md`.

ACP-10 may now establish a new no-writer snapshot and run one clean canonical gate for that changed
tree. This is not an immediate retry on the failed snapshot: diagnosis, contract-scoped parallel
correction, focused proof, root disposition, and a new freeze intervene. Completion still requires
exactly one passing full run on the final frozen inputs; the earlier failure is neither erased nor
counted as success.

## D-acp10-closes-on-the-clean-final-snapshot — Preserve the failed attempt and ship the proved tree

The final corrected no-writer snapshot passed every canonical gate: Ruff, strict Mypy, build,
frontend, 1,019 unit tests, and 103 e2e tests, with exit 0 and `VERIFY: PASS`. Its 1,162-entry input
manifest remained byte-for-byte unchanged. The earlier failed snapshot remains retained with its own
log, digest, failure counts, and unchanged manifest; it is not erased or presented as success.

Final Safari Computer Use against the unchanged verified build loaded the real Panels Ticket route as
`Connected`, with the restrained editor/proposal surface, narrow Claude rail, durable typed replay,
content-free compaction, and idle Worker. The registered functional-worker tuple remains exactly
`hermes, codex, claude`; Gemini remains absent. The independent review has zero unresolved findings,
the legacy/docs/backend/Computer Use artifacts all pass, the requirement ledger has zero unproved
rows, and ACP-10 plus the full ACP migration are complete.

## Chat panel redesign (worktree chat-panel-redesign)
- New-conversation placement: owner ruled "not beneath the composer box", location delegated. Decision: header overflow (`⋯` menu, confirm step) — it is a conversation-lifecycle action and the header is the conversation's identity row; keeps it far from Stop.
- Ticket pipeline collapsed to dispatch → implement → orchestrator review (per-ticket plan/plan-review skipped): tickets are contract-scoped against an owner-approved mockup + DESIGN.md, low ambiguity. One combined-diff review before integration instead.
- Wave-1 tickets do not touch test files; all test reconciliation is T6's, so parallel agents never share files. Program reserves one full ./verify for the settled tree (per CLAUDE.md allowance); per-ticket gates are svelte-check + vite build.
- T2 judgment calls accepted: `interrupted` pill uses neutral faint outline (no `--accent-warn` token exists and tickets forbid new tokens — revisit at token layer only if owner wants amber); step titles render as plain text (parsing path fragments out of server titles is fragile); mockup 12px/10px micro-labels map to `--type-xs`.
- Integration glue by orchestrator: one line in AcpConversationPane.svelte passing `activity={snapshot.activity}` to TranscriptView (T2 could not touch T1's file); check+build re-verified after.
- Wave 2 amendment: T3 barred from app.css (T5 owns its composer block concurrently); T3 styles component-scoped.
- T3 accepted: pill is a <button> (a11y + zero-warning invariant); plan data read from snapshot.session.plan (full-replace on `plan`); `plan_removed` stays routed to unsupported-content because the client doesn't advertise that ACP capability — pill clears via turn-end instead (flagged, not descoped); popover drop-shadow deferred (component-style invariant bans box-shadow; needs a global app.css class — add after T5 releases app.css).
- Combined-diff review (independent, read-only) findings and resolutions: (1) BLOCKER — T2 wrote the 218-line transcript CSS block into the MAIN tree's assets/app.css (worktree-boundary violation its report did not surface); block extracted from the main-tree diff, inserted into the worktree app.css, main tree restored clean (`git checkout -- assets/app.css`, pure-addition diff verified first). (2) Failure stanzas/steps were permanently forced open; changed to open-once-on-failure (failureOpened flag), collapsible afterwards, aria-expanded now truthful. (3) `working` activity reconciled as live/active in TranscriptView + TaskProgressStrip (composer already counted it); currently unreachable on the wire but now consistent. (4) PermissionPrompt's imperative mount() for diffs accepted as-is — teardown correct, works.
- T6 surfaced a genuine production regression (not a test artifact): TranscriptView keyed user render-items by message id alone (`u:${id}`), so a multi-part user message produced duplicate keys → Svelte each_key_duplicate runtime crash on the first prompt, freezing the transcript. Orchestrator fix: key includes the part index (`u:${id}:p${partIndex}`), mirroring the agent branch's per-part keys. check/build/web-suite green after.
- T6 env note accepted: `.venv/bin/pip install -e . --no-deps` in the worktree to register planner for verify's python gates (no source change).

## D-acp11-native-role-skills-and-message-kickoff — Use backend-native discovery and ordinary ACP delivery

Panels exposes its canonical repository `skills/` directory through Codex's `.agents/skills` and
Claude's `.claude/skills`, while retaining the existing Hermes-home symlinks. A shared ACP child
decorator adds the appropriate installed role skill to whichever real prompt arrives first in each
new conversation: `panels-worker` for Tickets and `panels-chief-of-staff` for the Chief. It does not
repeat on later prompts or loaded/forked continuations, and replay does not attribute the delivery-only
directive to the human. Claude's provider-specific `_meta.systemPrompt` append is removed. This gives
all supported backends the same role source and delivery mechanism without modifying an upstream
adapter, copying skill files, maintaining a second registry, or adding durable first-turn state. The
bounded slice collapses plan review into implementation and receives one independent final diff review.

## D-worker-type-default-agent-configuration — Defaults seed one Ticket's Kickoff setup

Each Worker type owns the default Employee backend, model, and reasoning effort for its Tickets. A new
Ticket copies the Worker type's supported defaults into that Ticket exactly once. The Worker type is
only the source of the starting values: afterward the user edits the Ticket's current Worker, Model,
and Reasoning directly. There is no reset-to-Worker-type-default action, no remembered per-backend
matrix, and switching back to a Worker does not restore the Worker type's original values.

These are not header or ongoing conversation controls. They live inside the Kickoff section beside
the point where Kickoff is approved, and are editable only during the existing pristine-Kickoff
window. The saved model and reasoning values are historical first-session launch inputs, not a live
mirror: after first binding ACP owns the session state, Panels does not reapply them on a load, and
the UI does not show them as current settings. The controls are backend-capability-aware: Codex and Claude
expose Worker, Model, and Reasoning, while Hermes exposes Worker and Model only because its pinned ACP
adapter has no functional session reasoning control. Combined setup presets are deferred.

## D-first-ticket-binding-fails-closed-on-launch-authority — Never bypass the prepared trio

An unbound Ticket may enter the durable ACP binding table only through the initial-binding operation
that compares the complete prepared Worker/Model/Reasoning launch request with the current Ticket row
inside the same transaction. The registry may retain optional constructor seams for Chief and
already-bound test runtimes, but it rejects an unbound Ticket before spawning when that operation is
absent. If another first binding wins, a Ticket race loser must also have the repository Employee
resolver: it re-resolves the winner after binding and requires launch Model/Reasoning to be null before
loading it. There is no compatibility fallback to ordinary binding CAS or to clearing the loser's
in-memory request, because either would weaken the one-time launch boundary.
# D-acp-replay-is-not-live-browser-backpressure

ACP conversation replay and live browser backpressure are different responsibilities. A complete
valid replay is streamed as the subscriber-local bootstrap phase of an ordered browser subscription;
it is never preloaded into the bounded live-delivery queue. First load, refresh, compaction, and
runtime recovery all build and validate a detached replay candidate before committing it. The newly
attaching browser receives that bootstrap directly; an existing browser receives an ordered cutover,
with later live updates behind it. The production live queue remains slow-client isolation and is
raised from 128 to 1,024 envelopes for operational headroom. Replay integrity failures and genuine
live slow-client evictions close through one idempotent permission-detach owner and are logged with
identity, generation, counts, and limits but no conversation content.

## D-compact-workers-current-main-closeout — preserve current ACP and schema history
**Context:** The approved compact Workers commits predated the current conversation composition,
environment isolation, managed-Markdown pipeline, and main's schema v28/v29 migrations. Main also
advanced again during Closeout.

**Decision:** Replay the approved behavior onto current main rather than restoring retired `minds`
or gateway-adapter code. Keep main's v28/v29 migrations and add captured ownership defaults as v30.
Use the configured runtime registry everywhere, materialize managed specialist skills from each
instance database parent after its canonical data tree is settled, and keep failed description/body
candidates independent from canonical publication. A successful save of one field updates that field
in the candidate without publishing or discarding the other field's failed draft.

**Why:** Worker settings belong beside the active database, while Employee sessions and current ACP
composition remain untouched. Versioned migration order protects live databases, and preserving a
failed draft across an independent save is required by the approved direct-edit interaction.

The current-main post-fork SDK regression continues to require exact-session routing, source-before-
replay ordering, and a fully drained healthy ingress. It does not require a candidate notification to
arrive before the subsequent load request: the private response epoch is installed first, and ACP does
not guarantee notification/request wire ordering across the prior fork response.
