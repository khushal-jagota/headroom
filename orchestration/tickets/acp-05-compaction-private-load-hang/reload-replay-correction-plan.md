# ACP-05 compacted replay correction plan

## Outcome

Make every controlled load publish a typed transcript in which a structurally valid Hermes summary
update is replaced at that update's replay position by the completed Panels compaction boundary. The
raw Hermes summary is never sent to the browser as an ACP user or assistant message.

The same result must be produced during the successful live N -> N+1 transition, a hard refresh, a
requested-cancel replacement, child recovery, and server restart. The browser remains backend-neutral
and receives only the existing `context_compaction` envelope; no wire or Svelte change is required.

The current implementation violates `orchestration/acp-migration/plan.md` lines 164-166 in two ways:
`ConversationHub.commit_compaction_transition` publishes the private load replay verbatim, and the
broker later publishes a second normalized compaction event. Ordinary attach and requested-cancel
recovery also publish their load replay verbatim, so a reload retains the raw summary but cannot
reconstruct the typed boundary.

## Required contract correction

The replay summary does not reliably contain the `/compact` prompt, and therefore cannot recover an
`explicit` versus `automatic` trigger. The boundary ID is also broker-owned today. Do not infer either
value from Hermes text, the ACP session ID, browser state, or token metadata.

Persist the ordered broker-owned boundary provenance on the successor binding in the same SQLite
transaction as the existing binding/product-mirror compare-and-swap. The stored value contains only
`boundary_id` and `trigger`; the summary remains Hermes-owned and is reconstructed from each typed load.
An initial binding or a New conversation binding stores an empty boundary tuple. There is no legacy
guess: a replay containing a Hermes summary without matching durable provenance fails closed instead
of inventing a trigger.

One capture can currently settle more than one already-visible broker boundary (for example an explicit
boundary and a distinct automatic observation). Preserve their broker order in storage. At the one raw
summary replay position, emit one completed `context_compaction` envelope for each persisted boundary,
all with the one normalized summary. The ordinary case is exactly one replacement envelope. This also
keeps repeated compactions distinct because their existing broker-generated IDs are persisted rather
than regenerated during replay.

Add these internal contracts:

- In `src/planner/conversation/contracts.py`, add immutable
  `ConversationCompactionBoundaryProvenance(boundary_id, trigger)`. It deliberately excludes summary
  and state.
- In `src/planner/conversation/backend_contracts.py`, add a strategy-owned pure replay classifier:
  `classify_replay(binding, replay, compacted_boundaries) -> tuple[ConversationReplayItem, ...]`, where
  `ConversationReplayItem` is a `SessionNotification`, `ProtocolUpdateRejectedPayload`, or completed
  `ContextCompaction`. The classifier must preserve every non-summary item and order, and replace only
  the unique summary-bearing item.
- Keep every child, registry, runtime-port, requested-cancel, and compaction-transition replay value
  raw as `SessionNotification | ProtocolUpdateRejectedPayload`. Carry the ordered boundary provenance
  through `PreparedCompactionCapture`, but do not classify or widen replay in
  `src/planner/conversation/runtime_ports.py`. `ConversationReplayItem` exists only as the private return
  of the hub's one batch-classification helper immediately before browser envelope publication.
- Extend the binding repository seam with an exact-binding provenance read and a compaction CAS which
  accepts the ordered provenance tuple. The read must validate employee, session, backend, and binding
  generation together so metadata from a later winner cannot be paired with an earlier replay.

This mechanically expands the ACP-05 allowed files to include
`src/planner/conversation/contracts.py`, `src/planner/conversation/hermes_turn_strategy.py`,
`src/planner/conversation/sqlite_binding_repository.py`, `src/planner/core/db.py`, their focused tests,
and `tests/support/acp_in_memory_binding_repository.py`. Amend `contract.md` with that exact reason
before implementation; no browser, wire-schema, product-domain, or Hermes-checkout file is needed.

## Phase 1 — make the production defect red

Update `tests/e2e/test_acp_conversation.py::test_official_fork_compaction_survives_refresh_child_death_and_restart`.
Replace every assertion that requires `HERMES_SUMMARY_PREFIX` inside an `acp_session_update` with these
assertions for the live transition, hard refresh, child respawn, and server restart:

- no ACP update contains `HERMES_SUMMARY_PREFIX`;
- the replay contains the completed `context_compaction` with the exact live boundary ID, trigger, and
  normalized summary;
- it occupies the raw summary's order between the same surrounding replay fixtures;
- the successful live transition contains no second completed event with that boundary ID; and
- the restarted client can send a later prompt on the same N+1 binding.

Extend `tests/support/acp_scripted_agent.py` only as needed to put ordinary replay messages immediately
before and after the durable summary, so the exact replacement position is observable rather than
inferred from counts.

