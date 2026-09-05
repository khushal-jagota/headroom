# Durable Outcomes and explicit Sprint commitments

Candidate contract for root review, 2026-09-05. Source: verified first-pass staging
`d55260b3`; this document does not authorize implementation before contract review.
No code, schema, tests, services, or live state were changed while preparing it.

## Decision and the responsibility removed

An Outcome holds shared intent and the conversation that helps achieve it. A Sprint
commitment says the user chose that Outcome for that timebox. A Ticket states when one
piece of work is scheduled. These are independent facts.

Currently, `sprints.data.assign_item_sprint` moves every child Ticket and associated
schedule template, including completed work. A fresh read-only inventory found 13
Items with both completed and unfinished Tickets; the code, rather than that count,
establishes why carrying an Outcome forward can relocate history.

Replace the Item's single Sprint field with a small explicit commitment relation.
Remove mirrored Sprint placement, automatic child/template moves, detach-on-Sprint-
change, and mandatory per-Sprint Planning Items. Do not replace these with hidden
Markdown links or implicit membership inferred only from Tickets: sprint planning
chooses Outcomes before Tickets exist, and the tracking surface must show that choice.

Retain Project coherence initially. An Outcome and its associated Tickets have the
same Project. This also preserves the supervisor's existing repository context.
Outcome Project changes retain today's explicit Project cascade; they must not change
Ticket Sprint placement, commitments, or conversation identity.

## Frozen boundaries

All implementation and intent within these paths remain byte-for-byte unchanged:

- `src/planner/conversation/`
- `src/planner/runtime/conversation_start.py`
- `src/planner/message_delivery/`
- `src/planner/worker_context/`
- `agent_backends/`
- `web/src/components/conversation/` and `web/src/lib/conversation/`

Keep physical table `sprint_items`, its existing IDs, `kind` values, supervisor columns,
and `tickets.sprint_item_id`. Keep supervisor agent keys, agent rows, conversation
links, artifact paths, existing Item workspace addresses, and child authorization.
Existing `kind='normal'` SQL in the protected conversation code continues to work.
There is no new `planning` kind: existing Planning Items are normal Items and remain so.

`planner.sprints.contracts.SprintItem` remains the actual importable record class, with
all supervisor fields intact. `sprints.data.read_item(...).item` still returns it.
`sprints.service.supervisor_lifecycle_lock` remains at the same import path and owns
the same lock registry. No second lock, supervisor identity, conversation layer,
provider adapter, prompt-delivery path, or replacement conversation API is introduced.

The legacy name is deliberately internal. User-facing planning copy says Outcome.
Existing CLI `sprint item` and `/api/items` remain the single identity-management doors;
adding a parallel `outcome` alias and second REST catalog earns nothing in this program.

## Exact data contracts

Root lands these contracts before delegating implementation. Definitions below extend
the existing contracts; unlisted fields retain their existing type and behavior.
Do not independently redeclare response shapes in presentation modules.

Python, `src/planner/sprints/contracts.py`:

```python
@dataclass
class SprintItem:
    id: str
    title: str
    body: str
    priority: Priority
    deadline: str | None
    project_id: str
    project_name: str
    supervisor_agent_key: str
    supervisor_launch_configuration: SprintItemSupervisorLaunchConfiguration
    kind: SprintItemKind = SprintItemKind.normal
    created_at: int = 0
    updated_at: int = 0
    # No sprint_id: there is no single truthful value to expose.

@dataclass(frozen=True)
class SprintOutcomeCommitment:
    sprint_id: str
    outcome_id: str

class SprintSummary(TypedDict):
    id: str
    name: str
    date_start: str
    date_end: str

class CarryOutcomeBody(TypedDict):
    target_sprint_id: str
    ticket_ids: list[str]  # Exact selection; empty means commitment only.

class CarryOutcomeResult(TypedDict):
    source_sprint_id: str
    target_sprint_id: str
    outcome_id: str
    ticket_ids: list[str]  # The requested selection now verified in target.

class OutcomeSummary(TypedDict):
    id: str
    title: str
    priority: str
    deadline: str | None
    project_id: str
    project: str
    created_at: int
    updated_at: int

class SprintTicketSummary(TypedDict):
    id: str
    title: str
    stage: str
    priority: str
    ticket_status: str
    project_id: str | None
    sprint_item_id: str | None
    waiting_to_closeout: bool

class SprintOutcomeGroup(TypedDict):
    outcome: OutcomeSummary
    committed: bool
    tickets: list[SprintTicketSummary]

class SprintTrackingBody(TypedDict):
    planning_date: str
    sprint: SprintWireBody | None  # Existing sprint_json fields, named in contracts.
    outcome_groups: list[SprintOutcomeGroup]
    unclassified_tickets: list[SprintTicketSummary]
```

