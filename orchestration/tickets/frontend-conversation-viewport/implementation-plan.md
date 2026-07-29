# Frontend conversation viewport implementation plan

## Locked outcome

Extract the scrolling thread and its complete viewport policy from the current
982-line `ConversationPane.svelte` into:

```text
web/src/components/conversation/viewport/
├── ConversationViewport.svelte
├── threadGeometry.ts
└── viewportConfiguration.ts
```

`ConversationViewport.svelte` presents exactly the prop interface in
`contract-lock.md`. It binds nothing back, dispatches nothing, and is rendered once by
`ConversationPane.svelte`.

This is a behavior-preserving move. The existing Playwright assertions are the
characterization contract. Do not add a source-shape test, redesign the scroll policy,
rename selectors, or change product wording.

Expected settled sizes:

- `ConversationPane.svelte`: approximately 330–420 lines;
- `ConversationViewport.svelte`: approximately 500–575 lines;
- `threadGeometry.ts`: approximately 130–190 lines;
- `viewportConfiguration.ts`: one small isolated tunables module;
- no new or changed production file above 600 lines.

## Why the private geometry module earns itself

Use one private `threadGeometry.ts`; do not put the entire move into one oversized
Svelte file and do not split any further.

The current pane has one cohesive set of stateless DOM geometry operations:

- translating an element's browser rectangle into the thread's scroll coordinate
  system;
- looking through `display: contents` wrappers to find laid-out content while excluding
  reserved answer room;
- finding the newest content edge and its minimum visible scroll position;
- locating the optimistic or canonical prompt that anchors a sent message;
- measuring answer room against the current viewport;
- capturing several visible lines and restoring from the first surviving line.

Those operations all answer “where is this thing in the thread?” They know the DOM
coordinate system and existing selectors but do not decide whether to follow, when a
reader has taken control, when a render is stale, or how Svelte schedules work. By the
deletion test, removing the module puts that coordinate knowledge back into the
viewport; it does not make the complexity disappear.

Implement `threadGeometry.ts` as a private on-demand geometry reader, with one factory,
the reader type, the reading type, and the held-view type as its complete exports:

```ts
export type HeldView = {
  scrollTop: number;
  lines: { element: Element; top: number }[];
};

export type ThreadReading = {
  newestLineBottomPixels: number;
  remainingReservedSpacePixels: number;
  newestLineIsInSight: boolean;
};

export type ThreadGeometry = {
  /** Live DOM reading. No retained rectangle or content list. */
  hasShape(): boolean;
  /** Live DOM reading, including laid-out traversal and reserved-room release math. */
  read(reservedSpacePixels: number): ThreadReading;
  /** Calculation from the supplied cached bottom and current scrollTop; no rectangles. */
  newestLineIsInSight(cachedNewestLineBottomPixels: number): boolean;
  /** Calculation only; caller decides whether and when to mutate scrollTop. */
  newestLineScrollTop(cachedNewestLineBottomPixels: number): number;
  /** Live selector reading for an optimistic message or its canonical prompt fallback. */
  sentMessage(messageId: string): Element | null;
  /** Live selector reading for the newest optimistic/canonical prompt. */
  answerRoomMessage(): Element | null;
  /** Live rectangle calculation; returns the room height, mutates nothing. */
  answerRoomPixels(message: Element): number;
  /** Live rectangle calculation; returns the target scrollTop, mutates nothing. */
  sentMessageScrollTop(message: Element): number;
  /** Live laid-out traversal and rectangle capture. */
  holdView(): HeldView;
  /** Live survivor reading; returns a target scrollTop or null, mutates nothing. */
  restoredScrollTop(held: HeldView): number | null;
};

export function threadGeometry(
  thread: HTMLDivElement,
  reservedSpaceElement: HTMLDivElement | null
): ThreadGeometry;
```

