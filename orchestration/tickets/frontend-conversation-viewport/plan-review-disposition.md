# Plan review disposition

## Standards 1: isolate tunables

Accepted. The planned module now includes a private `viewportConfiguration.ts` containing
all five pixel, timing, tolerance, and count values. The component and geometry reader
import their values from that one configuration module.

## Standards 2: specify and deepen geometry

Accepted. The plan now locks the complete private `ThreadGeometry` interface. Every
method is classified as a live DOM reading or a calculation from an explicitly supplied
cached value. The interface performs no DOM or Svelte mutation: it hides selectors,
laid-out traversal, coordinate conversion, and rectangle arithmetic, while every policy
decision and mutation stays in the component.

## Spec 1: preserve the cached scroll path

Accepted. `newestLineIsInSight(cachedNewestLineBottomPixels)` is explicitly forbidden
from traversing content or reading rectangles. The scroll handler supplies the
component's cached measurement. Reserved-room calculation receives the current value and
returns a new value; it cannot write policy state. Effect order and `tick`/`untrack`
placement are now pinned.

## Spec 2: correct false characterization

Accepted by correcting the ticket to the behavior that exists. Row changes, fold clicks,
and layer-state transitions preserve the held line. Generic resize and media-load shape
changes do not: when following, the viewport remeasures and keeps the newest line in
sight; when not following, it applies no deliberate scroll correction. The plan no
longer claims that the existing image tests prove a stronger same-line guarantee, and
the unrelated board/live-update test is removed from this ticket's viewport evidence.

No product behavior is added or removed.

## Implementation-gate harness correction

The first post-extraction pane harness failed because its source map reads only top-level
conversation components, so the existing `.chat-jump` class moved into the locked nested
component became invisible to the test. The ticket and plan now allow the harness to
compile and inspect `viewport/ConversationViewport.svelte` in addition to its unchanged
top-level inventory. This strengthens coverage of the new component and avoids a fake
production marker or flattened module.