`SprintWireBody` names the current Sprint JSON structure without changing its fields:
`id`, `name`, `date_start`, `date_end`, `primary_bet`, `kickoff`, `checkpoint`, `review`
are strings; `created_at`, `updated_at` are integers. The no-current-Sprint response has
`sprint=None` and both lists empty. `waiting_to_closeout` uses the existing server-owned
Ticket presentation projection; do not introduce a second readiness calculation.

Item detail/workspace responses retain their existing identity, body, supervisor,
artifact, child-Ticket and blocker fields, remove `sprint_id`, `status`, and the always-
empty `status_proposal`, remove `rollup`, and add
`committed_sprints: list[SprintSummary]` in date order.
Reuse the existing Sprint-summary fields and make their dates required in TypeScript,
matching the actual server projection. This response never duplicates Sprint documents.

Each `SprintItemWorkspaceTicket` adds these exact wire fields in both the Python
workspace-child contract and `web/src/lib/types.ts`:

```python
sprint_id: str | None
sprint_name: str | None
```

```typescript
sprint_id: string | null;
sprint_name: string | null;
```

`sprint_id` is the child's own `tickets.sprint_id`, never an Outcome commitment or a
current-Sprint default. A left join to that Ticket's Sprint resolves `sprint_name` in
the same workspace read; both are null for backlog. Preserve an empty stored Sprint
name as empty rather than synthesizing a name in the API. This lightweight name field
earns its existence by avoiding a new catalog query or a false lookup through only
`committed_sprints`: a child's historical Sprint need not remain committed. All other
workspace-child fields and every supervisor/conversation payload remain unchanged.

`ItemRead` retains `.item`, `blocking_ticket_ids`, and `blockers_cleared`, and drops its
aggregate `status`. Remove `ItemStatus`, `ITEM_STATUS_ORDER`, the Item status derivation,
and status filters. Keep direct Item blocking links and Ticket blocker semantics;
this program removes an unsupported outcome-completion claim, not blocking behavior.
Ticket progress is explicitly labeled “n/m Tickets done,” with dropped Tickets excluded
from the denominator. Zero children says “No Tickets”; it never says Outcome done.
This also applies when all children are dropped: zero non-dropped children means
“No Tickets.” Counts come from the loaded Ticket arrays: the full children in the
Outcome workspace, and that Sprint's group children in Sprint tracking. The existing
`workspaceProgress` already follows this child-based calculation. Delete
`sprints.views.item_rollup`, its separate aggregate SQL, Coding-stage zero filling, all
wire `rollup` fields/types and the CLI Item `rollup` record part. Do not replace it with
another stored or separately fetched aggregate. `panels sprint item show` continues to
show the Outcome record and blockers; detailed Ticket listing remains the existing
child/workspace reads. Keep `read_item(...).item` and its blocker fields as specified.

`SprintItemDeletion.sprint_ids` is the tuple of all commitment Sprint IDs, captured
before deletion, rather than a zero-or-one value. Existing deletion still refuses an
Outcome with children and preserves its file quarantine/lifecycle transaction boundary.

TypeScript, `web/src/lib/types.ts`, mirrors the above wire shapes exactly:

```typescript
export type SprintSummary = {
  id: string; name: string; date_start: string; date_end: string;
};
export type OutcomeSummary = {
  id: string; title: string; priority: Priority; deadline: string | null;
  project_id: string; project: string; created_at: number; updated_at: number;
};
export type SprintTicketSummary = {
  id: string; title: string; stage: string; priority: Priority;
  ticket_status: string; project_id: string | null; sprint_item_id: string | null;
  waiting_to_closeout: boolean;
};
export type SprintOutcomeGroup = {
  outcome: OutcomeSummary; committed: boolean; tickets: SprintTicketSummary[];
};
export type SprintTrackingBody = {
  planning_date: string; sprint: SprintWireBody | null;
  outcome_groups: SprintOutcomeGroup[];
  unclassified_tickets: SprintTicketSummary[];
};
export type CarryOutcomeBody = { target_sprint_id: string; ticket_ids: string[] };
export type CarryOutcomeResult = {
  source_sprint_id: string; target_sprint_id: string;
  outcome_id: string; ticket_ids: string[];
};
```

Use existing `ListPageFacts` for bounded Outcome summaries and existing Ticket workspace
types for full child reads. Preserve `SprintItemSummary`/`SprintItemWorkspace` type names
where their current consumers need them; remove their single-Sprint/status properties
and make their shared fields reuse `OutcomeSummary` instead of maintaining copies.

## Storage and migration

One new migration, chained after the actual settled preceding revision chosen by root:

```sql
CREATE TABLE sprint_outcomes (
    sprint_id TEXT NOT NULL REFERENCES sprints(id) ON DELETE CASCADE,
    outcome_id TEXT NOT NULL REFERENCES sprint_items(id) ON DELETE CASCADE,
    PRIMARY KEY (sprint_id, outcome_id)
);
CREATE INDEX idx_sprint_outcomes_outcome_id ON sprint_outcomes(outcome_id);
INSERT INTO sprint_outcomes(sprint_id, outcome_id)
    SELECT sprint_id, id FROM sprint_items WHERE sprint_id IS NOT NULL;
```

The reverse index serves Outcome detail and deletion. No synthetic row ID, status,
position, timestamps, approval record, or commitment history ledger is needed.

Before changes, snapshot into transaction-local temporary tables: all Ticket IDs,
Project/Sprint/Item links; all Item identities, old Sprint, kind, content, supervisor
fields and timestamps; schedule IDs plus effective Project/Sprint/Outcome destination;
and occurrence IDs/counts. Never export migration evidence to the filesystem as though
it were part of SQLite's transaction.

Remove `idx_sprint_items_one_other_per_sprint_project` before dropping the Item Sprint
column. It survives the first pass despite having no Other rows and references that
column in both its key and predicate. There is no Planning-kind unique index to add.
Preserve `kind`, including its existing CHECK, and preserve
`idx_sprint_items_supervisor_agent_key` and all supervisor guard/create triggers exactly.
Use SQLite native `ALTER TABLE sprint_items DROP COLUMN sprint_id` if supported by the
project runtime and the surviving schema permits it; do not rebuild a heavily linked
table and accidentally replay supervisor-create triggers. If native drop is unavailable,
stop this ticket at a concrete migration finding for root to resolve, rather than
inventing a live-table rebuild during implementation.

All Ticket Project/Sprint/Outcome links stay byte-for-byte unchanged. No attempt is made
to reconstruct already-relocated historical Tickets: the existing database has no
authoritative original placement to recover. All Item writing, IDs, kind, agent links,
supervisor launch values, artifacts and conversation history remain unchanged. Each
previously assigned Item gains exactly its old commitment; unassigned Items gain none.
Existing Planning Items and their commitments survive. No new Planning Items are
automatically created when making a Sprint, Ticket, or schedule occurrence.

Scheduled templates use existing storage columns but remove `sprint_item` as a placement
mode. Keep `current_sprint` and `backlog`; `sprint_item_id` is optional context in either.
For `current_sprint`, a non-null `sprint_id` retains the existing explicit fixed-Sprint
semantics, and null resolves the current Sprint at occurrence time. The UI names the
three actual choices Current Sprint, Backlog, and a specific Sprint; no new mode is
required merely to encode the already-supported fixed choice.