This interface is deliberately read-only. It hides the thread coordinate system,
`display: contents` traversal, selectors, and rectangle arithmetic, while the component
retains every decision and every mutation. In particular, the scroll handler calls
`newestLineIsInSight(newestLineBottomPixels)` with the component's cached measurement;
that method must not traverse content or call `getBoundingClientRect`. `read(...)` is the
only aggregate full-thread remeasurement and returns the remaining answer-room value
rather than writing Svelte state.

Put all tunable values in the private
`viewportConfiguration.ts` module:

- `NEAR_NEWEST_LINE_PIXELS`;
- `SENT_MESSAGE_TOP_GAP_PIXELS`;
- `NEWEST_LINE_BOTTOM_GAP_PIXELS`;
- `MOST_HELD_LINES`.
- `READER_DRIVING_MILLISECONDS`.

`threadGeometry.ts` imports the four geometry tunables. `ConversationViewport.svelte`
imports only `READER_DRIVING_MILLISECONDS`. Create a fresh geometry reader whenever the
bound thread or reserved-space element may have changed. Do not cache content elements,
rectangles, or measurements across a `tick`.

Keep policy in `ConversationViewport.svelte`:

- `following`, `jumpVisible`, `reservedSpacePixels`, settled-message identity, and
  stale-render request state;
- reader-intent timing and pointer state;
- decisions about forward-only following versus explicit backward Latest movement;
- row/outgoing, resize/load, fold-click, and conversation-state effects;
- the fallback to following when no held line survives;
- all `tick` and `untrack` sequencing.

There is no second controller, store, action, public barrel, or direct test interface for
geometry. The public component remains the test surface.

## Exact ownership after the move

### `ConversationViewport.svelte`

Move the following together, preserving their relative behavior and comments:

- the viewport portion of the pane's opening documentation;
- `tick` and `untrack`;
- `ConversationTranscript`, `MessagePieces`, and `outgoingMessageNote`;
- the locked contract types;
- all thread and reserved-space element bindings;
- all measured pixel values, following state, held views, reader-intent state,
  settled-message ids, and render request counters;
- `modeChip`;
- the viewport policy functions and Svelte effects;
- a viewport-owned `<svelte:window>` containing only reader pointer release/cancel;
- the complete `.chat-thread-shell` subtree;
- `.chat-thread`, its role, name, tabindex, bindings, load/scroll/wheel/touch/key/pointer
  handlers, and click handler;
- the empty snippet condition;
- `ConversationTranscript`;
- keyed optimistic outgoing messages, their labels/chips, conditional discard control,
  and `MessagePieces`;
- the conditional reserved-space element;
- the conditional Latest button;
- viewport styles:
  - `:global([data-conversation-thread]) { overflow-anchor: none; }`;
  - `.c2-reserved`;
  - `.c2-label`;
  - `.c2-chip`;
  - `.c2-discard` and its hover rule.

Preserve every existing selector, class, role, accessible name, key, and event:

```text
.chat-thread-shell
[data-conversation-thread]
[data-conversation-outgoing]
[data-conversation-outgoing-label]
[data-conversation-outgoing-discard]
[data-conversation-reserved-space]
.chat-jump
aria-label="Conversation"
aria-label="Jump to latest message"
```

The viewport receives `conversationState` only so its pre-effect can hold and restore
the view across rest/peeked/opened height changes. It never mutates that state.

Keep the source effect order explicit and unchanged:

1. the rows/outgoing `$effect.pre`;
2. the generic size `$effect`;
3. the conversation-state `$effect.pre`;
4. the fold-click handler.

Preserve request invalidation, `tick`, and `untrack` placement exactly. Geometry adds no
extra awaited frame and no value read before a `tick` may be reused after it.

### `ConversationPane.svelte`

Keep:

- the full application-facing prop interface;
- the `.chat-panel` root and `data-conversation-state`;
- pane element binding used to recognize a press on this pane's composer/rest bar;
- header identity, connection state, workspace, state control, menu, and New
  confirmation;
