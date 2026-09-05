# Independent combined implementation review

Reviewed the combined change from `d55260b3` through `24569369`, including the root
integration repairs and Outcome interface follow-through. Read the standing principles,
deep program, plans 09/10, design review 11, implementation reports 12/13, claim repair
14 and UI addendum 16. This is the one focused implementation review; no full verify,
live writes, provider calls or broad repeated test runs were performed.

## Findings

Three P2 findings were raised promptly and accepted by the root. All three are
resolved by the targeted six-file repair reviewed below. File references in this
section identify the original pre-repair locations.

1. **Dropped Outcome children are presented as work still to do.**
   `web/src/routes/SprintRoute.svelte:278` renders `outcomeGroup.tickets` directly.
   `web/src/lib/sprintPresentation.ts:88` has no dropped-stage condition and returns
   “to do” for a dropped Ticket resting at empty. The Outcome progress excludes that
   Ticket while its disclosure now labels it as active work. Unclassified dropped
   Tickets follow the old helper and disappear, so the visibility policy is also
   inconsistent. Use explicit truthful dropped presentation or the agreed consistent
   terminal visibility policy; prove the Outcome disclosure with a dropped child.

2. **Changing only Sprint can silently remove Outcome context when the catalog is
   unavailable.** `web/src/routes/TicketRoute.svelte:278–284` looks up the current
   Outcome in `sprintItems.data` for every placement change and writes null when that
   lookup is unavailable. The route can render the Ticket while `/api/items` is still
   loading or has failed. Changing the newly added Sprint selector then sends a
   three-member PATCH including `sprint_item_id: null`, detaching context without an
   Outcome decision. A Sprint-only edit must preserve the current ID without requiring
   an unrelated catalog read. Project and Outcome edits can make their own explicit
   classification decisions. Prove a Sprint edit with delayed/failed Items data.

3. **Schedule Project edits retain an incompatible hidden Outcome.**
   `web/src/routes/ScheduledTasksRoute.svelte:98–100` changes only the Project, while
   its new Outcome picker filters away the old Outcome at line 303. The draft still
   carries that hidden ID. Moving a schedule from Project A/Outcome A to Project B
   therefore appears to have no selected Outcome but Save sends both A's Outcome and
   B's Project, and the canonical backend correctly rejects it. Clear incompatible
   context as part of the explicit Project edit or present the conflict visibly; prove
   the resulting payload in the existing frontend harness.

## Load-bearing review

No additional actionable backend finding was identified in these reviewed paths:

- Decision is the complete six-member prospective state. The persistence door validates
  declared strings and current-gate proposal identity and persists explicit null clear
  semantics. Worker submissions infer the gate inside the write transaction; scope,
  ownership, one-step acceptance, direct saved edits and passed-field rules remain.
  Arbitrary-field proposal and arbitrary Stage routes/writers are removed.
- Drop appends explicitly unapproved content before clearing the proposal, with Stage,
  status and blocker effects inside the transaction. Archive text has read/search/copy
  consumers and no public writer or approval mechanism.
- Revision handoff snapshots the complete pending proposal, prepares actual worker
  context for the real send, acknowledges only successful delivery, then compares the
  proposal before clearing. Existing refusal/delivery/supersession supervisor cases
  exercise the real adapter prompt boundary and exact-child admission.
- The raw-field migration freezes the source Worker/gate definitions, validates every
  raw Ticket before DDL, preserves current saved value plus draft, archives off-stage
  and terminal drafts, retains recursively equal unknown metadata with safe fences,
  and rejects unknown workflows/corrupt shapes rather than guessing. Its synthetic
  proof covers late-write schema/row/revision rollback and repeated startup.
- Outcome migration preserves physical IDs, links and surviving row columns, backfills
  exact commitments, converts legacy Item schedules to fixed Sprint or explicit
  backlog, and checks records in both directions plus foreign keys. The populated
  proof compares supervisor triggers, agent conversation links, receipts, Ticket
  values and archival metadata, and an artifact sentinel.
