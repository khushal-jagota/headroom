# Frontend conversation thread-layout implementation plan

## Locked scope and current state

This ticket is a behavior-preserving extraction from the current 934-line
`web/src/lib/conversation/transcript.ts`.

The layout implementation is already one contiguous policy:

- `ThreadItem` describes the laid-out row, turn-anchor, and work-group result;
- `OpenTurn` and `NO_TURN` hold private construction state;
- `threadItems` anchors turns, groups consecutive calls, settles folds, attributes
  duration, marks the latest turn, and leaves the newest plan on one anchor.

The external seam is exactly the contract in `contract-lock.md`:

```ts
export type ThreadItem = /* locked discriminated union */;

export function threadItems(rows: readonly TranscriptRow[]): ThreadItem[];
```

Keep the locked public names. Do not add compatibility exports from
`transcript.ts`, aliases, subtype exports, counters, constants, or helper functions.

Use one file:

```text
web/src/lib/conversation/threadLayout/index.ts
```

The moved implementation is expected to remain below 300 lines. A second private
implementation file would add an internal export and another navigation hop without
isolating a separately changing concern, so it does not earn its existence here.

`VISIBLE_RUNNING_WORK_ENTRIES`, `hiddenWorkSentence`, turn wording, timer
calculations, plan wording, `TranscriptRow`, and `ToolCallRow` stay where they are.
This ticket does not redesign `OpenTurn` or the flat chronological output.

## Dependency direction

Keep projection independent from layout and keep the runtime graph acyclic:

```text
ConversationTranscript ──runtime──▶ threadLayout
restLine ────────────────runtime──▶ threadLayout

threadLayout ──type-only──▶ transcript
threadLayout ──type-only──▶ wire
transcript ───────────────runtime──▶ wire/feed
```

In `threadLayout/index.ts`:

```ts
import type { TranscriptRow, ToolCallRow } from "../transcript";
import type { ConversationTurnEnding, PlanEntry } from "../wire";
```

Those are the only imports. `threadLayout` performs no I/O and imports no Svelte,
feed, presentation, or runtime transcript value.

`transcript.ts` must not import or re-export `threadLayout`. Callers and tests move
directly to the semantic folder interface.

## RED/GREEN implementation sequence

### 1. Establish the interface RED

In `conversation-thread-items.test.ts`, split the current transcript import:

- import `ThreadItem` and `threadItems` from
  `../src/lib/conversation/threadLayout`;
- remove the existing local
  `type ThreadItem = ReturnType<typeof threadItems>[number]` alias so the locked
  exported type is the test surface;
- continue importing `transcriptRows`, `VISIBLE_RUNNING_WORK_ENTRIES`,
  `hiddenWorkSentence`, `foldedWorkSentence`, `turnFoldLabel`, and
  `workedSentence` from `transcript`.

Do not weaken or replace any behavioral assertion. Run:

```sh
npm --prefix web run test:vitest -- \
  tests/conversation-thread-items.test.ts
```

Record the expected missing-module failure for
`../src/lib/conversation/threadLayout`. That is the RED: the agreed seam does not
yet exist.

### 2. Make the first interface slice GREEN

Add `threadLayout/index.ts` with:

- the locked `ThreadItem` type;
- private `OpenTurn` and `NO_TURN`;
- the existing `threadItems` algorithm and its nested private settlement/counting
  helpers.

Move the implementation without changing keys, mutation order, array ordering,
duration math, plan handling, or output shape. Run the single thread-items suite
again and record GREEN.

This first slice proves through the new interface:

- one stable anchor per ordinary prompt-started turn;
- steer prompts joining the open turn;
- chronological contiguous work groups;
- running work counts;
- settled work and intermediate messages folding under the right turn;
- prompts and permission decisions remaining outside folds;
- silent, interrupted, tools-only, and stopped turn behavior;
- separate durations and ownership across multiple turns.

### 3. Redirect the remaining pure interface tests

Update imports without changing assertions:

- `conversation-thread-plan.test.ts`
  - import `ThreadItem` and `threadItems` from `threadLayout`;
  - remove its existing local `ReturnType`-derived `ThreadItem` alias;
  - retain `transcriptRows` from `transcript`.
- `conversation-turn-time.test.ts`
  - import `ThreadItem` and `threadItems` from `threadLayout`;
  - replace its `ReturnType`-derived `TurnItem` with
    `Extract<ThreadItem, { kind: "turn" }>`;
  - retain all duration, sentence, clock, and `transcriptRows` imports from
    `transcript`.

The plan suite remains the interface proof for plan replacement and single newest
ownership. The turn-time suite remains presentation/timing coverage while using the
new layout seam to obtain turn anchors; it must not cause timing functions to move.

Run the three focused Vitest files together before changing production callers:

```sh
npm --prefix web run test:vitest -- \
  tests/conversation-thread-items.test.ts \
  tests/conversation-thread-plan.test.ts \
  tests/conversation-turn-time.test.ts
```

### 4. Move both production callers directly to the seam

In `ConversationTranscript.svelte`:

- import `ThreadItem` with `import type` from `threadLayout`;
- import `threadItems` at runtime from `threadLayout`;
- retain `TranscriptRow` and all row/presentation wording imports from
  `transcript`;
- do not change expansion state, filtering, item keys, component props, DOM, or CSS.

In `restLine.ts`:

- import `ThreadItem` with `import type` from `threadLayout`;
- import `threadItems` at runtime from `threadLayout`;
- retain `TranscriptRow`, `ToolCallRow`, live-ask, plan wording, turn wording, and
  working wording imports from `transcript`;