For each legacy `placement_mode='sprint_item'` template, snapshot the referenced Item's
old Sprint and Project. If that Sprint is non-null, set mode `current_sprint` and that
explicit `sprint_id`; otherwise set mode `backlog`, `sprint_id=NULL`. Preserve its
`sprint_item_id`, effective Project, all other template values and occurrence receipts.
Other modes retain their resolved behavior and explicit Sprint values. No migration
silently changes a fixed legacy schedule into a rolling current-Sprint schedule.
Update the schedule table CHECK if it constrains the placement mode; preserve its
occurrence foreign keys and slot indexes. All enabled flags and receipts remain exact.

Before committing, assert snapshot equality for all preserved rows/values, exact
commitment correspondence, unchanged effective schedule destinations, absence of a
remaining Item Sprint column/index reference, and `PRAGMA foreign_key_check` empty.
No downgrade can encode multi-Sprint commitments as one Item Sprint without loss;
use the project's explicit unsupported-downgrade pattern.

## Writers and invariants

1. A Ticket's Sprint update changes only its scheduling fact and ordinary existing
   placement-change bookkeeping. Its Outcome membership survives. The backend rejects
   a final Project/Outcome mismatch; it does not infer detachment. When the user changes
   Project in the Ticket UI, that UI explicitly includes `sprint_item_id: null` in the
   submitted patch if the existing Outcome is incompatible. Changing Project and the
   explicit removal of incompatible context remain reviewable parts of that request.
2. Classifying a Ticket under an Outcome aligns its Project and changes its Item link,
   but preserves its Sprint exactly. Unclassifying it preserves both Project and Sprint.
3. Outcome commitment changes never write Ticket, Day, schedule, or conversation rows.
4. Ticket creation with an Outcome inherits its Project; omitted Sprint resolves through
   the ordinary current-Sprint default. Explicit backlog or Sprint wins independently.
   An explicitly conflicting Project is rejected. No synthetic Planning Item override.
5. Outcome Project edits update associated Ticket/template Projects in the existing
   transaction, preserving their Sprint destinations. Outcome title/body/priority/
   deadline edits cannot affect time placement.
6. Schedule validation resolves optional Outcome Project independently of its placement
   choice. The occurrence writer resolves time placement first, supplies it explicitly
   to ordinary Ticket creation, and supplies optional Outcome context independently.
   Planning schedules continue to use Personal through their template, with no forced
   Outcome. Current-minute matching, duplicate suppression, receipts, readiness and
   launch behavior are unchanged.

Carry-forward is one compound write, not a sequence of client mutations. Its transaction
validates both existing Sprint IDs, distinct source and target, source commitment,
Outcome identity, and every unique selected Ticket before any write. Selected Tickets
must be current children of that Outcome, in the source or already in the target, and
nonterminal under their Worker type registry. Done and dropped cannot be selected.
Tickets in a third Sprint or backlog, or reclassified Tickets, reject the whole request.
An empty list is valid: commit the Outcome without moving work. Add target commitment;
move only selected source Tickets through the canonical Ticket placement writer;
already-target selections are harmless repeats. Keep the source commitment and all
unselected Tickets unchanged. Neither Day membership nor scope/status/stage changes.
If a selected Ticket became terminal between selection and submission, fail visibly and
refresh; elapsed UI time is not permission to move newly completed history.

The existing planning-sprint authority admits commitment and carry writes, in addition
to ordinary direct/Chief access already granted by `require_planning_write`. Ordinary
Ticket PATCH placement fields (Project, Sprint and Item) remain direct-only under the
existing field-admission rules. The new classification PUT/DELETE routes use exactly
the existing `require_ticket_worker_write` guard: direct access or any valid Ticket-
backed Worker claim, without an own-target restriction. This existing broad boundary
allows initiative-planning work to classify other Tickets; this program neither narrows
it nor extends it to scheduling, commitments or carry selection. Supervisor authority
and current-child checks remain exactly as now. New commitment routes do not widen
supervisor powers. Consolidating the separate supervisor action dialect is later work.

