# Deeper Panels simplification

The owner explicitly asked to continue beyond the first removals and take on larger
structures where a better model would simplify working in Panels. This continues
the same goal. The first pass is complete at staging d55260b3de6e88b8d69275e19f9c3c244ef21030,
with full verification, independent review, and remote confirmation. Its 54.1% test
reduction remains an acceptance constraint for the final combined work.

Worktree: `/Users/khushaljagota/Coding/planning-v2-worktrees/panels-simplification-deeper`.
Branch: `codex/panels-simplification-deeper`. Base: current verified staging d55260b3.
Own Python and npm dependencies are installed; the imported source resolves to this
worktree and no ambient PLAN runtime paths point elsewhere.

The goal is fewer concepts, invalid states, and repeated decisions. Renaming things,
removing a Worker type, and merely splitting a large file are not sufficient.
Remove an existing responsibility before introducing a boundary or replacement.

## Candidates under design

### Durable outcomes and Sprint commitments

A Sprint Item currently combines shared context, supervisor scope, and one Sprint's
placement. Assigning it to another Sprint cascades to every child Ticket, including
completed work, and schedule templates. Conversely, moving a Ticket may detach its
outcome context. A fresh read-only inventory finds 13 Items containing both completed
and unfinished Tickets. The code establishes the problem; population is supporting
evidence rather than a claim about usefulness.

Candidate: retain each shared brief and its identity across Sprints. A small explicit
Sprint-to-outcome commitment relation represents choosing an outcome before Tickets
exist. Ticket scheduling remains independent. Current Sprint tracking shows committed
outcomes even when empty, associated work in that Sprint, and work under uncommitted
outcomes without hiding it. Carry-forward selects unfinished work; it never relocates
completed history. Outcome context/artifacts and supervisor conversations remain.

Remove mirrored Sprint placement, cascades, detach-on-Sprint-move behavior, synthetic
per-Sprint Planning containers, and misleading aggregate 'outcome done' claims. A
Ticket completion count can say exactly what it measures. Preserve Project coherence
initially because it also establishes the supervisor's repository context.

### One current Ticket proposal

The ordinary workflow and Review have one current approval. Storage instead allows
an independent proposal on every field, and an arbitrary-field agent route can park
a proposal below scope without requiring the current field. Review reads only the
current gate. This creates invisible pending work and needless acceptance branches.

Candidate: plain settled field values plus one pending Ticket proposal for the
current gate. A Worker files its current result; it either settles and advances once
or parks for the user's decision. Passed values remain directly editable. Stage jumps
must not orphan pending text. Preserve scope, ownership, actual prompt delivery, and
failed-send receipts.

Historical off-stage proposals must remain explicitly unapproved and accessible.
There is no existing Ticket event-history reader: EventSpecs are not persisted by
_apply_decision, and supervisor history is protected conversation history. A small
read-only archival document may earn its existence for removed field content; do not
mislabel proposals as approved values or user Guidance, and do not pretend filesystem
export is atomic with SQLite. Exact shape and migration await contract review.

### Further candidates

Audits also found worthwhile follow-ons: truthful outcome recording for work completed
outside Panels (instead of reconstructing a fictional settled stage prefix), fewer
mandatory planning documents, and ordinary scoped domain commands instead of a second
supervisor action dialect. These are evidence-backed candidates, not authorization
to introduce all replacements together. Select coherent chunks after removing the
current coupling and reviewing the resulting interfaces.

## Protected boundary

No implementation or intent changes to the multi-backend conversation system:
`src/planner/conversation/`, `runtime/conversation_start.py`, `message_delivery/`,
`worker_context/`, `agent_backends/`, or frontend conversation modules.

Concrete frozen consumers include `planner.sprints.contracts.SprintItem`,
`sprints.data.read_item(...).item`, `sprints.service.supervisor_lifecycle_lock`,
`tickets.sprint_item_id`, and the `sprint_items` identity/kind/agent link queried by
conversation_start. Keep these existing interfaces stable; Outcome semantics do not
justify editing the protected code or building a parallel conversation system.

## Execution and verification

Root owns intent, exact contracts, scoped tickets, independent reviews, and serial
integration. Astra audits and handles intricate semantic changes; Sol handles bounded
inventories and mechanical work. Implementation follows reviewed contracts. Parallel
work must use disjoint files or separate worktrees.

No full `./verify` during this audit or implementation chunks. Use named focused gates;
reserve the next complete run for this program's final settled tree. Actual tests may
be deleted further where removal eliminates their responsibility; new meaningful proof
counts against the final inventory. Never hide cases in loops or new skips to satisfy
the reduction. Baseline remains 2,505 runtime/pytest cases, final ceiling 1,252, E2E at
most 19. First-pass final count is 1,151.

Final integration follows the same route: current staging, focused repairs where needed,
one clean settled-tree verification, exact origin/staging push and remote confirmation,
existing rolling PR58, then task-owned cleanup. Main deployment remains a later owner
action. Live Panels access remains read-only.

## Fresh live evidence

Private read-only API exports are under `data/simplification-deeper/`, outside git.
Bounded summaries returned all 778 Tickets and 78 Items. 184 Tickets have no Item;
13 Items contain both done and active Tickets. The earlier live server remains older
than staged work; migrations and rehearsals operate only on local temporary copies.
