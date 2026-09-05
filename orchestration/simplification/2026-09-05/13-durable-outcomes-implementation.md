# Durable Outcomes implementation

Implemented plan 10 against root-owned contracts `620e1e38`, following the repaired
plan `01e420c8`. Source first-pass base remains `d55260b3`. This isolated branch owns
O1–O6; root integrates it after the single-proposal branch and reparents the migration.

## Backend and CLI result

Outcome identity, body, artifacts and supervisor remain in the existing `sprint_items`
record. Its single Sprint placement and aggregate status/rollup are removed. Creating a
Sprint no longer manufactures Planning Items. Existing Planning records remain ordinary,
mutable Outcomes with the same IDs, agent links and content.

`sprints/commitments.py` owns explicit commitments and one atomic carry transaction.
Carry validates the exact selected IDs under the writer lock, preserves the source
commitment, adds the target commitment, and changes only selected source-Sprint Tickets.
Already-carried selections are harmless repeats. A terminal, reclassified or otherwise
rescheduled selection rejects the whole request. Empty selection adds only commitment.

Ticket placement and Outcome classification are independent. Classification aligns
Project; removing classification clears only its Item ID. Both preserve Sprint.
Changing a Ticket's Sprint keeps its Outcome. Backend placement validation rejects an
incoherent final Project/Outcome pair. Both ordinary and external-work creation validate
placement and blockers unconditionally after optional Project inference. Classification
retains the existing `require_ticket_worker_write` authority, including any valid
Ticket-backed Worker classifying another Ticket. Ordinary placement PATCH authority is
unchanged; commitments/carry use existing planning-sprint authority.

Tracking returns the root-owned `SprintTrackingBody`, `OutcomeSummary`,
`SprintTicketSummary` and `SprintWireBody` projections. It partitions every directly
scheduled Ticket exactly once, retaining committed empty Outcomes and uncommitted work.
Current-Sprint resolution and tracking run inside one deferred read snapshot, preserving
an existing transaction without acquiring the writer lock. Workspace reads keep the
existing child projection and add nullable `sprint_id`/`sprint_name` from one join;
committed Sprints are separate facts. Unused coding-stage rollup SQL and its CLI part
are gone.

Schedule creation validates Project inference within its transaction. Current, fixed,
and backlog placement accept optional independent Outcome context. Changing scheduling
does not clear context. Legacy Item-mode schedules migrate to the Item's old fixed
Sprint or explicit backlog, retaining their Project and Item link. Recurrence receipts,
suppression, current-minute evaluation and downstream Worker handoff remain unchanged.

CLI removes Item Sprint/status controls and old move verbs. `sprint outcome
add/remove/list/carry`, plus Item `add-ticket/remove-ticket`, expose the canonical
operations without parallel renamed CRUD. Ticket and schedule `--backlog` explicitly
set null Sprint while allowing Outcome context. Planning skills and live domain docs
now describe shared context, explicit commitments and selected Ticket carry.

## Migration proof

The migration creates `sprint_outcomes`, backfills old Item placement, removes the
obsolete per-Sprint special-Item unique index, drops only Item `sprint_id`, and rebuilds
the schedule placement CHECK with the two supported modes. Snapshot reconciliation
compares preserved Ticket, Item, agent and occurrence columns in both directions and
compares each full schedule against its expected destination conversion. Foreign keys
are checked before completion.

The populated rehearsal preserves mutable `si_planning_*` identity/body, a loose
Outcome, mixed done/active child history, unknown Ticket JSON, agent conversation link,
an artifact byte sentinel, fixed/backlog schedule destinations, receipt rows, and Item
trigger definitions. The migration's temporary branch parent is `ticket_guidance`;
root must reparent it to the integrated single-proposal revision.

## Gates and integration limits

- Backend focused gate: **85 passed**. It covers the two-Sprint journey, stale carry
  rollback, broad classification authority and current-child supervisor boundary,
  read snapshots without writer-lock acquisition, both creation placement paths,
  populated/full-chain migrations, fake fixture, CLI commitment/carry flow, existing
  supervisor lifecycle, current Sprint, typed workspace views, schedules and Ticket
  engine. Full output: `data/durable-outcomes-proof/backend-focused.txt`.
- Ruff: all changed Python files passed. Mypy: all changed Python files passed
  (**22 source files**). Output: `data/durable-outcomes-proof/static.txt`.
- Read-only collection: **970 pytest cases, including 19 E2E** on this isolated branch.
  Output: `data/durable-outcomes-proof/collection.txt`. Root owns combined runtime and
  pytest inventory after serial integration.
- `git diff --check` passed. Protected conversation paths have no diff against the
  root contract commit. `read_item(...).item`, physical identity/kind/link columns,
  supervisor configuration, and lifecycle lock remain available.
- Existing Sprint workspace E2E fixture was adapted with root approval to ordinary
  Outcome creation, explicit commitment and direct Ticket Sprint placement. No E2E
  cases were added or executed here. Fake-environment fixture was similarly adapted.
- No `./verify` run and no `web/dist` changes retained. Root owns final combined
  verification, actual-data rehearsal, independent review and publication.

## Frontend status

O5 was delegated to a bounded Sol child in disjoint frontend files. Initial commit
`20e523c3` implements grouped tracking, shared picker, collapsed Backlog catalog,
independent placement controls and workspace metadata. It passed 334 Vitest cases,
Svelte checking and build, but root review found material interaction gaps.

Plan addendum 16 is implemented in `b7501dc4`: retry reuses the retained identity;
historical Sprint links select explicit tracking and retain that Sprint in document/back
links; Actions and Tickets disclosures stay compact; and the Backlog catalog actually
unmounts while closed. Named mocked-browser harnesses prove one creation POST across a
failed commitment and retry, initially unchecked exact carry selection, catalog query
mounting only on expansion, historical navigation, and collapsed rows. These harnesses
passed; Vitest passed **338 cases**; Svelte checking reported zero errors/warnings; the
production build passed. Generated assets were restored. The owning agent spot-checked
the retry guard, explicit tracking query and collapsed catalog mount against the addendum.
Root's independent combined review and final verification remain the acceptance gate.