## API and CLI

Keep existing `/api/items`, `/api/items/{id}`, workspace, supervisor and artifact paths.
Item create/update no longer accept `sprint_id`; explicitly reject it instead of silently
ignoring an old request. Item list removes status/Sprint filters. Bounded summaries
accept Project plus optional `search` text applied to title, and existing limit/offset;
this is the catalog shared by Add outcome and Backlog's collapsed Outcomes browser. No new search
engine: SQLite title matching and deterministic priority/created/id ordering suffice.

New minimal operations:

| Operation | Contract |
| --- | --- |
| `PUT /api/sprints/{sprint_id}/outcomes/{outcome_id}` | Empty body; idempotently add commitment; return the two IDs. |
| `DELETE /api/sprints/{sprint_id}/outcomes/{outcome_id}` | Idempotently remove commitment only; return the two IDs. |
| `POST /api/sprints/{source_id}/outcomes/{outcome_id}/carry` | `CarryOutcomeBody`; atomic semantics above; `CarryOutcomeResult`. |
| `GET /api/sprints/{sprint_id}/tracking` | `SprintTrackingBody` for explicit historical/current/future Sprint. |

Existing `GET /api/sprint/current` delegates to the same tracking projection. A second
explicit-ID read earns its existence: carry selection and historical inspection must
not depend on the wall clock. No separate commitment-list endpoint is needed.

CLI adds `panels sprint outcome add <sprint> <outcome>`, `remove <sprint> <outcome>`,
`carry <source-sprint> <outcome> --to <target-sprint> [--ticket <id> ...]`, and
`list <sprint>` backed by the shared tracking read. Empty carry selection is explicit
commitment-only, not an implied “move all.” Keep ordinary `sprint show` for documents.

Remove `sprint item create --sprint`, `sprint item set ... sprint`, Item `--status` and
`--sprint` list filtering, `move-ticket`, and `move-ticket-to-backlog`. The latter two
couple classification to scheduling; replace classification with
`panels sprint item add-ticket <outcome> <ticket>` and `remove-ticket <outcome> <ticket>`.
Use ordinary `panels ticket set <ticket> sprint --value .../--clear` for scheduling.
Their API counterparts are PUT/DELETE `/api/items/{outcome_id}/tickets/{ticket_id}`;
remove the old POST/classification and special backlog compound route. Removal checks
the named current Outcome before clearing so a stale request cannot detach a new one.
Return unchanged Ticket on an already-cleared repeat; reject a different current Outcome.

No conversation/message/restart or supervisor command is renamed. The independent
proposal program owns proposal shapes and Ticket gate behavior.

## Tracking query and minimum UI

For a requested Sprint, read one coherent snapshot. The displayed Outcome IDs are the
union of explicit commitments and non-null Item links on Tickets directly in that
Sprint. Each group has `committed` from the relation and only Tickets with that direct
Sprint ID. Unclassified Tickets are the remaining directly scheduled Tickets. Every
Sprint Ticket appears exactly once; every commitment appears even with zero children.
Never select all children of a committed Outcome for this screen. Sort Projects by
existing Project priority; Outcomes by priority/created/id, without aggregate done
sorting. Reuse current Ticket condition presentation and existing Ticket ordering.

Sprint tracking retains Primary bet and documents. Within Projects, distinguish
Committed outcomes from Other work. Outcome groups without a commitment live in Other
work alongside unclassified Tickets; they stay readable and link to the canonical
workspace. “n/m Tickets done” is qualified as Ticket progress, not Outcome completion.

Add outcome opens a compact existing-pattern selector: search and Project filter,
existing outcome titles, and New outcome with title, Project, optional brief. Select
calls PUT. New creates via ordinary Item creation then commits the returned ID. If the
second request fails, retain that returned ID and offer retry; never recreate after an
ambiguous response. Existing local list data shows the newly created Outcome.