Add a second compaction in the e2e or focused integration proof and assert its persisted boundary ID is
different from the first. Reload N+1 while it is current and recover the first capture's ID; after the
second N+1 -> N+2 compaction, reload N+2 and recover only the second capture's boundary tuple. The first
boundary was compacted into the new summary and must not be emitted again. Do not compare newly generated
IDs across processes; compare each replayed current-binding ID with the ID first published live.

## Phase 2 — persist exact boundary provenance with the binding winner

In `src/planner/core/db.py`, add a non-null `compaction_boundaries_json` column to
`conversation_session_bindings`, defaulting to the canonical empty JSON array. This is a no-version-bump
amendment to the still-unlanded schema v24: `SCHEMA_VERSION` remains 24. Extend the canonical v24 DDL and
`_migrate_conversation_session_bindings` with an idempotent add/backfill path so an already-created
dogfood v24 database gains the column without rewriting binding identity or provenance already present.
The repository, not SQLite JSON functions, validates the decoded value as an ordered array of exact
objects with non-blank unique boundary IDs and `explicit | automatic` triggers. Serialize it in one
canonical compact form.

In `src/planner/conversation/sqlite_binding_repository.py`:

- ordinary initial/new-conversation CAS writes `[]`;
- the compaction CAS receives the exact expected persisted provenance read with N and the ordered
  boundaries settled by the current capture; its validation includes that expected provenance so
  binding identity and provenance cannot suffer a lost update;
- the winning N -> N+1 write replaces N's provenance with the current capture's tuple in the same
  `BEGIN IMMEDIATE` transaction that updates the product mirror and binding row. It does not append N's
  older boundaries: Hermes's one N+1 summary has compacted that earlier transcript, so replaying an old
  boundary with the new summary would be false;
- boundary IDs must be unique within the current capture tuple, including the combination of explicit
  and automatic observations settled by that capture;
- losing CAS returns the winner without overwriting its provenance;
- the exact-binding provenance read rejects a changed or malformed row; and
- no summary text is persisted.

In `src/planner/conversation/employee_registry.py`, pass the active broker boundary provenance through
`PreparedCompactionCapture` into the existing durable CAS. Resolve the persisted winner's provenance on
ambiguous CAS/external-winner adoption rather than borrowing the loser's pending values. All other
binding creation/replacement paths remain empty or preserve the exact current row as appropriate;
requested-cancel changes the child generation, not the binding or its provenance.

Red/green tests:

- `tests/unit/test_db.py`: a pre-column v24 schema and an already-amended dogfood v24 schema both reopen
  without a version bump; add/backfill is idempotent, defaults missing provenance to `[]`, and preserves
  every binding field and any valid existing provenance.
- `tests/unit/test_acp_binding_repository.py`: initial/new bindings are empty; compaction binding and its
  ordered current-capture provenance commit atomically against the expected N provenance; stale expected
  provenance loses; CAS loss retains only the durable winner's tuple; malformed JSON, duplicate IDs in
  the current tuple, invalid triggers, and exact-binding mismatch fail.
- `tests/unit/test_acp_employee_registry.py`: normal winner, ambiguous winner resolution, and external
  winner adoption carry only the durable winner's provenance; a failed/pre-CAS capture writes none.
- Update `tests/support/acp_in_memory_binding_repository.py` to model the same atomic tuple semantics for
  broker/registry tests, rather than giving tests a weaker repository.

## Phase 3 — classify replay once, in the backend strategy

In `src/planner/conversation/hermes_turn_strategy.py`, extract the existing candidate scan used by
`capture_compaction_from_updates` into one private parser returning the unique candidate's replay index
and normalized summary. Both capture validation and `classify_replay` call that parser; there must not be
a second prefix/marker implementation.

Classification rules are exact:

1. With no persisted boundaries and no candidate, return the replay unchanged.
2. With persisted boundaries, require exactly one candidate for the exact session and no
   `ProtocolUpdateRejectedPayload`; replace that item in place with completed `ContextCompaction`
   item(s), preserving the stored order and using the normalized summary.
3. A candidate without durable provenance, provenance without one candidate, multiple candidates,
   malformed merged markers, wrong-session candidates, or any rejected capture fails closed. It never
   publishes the raw candidate and never falls back to assistant text.

Other backend strategies implement their own classifier; the generic hub never checks `backend_key` and
never imports Hermes constants. Test doubles may use an identity classifier only when their fixture has
no compacted provenance. No registry or transition calls this API; doing so would classify the same load
again when it reaches the hub.

Red/green tests in `tests/unit/test_hermes_acp_turn_strategy.py` cover user and assistant placements,
merged and unmerged summaries, exact before/after item order, one and multiple persisted boundaries,
stable stored IDs/triggers, repeated distinct IDs, wrong-session text, missing/multiple/malformed
candidates, rejected replay, and summary-without-provenance. Retain the pinned prefix hash test.

