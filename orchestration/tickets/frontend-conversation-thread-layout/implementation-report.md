# Implementation report

## Outcome

The conversation thread-layout policy now lives in the semantic
`web/src/lib/conversation/threadLayout/index.ts` module. Its complete public
interface is the locked `ThreadItem` type and `threadItems` function. Turn
construction state and layout helpers remain private.

Both production consumers now depend directly on that interface:

- `ConversationTranscript.svelte` obtains its laid-out thread from
  `threadLayout`;
- `restLine.ts` obtains the same laid-out thread from `threadLayout`.

`transcript.ts` still owns row projection, transcript row contracts, timer
calculations, display constants, and presentation language. It has no import or
re-export of the new module.

## Changes

- Moved the existing `ThreadItem`, `OpenTurn`, `NO_TURN`, and `threadItems`
  implementation into `threadLayout/index.ts` without changing its keys,
  ordering, mutation sequence, duration math, folding, latest-turn, or plan
  ownership behavior.
- Removed the old layout export and implementation from `transcript.ts`.
- Redirected the two production callers and the three named pure suites to the
  new interface.
- Updated the rest-line harness's closed transpilation map and nested-module
  rewrites.
- Rebuilt and retained the checked-in `web/dist` bundle.
- Did not change CSS, backend code, HTTP routes, wire contracts, feed behavior,
  presentation wording, or product behavior.

## Contract and size audit

`threadLayout/index.ts` has exactly two exports:

```text
22:export type ThreadItem =
111:export function threadItems(rows: readonly TranscriptRow[]): ThreadItem[] {
```

Its only imports are the locked type-only imports from `../transcript` and
`../wire`. There is no flat `threadLayout.ts` compatibility file.

Final line counts:

```text
655 web/src/lib/conversation/transcript.ts
292 web/src/lib/conversation/threadLayout/index.ts
```

The extraction therefore meets the ticket's approximately 650–675-line target
for `transcript.ts`, keeps the new module below 400 lines, and adds no production
file above 600 lines.

## TDD evidence

The first test change redirected `conversation-thread-items.test.ts` to the
nonexistent locked module. The expected missing-module RED, including the full
command output, is in `red-evidence.txt`.

Adding the locked interface made the narrow suite GREEN:

```text
Test Files  2 passed (2)
Tests       18 passed (18)
Type Errors no errors
```

No behavior assertion was weakened or replaced.

## Verification

Every settled focused gate passed. The complete commands and outputs are in
`verification-evidence.md`.

- Focused Vitest selection: 6 files, 48 tests, no type errors.
- Rest-line harness: passed.
- Rendered-pane harness: passed.
- `svelte-check`: 0 errors and 0 warnings.
- Production build: passed, 549 modules transformed.
- Aggregate frontend test: 38 Vitest files, 366 tests, all legacy harnesses
  passed.
- `git diff --check`: passed.

The pane harness initially could not spawn
`.venv/bin/python` because this new worktree had no virtual environment. No
assertion ran in that failed attempt. The worktree-local venv was installed from
`requirements.txt`, the package was installed editable, and
`planner.__file__` was confirmed under this worktree. The harness then passed.
Exact PID-495 fixtures and its generated SSR directory left by the environment
failure were removed; the settled harness run cleaned its own artifacts.

The repository-wide `./verify` was not run because the architecture program
reserves one canonical run for its final settled tree. No commit was made.
