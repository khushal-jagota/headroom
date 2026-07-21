# MR-01 implementation-plan review

Status: **READY**

Reviewed against `AGENTS.md`, `PRINCIPLES.md`, the frozen employee-configuration
contract and ticket, the research note, and the current Ticket, SQLite binding,
ACP registry, runtime, and Kickoff seams.

The revised plan now has the correct product boundary: defaults seed once; controls
exist only inside pristine Kickoff; there is no reset, header control, post-Kickoff
control, or current-state display; and the requested model/reasoning are applied only
to the first unbound session. The schema, atomic writer, catalog boundary, first-CAS
ordering, UI placement, and negative bound-load/replacement tests are otherwise
appropriately scoped.

## Finding

1. **The first-binding loser path does not explicitly discard stale launch inputs
   before publishing the durable winner.** `ConversationEmployee` equality is part of
   `AcpEmployeeRegistry._record_matches`, and the current `_adopt_winner` /
   `_adopt_compaction_winner_under_reservation` code derives the adopted employee with
   `model_copy(update={"backend_key": ...})`. After MR-01, that preserves the losing
   unbound read's requested model/reasoning even though a binding now exists. This
   contradicts the revised contract that bound employee resolution carries no launch
   inputs, and leaves the publication record inconsistent until a later resolution
   notices the mismatch and respawns it. Amend step 4 to clear both requested launch
   fields on every first-binding external-winner adoption (or re-resolve the bound
   employee) before load/publication, and add that assertion to the existing two-order
   writer-versus-first-binding race proof. No configuration should be reapplied to the
   winner's bound session.

No other concrete blocker found.

## Narrow correction disposition

**Resolved.** The revised plan now requires the first-binding loser to retire its
candidate, re-resolve the durable winner as a bound Employee, require null launch
inputs, and load/publish that winner without invoking the configuration adapter. The
two-order race proof explicitly checks both absence of stale loser launch inputs and
absence of a configuration request against the winning session. The plan and contract
also consistently use `employee_launch_model` and
`employee_launch_reasoning_effort`, which truthfully name the fields as historical
first-session launch inputs rather than current settings.