## Phase 4 — use classified replay at every server-side load boundary

Add one private batch publication helper in `src/planner/conversation/hub.py`. It accepts raw replay,
obtains the exact binding's persisted provenance, calls that runtime handle's backend strategy classifier
exactly once for the complete batch, and immediately publishes the result through one ordered path. A
`ContextCompaction` item becomes the existing `ContextCompactionEnvelope`; ACP notifications and protocol
rejections keep their existing behavior. Raw replay never leaves the hub as a summary message, and a
classified tuple never crosses back into child, registry, runtime, or transition APIs. No browser parsing
or new envelope type is introduced.

Use that helper in all controlled replay paths:

- `_bind_stream` / `_establish_stream` for ordinary attach, refresh, child respawn, and server restart;
- `commit_requested_cancel_recovery_transition` for same-binding replacement;
- `commit_compaction_transition` for the winning N+1 private replay; and
- external-winner adoption, which reaches the same compaction commit with the winner's persisted
  provenance.

Keep `reset -> hub-classified replay (raw summary replaced by persisted typed boundaries at its exact
position) -> permitted quarantine -> ready -> human echoes -> queue` unchanged. There is no completed
compaction publication after the queue. Live ingress and quarantine remain typed ACP traffic and are not
retrospectively parsed as Hermes replay.

In `src/planner/conversation/turn_broker.py`, pass the ordered active boundary provenance into capture.
After a winning hub commit, do not call `publish_compaction` again for successful boundaries: the hub has
already published their completed forms at the summary position. Continue to publish `compacting` before
the prompt, and preserve current failed-boundary publication for capture/commit failure and CAS loss.
Settlement, activity, queue advancement, actor rekey, and transition completion order do not change.

Update `src/planner/conversation/composition.py` to wire the exact provenance read/compaction CAS. Export
the new internal types from `src/planner/conversation/__init__.py` only if existing internal consumers
require it.

Red/green tests:

- `tests/unit/test_conversation_hub.py`: the sole helper is called once per raw replay batch; compaction
  commit replaces the summary between its surrounding replay updates, publishes it once to two browsers,
  and preserves reset/replacement/quarantine/ready/echo/queue order with nothing appended after queue;
  ordinary refresh and requested-cancel recovery produce the same typed replacement and no raw summary.
- `tests/unit/test_conversation_turn_broker.py`: the exact active boundary tuple reaches CAS; successful
  live capture does not separately republish completed boundaries; failures still publish failed
  boundaries; two distinct observations sharing one capture preserve order and identity.
- `tests/unit/test_acp_conversation_composition.py`: production composition wires one repository-backed
  provenance source to every load path.
- Keep the existing `tests/unit/test_acp_employee_child.py` private-capture proofs unchanged: the SDK
  child still returns raw typed ACP replay, and classification happens above that transport seam.

## Phase 5 — focused verification and review

After implementation, run scoped Ruff, strict Mypy over the affected conversation/database modules,
the focused tests named above, and the existing ACP e2e. Do not weaken assertions to accept either raw
or typed output.

The independent review must check:

- SQLite migration and CAS atomicity, including loser/adoption behavior;
- exact-binding provenance reads across refresh and restart;
- one shared Hermes parser and fail-closed multiple/malformed handling;
- in-place replay ordering and absence of raw summary ACP envelopes;
- no duplicate successful live completion after hub commit;
- requested-cancel, child respawn, and ordinary attach parity; and
- stable IDs for replay of one compaction and distinct IDs for later compactions.

This correction does not change ACP transport ordering, lifecycle/publication locks, compaction timeout,
wire schemas, browser reducers, or Hermes behavior.

## One-review disposition

1. **Accepted — v24/v25 migration seam.** ACP-05 is explicitly a no-version-bump amendment to unlanded
   v24 with idempotent current-dogfood add/backfill. ACP-06 must also support direct pre-column -> v25 by
   creating/backfilling and preserving the column before its terminal transaction deletes binding rows.
2. **Refuted — append all historical provenance.** The CAS must compare the exact expected N provenance,
   but the N+1 value is only the ordered boundaries settled by the current capture. Appending N's older
   boundaries would emit them with N+1's replacement summary, which is semantically false and recreates
   duplicate compaction UI. Multiple boundaries within one capture remain ordered and unique; a later
   capture replaces that tuple, and a loser adopts only the durable winner's tuple.
3. **Accepted — classify once at publication.** Child, registry, runtime, and transition replay stays raw.
   The hub's one complete-batch helper is the sole classifier and invokes the strategy exactly once before
   publishing browser envelopes.
