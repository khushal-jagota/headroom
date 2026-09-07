# Independent integrated product review

Approved: no actionable defects or unresolved contract violations found in the
production simplification through `16812ffe4d1395684496270ac465c2a984ba43fe`, compared
with `b7ca8e047967e405feeebe058f5cd2ef82b2c2e5`.

Read PRINCIPLES, the program, plans 01/02/03/05, the first independent review, and
the root integration reviews. This review covers production code, contracts,
current documentation and skills, and the focused migration/frontend proof design.
The separate test-pruning review and final canonical verification remain outside
this review. No tests, browser services, live writes, or `./verify` were run here.
This artifact is the only file written by this reviewer.

## Preservation and migration

`sprint_documents.py:38` copies all eleven retired prose columns into the three
specified documents before dropping the old columns. Its inclusion condition
preserves whitespace-only text. It does not update the primary bet, timestamps,
identity, dates, or other tables, and does not rebuild the referenced Sprint table.

`ticket_guidance.py:16` preserves both historical note sources, including equal
texts, a legacy note hidden by a null current note, unknown field names, and other
slot metadata. It removes only the two note keys. `ticket_guidance.py:53` converts
and validates every Ticket before writing this migration's schema or records.
The existing `core/db.py:224` upgrade wraps both migrations in one transaction;
`core/db.py:258` begins it explicitly with the SQLite write lock. A later guidance
failure therefore also rolls back the preceding Sprint migration.

The populated migration proofs compare exact document text and preserved columns,
exercise linked records and foreign-key integrity, and test corrupt later content
without a partial cutover. They provide meaningful synthetic proof beyond the
reported rehearsal on visible live exports. That rehearsal is useful additional
evidence, not a claim to have inspected every row of a production backup.

## Guidance, authority, and conversation boundary

The proposal resolver diff removes only the retired note member from constructed
field slots. It retains proposal text and provenance, canonical value handling,
stage/scope decisions, and event behavior. Guidance is outside those slot writes.
`tickets/data.py:2104` and `:2123` retain the existing transaction and generic
Ticket-changed producer; append reads and writes under the same transaction.

`runtime/logic/worker_step_prompt.py:41` inserts current guidance into both automatic
step opener branches. The amended readiness-loop proof checks the exact document
in the actual backend-bound text and the acknowledged context receipts. This
proves delivery at the required boundary rather than inferring it from stored data.
The current docs and base Worker skill accurately distinguish an automatic opener
from ordinary chat and return-for-revision. No generic context service,
conversation-start implementation, conversation package, backend adapter, or
frontend conversation module appears in the protected-path diff.

CLI reads retain the manifest-first grammar, with recap and guidance as explicit
parts. The note command has one body-only write and no field selection. The Ticket
and Review frontend proof exercises the real editor's write mapping and the shared
document's Review read. Sprint proof similarly exercises all four editor mappings,
readback, and the useful primary-bet tracking consumer without a live backend.

## Backlog and removal boundaries

The two Backlog queries use the existing bounded summary APIs and keep independent
offsets. The existing Ticket endpoint excludes terminal stages by default and
filters canonical null Sprint placement. Its normal summary query does not select
guidance or field prose. Both lists use returned page facts and canonical Workspace
links. Empty current pages keep a usable Previous action.

The create form starts with null priority, which the canonical Ticket ingress
passes through to Project priority resolution; explicit priority remains an
override. Served Project and Worker-type choices supply the form, and explicit
null Sprint/Item placement prevents current-Sprint inference. No new backend
resource or authority was introduced.

`BoardRoute.svelte:52` now derives the Item pane from the address, and the direct
Item branch mounts the existing workspace by that id. The board-only rejection
effect is removed. The fixture opens an off-board brief through the canonical link
and checks that its pane/address survive the empty board response. Ticket selection,
file addressing, and conversation hosts retain their existing implementation.

Atlas removal is confined to its alternate rendering, route, dependencies, and
owned styles. Searches found no remaining live consumer of the retired Sprint
fields, field-note API, or Atlas route. Historical migration names and protected
conversation CSS comments remain intentionally historical; the pre-existing unused
`note_updated` event declaration is not a live write/read dependency. No new
compatibility mechanism is needed.

Repository-wide completeness remains contingent on the program's final settled-tree
`./verify`; this is independent source and proof review approval, not a substitute
for that gate.