- Carry validates the entire exact selected list under the writer transaction before
  adding commitment or changing placement. It rejects duplicates, terminal/reclassified
  or differently scheduled selections, supports empty selection and already-target
  repeats, preserves source commitments and uses canonical Ticket placement writes.
  Tracking partitions directly scheduled Tickets plus zero-child commitments in one
  read snapshot without acquiring a writer lock.
- Classification and unclassification preserve Sprint; scheduling preserves Outcome in
  the backend; creation validates final Project/Outcome/Sprint/blocker coherence.
  Commitment/carry authority remains planning-sprint and classification retains the
  approved broad Ticket-backed Worker boundary. Optional Outcome context is independent
  of current/fixed/backlog schedule destination.
- Claim release uses the existing monotonic status revision, preventing the specified
  same-second release/reclaim ABA without adding stored state.

The requested protected-path diff is empty against `d55260b3`: conversation,
conversation_start, message_delivery, worker_context, agent_backends, and frontend
conversation component/library directories remain byte-identical. Retained
`SprintItem`, `read_item(...).item`, physical identity/kind/agent links and the supervisor
lifecycle lock continue to satisfy the protected callers.

UI addendum 16 is materially implemented: failed commitment retains and retries the
created ID, historical links select explicit Sprint tracking and preserve it through
workspace/document back links, actions and children start collapsed, and Backlog only
mounts its catalog while expanded. Existing mocked-browser proofs exercise those
interactions. The findings above concern further concrete gaps in the final combined UI.

## Targeted repair recheck

The author repaired exactly the three routes and their three existing mocked-browser
harnesses; these unstaged changes above `24569369` were inspected as part of this same
review. Each finding is resolved:

1. The root chose to preserve existing dropped-Ticket hiding in Sprint planning UI.
   Outcome disclosure rows and counts now filter dropped Tickets, matching unclassified
   work and progress. Backend tracking retains its complete partition. The Sprint
   browser fixture now includes a dropped child and asserts two visible child rows,
   a two-Ticket count and absence of the dropped row.
2. Ticket placement now PATCHes only requested keys. Sprint changes therefore have
   no dependency on the Outcome catalog and cannot detach context. An explicit Project
   change clears incompatible Outcome membership using the known Ticket Project;
   a same-Project selection does not clear it. The Ticket harness holds Items pending,
   changes Sprint, returns an Items failure, then unschedules; both payloads contain
   only `sprint_id`, and Outcome/Project remain unchanged.
3. Schedule Project changes clear the previous Outcome only when the Project changes,
   using the known draft coherence instead of relying on a catalog lookup. Its harness
   proves same-Project selection retains context, changed Project visibly clears it,
   and the submitted payload contains the new Project and null Outcome.

Author-observed focused checks (tool output reported to this reviewer; no separate
output file was saved):

- `node web/tests/ticket-guidance-browser.test.mjs`: exit 0, silent success.
- `node web/tests/scheduled-tasks-browser.test.mjs`: exit 0, all assertions passed.
- `node web/tests/sprint-documents-browser.test.mjs`: exit 0, Sprint document editing
  and summary readback passed.
- `npm --prefix web run check`: exit 0, Svelte reported zero errors and warnings.
- `git diff --check`: exit 0.

The reviewer inspected both repair logic and assertions; the focused checks were not
redundantly rerun. No protected implementation changed in these repairs.

## Verdict and remaining gate

**Approved: no unresolved implementation review findings.** The three P2 findings
above are addressed in writing and in the targeted repair/proof. This is one focused
review with a concrete finding recheck, not a second general review.

The root owns final comparable test inventory and the one settled-tree `./verify`;
neither is asserted by this review. The retained synthetic migration, transactional
Outcome journey, proposal engine/admission and handoff tests are meaningful behavioral
evidence, rather than assertions of discarded EventSpecs.

### Workspace wording recheck

Inspected the subsequent one-line `SprintItemWorkspace.svelte:223` change from
“Remaining Tickets” to “Other Tickets.” That disclosure contains all children outside
Today, including completed history; the new label describes its existing contents
without implying unfinished work. No selection, grouping or conversation logic changed.
The approval remains unchanged: no unresolved findings. No additional test was needed
for this text-only correction; final verification remains root-owned.
