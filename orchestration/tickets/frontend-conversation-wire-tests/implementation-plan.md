# Conversation wire test decomposition implementation plan

## Intent and boundary

Replace `web/tests/conversation-wire.test.mjs` with the twelve contract-named Vitest suites.
Each suite imports the real public Conversation module through Vite's resolver and owns one
behavior domain. This test migration does not change production, dependencies, Vitest
configuration, other legacy tests, or browser tests.

Scope is `web/package.json`, deletion of the old file, the twelve named
`web/tests/conversation-*.test.ts` files, one shared
`web/tests/support/conversationEvents.ts`, and this Ticket's artifacts. Keep every replacement
below 600 lines and split cases inside its behavior domain rather than adding a thirteenth suite.

## Test conventions and seams

- Import Vitest APIs explicitly and production functions/types through ordinary relative imports.
- Do not mock a production Conversation module. Do not read source, invoke TypeScript, rewrite
  imports, generate modules, inspect physical module inventories, or print a success line.
- Fake only the existing boundaries:
  - a typed `ConversationStreamPorts` object for feed connection tests;
  - injected object-URL creation and revocation callbacks for pending images;
  - a suite-local `MemoryStorage implements Storage` installed as `window.sessionStorage`;
  - `Date.now` and `Math.random` spies for deterministic outgoing identity.
- Use a controlled promise for reconnect ordering, not the legacy real `setTimeout(0)`.
- Clean up owned globals/spies in `afterEach`, alongside the runner's existing automatic cleanup.
- Assert concrete outputs. Do not restate the timer-margin, summary-limit, or visible-entry
  constants: assert 1,020 ms scheduling, the exact truncated line, and two previous calls.
- Keep exact backend keys and stream names because they are public wire values.

## Shared typed event builders

Add `web/tests/support/conversationEvents.ts` at roughly 50–90 lines. It imports
`ConversationEvent` and exports only `promptEvent`, `agentMessageEvent`, `turnEndedEvent`,
`toolCallStartedEvent`, `toolCallFinishedEvent`, and `planUpdatedEvent`.
It also exports `permissionAskedEvent`, because feed liveness, transcript projection,
and thread folding all need that same discriminated event.

Each returns the matching `Extract<ConversationEvent, { kind: ... }>` and accepts only needed
payload/time overrides. Use stable Conversation/time defaults and mandatory sequence. Less-shared
events stay typed inline. Export no assertions, expected values, feed/transcript helpers, or
production reimplementation.

## Suite ownership

### 1. `conversation-pending-images.test.ts` (target 150–230 lines)

Import `createPendingConversationImages`, `pendingImagesAsPieces`,
`restoredPendingImages`, and `releasePendingImages` from `pendingImages`.
Migrate legacy lines 134–283:

- preserve selection order and accepted ids/names/previews, with table-driven
  admission of PNG, JPEG, GIF, and WebP;
- preserve rejection of text, empty files, SVG, HEIC, per-image oversize,
  aggregate oversize, and
  already-pending overflow;
- use independent concrete 3 MiB and 3 MiB + 1 byte inputs to prove exact
  boundary admission and pre-read rejection, rather than deriving both sides
  from an exported limit constant;
- prove rejected files are not read or previewed, accepted bytes encode to the
  exact image pieces, restored images receive ordered ids and data previews,
  blob previews are revoked, data previews are not, and a partially-created
  batch revokes resources already owned.

Keep created/revoked URL arrays local to each case. No URL global is needed.

### 2. `conversation-feed.test.ts` (target 260–380 lines)

Import the public feed functions and `ConversationStreamPorts` from `feed`;
use the shared prompt, agent, and ending builders. Migrate legacy lines
285–399, 415–531, and the feed-only frame cases at 1523–1549:

- sequence overlap, replacement, ascending insertion, and latest sequence;
- transient agent text and tool progress, committed replacement/cleanup,
  ghost-frame rejection, running state, thinking liveness, and unknown frames;
- snapshot freshness, stopped-without-ending reconciliation, idle/running
  snapshots, and latest model/effort values;
- initial fetch-then-tail, live publication, reconnect from the held sequence,
  old-tail closure before replacement, connection callbacks, and final close.

