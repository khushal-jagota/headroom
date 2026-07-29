# Frontend conversation viewport

## Outcome

Turn the viewport controller currently embedded in the 982-line
`web/src/components/conversation/ConversationPane.svelte` into one deep Svelte module.

The viewport module must own the scrolling thread and the policy that keeps a reader's
place while rows, folds, images, pane height, and optimistic messages change. The pane
must become the composition shell for identity, menu, layer-state controls, rest bar,
and composer.

This is a behaviour-preserving architecture ticket. It is the next ticket because the
pane is the remaining severe frontend size smell and the viewport is a real semantic
domain inside it, not because a smaller filename is itself an outcome.

## Why this seam

The pane currently combines two independently understandable things:

- composition of the conversation header, rest state, composer, and their inputs;
- a roughly 500-line viewport controller for DOM measurement, following, reader intent,
  sent-message anchoring, reserved answer room, and restoration after shape changes.

By the deletion test, removing the proposed module would force all of that viewport
policy and its thread DOM back into the pane. The module therefore has depth: a small
component interface hides substantial coordinated behavior. Every conversation display
gets leverage from it, and viewport defects become local to the module named for them.

This is an in-process Svelte component seam. No port or adapter is justified.

## Contract files

- New public component interface:
  `web/src/components/conversation/viewport/ConversationViewport.svelte`.
- Existing input contracts:
  - `ConversationState` from `web/src/lib/conversation/conversationState.ts`;
  - `TranscriptRow` from `web/src/lib/conversation/transcript.ts`;
  - `OutgoingMessage` from `web/src/lib/conversation/outgoing.ts`;
  - `BackendModel` from `web/src/lib/conversation/wire.ts`.

The exact locked prop interface is in `contract-lock.md`.

The folder may contain one private geometry submodule if the implementation plan proves
that its DOM measurement operations form a coherent internal concern and the direct
component would otherwise remain above 600 lines. It may not add a public barrel or split
helpers merely to meet a line count.

The viewport's tunable pixel, timing, and count values live in one private
`viewportConfiguration.ts` module, as required by `PRINCIPLES.md`.

## Required work

1. Add the non-trivial `conversation/viewport/` module folder and its
   `ConversationViewport.svelte` interface.
2. Move the complete thread viewport into it:
   - the thread shell and scrolling region;
   - transcript and optimistic outgoing-message rendering;
   - the reserved answer-room element;
   - the jump-to-latest control;
   - reader-intent and following state;
   - thread geometry and content measurement;
   - sent-message anchoring and answer-room release;
   - held-view capture and restoration;
   - reactions to rows, outgoing messages, fold clicks, resize, loaded media, and
     rest/peek/open transitions;
   - viewport-owned constants, event handlers, and styles.
3. Keep `ConversationPane.svelte` responsible for:
   - its external application-facing props;
   - the pane/header identity and connection state;
   - conversation options and new-conversation confirmation;
   - rest/peek/open controls and pointer/Escape transitions;
   - rest-line derivation;
   - composer composition and callbacks;
   - the top-level rule that hides header and viewport at rest.
4. Render one `ConversationViewport` from the pane and pass only the locked facts. Do not
   expose DOM elements, reactive setters, geometry helpers, or scroll callbacks through
   the component interface.
5. Preserve the exact DOM selectors, accessibility names, classes, keyboard/pointer
   behavior, outgoing-message order, and styling consumed by current browser tests.
6. Keep all viewport behavior exact:
   - opening begins at the newest line;
   - following moves only forward by the least necessary amount;
   - wheel, touch, scrollbar, and keyboard input hand control to the reader;
   - the Latest control appears whenever the newest line is outside the viewport and can
     move both forward and backward;
   - sending settles the message near the top and reserves room for its answer;
   - answer room shrinks without moving the reader;
   - row changes, fold clicks, and layer-state changes preserve the line being read;
   - resize and media-load shape changes remeasure and keep the newest line visible only
     for a reader who was already following; a reader who is not following receives no
     deliberate scroll correction;
   - a vanished held view falls back to the newest line;
   - optimistic messages disappear when their canonical rows replace them;
   - only a held outgoing message with a discard callback offers discard.
7. Rebuild and retain the checked-in `web/dist` bundle.
8. Update `web/tests/conversation-pane.test.mjs` so its component-source checks compile
   and inspect the nested `viewport/ConversationViewport.svelte` module as well as the
   existing top-level inventory. Preserve every behavior assertion and the exact
   top-level inventory; this is a harness correction for the new semantic folder, not a
   source-shape assertion or a weakened class-reuse check.

## Dependency direction

```text
ConversationPane ──runtime──▶ viewport/ConversationViewport

ConversationViewport ──runtime──▶ ConversationTranscript
ConversationViewport ──runtime──▶ MessagePieces
ConversationViewport ──runtime──▶ outgoingMessageNote
ConversationViewport ──type─────▶ existing conversation contracts
```

The viewport must not import the composer, rest bar, pane, route code, HTTP clients, or
live-conversation orchestration. The pane must not reach back into viewport state.

## Testing method

This refactor changes no product behavior, so existing browser behavior is the
characterization contract. Record a focused baseline before moving code, extract through
the locked component seam, then run the same gates on the new structure.

Do not invent a source-shape assertion and do not claim visual inspection as proof. The
existing Playwright tests exercise the actual DOM geometry and reader behavior.

## Acceptance

- `ConversationPane.svelte` is 300–450 lines and reads as the conversation composition
  shell.
- `ConversationViewport.svelte` is at most 600 lines.
- Any private geometry submodule is cohesive, used only inside the viewport folder, and
  exists for semantic locality rather than arithmetic line-count compliance.
- Every viewport size, timing, tolerance, and count intended for tuning lives in the
  private viewport configuration module rather than inline.
- No new production file exceeds 600 lines.
- The viewport component has exactly the locked prop interface and emits no custom event.
- Existing browser selectors and behavior remain exact.
- The pane harness compiles the nested viewport and continues to prove that shared
  conversation classes, token rules, and rendered behavior are preserved.
- `ConversationPane.svelte` contains no scroll position, geometry, reserved-room,
  held-view, following, or thread-element state after the extraction.
- No backend, HTTP, wire, feed, transcript, thread-layout, composer, route, or product
  behavior changes.

## Focused gates

Run after the ticket is complete:

```sh
node web/tests/conversation-pane.test.mjs
npm --prefix web run check
npm --prefix web run build
npm --prefix web test
.venv/bin/pytest -q \
  tests/e2e/test_dev_conversation_pane.py \
  tests/e2e/test_conversation_three_states.py
git diff --check
```

The frontend architecture program reserves one canonical repository-wide `./verify` for
its final settled tree; this ticket does not repeat it.

## Outside this ticket

- Composer decomposition.
- Transcript turn-presentation extraction.
- Route decomposition.
- General CSS restructuring or Tailwind.
- Any viewport behavior redesign.
