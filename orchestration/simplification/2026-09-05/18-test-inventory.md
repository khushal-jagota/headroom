# Current combined-tree test inventory

This is the collection-only acceptance inventory for
`24569369974a3eee27799628a03a5846ef355a0a`. It uses the same units as the
fixed baseline and the first pruning measurement: collected pytest cases and
expanded runtime Vitest cases. The pytest groups were collected separately
because whole-root collection encounters the existing nested integration
`pytest_plugins` restriction.

| Suite | Fixed baseline `b7ca8e04` | First pass `d55260b3` | Current tree | Change from first pass |
|---|---:|---:|---:|---:|
| Python unit | 1,924 | 946 | 948 | +2 |
| Python integration | 14 | 8 | 8 | 0 |
| Playwright E2E | 39 | 19 | 19 | 0 |
| Vitest runtime | 528 | 178 | 180 | +2 |
| **Comparable total** | **2,505** | **1,151** | **1,155** | **+4** |

The current tree removes 1,350 of 2,505 baseline cases, or 53.9%. Retaining at
most 1,252 cases is the exact at-least-half ceiling for the odd baseline, so
1,155 leaves 97 cases of margin. E2E removes 20 of 39 cases and retains 19, so
it meets the separate E2E ceiling exactly.

The raw text collections were captured with these commands from the worktree
root:

```text
script -q data/simplification-deeper/current-unit-collection.txt .venv/bin/pytest --collect-only -q -o addopts= tests/unit
script -q data/simplification-deeper/current-integration-collection.txt .venv/bin/pytest --collect-only -q -o addopts= tests/integration
script -q data/simplification-deeper/current-e2e-collection.txt .venv/bin/pytest --collect-only -q -o addopts= tests/e2e
```

Their pytest summaries report 948, 8, and 19 collected cases respectively.
Vitest was collected from `web/`, with its typecheck project disabled, using:

```text
script -q ../data/simplification-deeper/current-vitest-runtime-list.txt node_modules/.bin/vitest list --typecheck.enabled=false --no-color
node_modules/.bin/vitest list --typecheck.enabled=false --no-color --json=../data/simplification-deeper/current-vitest-runtime-list.json
jq 'length' ../data/simplification-deeper/current-vitest-runtime-list.json
```

The JSON length is 180. This is the exact expanded runtime collection, including
generated `it.each` cases. It excludes the duplicate source cases from the
typecheck project; the combined typecheck count is not used.

The gitignored evidence hashes are:

```text
current-unit-collection.txt          30966be8cce9fb57ff077d6ac79ca6a6d2dac7ddc2a3ba5b6481d76a02b98904
current-integration-collection.txt   e63621b4a8670139f52c6e95534c46fd8bd85c7c6ecf44089b4152e3138157b4
current-e2e-collection.txt           0ea5beebfa2e5c58e51bed890640cbb2aaebcc0e90c983dd128f4cab940452e3
current-vitest-runtime-list.txt      974ba99538610cc9ed515495b4a9abdece2c9545f3aaeace6db1cf4d7f636649
current-vitest-runtime-list.json     0fc8063d167313fc7f9efbbd28af05ca78eb059d782fc88e3698f5ca9649d038
```

The standalone legacy browser/Node scripts remain a separate execution unit.
`web/package.json` invokes 11 `.test.mjs` scripts through `test:legacy`:
`managed-markdown`, `markdown-renderer`, `backlog-ideas`,
`scheduled-tasks-browser`, `worker-configuration-setup`,
`conversation-rest-line`, `conversation-pane-browser`,
`voice-everywhere-browser`, `file-preview-browser`,
`sprint-documents-browser`, and `ticket-guidance-browser`. They are not mixed
into the 1,155 pytest/Vitest case total because one script can contain many
assertions and is not comparable to one collected test case.

No skip marker changed from `d55260b3` to the current tree. The comparison was:

```text
git grep -n -E '(pytest\.mark\.(skip|skipif)|pytest\.skip\(|(^|[^[:alnum:]_])(it|test|describe)\.skip\(|(^|[^[:alnum:]_])(xit|xtest|xdescribe)\()' d55260b3 -- tests web/tests
git grep -n -E '(pytest\.mark\.(skip|skipif)|pytest\.skip\(|(^|[^[:alnum:]_])(it|test|describe)\.skip\(|(^|[^[:alnum:]_])(xit|xtest|xdescribe)\()' HEAD -- tests web/tests
```

After removing Git's revision prefix, both outputs contain the same five marker
declarations. Their five files are byte-unchanged across the comparison. They
still cover the same 20 environment-gated real-provider cases and one
pinned-binary availability case; no case was newly skipped for this reduction.

A zero-context diff audit found seven added-line executable loops in collected
test code. Four are direct rewrites of loops already present in the same
`d55260b3` Ticket-engine cases, with the new proposal writer. Of the three new
loops, one inserts two migration rows before a late-write rollback assertion and
two advance a Ticket through prerequisite fields before ownership assertions.
They are setup for one asserted behavior; they do not fold independent cases
behind one collected definition. Added comprehensions project rows for equality
and identity assertions rather than iterating hidden scenarios. Parameterized
cases remain expanded by pytest collection, and Vitest's JSON collection expands
`it.each`. No count reduction from `d55260b3` is attributable to hiding cases
inside a new loop.

After this collection, the approved implementation review produced three UI repairs.
The corresponding diff changes only the existing
`scheduled-tasks-browser.test.mjs`, `sprint-documents-browser.test.mjs`, and
`ticket-guidance-browser.test.mjs` harnesses. It adds no pytest file, E2E case,
`.test.ts` case, `it.each`, or Vitest configuration change. Those scripts remain three
of the separately counted 11 legacy executions, so the comparable inventory is still
1,155 cases, a 53.9% baseline reduction, with E2E still 19. A broad recollection was
not repeated because the collection inputs did not change.