Use a controlled reconnect read promise plus a second deferred that the fake
`openTail` resolves when the replacement tail opens. Invoke `onTrouble`, assert
the synchronous `close-old-tail` then `read-events` order, resolve the read,
await the replacement-tail deferred, then assert its sequence, feed, and
connection callback count. This makes completion deterministic without a real
timer.

Pass the unknown-frame characterization through one narrow cast at the
untrusted-input boundary. Do not widen `ConversationLiveFrame`, the shared
builders, or a production type merely to express input the browser has not
learned.

### 3. `conversation-transcript.test.ts` (target 240–360 lines)

Import `transcriptRows`, `liveAskFrom`, `askDeadSentence`, `refusalSentence`, and
`turnEndingSentence` from `transcript`. Migrate legacy lines 400–413, 426–427,
536–648, and 2043–2097:

- reconcile tool start/finish into one row, including status/detail;
- project live, answered, ended-dead, and stopped-without-ending asks, including
  labels, dead reason/sentence, live-ask removal, and the synthetic stopped row;
- replace streaming text when its committed message arrives;
- project refusal and ending wording;
- retain prompt and agent image pieces, normalize legacy text rows, and project
  usage plus compaction rows with absent optional values as `null`.

Assert row discriminants before reading variant fields so the TypeScript tests
narrow normally; do not use blanket casts.

### 4. `conversation-composer-asks.test.ts` (target 130–210 lines)

Import the public ask functions from `composer`. Migrate legacy lines 668–727,
the placeholder cases at 721–727, and lines 1937–1986:

- backend-provided permission options retain labels, ids, ordering, emphasis,
  and supplied status;
- missing or unfamiliar options recover cancel/decline/approve anchors without
  dropping vendor options;
- permission, question, and shapeless classification;
- all question choices remain available, only the first nine receive digit
  shortcuts, and invalid/out-of-range digits select nothing;
- placeholders use one short prose detail but fall back to the title for
  structured, multiline, overlong, absent, or null detail.

### 5. `conversation-composer-delivery.test.ts` (target 170–260 lines)

Import delivery, change, body, picker, model/effort/detail, and fate functions from
`composer`. Use a fixed typed `OutgoingMessage` value for body tests; minting belongs to the
outgoing suite. Migrate legacy lines 649–662, 729–841, 1095–1104, 1675–1719, and 1988–1997:

- Hermes offers steer while Codex, Claude, and unknown backends do not;
- untouched/equal selections arm nothing, differing model/effort selections
  arm on ordinary delivery, and steer carries no change;
- the send body preserves the already-drawn content, id, send instant, sender,
  and mode;
- backend selection appears only when the message creates a Conversation;
- concrete model/effort preselection and per-model effort fallback, including a
  model's authoritative empty effort list;
- model detail is present only when the backend catalog supplies it;
- started, queued, injected, and refused delivery-fate wording.

Assert bodies and visible option modes, not private composer implementation details.

### 6. `conversation-outgoing.test.ts` (target 130–210 lines)

Import `mintOutgoingMessage`, `outgoingMessageNote`,
`outgoingMessagesTheRecordHasNot`, and `afterTheRecordHasBeenRead` from
`outgoing`. Migrate legacy lines 843–926 and the post-read transition at
993–1006:

- two messages minted at the same fixed millisecond still receive different
  ids from controlled random values, and carry that exact Unix-millisecond
  send time;
- all visible known-fate notes are correct;
- prompt, refusal, and discarded rows reconcile by sender message id while
  unrelated rows do not;
- recalled-in-flight messages transition only after the record is read, and an
  already-settled list remains unchanged.

Do not test `senderMessageIdsInTheRecord` directly; reconciliation through
`outgoingMessagesTheRecordHasNot` is its public behavior.

### 7. `conversation-outgoing-storage.test.ts` (target 240–360 lines)

Use type-only imports for `OutgoingMessage`, then load the real `outgoing`
module with `await import("../src/lib/conversation/outgoing")`. Install one
suite-local `MemoryStorage` as `window.sessionStorage`. Migrate legacy lines
928–992 and 1008–1093; lines 993–1006 belong only to the pure outgoing suite:

- persistence is per Conversation and preserves text, image bytes, names,
  modes, ids, times, and known fate;