Each committed Outcome exposes Remove from Sprint and Carry forward in its ordinary
action menu. Carry selects a target from existing Sprint summaries and presents only
that Outcome's source-Sprint nonterminal Tickets, all initially unchecked. Names make
the exact movement reviewable. Submission sends selected IDs once; no default “all.”
Removing a commitment leaves scheduled work visible under Other work. Clicking the
Outcome opens its existing workspace: full brief/artifacts and all its Tickets across
Sprints with existing Today/remaining grouping, plus concise committed-Sprint links.
Every visible child row also shows its own Sprint in quiet metadata beside the Ticket
title: `Backlog` for a null `sprint_id`, otherwise its `sprint_name`, with the actual
`sprint_id` as the fallback for an unnamed Sprint. Do not infer this label from the
workspace's current Sprint or committed-Sprint list. Keep Ticket rows as links to their
canonical Ticket; avoid nested links in the row. This metadata remains legible on phone
by wrapping below the title. Changing or removing an Outcome commitment must not relabel
the historical Tickets beneath it.
The conversation pane and behavior remain unchanged.

Backlog keeps unscheduled active Tickets as the main list. Replace its Unscheduled
briefs list with a secondary Outcomes browser that arrives collapsed. Mount its bounded
Project/search catalog query only while the browser is open; closing it unmounts that
query. Expanding exposes the same search, sorting and paging used by Add outcome,
irrespective of commitments. It does not render the full historical catalog by default
or describe all Outcomes as unfinished. Selecting an entry opens its existing workspace;
New outcome creates an ordinary Outcome and opens the returned identity, without adding
a Sprint commitment. Preserve the returned ID after an ambiguous response rather than
creating again. No new sidebar surface, lifecycle flag or archive operation is added.
Ticket placement controls label Outcome and Sprint separately. Scheduled tasks do the
same; choosing a time destination never clears optional Outcome context merely because
the placement mode changes.

Planning skills: sprint planning selects commitments rather than moving Outcome
containers. Its approved package names exact Outcome IDs or creations; carrying any
Tickets requires an explicit selected list, otherwise it is commitment-only. Outcome-
first planning still does not create implementation Tickets or change Today. Daily and
Ticket-creation skills stop requiring Personal/Planning containers. Initiative planning
keeps shared context membership when creating Tickets but names their Sprint separately.
Supervisor role intent remains unchanged; it still scopes itself to its Outcome and
current children. Update only planning names and any now-invalid placement instructions.

## Module boundary and scoped implementation groups

Do not extract a new `outcomes/` CRUD tree merely to rename the same record while
re-exporting every function through frozen `sprints` interfaces. That increases doors
without removing a responsibility. Retain existing record, lifecycle and supervisor
owners. Add `sprints/commitments.py` as a deep application boundary: it alone owns
commitment mutations and validated atomic carry. Keep shapes in `sprints/contracts.py`;
put framework-free carry validation in `sprints/logic/commitments.py`. `sprints/views.py`
owns the single tracking projection. This boundary earns its existence by removing
Item-wide placement cascades and hiding the one new compound transaction from clients.

Root owns all contract/type edits and migration ordering, then issues reviewed,
contract-scoped tickets. Suggested allowed groups, with no concurrent shared-file edits:

