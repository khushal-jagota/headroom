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

The combined upgrade and preservation comparison passed after both feature migrations
were integrated. The check copied the legacy fixture to a fresh local database, ran the
ordinary `db.create_schema` migration route, and established:

- all 778 Ticket, 78 Outcome, seven Sprint, and six schedule identities survived;
- 4,069 non-null saved values remained exact in the flat value map or, where the new
  model cannot keep them current, in the historical record;
- all 642 nonempty guidance sections remained exact;
- all 39 old proposals were accounted for: 36 current-gate proposals retained their
  exact field, body, author, and timestamp, while three off-stage proposals were
  preserved in history with an explicit unapproved label;
- the 75 expected Sprint commitments exactly matched the old non-null Item-to-Sprint
  placements, while Outcome identity and every other exported Item column stayed exact;
- Sprint identity, dates, primary bet, timestamps, and the source text consolidated
  into each new document were preserved;
- schedules matched the explicit migration mapping, and exported agent rows stayed
  byte-for-byte equal;
- `PRAGMA foreign_key_check` returned no rows, `PRAGMA integrity_check` returned `ok`,
  and a second `db.create_schema` call left the complete database dump unchanged.

The machine-readable result is
`data/simplification-deeper/combined-rehearsal-result.json`; the private fixture,
upgraded copy, and checking script remain gitignored. This is a passed rehearsal of
the API-visible projection only. It is not a raw production-backup rehearsal and does
not cover the omitted unknown field keys, status bookkeeping, occurrence receipts, or
supervisor conversation links. The populated synthetic migration tests remain the
evidence for those raw database states, relationships, and rollback behavior.
