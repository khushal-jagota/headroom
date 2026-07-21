# ACP-00a implementation report

## Result

Implemented the bounded terminal-state wire correction. The closed server union now has an eleventh
`terminal_state` envelope. Its Panels wrapper carries a validated terminal ID, the frozen
`active | released` lifecycle, and the exact Python/TypeScript SDK `TerminalOutputResponse`. The ten
pre-existing server payload shapes and five browser actions were not changed; the canonical terminal
fixture is appended at sequence 18.

No route, runtime, terminal service, Svelte component, dependency, legacy path, configuration, shared
asset, or Hermes-checkout file was changed.

## Files changed

- `src/planner/conversation/contracts.py`
  - added `ConversationTerminalLifecycle` and `ConversationTerminalState`;
  - embedded `acp.schema.TerminalOutputResponse` directly.
- `src/planner/conversation/wire_contracts.py`
  - added `TerminalStateEnvelope` to the discriminated `ServerEnvelope` union.
- `src/planner/conversation/__init__.py`
  - exported the new public contract names.
- `tests/unit/test_conversation_contracts.py`
  - added active/running, active/exited, truncated, and released contract cases;
  - added unknown lifecycle, empty ID, partial SDK output, extra wrapper field, exact serialization,
    common sequence validation, and union coverage cases.
- `tests/support/acp_fixture_writer.py`
  - appended the canonical `terminal_state` fixture using the exact Python SDK response and exit
    status models.
- `tests/fixtures/acp/server-envelopes-v1.json`
  - appended the canonical sequence-18 terminal snapshot.
- `web/src/lib/acp/contracts.ts`
  - imported `TerminalOutputResponse` from `@agentclientprotocol/sdk` and added only the Panels-owned
    wrapper/envelope.
- `web/tests/acp-contracts.test.mjs`
  - added SDK-type ownership, discriminator, exact fixture, and structural type-check coverage.
- `orchestration/tickets/acp-00a-terminal-state-wire/implementation-report.md`
  - this report.

## Test-first evidence

The first focused Python run failed during collection because the tests and fixture writer imported
the not-yet-implemented `ConversationTerminalState`. After implementing the production contract, the
next run failed only because the canonical fixture was intentionally stale. Appending the generated
terminal fixture made the focused contract suite green.

## Verification results

Green:

```text
.venv/bin/ruff check src/planner/conversation/contracts.py \
  src/planner/conversation/wire_contracts.py src/planner/conversation/__init__.py \
  tests/unit/test_conversation_contracts.py tests/support/acp_fixture_writer.py
All checks passed!

.venv/bin/mypy src/planner/conversation
Success: no issues found in 5 source files

.venv/bin/pytest tests/unit/test_conversation_contracts.py -q
..........................................................               [100%]

node web/tests/acp-contracts.test.mjs
acp-contracts.test.mjs: all assertions passed

npm --prefix web test
all listed web tests passed, including acp-contracts.test.mjs
```

`git diff --no-index --check /dev/null <path>` was run for each implementation/test/fixture path.
Each reported no whitespace errors. Exit status 1 is the expected no-index status for a wholly new,
currently untracked ACP-00 file relative to `/dev/null`.

## Existing ACP-00 harness failure outside this correction

The broader command

```text
.venv/bin/pytest tests/unit/test_conversation_contracts.py \
  tests/unit/test_acp_conformance_harness.py
```

reported `61 passed, 11 failed`. Running the conformance test file alone reproduced the same eleven
failures. The primary failure is the existing load evidence recording replay callback position 18
after load response position 17 (`18 < 17` fails); the remaining ten failures cascade because the
unmutated load probe is then also red in each mutation-strength case. This is the SDK callback/load
race already captured by `D-acp-load-means-replay-consumed`, not a terminal-envelope failure. None of
the failing harness/reference files is in ACP-00a's allowlist, so this implementation did not alter
them or manufacture a green result.

## Contract check

No ACP-00a contract contradiction was found. The broader ACP-00 acceptance claiming a green
conformance harness is currently blocked by the independently reproducible load-callback ordering
failure described above.

## Independent review disposition

The one focused implementation review found no production defect and one medium acceptance-proof
gap: the terminal-envelope test exercised only invalid sequence, not every inherited common identity
field. The orchestrator parameterized that test over invalid wire version, employee, entity kind,
entity, ACP session, binding generation, and sequence. The correction passed Ruff, all 64 conversation
contract tests, and the TypeScript contract test. No second broad review was warranted.