| Ticket | Allowed implementation files | Named acceptance ownership |
| --- | --- | --- |
| O1 record + migration | new migration version; `sprints/data.py`, `sprints/logic/status.py` removal; relevant existing migration/Sprint tests | Snapshot-preserving migration, normal Planning identity preservation, no Item Sprint/aggregate status. |
| O2 independent Ticket placement | `tickets/data.py` placement functions only, `tickets/actions.py` creation placement only; focused Ticket placement tests | Classification does not schedule; Sprint edit does not detach; Project coherence preserved. Shares Ticket files with proposal work: serial integration or isolated worktree, never simultaneous edits. |
| O3 commitments + projection | new `sprints/commitments.py`, `sprints/logic/commitments.py`; `sprints/views.py`; narrow route additions/removals in `sprints/api.py`; focused `test_sprints.py`/`test_sprint_current_api.py` | Two-Sprint journey, stale carry rejection, zero-child commitments, truthful partition. Depends on O1/O2. |
| O4 schedule semantics | `scheduled_tickets/actions.py`, `data.py`, `api.py`, `views.py`; focused schedule tests | Current/fixed/backlog plus optional Outcome; no Planning synthesis; receipt behavior unchanged. Depends on root shapes/O1 migration. |
| O5 UI | `SprintRoute.svelte`, `BacklogRoute.svelte`, `ScheduledTasksRoute.svelte`, Ticket placement region of `TicketRoute.svelte`, nonconversation region of `SprintItemWorkspace.svelte`; `sprintPresentation.ts`, `sprintItemWorkspace.ts`, `scheduledTasks.ts`, `queryCatalogue.ts`; smallest shared Outcome picker component; scoped `assets/app.css`; related `web/tests` | Planned empty Outcome, reuse/create, explicit carry selection, Other work, catalog discovery, context-preserving controls. No edits inside protected conversation components. |
| O6 CLI + skills/docs | `cli/main.py`, `cli/record_projection.py`; planning/Ticket-creation/initiative/supervisor role skills for changed instructions only; `docs/sprints.md`, `projects.md`, `backlog-and-ideas.md`, `scheduled-tickets.md`, `cli.md`, `days.md`, `README.md`, `systems.md` and generated systems artifact if affected | CLI commands/read parts match contracts; plain-language model and discoverability documented. |

`sprints/service.py` deletion's commitment-ID collection may need a narrow O1/root
integration repair; the supervisor lock and conversation teardown path stay unchanged.
Other file discoveries return to root for an explicit scope adjustment. Agents do not
change contracts or touch protected files to make imports pass.

## Minimum meaningful proof and finish

- One integrated two-Sprint story creates an empty committed Outcome, associates done
  and unfinished Tickets in Sprint A, carries only the selected unfinished Ticket to B,
  and proves both commitments, unchanged completed placement, unchanged context/agent
  identities, exact tracking partitions, independent scheduling, and harmless repeat.
- One focused rejection story proves a mixed valid/stale or newly terminal carry changes
  nothing. Exercise ordinary authority and the unchanged supervisor current-child guard
  using existing integration fixtures; moving a Ticket's Sprint cannot revoke its
  supervisor membership, while actual reclassification does.
- One migration rehearsal with historical Planning Items, a mixed-work Outcome, null-
  Sprint Outcome, and legacy schedule modes proves row/content/identity preservation,
  fixed vs rolling destinations, indexes/triggers, and foreign keys.
- Extend the existing schedule behavior proof to optional Outcome with independent
  current/fixed/backlog Sprint and no auto-Planning Item. Keep current-minute receipts
  and suppression checks; do not duplicate the scheduling suite.
- Frontend tests prove empty commitments render, each Sprint Ticket appears once,
  uncommitted work remains visible, create/reuse failures retain the returned ID, and
  carry sends exactly the checked unfinished IDs. Backlog's Outcomes browser starts
  collapsed without a mounted catalog query, opens the bounded catalog on expansion,
  and opens existing/new identities without implying they are unfinished. The two-Sprint
  fixture also proves workspace rows label each child's actual Sprint (including one
  without a commitment), backlog rows say Backlog, and Ticket-derived progress excludes
  dropped children without requiring a rollup response. Verify the changed document/controls
  fit existing desktop and mobile layout in `web/tests`, using the existing browser
  component harness if needed. No live-server Playwright E2E: the risks are server
  transaction/query contracts and frontend selection/rendering, covered separately.

Delete tests for removed aggregate status, Item Sprint cascades, synthetic Planning
creation, and the obsolete special backlog move. Replace their responsibility rather
than hiding case counts. Respect the parent program's final test-count ceiling.
One independent review of the combined implementation checks this contract and the
frozen-boundary diff. Root spot-checks migration and atomic carry. Reserve `./verify`
for the entire deeper program's final settled tree, not this plan or individual tickets.