- in-flight recall becomes `sent_before_this_page`, while a held message keeps
  its known waiting fate;
- invalid JSON, invalid entries, missing fields, and non-image media in an
  image piece are rejected;
- a concrete 3 MiB image survives the conservative storage envelope;
- concurrent image reservations cannot evade the shared budget across
  Conversations or reloads;
- removing the canonical optimistic copy releases its reservation so the
  blocked send can be retried, recalled, removed, and finally released.

Call `vi.resetModules()` in `beforeEach`, then dynamically import the module
into one mutable module variable. For the mid-case reload, call
`vi.resetModules()`, reassign that variable to the fresh import, and use only
the fresh module's functions thereafter while leaving the same
`sessionStorage` installed. This proves reconstruction from storage without
leaving two test-visible ledgers and avoids any dependency on
`resetOutgoingImageReservationsForTest`; do not import or call that test hook.

### 8. `conversation-thread-items.test.ts` (target 320–500 lines)

Import `threadItems`, `transcriptRows`, `hiddenWorkSentence`,
`foldedWorkSentence`, `workedSentence`, and `turnFoldLabel`; use the shared
prompt, agent, tool, and ending builders. Migrate legacy lines 1109–1302,
1464–1520, and 1627–1673:

- a turn anchor appears at its prompt before work and stays in chronological
  position;
- unbroken tool runs form work groups where they occurred, and commentary
  separates groups;
- running turns remain unfolded; settled turns fold all work plus intermediate
  commentary while retaining the final answer;
- prompts and permission asks stay outside folds;
- interrupted, silent/tool-only, stopped-without-ending, and multiple turns
  retain their correct anchors, ownership, counts, durations, and labels;
- steer joins the current turn without creating another anchor;
- a three-call running group has the newest work entry and reports
  `+2 previous tool calls`; settled groups sit behind the turn fold.

Do not assert the exported visible-entry constant.

### 9. `conversation-thread-plan.test.ts` (target 100–170 lines)

Import `threadItems` and `transcriptRows`; use shared prompt, plan, agent, and
ending builders. Migrate legacy lines 1553–1625:

- plan rows remain in the transcript record but not as thread lines;
- each plan replaces rather than merges the previous plan;
- the plan persists after its turn settles;
- a later turn owns the newest plan and the earlier anchor loses it;
- a Conversation with no plan gives every turn anchor `plan: null`.

### 10. `conversation-turn-time.test.ts` (target 200–300 lines)

Import the public timing and wording functions plus `threadItems` and
`transcriptRows`; use shared prompt and ending builders. Migrate legacy lines
1304–1462:

- duration formatting at 0, 59, 60, 61, 80, 120, and 3,600 seconds, plus
  running/settled/stopped wording;
- elapsed boundaries are relative to the turn start, not the wall-clock
  second;
- next-tick values are asserted concretely as 1,020/720/etc., including
  early/late scheduling and recovery after a 65-second background gap;
- sender time is used when believable, seconds masquerading as milliseconds
  and implausible future/past values fall back to the row second, and ordinary
  skew/slow delivery remains believable;
- steer does not reset the current turn start.

Do not import the tick-margin constant or recreate the private believability
algorithm in the test.

### 11. `conversation-tool-presentation.test.ts` (target 220–340 lines)

Import `readableDetail`, `promptLabelFor`, `toolGlyphKind`, `toolCallLine`,
`commandWithoutShellInvocation`, and `lineShowsWholeDetail` from
`transcript`. Migrate legacy lines 1721–1935:

- readable JSON layout and unchanged prose;
- own/other prompt labels;
- glyph classification for protocol kinds and current backend tool names;
- Claude argument subjects, Codex shell-wrapped titles, and Hermes descriptive
  titles/details;
- unknown subject-bearing payloads do not invent a summary;
- summaries are one line and a concrete overlong command yields the exact
  truncated text ending in an ellipsis;
- shell wrappers and their quoting are removed without touching bare commands;
- word-aware duplicate suppression and disclosure decisions.

Do not import the summary-length constant.

### 12. `conversation-wire-values.test.ts` (target 90–150 lines)

Import the public wire constants and helpers from `wire`. Migrate legacy lines
1999–2041 plus the capability assertions at 663–664:

