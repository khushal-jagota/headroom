# Frontend conversation thread layout

## Outcome

Turn the thread-layout policy currently embedded in the 934-line
`web/src/lib/conversation/transcript.ts` into one deep in-process module.

The new module must give both production callers a laid-out thread rather than making
the transcript projection file also own turn anchoring, contiguous work grouping,
settled-work folding, duration attribution, latest-turn marking, and ownership of the
conversation's newest plan. This is a behaviour-preserving architecture ticket.

## Why this seam

`ConversationTranscript.svelte` and `restLine.ts` both consume the same derived thread.
By the deletion test, removing the proposed module would force both callers to recreate
the rules that turn transcript rows into stable turn anchors and chronological work
groups. The module therefore earns its seam and gives callers leverage while
concentrating layout knowledge for locality.

The module does not own wording, timer scheduling, row projection, or tool-call
presentation. Those things are adjacent in the current file but do not participate in
laying out a thread. Moving them here would make the interface shallower rather than
deeper.

All dependencies are in-process. No port or adapter is justified.

## Contract files

- Existing input contracts: `TranscriptRow` and `ToolCallRow` in
  `web/src/lib/conversation/transcript.ts`.
- Existing plan and turn-ending contracts in
  `web/src/lib/conversation/wire.ts`.
- New layout contract:
  `web/src/lib/conversation/threadLayout/index.ts`.

The exact locked interface is in `contract-lock.md`. The implementation may not add
extra exports or a compatibility re-export from `transcript.ts`.

## Required work

1. Add the non-trivial `threadLayout/` module folder.
2. Move `ThreadItem`, `OpenTurn`, `NO_TURN`, and `threadItems` behind the folder's
   single interface. Keep `OpenTurn`, `NO_TURN`, and all layout helpers private.
3. Preserve exactly:
   - prompt-created stable turn anchors;
   - steer prompts joining the running turn;
   - contiguous tool-call groups in chronological position;
   - settled work and intermediate agent messages going behind the correct fold;
   - prompts and permission decisions remaining outside folds;
   - stopped, interrupted, silent, and tools-only turn results;
   - duration attribution;
   - latest-turn marking;
   - replacement and single ownership of the newest plan.
4. Change `ConversationTranscript.svelte` and `restLine.ts` to import
   `ThreadItem` and `threadItems` from the new module.
5. Change the thread-layout tests to exercise the new module interface directly.
   Keep presentation-language assertions importing their existing functions from
   `transcript.ts`.
6. Update the closed transpilation map and import rewrites in
   `conversation-rest-line.test.mjs` for `threadLayout/index.ts`.
7. Remove the moved implementation and exports from `transcript.ts`.
8. Do not move `TranscriptRow` or `ToolCallRow`. Tool-call presentation is not a
   consumer of thread layout and must not depend on it merely to name an input row.

## Dependency direction

```text
transcript TranscriptRow/ToolCallRow ──type──▶ threadLayout
wire ConversationTurnEnding/PlanEntry ─type──▶ threadLayout
threadLayout ──runtime──▶ nowhere
```

`transcript.ts` must not import or re-export `threadLayout`. The new module's imports
must be type-only, keeping projection independent from layout and the runtime graph
acyclic.

## Acceptance

- The existing thread-layout behavior listed under Required work remains exact.
- `ConversationTranscript.svelte` and `restLine.ts` consume the new interface directly.
- No compatibility re-export or second thread-layout interface remains in
  `transcript.ts`.
- `transcript.ts` falls from 934 lines to approximately 650–675 lines.
- `threadLayout/index.ts` remains below 400 lines.
- No production file added by this ticket exceeds 600 lines.
- No backend, HTTP, live-tail, CSS, route, wire, feed, presentation wording, or product
  behavior changes.
- The non-trivial module is represented by its own semantic folder rather than another
  entry in the flat conversation directory.

`transcript.ts` will still be above the preferred 300–600-line range. That is not
accepted as a finished structure: turn timing and presentation language remain named
follow-up seams. They are excluded here because crossing those seams in the same ticket
would weaken this module's cohesion.

## TDD sequence

1. Redirect one thread-layout suite to the nonexistent `threadLayout` interface and
   record the missing-module RED result.
2. Add the locked interface and move the existing algorithm without redesigning it.
3. Redirect the remaining structural tests and production callers.
4. Remove the old interface and implementation from `transcript.ts`.
5. Run the focused pure suites, then the rest-line and rendered-pane harnesses.

## Focused gates

Run after the ticket is complete:

```sh
npm --prefix web run test:vitest -- \
  tests/conversation-thread-items.test.ts \
  tests/conversation-thread-plan.test.ts \
  tests/conversation-turn-time.test.ts
node web/tests/conversation-rest-line.test.mjs
node web/tests/conversation-pane.test.mjs
npm --prefix web run check
npm --prefix web run build
npm --prefix web test
git diff --check
```

The frontend architecture program reserves one canonical `./verify` for its final
settled tree; this ticket does not repeat it.

## Outside this ticket

- Turn-timing and turn-language extraction from `transcript.ts`.
- Transcript row contracts and projection package decomposition.
- Work-group presentation policy, including how many running entries are visible.
- Conversation viewport/scroll extraction.
- Composer, route, or CSS restructuring.
- Tailwind or any stack change.