- do not change `restLineFrom` or any visible rest-line result.

No other production caller should import `threadLayout`.

### 5. Update the closed legacy transpilation harness exactly

`conversation-rest-line.test.mjs` manually transpiles a closed module set. Extend
its explicit source/output table with:

```text
threadLayout/index.ts → threadLayout.mjs
```

Place it after `transcript.ts` and before `restLine.ts`.

Add the sibling runtime rewrite:

```js
.replace(
  /from\s+["']\.\/threadLayout["']/g,
  'from "./threadLayout.mjs"'
)
```

Also retain/add explicit parent-specifier rewrites for the folder module:

```js
.replace(
  /from\s+["']\.\.\/transcript["']/g,
  'from "./transcript.mjs"'
)
.replace(
  /from\s+["']\.\.\/wire["']/g,
  'from "./wire.mjs"'
)
```

The parent imports are type-only and should be erased by TypeScript today; keeping
the mapping explicit makes the nested source/output relationship correct rather
than relying on that erasure. Do not add a flat compatibility file.

Run:

```sh
node web/tests/conversation-rest-line.test.mjs
```

Its existing assertions must continue to prove that the rest line reads the running
turn's newest call, plan, start time, waiting ask, and stopped result through the
new layout module.

### 6. Delete the old seam and implementation

Remove from `transcript.ts`:

- the `ThreadItem` export;
- private `OpenTurn`;
- private `NO_TURN`;
- the `threadItems` export and its implementation comments/helpers.

Do not remove or relocate:

- `ToolCallRow`;
- `VISIBLE_RUNNING_WORK_ENTRIES`;
- `hiddenWorkSentence`;
- any duration, fold, plan, prompt, ask, or turn wording;
- any timer constant or calculation.

Search production and tests for imports of `ThreadItem` or `threadItems` from
`transcript`. None may remain. Search `transcript.ts` for a `threadLayout` import or
re-export. None may exist.

### 7. Preserve rendered behavior

Run the existing pane harness without changing source-shape assertions or replacing
observable coverage:

```sh
node web/tests/conversation-pane.test.mjs
```

It must continue to prove:

- settled tool runs and intermediate commentary start folded;
- opening a turn restores commentary and work in chronological position;
- separate running work groups expand independently;
- only the newest running call in each group starts visible;
- running, settled, interrupted, and stopped heads retain their wording;
- the newest plan remains visible on its owning anchor.

No browser, CSS, route, or viewport behavior changes are authorized.

## Interface and scope audits

After the tree is settled:

1. Record line counts:

   ```sh
   wc -l \
     web/src/lib/conversation/transcript.ts \
     web/src/lib/conversation/threadLayout/index.ts
   ```

   Expected:

   - `transcript.ts`: approximately 650–675 lines;
   - `threadLayout/index.ts`: below 400 lines;
   - no added production file above 600 lines.

2. Prove the locked export surface:

   ```sh
   rg -n "^export\\b" \
     web/src/lib/conversation/threadLayout/index.ts
   ```

   The complete result must contain exactly the locked `ThreadItem` type and
   `threadItems` function declarations. Any third result is a contract violation.

3. Prove the old seam is gone:

   ```sh
   rg -n "from .*conversation/transcript|from [\"']\\./transcript|from [\"']\\.\\./transcript" \
     web/src web/tests
   ```

   Inspect every `ThreadItem`/`threadItems` result: production callers and
   thread-layout tests must import the folder; legitimate row and wording imports
   remain on `transcript`.

4. Prove the dependency direction:

   - `threadLayout/index.ts` has only the two locked type-only import statements;
   - `transcript.ts` contains no `threadLayout`;
   - no compatibility barrel or flat `threadLayout.ts` exists.

5. Check `git status --short` and confirm that changes are limited to the locked
   production callers/module, named tests/harness, required ticket evidence, and
   the freshly rebuilt checked-in `web/dist` bundle.

## Settled focused gates

Run this sequence once after all implementation and evidence edits are settled:

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

Retain the rebuilt tracked `web/dist` changes because FastAPI serves that checked-in
bundle. Record the full output of every final gate. Do not run repository-wide
`./verify`; the architecture program reserves one canonical run for the final
settled tree.

## Risks and controls

- **Compatibility seam left behind.** Re-exporting from `transcript.ts` would leave
  two interfaces and hide stale callers. Delete the old exports and update every
  caller directly.
- **Runtime cycle.** A value import from `transcript`/`wire`, or a reverse import
  from `transcript`, would make projection and layout depend on each other at
  runtime. Require `import type` in the new module and no reverse dependency.
- **Behavior drift during a structural move.** Turn anchoring, chronological group
  breaks, fold membership, final-answer retention, duration, latest-turn marking,
  and plan ownership are coupled. Move the existing algorithm before considering
  any internal rewrite; the ticket authorizes no redesign.
- **Presentation scope creep.** The visibility constant and hidden-work sentence
  happen to sit beside layout today but belong to WorkGroup presentation. Leave
  them and every timing/wording helper unchanged.
- **Nested module resolution in the legacy harness.** The harness does not discover
  modules. Missing the explicit source/output entry or `./threadLayout` rewrite
  fails independently of production correctness.
- **Coverage accidentally becomes source inspection.** The pure suites assert the
  locked `ThreadItem[]` result; the pane and rest-line harnesses assert visible
  behavior through both production callers. Do not replace either with import-text
  assertions.
- **Checked-in bundle drift.** The build is a gate and an artifact. Retain its
  updated `web/dist/index.html`, new hashed asset, and deletion of the superseded
  asset.
