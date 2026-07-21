# ACP-00a implementation review — round 1

## Finding

### Medium — the terminal envelope test does not prove all common identity validation

`tests/unit/test_conversation_contracts.py:623-665` is named as if it covers the common envelope
identity and sequence contract, but its only invalid-envelope case changes `sequence` to zero. It
does not assert rejection of an invalid `wireVersion`, empty `employeeId`, invalid `entityKind`,
empty `entityId`, empty `acpSessionId`, or non-positive `bindingGeneration`. Those rules currently
work through `_ServerEnvelope` inheritance (a read-only spot check confirmed each mutation is
rejected), so this is not a production-model defect. It is nevertheless a missing part of named
acceptance 2, and a later regression in the terminal variant's common identity validation would not
be caught by the ACP-00a-specific proof.

Parameterize the terminal-envelope test over every common invalid identity/generation/sequence case
and validate each mutated wire object through `SERVER_ENVELOPE_ADAPTER` with the same strict,
alias-only settings.

## Confirmed correct

- Python embeds `acp.schema.TerminalOutputResponse`; TypeScript imports
  `TerminalOutputResponse` from the pinned `@agentclientprotocol/sdk`. Their output, truncation,
  optional exit-status, and `_meta` shapes agree at the pinned versions.
- The Panels wrapper forbids extra wrapper fields and validates the exact non-empty terminal ID and
  `active | released` lifecycle. Running, exited, truncated, and released snapshots serialize as
  required.
- `terminal_state` is present in the Python and TypeScript closed server unions and public exports.
  The original ten server discriminators and five browser actions remain present and unchanged; the
  terminal fixture is appended at sequence 18 and structurally type-checks against the TypeScript
  union.
- Focused Ruff, mypy, Python contract tests, and the TypeScript contract test passed. The broader
  conformance harness remains timing-sensitive at the pre-existing load-callback ordering check: one
  run passed and the bounded repeat reproduced `max(load_replay_positions) == 18` after response
  position 17, with the expected cascading mutation failures. ACP-00a changes none of the harness,
  process, observer, or reference-subject files, so that failure is outside this diff.

## Verdict

**NOT READY** — the wire implementation is conformant, but the explicitly required common-identity
acceptance proof is incomplete.