- window pointer-down and Escape behavior;
- all state transitions;
- `restLineFrom` and `ConversationRestBar`;
- `ConversationComposer` and every callback;
- the pane gap rules;
- the top-level rest rule that hides `.chat-head` and `.chat-thread-shell`.

The rest hiding rule remains in the pane because the pane owns what each layer state
draws, even though the thread shell is rendered by its child.

Replace the existing thread-shell block with exactly one explicit locked call:

```svelte
<ConversationViewport
  {conversationId}
  {rows}
  {outgoingMessages}
  {models}
  {ownSenderLabel}
  {livenessPulse}
  {conversationState}
  {emptyState}
  {onDiscardHeldPrompt}
/>
```

Do not pass pane DOM, setters, following state, geometry callbacks, composer state, rest
line, running state, or route/live-conversation facts not present in `contract-lock.md`.

After the move the pane must contain none of:

```text
threadElement
reservedSpaceElement
reservedSpacePixels
following
jumpVisible
newestLineBottomPixels
HeldView
scrollTop
scrollHeight
getBoundingClientRect
settledMessageIds
readerDrivingUntilMilliseconds
scrollRenderRequest
```

## Characterization baseline

Before editing production code:

1. Confirm the worktree and dependencies:

   ```sh
   git rev-parse --show-toplevel
   .venv/bin/python -c 'import planner; print(planner.__file__)'
   env | rg '^PLAN_(DB_PATH|LOGS_DIR|DISPATCHER_LOCK_PATH|SERVER_CONTROL_SOCKET|HERMES_HOME)='
   ```

   The git root and `planner.__file__` must resolve inside this worktree. Any printed
   `PLAN_*` runtime path must also resolve here before it is used. Use the worktree's
   `.venv`, `web/node_modules`, and installed Playwright Chromium. If dependencies are
   absent, perform the standard worktree setup from `AGENTS.md` before testing.

2. Record `git status --short` and the initial checked-in `web/dist` asset names.

3. Build the untouched source so the server-backed browser tests exercise this
   worktree's source:

   ```sh
   npm --prefix web run build
   ```

   If an untouched build produces unexplained bundle drift, record and resolve that
   before treating later bundle changes as this ticket's artifact.

4. Run and record the full output of:

   ```sh
   node web/tests/conversation-pane.test.mjs
   .venv/bin/pytest -q \
     tests/e2e/test_dev_conversation_pane.py \
     tests/e2e/test_conversation_three_states.py
   ```

   Save the commands, exit codes, and complete output in
   `orchestration/tickets/frontend-conversation-viewport/baseline-evidence.md`.

The pytest invocation uses pytest-playwright's session Chromium. Its harness scrubs
ambient `PLAN_*`, starts `panels serve` from this worktree on an OS-assigned port, and
uses temporary DB, logs, lock, managed files, and Hermes home under
`PLAN_TEST_MODE=1`. Do not start a separate server or reserve a fixed port.

The browser baseline covers:

- optimistic messages before the server answers, their fate changes, reload, image
  content, and canonical takeover;
- first-send, refused-send, and unanswered-send behavior;
- reader takeover, Latest, fold shrink/growth, and preservation of the line being read;
- sent-message anchoring, reserved answer room, room release, and forward-only following;
- image content rendering and loading through the viewport;
- rest/peeked/opened visibility and controls;
- held reader position and composer draft/focus through every layer transition;

The current generic shape handler deliberately has no held-line snapshot. On resize or
media load it remeasures and, if already following, keeps the newest line visible. For a
reader who is not following it applies no deliberate scroll correction. This ticket
preserves that behavior; it does not claim the stronger same-line guarantee that the
current source and tests do not provide.

No test edit is expected. A failure after the move is a regression to fix in the
extraction, not a reason to weaken characterization.

## Implementation sequence

