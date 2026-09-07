# Independent deeper-design review

Reviewed the repaired `09-single-proposal-plan.md` and
`10-durable-outcomes-plan.md` against `deeper-program.md`, `AGENTS.md`,
`PRINCIPLES.md`, `DESIGN.md`, and the current implementation at first-pass base
`d55260b3`. This was a read-only code/design review. Only this review document was
written; no code, contracts, tests, verification, services, or live state were changed.

## Verdict

Approved for root-owned contract application and scoped implementation. No unresolved
design violations remain in the repaired plans. This approves the proposed contracts,
not an implementation or deployment. The named focused gates, independent implementation
review, protected-boundary comparison, and final settled-tree verification remain due.

The two changes remove concrete responsibilities. One pending proposal removes
arbitrary-field pending work and nongating approval; a flat value map removes the slot
wrapper. Explicit commitments remove Item-wide scheduling and its cascades while still
representing a chosen Outcome before any Tickets exist. The retained archive document
and commitment table each have a reader and a specific responsibility; neither recreates
the removed workflow under another name.

## Findings and their resolution

### 1. Empty Kickoff proposals are valid stored state — resolved

The original proposal invariant required a nonempty body. Current
`tickets.data._seed_kickoff` deliberately parks `kickoff_note=''` on ordinary creation,
and the old codec accepts that string. Enforcing the original invariant in migration or
the canonical write door would reject valid Tickets and change creation behavior.

Plan 09 now distinguishes stored strings from submission validation. Its invariant and
migration preserve empty seeded Kickoff proposals; worker submission and pending edits
retain their existing nonempty checks. The migration gate explicitly covers the case.

### 2. Placement authority was described incorrectly — resolved

The original Outcome plan described existing "own-Ticket placement access." Current
`tickets.api._TICKET_DIRECT_ONLY_FIELDS` instead denies agent PATCH placement edits.
Existing Item classification routes use `core.authctx.require_ticket_worker_write`,
which admits direct callers or a valid Ticket-backed Worker without checking that the
claimed Ticket is the classification target. Reproducing the original wording could
either widen scheduling authority or silently remove initiative-planning capabilities.

Plan 10 now states the exact existing boundaries: classification retains that guard;
ordinary PATCH placement stays direct-only; commitment and carry writes use the existing
planning-sprint/direct authority. It does not invent an own-target restriction or widen
supervisor authority.

### 3. Project detachment is an explicit UI edit, not a backend inference — resolved

`TicketRoute.patchPlacement` currently supplies a cleared Item in its submitted patch
when the selected placement is incompatible. `tickets.data.edit_ticket` validates the
final Project/Item pair and rejects a mismatch; it does not infer a detach. The initial
plan's "clear-if-incoherent" wording did not identify this boundary.

Plan 10 now preserves backend rejection and explicitly describes the UI's Project-plus-
Item-clear request. Sprint changes preserve Item membership. Classification aligns
Project while preserving Sprint, and unclassification preserves both Project and Sprint.

### 4. Partial Decision replacement carried unnecessary invalid combinations — resolved

Root selected a complete six-member Decision instead of a replacement boolean paired
with a nullable proposal patch. The repaired plan removes clear/untouched ambiguity and
the representable contradictory boolean/value pair. All runtime persistence callers
derive the decision from a Ticket loaded in their write transaction; revision preflight
is read-only and its decision is recomputed after delivery. Carrying only resolution-
owned fields avoids overwriting unrelated status, configuration, or metadata.

The external-work operation can return and apply one complete Decision. The former
two-part interface exists to separate value and position event construction, while its
events are discarded. The repaired plan preserves exact-prefix validation, final-stage
blocker checks, captured entry ownership, and the transaction containing recap.

## Load-bearing constraints checked

- `EventKind` has no hidden production history consumer. Its live Ticket consumers are
  the two `proposal_filed` scans in `tickets.data`; `_apply_decision` never persists
  EventSpecs. Resolution and external-work code manufacture the other discarded
  payloads. Protected `ConversationEventKind` and conversation history are separate and
  remain unchanged.
- A saved current-field value and a current pending proposal can coexist after an old
  rewind. Plan 09 preserves both. It reads raw stored JSON, since the declared-field
  codec and API projection omit unknown historical field keys. Off-stage/terminal
  proposals stay explicitly unapproved; unknown stored values and metadata survive in
  the read-only archive. The migration does not pass off unapproved work as Guidance or
  approved values, and does not claim filesystem/SQLite atomicity.
- The original arbitrary-field proposal writer can create `awaiting_approval` without
  a current-gate proposal. Plan 09 deliberately rejects that inconsistent input during
  all-row prevalidation, naming affected Tickets before writes, rather than inventing a
  control transition. The supplied live aggregate reports no such rows, but it is not a
  substitute for raw-database rehearsal. Unknown Worker/stage pairs also fail before
  writes. These are explicit migration limits, not a claim that every historical
  database can be upgraded without a repair decision.
- Revision guidance must reach the conversation before pending state is cleared.
  `tickets.actions.return_ticket_for_revision` snapshots the proposal, prepares actual
  model text, sends it, handles refusal, acknowledges delivered receipts, then asks the
  writer to compare the proposal before clearing. Plan 09 preserves this sequence and
  the current-child supervisor check. Proposal presence does not replace ownership or
  Ticket status; a human reply can leave a proposal present at `paired`.
- Ordinary Planning Items are mutable normal Items, with potential briefs, artifacts,
  supervisor conversations, and Ticket history. Plan 10 retains every existing identity
  and commitment while removing future automatic creation. Deleting `si_planning_*`
  rows would not have been a safe simplification.
- Frozen consumers use the real `SprintItem` class, `read_item(...).item`, the existing
  supervisor lifecycle lock, and the physical Item identity/kind/agent relationship.
  None of those protected consumers reads `SprintItem.sprint_id`. The proposed contract
  preserves those actual dependencies, child membership, workspace addresses, and file
  paths without a replacement conversation layer.
- Carry-forward must operate on an explicit selection in one transaction. Plan 10
  preserves completed history, validates each selected child's membership, Sprint and
  terminality before writes, retains both commitments, and permits commitment-only
  carry. Tracking uses the union of commitments and directly scheduled Ticket context;
  it shows empty commitments and uncommitted work without losing or duplicating Tickets.
- Legacy `sprint_item` schedules inherit a real effective destination. Plan 10 migrates
  that destination to an explicit fixed Sprint or backlog, keeping optional Outcome
  context, receipts and Project. It does not silently make fixed schedules roll with the
  current Sprint. Occurrence-time placement and planning-date math remain unchanged.
- Removing Item aggregate status does not remove direct Item blocking links or Ticket
  blocker behavior. Retained Project coherence still serves repository context and
  existing readiness checks. The new progress copy says exactly what Ticket completion
  counts mean, without asserting an Outcome is achieved.

## Implementation review focus

Review the final combined diff once the scoped work is settled. In particular, trace
all proposal/stage writers after the old routes are removed, enforce the flat-value and
one-current-proposal invariants at persistence, and check lossless conversion and rollback
through the real migration entry point. For Outcomes, trace both API and CLI placement
paths, compound carry validation, the tracking partition, schedule destination
conversion, and unchanged supervisor identities/triggers.

The plans name focused backend/frontend proofs and avoid a new live-server E2E matrix.
Keep the existing narrow Ticket/Review persistence E2E only for its named cross-screen
boundary. Respect the program test-count ceiling without removing surviving behavioral
proof. No tests were run or results claimed by this design review.
