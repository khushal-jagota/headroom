# Independent viewport implementation-plan review

## Verdicts

- **Standards: NOT READY.**
- **Spec: NOT READY.**

The locked `ConversationViewport` prop interface is not challenged. The blockers are in
the private implementation plan and its claimed proof.

## Standards findings

### 1. Blocking — viewport tunables are still inline instead of in configuration

`PRINCIPLES.md:8` requires sizes, formulas, timings, and limits intended for tuning to
live in an isolated configuration module. The plan instead puts four pixel/limit values
inside `threadGeometry.ts` and keeps `READER_DRIVING_MILLISECONDS` inside the Svelte
module (`implementation-plan.md:71-76,91-92`). These are exactly the categories the
standing rule names.

Required correction:

- Add one private viewport configuration module containing
  `NEAR_NEWEST_LINE_PIXELS`, `SENT_MESSAGE_TOP_GAP_PIXELS`,
  `NEWEST_LINE_BOTTOM_GAP_PIXELS`, `MOST_HELD_LINES`, and
  `READER_DRIVING_MILLISECONDS`.
- Update the planned tree, dependency audit, size audit, and allowed scope for that file.
  This does not change the locked component interface.

### 2. Blocking — the geometry module's interface is unspecified and does not establish
the claimed depth

The plan locks one factory but leaves its returned interface as a comment
(`implementation-plan.md:51-70`). The proposed return may expose shape checks,
measurements, visibility, scroll positions, DOM corrections, prompt lookup, answer-room
calculation, held-view capture, and restoration. That is not a concrete interface, and
it mixes readings with DOM mutations while describing the module as stateless
(`implementation-plan.md:34,68-70`). The single caller would still need to know which
operations require cached state, which mutate `scrollTop`, and which must occur before or
after a `tick`; the coordination has not been hidden.

This also violates the requirement that design decisions be concrete
(`PRINCIPLES.md:38`). The deletion test offered at `implementation-plan.md:45-49` only
moves the same helper cluster back into its sole caller; it does not by itself prove a
deep internal module.

Required correction:

- State the complete private interface and classify every operation as a live DOM
  reading, a calculation from an existing snapshot, or an intentional DOM mutation.
- Keep follow/settle/release decisions and all Svelte state writes in
  `ConversationViewport.svelte`; do not return a catch-all object whose caller must
  coordinate a dozen low-level methods.
- If no smaller interface hides that coordination, keep the geometry implementation in
  the viewport and use the permitted line budget honestly rather than extracting by
  proximity.

## Spec findings

### 1. Blocking — the proposed geometry factory cannot preserve the current no-remeasure
scroll path as specified

The factory accepts only the thread and reserved-space element
(`implementation-plan.md:60-65`), while the plan says it may answer newest-line
visibility and perform latest-line corrections (`implementation-plan.md:68-70`).
Current behavior deliberately computes visibility during `scroll` from the cached
`newestLineBottomPixels`; its comment explicitly says scrolling does not remeasure the
DOM (`ConversationPane.svelte:390-419`). The same operations also depend on
policy-owned `reservedSpacePixels` when releasing answer room
(`ConversationPane.svelte:368-399`).

With only the proposed factory inputs, an implementation must either:

- rewalk and remeasure content on every scroll, changing the current behavior and hot
  path; or
- add unplanned state parameters/setters to the returned interface, recreating the
  coordination the seam is supposed to hide.

Required correction:

- Specify that scroll visibility consumes the already-cached newest-line measurement and
  performs no `contentElements`/rectangle read.
- Specify exact value inputs and return values for answer-room calculations; geometry
  must not own or mutate Svelte policy state.
- Pin the current effect order in the target source: row/outgoing pre-effect before the
  state-transition pre-effect, with request invalidation, `tick`, and `untrack`
  placement unchanged. The geometry extraction must introduce no additional awaited
  frame or cross-`tick` cached DOM reading.

### 2. Blocking — the named characterization does not prove two required behaviors and
the current source suggests they are not behavior-preserving

The ticket requires resize and media-load changes to preserve the line being read
(`ticket.md:78-89`), and the plan claims the existing browser baseline covers loaded
media changing thread shape (`implementation-plan.md:247-257`). It does not:

- Neither `test_dev_conversation_pane.py` nor
  `test_conversation_three_states.py` changes browser width while a reader is away from
  the newest line.
- The image tests assert rendering, decoding, and reload survival; they do not assert a
  reader's line or position before and after an image above them loads.
- `test_live_update.py` mounts the board and ticket recap editor, not a conversation
  viewport, so it proves no viewport change-signal behavior.

The missing cases matter because the current generic shape-change handler calls
`settleTheViewAfterAChangeOfShape(thread, null)`
(`ConversationPane.svelte:618-670`). For a reader who is not following, that retains the
numeric `scrollTop`; it has no held line from which to restore the same reading position.
The plan therefore cannot simultaneously claim a behavior-preserving move and claim
that the untested resize/media-load line-preservation requirement already exists.

Required correction:

- Add baseline browser characterization for a non-following reader while content above
  them rewraps after a width change and while an image above them finishes loading.
- Record those tests against untouched source before implementation and run the same
  tests afterward.
- If either baseline fails, stop for an owner decision: this ticket may not silently add
  behavior while its fixed outcome says behavior-preserving.
- Retain `test_live_update.py` if the ticket requires it as an adjacent gate, but remove
  the false claim that it characterizes this viewport.

No production code or tests were edited or run during this plan review.