1. **Add the private configuration and geometry readers.**

   Add `viewport/viewportConfiguration.ts` with the five existing tunables, then add
   `viewport/threadGeometry.ts` with the exact read-only interface above. Move the current
   coordinate calculations and DOM selectors without arithmetic, tolerance, rounding,
   traversal, fallback, or mutation-order changes. Keep both private to the folder and do
   not add a barrel.

2. **Create the locked viewport component.**

   Add `viewport/ConversationViewport.svelte` with exactly the contract-lock props.
   Move the viewport state, policy, effects, event handlers, thread-shell DOM, optimistic
   rendering, reserved room, Latest control, and viewport-owned styles as one behavior.
   Use `threadGeometry` only for live DOM geometry; keep all decisions and Svelte
   scheduling in this file.

3. **Replace the pane's embedded viewport.**

   Import `ConversationViewport`, render one instance with the nine locked facts, and
   delete the moved imports, state, functions, effects, DOM, and styles from the pane.
   Retain the pane's state transitions and rest hiding rule. Do not introduce a
   compatibility component or re-export.

4. **Teach the existing harness about the semantic child folder.**

   Keep its exact top-level component inventory assertion. Add
   `viewport/ConversationViewport.svelte` to the source files it compiles and inspects
   for warnings, token usage, and shared conversation class reuse. Do not put a marker
   string into production, flatten the component, add a second inventory assertion, or
   weaken any rendered/behavior assertion.

5. **Run the narrow structural and rendered checks.**

   Once the move is complete, run:

   ```sh
   node web/tests/conversation-pane.test.mjs
   npm --prefix web run check
   ```

   The Node harness must still compile `ConversationPane` without warnings and must
   retain its existing SSR assertions for optimistic messages, conditional discard, and
   image rendering through the child viewport.

6. **Audit the settled source before the final browser gate.**

   Run the size, interface, dependency, and scope audits below. Correct structural
   violations before rebuilding and running the final browser characterization.

7. **Build the settled production bundle.**

   ```sh
   npm --prefix web run build
   ```

   Keep the rebuilt checked-in `web/dist/index.html`, new hashed JS/CSS assets, and
   deletion of every superseded hashed asset. FastAPI serves this bundle; it is part of
   the implementation, not disposable output.

8. **Run the final browser and frontend gates once on the settled tree.**

   Use the exact focused-gate sequence below. Record full outputs in
   `verification-evidence.md`, then write `implementation-report.md`.

## Source and dependency audits

### Size

```sh
wc -l \
  web/src/components/conversation/ConversationPane.svelte \
  web/src/components/conversation/viewport/ConversationViewport.svelte \
  web/src/components/conversation/viewport/threadGeometry.ts \
  web/src/components/conversation/viewport/viewportConfiguration.ts
```

Require the pane to be 300–450 lines, the viewport to be at most 600, the geometry file
to remain a smaller internal implementation concern, and no changed production file to
exceed 600 lines.

### Locked interface

Inspect the complete `$props()` declaration in `ConversationViewport.svelte`. It must
contain exactly the nine names and types in `contract-lock.md`, with no bindable prop.
Search for forbidden custom output:

```sh
rg -n '\\$bindable|createEventDispatcher|dispatch\\(|on[A-Z].*=' \
  web/src/components/conversation/viewport
```

`onDiscardHeldPrompt` is the one locked callback and is not a custom emitted event.
Inspect any result rather than accepting the search mechanically.

### Dependency direction

```sh
rg -n '^\\s*import ' \
  web/src/components/conversation/viewport/ConversationViewport.svelte \
  web/src/components/conversation/viewport/threadGeometry.ts \
  web/src/components/conversation/viewport/viewportConfiguration.ts
rg -n 'ConversationViewport|threadGeometry' web/src web/tests
```

Require:

- only `ConversationPane.svelte` imports `ConversationViewport`;
- only `ConversationViewport.svelte` imports `threadGeometry`;
- only the viewport folder imports `viewportConfiguration`;
- the viewport imports transcript/message rendering, outgoing presentation, Svelte, and
  the locked types;
- neither viewport file imports the composer, rest bar, pane, route code, HTTP clients,
  live-conversation orchestration, feed, or backend runtime code;
- no barrel or flat compatibility module exists.

### Pane deletion test

Search `ConversationPane.svelte` for every forbidden viewport-state term listed under
Exact ownership. There must be no hit. Inspect the remaining pane and confirm it reads as
composition: header/menu/state controls, one viewport, rest bar, composer.

### DOM and style ownership

Search the source tree for the locked selectors and viewport styles. Each viewport DOM
selector must have one rendering owner in `ConversationViewport.svelte`; outgoing
label/chip/discard and reserved-space styles must move with that markup. The pane alone
must retain the top-level rest hiding rule and pane gap rules.

### Scope

```sh
git status --short --untracked-files=all
git diff --stat
git diff --check
```

Changes must be limited to:

- `ConversationPane.svelte`;
- `viewport/ConversationViewport.svelte`;
- `viewport/threadGeometry.ts`;
- `viewport/viewportConfiguration.ts`;
- `web/tests/conversation-pane.test.mjs`;
- ticket evidence/reports;
- freshly rebuilt `web/dist`.

No production test edit is planned.

## Focused gates

Run after production, evidence inputs, and source audits are settled:

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

Build must precede pytest because the real FastAPI server serves `web/dist`. The
frontend architecture program reserves one canonical repository-wide `./verify` for its
final settled tree; do not run it for this ticket.

## Risks and controls

- **Shallow helper fragmentation.** Use only the required configuration module and the
  exact read-only `threadGeometry.ts` interface. Keep policy, mutations, and Svelte
  lifecycle together in the viewport.
- **Stale geometry.** Never retain content lists or rectangles across DOM changes or
  `tick`; construct geometry against the current thread and reserved element each time.
- **Scroll hot-path regression.** The scroll handler supplies the cached
  `newestLineBottomPixels` to `newestLineIsInSight`; it performs no laid-out traversal or
  rectangle measurement.
- **Effect-order drift.** Move the row/outgoing pre-effect, size effect, state-transition
  pre-effect, and fold-click correction without changing their relative scheduling,
  request invalidation, `tick`, or `untrack` placement.
- **Scoped-style drift.** Move outgoing and reserved-room styles into the same Svelte
  file as their markup. Retain class names and global `overflow-anchor: none`.
- **Rest-state ownership leak.** The viewport observes `conversationState` for
  restoration, but only the pane mutates it and owns the CSS rule that hides the header
  and thread shell at rest.
- **Split window events.** Pane window pointer-down/Escape behavior remains in the pane;
  reader pointer release/cancel moves with viewport reader state. Thread keydown,
  wheel, touch, scroll, pointer-down, click, and load handlers remain on the same DOM
  region.
- **Optimistic/canonical identity drift.** Preserve message keys, outgoing ordering,
  selector lookup, the fallback to the newest recorded prompt, and settled-message-id
  reset behavior.
- **Reserved-room drift.** Preserve `Math.ceil`, clamping, top/bottom gaps, release
  order, and the two-frame restoration sequence when pane height changes.
- **Held view vanishes.** Preserve the first-surviving-line rule and the explicit fallback
  to following/latest when no held element remains after rest or folding.
- **Snippet remount.** Pass the existing `Snippet` unchanged and keep the exact
  rows-and-outgoing empty condition; do not key or conditionally mount the viewport.
- **Browser tests exercise stale output.** Rebuild checked-in `web/dist` immediately
  before the final pytest invocation.
- **Flat harness inventory.** Preserve the top-level inventory while explicitly adding
  the nested viewport to the harness's compile/source checks. Never accommodate the old
  flat lookup with production-only marker text.
