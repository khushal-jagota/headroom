# ACP-00b implementation report

## Result

Implemented the bounded Steer-capability wire correction. `ConnectionPayload` now requires a real
Python `bool` / TypeScript `boolean` named `supports_steer` / `supportsSteer`. The field remains part
of the existing `connection` payload; no event, browser action, capability dictionary, backend key,
or browser inference was added.

The canonical fixture keeps the pre-existing payloads and their sequences unchanged. The existing
reset snapshot now declares Steer support, and ready/closed/error connection snapshots are appended
at sequences 19–21 with explicit `true`, `false`, and `false` values. The eleven server
discriminators, five browser actions, and exact terminal-state payload remain unchanged.

## Files changed

- `src/planner/conversation/wire_contracts.py`
  - added the required field-level-strict `supports_steer: bool` to `ConnectionPayload`.
- `tests/unit/test_conversation_contracts.py`
  - updated every connection construction;
  - covered all four connection states with explicit capability values;
  - proved strict rejection of a missing, null, string, integer, or extra capability field.
- `tests/support/acp_fixture_writer.py`
  - made every canonical connection payload explicit and added ready/closed/error examples.
- `tests/fixtures/acp/server-envelopes-v1.json`
  - regenerated from the canonical Python writer.
- `web/src/lib/acp/contracts.ts`
  - added required `supportsSteer: boolean` to the Panels connection wrapper.
- `web/tests/acp-contracts.test.mjs`
  - asserted the four canonical state/capability pairs;
  - used TypeScript error expectations to prove missing, null, string, integer, and extra fields do
    not satisfy `ConnectionPayload`.
- `orchestration/tickets/acp-00b-steer-capability-wire/implementation-report.md`
  - this report.

`src/planner/conversation/__init__.py` did not require a change because it already exports the
unchanged `ConnectionPayload` public type.

## Test-first evidence

The first focused Python contract run failed in six places before production implementation:
`supports_steer` was rejected as extra input for valid connection constructions, while the missing
field case incorrectly validated. Adding the required production field made those public-seam tests
green before the fixture was regenerated. A follow-up red case removed call-level strictness and
exposed Pydantic's default string/integer coercion; making the field itself strict closed that gap, so
all callers now require a real boolean rather than depending on a validation flag.

## Focused verification

```text
.venv/bin/ruff check src/planner/conversation/wire_contracts.py \
  src/planner/conversation/__init__.py tests/unit/test_conversation_contracts.py \
  tests/support/acp_fixture_writer.py
All checks passed!

.venv/bin/mypy src/planner/conversation
Success: no issues found in 5 source files

.venv/bin/pytest tests/unit/test_conversation_contracts.py -q
.....................................................................    [100%]

node web/tests/acp-contracts.test.mjs
acp-contracts.test.mjs: all assertions passed

npm --prefix web run check
svelte-check found 0 errors and 0 warnings

npm --prefix web test
all listed web tests passed, including acp-contracts.test.mjs

git diff --no-index --check /dev/null <each changed implementation/test/fixture path>
ACP-00b diff check passed
```

## Scope and contract check

No ACP-00b contract contradiction was found. No route, runtime, dependency, Svelte component, shared
asset, configuration, database, legacy path, documentation, memory file, or Hermes-checkout file was
changed. No commit was created, as required by the dispatch.
