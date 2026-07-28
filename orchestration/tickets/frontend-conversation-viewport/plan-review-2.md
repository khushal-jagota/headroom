# Viewport plan correction review

## Final verdicts

- **Standards: READY.**
- **Spec: READY.**

The four findings in `plan-review.md` are resolved. No concrete violation remains.

## Finding 1 — RESOLVED: viewport configuration is isolated

The ticket now requires one private `viewportConfiguration.ts`
(`ticket.md:49-50,128-129`). The plan puts all five existing pixel, timing, tolerance,
and count values there and names the two consumers
(`implementation-plan.md:105-117`). The planned tree, size audit, dependency audit, and
scope allowlist all include the configuration module
(`implementation-plan.md:8-13,373-385,401-420,435-450`).

This satisfies the configuration rule without changing the locked component interface.

## Finding 2 — RESOLVED: geometry has an exact read-only interface

The plan now enumerates the complete private `HeldView`, `ThreadReading`, and
`ThreadGeometry` interface plus its sole factory
(`implementation-plan.md:53-95`). Each operation is explicitly a live DOM reading or a
calculation from a supplied cached value. The geometry reader returns measurements and
target positions; it does not mutate `scrollTop`, Svelte state, or policy
(`implementation-plan.md:97-103`).

The interface hides selectors, `display: contents` traversal, coordinate conversion,
rectangle arithmetic, answer-room arithmetic, and survivor lookup. Follow, settle,
release, request invalidation, and every mutation remain local to
`ConversationViewport.svelte` (`implementation-plan.md:119-130`). The geometry seam is
therefore a coherent private implementation concern rather than a catch-all policy
interface.

## Finding 3 — RESOLVED: cached scrolling and effect order are pinned

The scroll handler must call
`newestLineIsInSight(newestLineBottomPixels)` with its cached measurement, and that
operation is forbidden from traversing content or calling `getBoundingClientRect`
(`implementation-plan.md:73-76,97-103`). Full-thread remeasurement is confined to
`read(reservedSpacePixels)`, which returns the next reserved-space value rather than
writing component state.

This preserves the current deliberate no-remeasure scroll path in
`ConversationPane.svelte:390-419`. The target source order is now explicit—rows/outgoing
pre-effect, size effect, conversation-state pre-effect, fold-click handler—and the plan
forbids an additional awaited frame or any DOM reading retained across a `tick`
(`implementation-plan.md:181-189,478-485`).

## Finding 4 — RESOLVED: characterization states current behavior and uses relevant gates

The ticket now distinguishes the behaviors the current source actually provides:
row/fold/layer changes restore a held line, while resize/media-load shape changes
remeasure and follow only when the reader was already following; otherwise they apply no
deliberate scroll correction (`ticket.md:81-95`). That matches the current
`theThreadIsADifferentShapeNow` path, which calls
`settleTheViewAfterAChangeOfShape(thread, null)`
(`ConversationPane.svelte:618-670`).

The implementation plan no longer claims that image rendering proves same-line
restoration. It describes the image coverage as rendering/loading and states the generic
shape behavior separately (`implementation-plan.md:293-311`). The unrelated
`test_live_update.py` gate has been removed. Baseline and final browser gates now use
only `test_dev_conversation_pane.py` and `test_conversation_three_states.py`, with the
production bundle rebuilt before the server-backed run
(`implementation-plan.md:264-291,454-471`; `ticket.md:138-154`).

No production code or tests were edited or run during this correction review.