- assert the exact closed backend list, conversation base, committed/live
  stream names, and steer capability for each backend and `null`;
- normalize legacy `{ text }`, preserve stored image pieces by identity/value,
  and extract only text from mixed content;
- assert encoded Conversation file addresses for ordinary and path-like ids.

These exact literals are the wire's public values, not source-shape assertions.

## Package update and old harness removal

Keep `test` and `test:vitest` unchanged. Change only `test:legacy` to:

```json
"test:legacy": "node tests/managed-markdown.test.mjs && node tests/markdown-renderer.test.mjs && node tests/browser-css.test.mjs && node tests/vps-status.test.mjs && node tests/backlog-ideas.test.mjs && node tests/production-surfaces.test.mjs && node tests/worker-configuration-setup.test.mjs && node tests/conversation-rest-line.test.mjs && node tests/conversation-pane.test.mjs"
```

Delete `conversation-wire.test.mjs` only after all twelve replacements pass
together. Do not reorder or otherwise edit the remaining nine commands.

## RED/GREEN migration sequence

1. Before creating each target, run its focused Vitest command and record the
   expected RED (`No test files found`). This proves the exact path is gated;
   do not manufacture a product failure by changing production code.
2. Add the shared builders when the first consumer needs them. Port one
   semantic group at a time into its owning suite, run that suite to GREEN, and
   run the still-present legacy harness after each completed suite. A
   characterization may pass on its first behavioral run because production
   already implements it; the meaningful RED is the absent replacement gate,
   not an artificial assertion.
3. After every suite is independently GREEN, run all twelve together with
   typecheck enabled. Repair fixture narrowing and isolation only within this
   Ticket's test files.
4. Remove the old file and its one package-script command. Run the twelve-suite
   gate again, then the final focused gates once on the settled tree.

Per-suite command form:

```sh
npm --prefix web run test:vitest -- tests/<suite-name>.test.ts
```

## Final focused gates

```sh
npm --prefix web run test:vitest -- \
  tests/conversation-pending-images.test.ts \
  tests/conversation-feed.test.ts \
  tests/conversation-transcript.test.ts \
  tests/conversation-composer-asks.test.ts \
  tests/conversation-composer-delivery.test.ts \
  tests/conversation-outgoing.test.ts \
  tests/conversation-outgoing-storage.test.ts \
  tests/conversation-thread-items.test.ts \
  tests/conversation-thread-plan.test.ts \
  tests/conversation-turn-time.test.ts \
  tests/conversation-tool-presentation.test.ts \
  tests/conversation-wire-values.test.ts
npm --prefix web run check
npm --prefix web run build
npm --prefix web test
git diff --check
```

Then inspect scope and size:

```sh
find web/tests -maxdepth 1 -name 'conversation-*.test.ts' -print0 | xargs -0 wc -l
rg -n 'readFile|transpileModule|mkdtemp|writeFile|node:fs|from "typescript"|console\.log' \
  web/tests/conversation-*.test.ts web/tests/support/conversationEvents.ts
git diff --name-status
```

Fail the Ticket if any replacement exceeds 600 lines, the harness search finds
anything, the old file still exists, or scope includes an unlisted file. Do not
run `./verify`; the program reserves it for the final assembled tree.

## Contract ambiguities and decisions

1. “Recovered ask actions” is not a persistence behavior in the legacy suite.
   Treat it as recovery from missing or unfamiliar backend options by supplying
   the cancel/decline/approve anchors, which is the existing composer behavior.
2. Required behavior asks to preserve public interface values, while the
   migration must avoid tautological constant assertions. Preserve exact wire
   constants because they are protocol values. Preserve timer, truncation, and
   running-work policy through concrete visible outputs, without asserting
   their exported implementation constants.
3. The legacy storage block uses
   `resetOutgoingImageReservationsForTest` to imitate reload. The Ticket
   direction is authoritative: a fresh dynamic import after
   `vi.resetModules()` is the reload boundary, with storage intentionally
   retained across module instances.
4. The event support file is optional only below the sharing threshold. Five
   semantic suites need the same discriminated events, so one typed builder
   file is warranted; no browser or storage fake is shared because each has
   only one owning suite.
