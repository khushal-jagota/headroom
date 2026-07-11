# Ticket t_5m7fmdk3 — implementation brief

## Accepted success

Tickets have an explicit, user-approved Kickoff before worker-produced stages begin. New ordinary Tickets hold a proposed title and kickoff note in `needs_kickoff`; the user can edit and approve them, and no worker session or automatic step starts until approval advances the Ticket to `needs_success` and normal readiness. The approved kickoff note becomes the Ticket’s canonical top-level premise/intake note, replacing the current `user_note` concept rather than duplicating it. Kickoff is represented honestly in UI/readiness but is not another worker-produced stage. Later direct user edits remain authoritative; external-work intake may create an already-settled kickoff.

## Accepted approach

Add a dedicated pre-worker `needs_kickoff` state with a compound proposal/resolution for title plus `kickoff_note`. Ordinary creation parks the proposal with `awaiting_approval`; one canonical resolver applies edits, settles Kickoff, advances to `needs_success`, and rings readiness. Keep the five worker-produced fields unchanged. Rename/migrate the top-level `user_note` concept to `kickoff_note`, preserving existing values as settled intake context. Reuse the established proposal/approval interaction and do not add a sixth worker stage or parallel note field.

## Accepted plan and owner correction

1. Extend contracts and migrations with `needs_kickoff`, a title/note Kickoff proposal, and the `kickoff_note` rename/migration.
2. Every ordinary creation path atomically creates `needs_kickoff` with the proposal already present and `ticket_status=awaiting_approval`.
3. Reuse existing parked-proposal behavior. **Do not add special readiness or runner exclusion logic for Kickoff.** Approval uses the canonical resolution path, applies edits, advances to `needs_success`, and rings normal readiness.
4. External-work intake may create an already-settled Kickoff. Update API, CLI, seed/import, Ticket UI, events/invalidation, and docs while preserving the existing five-stage interactions.
5. Prove migration, creation, parked pending state, edited approval, normal Success readiness after approval, settled external intake, later authoritative edits, and UI behavior through focused and browser tests; independently review; run one clean `./verify`.

## Reviewed implementation constraints

- Store Kickoff as an explicit **ticket-level** `KickoffProposal` for `title` plus `kickoff_note`; do not add `FieldName.kickoff` or a sixth entry to the five-field JSON contract.
- In `needs_kickoff`, title/note edits must be part of the Kickoff approval payload and canonical resolver. Ordinary direct title/note edits are available only after Kickoff is settled.
- Extend the approval queue and Ticket page with a non-field Kickoff approval kind. Reuse the established edit/approve interaction without pretending Kickoff is a worker field.
- Rebuild the live `tickets` table constraints safely: allow `needs_kickoff`, rename/preserve `user_note` as `kickoff_note`, preserve every existing Ticket as already settled, and create no synthetic pending Kickoff proposal for existing rows.
- Give Kickoff explicit ticket-level event semantics and map them to existing Ticket/aggregate invalidations; do not emit field-proposal events with `field="kickoff"`.
- Guard or hide takeover/release while `needs_kickoff` so those controls cannot change `awaiting_approval` before approval. This is a control-integrity guard, not special readiness/runner exclusion logic.
- Existing parked-proposal behavior remains the sole reason ordinary Kickoff Tickets do not run. The focused test must prove the pending ticket-level proposal is visible to that generic predicate.

## Implementation boundary

- Work only on branch `ticket/t_5m7fmdk3-kickoff` in its dedicated worktree.
- Implementation ends with a verified commit. Do not merge or remove the worktree; Closeout owns integration.
- Preserve unrelated primary-worktree changes.
- Follow `PRINCIPLES.md`, current domain contracts, and canonical writer/event/doorbell patterns.
