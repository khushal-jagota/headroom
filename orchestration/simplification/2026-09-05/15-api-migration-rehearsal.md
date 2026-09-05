# Deeper migration rehearsal

Prepared a private local SQLite fixture at `data/simplification-deeper/api-rehearsal-legacy.db`
from read-only live API exports: 9 Projects, 7 Sprints, 78 Items, 778 Tickets, 6 schedules.
The schema was created through the normal historical Alembic entry at
`planning_day_direction`, before the first-pass document/guidance migrations. The
fixture contains the exported declared field slots, proposal author/timestamps, all
visible text, direct Ticket placement, Item placement/identity/configuration, and schedule
configuration. Foreign-key check is empty and integrity check is `ok` before upgrading.

This is explicitly not a raw production backup. The API omits unknown raw field keys,
status revision/timestamp columns, occurrence receipts, and supervisor conversation
links. The fixture uses schema defaults for omitted status bookkeeping and null agent
conversation pointers, while retaining exported Ticket conversation pointers. It does
not claim to rehearse those absent facts; meaningful synthetic migration tests must
prove unknown JSON metadata, agent/conversation references, receipts and rollback.
No live rows were written. Private exports/fixtures remain gitignored and will be removed
with the task's local worktree at closeout.

The upgrade and preservation comparison are pending until both feature migrations are
integrated. Do not describe this preparation as a passed migration test.
