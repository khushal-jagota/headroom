# Upstream provenance and stacked branch handoffs

Use this when a Worker or planning Ticket creates downstream Tickets that share decisions or must land in sequence.

## Preserve provenance

Every downstream Ticket should contain a direct Panels link to its upstream Exploration, Initiative Planning, or parent Ticket in a field user note, normally Kickoff. Say which accepted fields contain the shared decisions. Do not copy the whole upstream record into every Ticket, and do not rely on title search or sprint-item placement alone.

Read the Ticket back and verify the link. Adding a user note must not accept a proposal or advance the Ticket.

## Encode dependencies

When one Ticket cannot safely proceed before another, write the canonical blocker relationship through an ordinary planning surface. Prose ordering is useful context but is not a substitute for the relationship.

If a Worker actor is forbidden from writing the link, keep the omission visible and ask Chief or the user to reconcile it. Do not report the package as fully represented when its canonical dependencies are missing.

## Carry a stacked branch chain

When sequential Tickets must build on work not yet integrated into the eventual target branch:

1. Finish and verify the predecessor implementation branch.
2. Add a concise downstream user note naming that branch and, when useful, its verified commit.
3. Tell the downstream Ticket to branch from the predecessor branch rather than from `main`.
4. Name the Closeout integration target explicitly: merge back into the predecessor or combined branch, not `main`.
5. When the next Ticket becomes current, update its note with the newly combined branch rather than guessing a future name early.

Keep these notes to provenance, ordering, branch base, and integration target. Local implementation decisions remain with the Ticket Worker.
