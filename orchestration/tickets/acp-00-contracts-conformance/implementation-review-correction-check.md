# ACP-00 implementation review — correction check

## Finding 1 — RESOLVED

`validate_browser_action` now validates raw actions with `strict=True`, `by_alias=True`, and
`by_name=False` at `src/planner/conversation/wire_contracts.py:277-285`. The raw ACP notification path
applies the same boundary at `src/planner/conversation/wire_contracts.py:298-304`. Regression coverage
at `tests/unit/test_conversation_contracts.py:609-658,695-704` proves snake-case ACP/browser keys fail
closed and string-valued integer fields are rejected. A fresh narrow check confirmed both malformed
browser examples raise `ValidationError` and both malformed ACP alias examples produce
`ProtocolUpdateRejectedEnvelope`.

## Finding 2 — RESOLVED

`ThoughtEvidence` now separates live/replay thought chunks and live/replay reduced assistant outputs at
`tests/support/acp_conformance.py:28-35`. `assert_typed_thought` compares the replay reduction with the
live reduction and rejects individual or concatenated thought content in either assistant output at
`tests/support/acp_conformance.py:151-166`. The reference subject runs the same typed transcript
reducer over both streams at `tests/support/acp_reference_subject.py:613-625,777-817`, and the targeted
mutation now corrupts the replay assistant output at `tests/support/acp_conformance.py:349-355`.

## Finding 3 — RESOLVED

The reference exercise now performs refresh through a stored `ConversationSessionBinding` and the
official connection's `load_session` at `tests/support/acp_reference_subject.py:193-206,251-277`.
Declared steer and broker-owned queue/send-now are executed by concrete test-only implementations at
`tests/support/acp_reference_subject.py:472-520,583-595,684-695`. Explicit and automatic compaction
states are emitted by the scripted subprocess at `tests/support/acp_scripted_agent.py:131-162,283-324`
and reduced by the observer at `tests/support/acp_reference_subject.py:523-574,697-703`. Finally, SDK
callbacks enqueue into one consumer and broadcast through a separately recorded step at
`tests/support/acp_reference_subject.py:65-125`; callback evidence independently derives wire,
reduction, and broadcast order at `tests/support/acp_reference_subject.py:705-716`.

The corrected focused evidence records the complete focused sequence green. This correction check
also reran the two focused Python test files: `66 passed in 0.66s`. `./verify` was not run.

## Verdict

**READY** — all three round-one findings are resolved.
