# Diagnostic history

Diagnostic history is the append-only event record that conversations and related diagnostics build up in the database. Headroom keeps all of it until Khushal explicitly decides to expire or compact it. The history is evidence, so nothing removes it on its own. Install, deploy, startup, restart, background loops, and shutdown never run expiry, and the nightly host cleanup does not touch it.

## What it is made of

- **A retention policy.** It picks which diagnostic events are old enough to remove. It never picks current work (including a Mac run that has not proved it stopped), control records, records it does not recognise, or half of a tool call. Every conversation linked to an unfinished Ticket is kept, however old, and so are Chief and unsettled Managers.
- **A reviewed report.** A measurement that names exactly which payloads would go and how much space each choice would recover. It is fixed to one cutoff and to the database contents it measured.
- **Two separate acts.** **Expiry** removes the selected event payloads. **Compaction** removes no rows and only gives free pages back to the file system.

## How it works

A measurement runs against a private copy, so trying the policy never changes the real database. The report separates what compaction alone would recover from what expiry would remove. When disk space is not urgent, the recommendation is compaction without expiry.

Reports accept only explicitly supported database schemas. A newer shipped schema can remain unsupported until its retention review.

Applying a report needs separate owner approval, with Headroom stopped and no Worker identity in the environment. It refuses a report that no longer matches the database, since any change makes a report stale, and it refuses while an agent backend is still using the database. Compaction is a later, separate step that chooses nothing new to delete. If it fails, an expiry already applied stays applied. The steps are in [ops/runbooks/diagnostic-history.md](../ops/runbooks/diagnostic-history.md).

## Decisions that shape it

- History is evidence. Removing it is an explicit owner decision, never a side effect.
- Decide from a fresh report of the exact database. An old report's numbers do not authorise anything.
- Reclaiming space and deleting payloads are different acts with different risks.
- Offline only, so nothing writes while history is removed.

## Code

- `src/planner/conversation/retention/`: the policy and the report.
- `src/planner/maintenance/`: measure, apply, and reclaim, under the hidden `panels maintenance diagnostic-history` commands.

_Last verified: 2026-11-04._
