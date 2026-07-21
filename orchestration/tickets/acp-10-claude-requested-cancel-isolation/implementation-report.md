# ACP-10 Claude requested-cancel isolation implementation report

## Outcome

Claude requested Stop and Send Now now keep the existing source quarantine closed after a normal
terminal `PromptResponse`. The broker completes the existing same-binding fresh-child recovery
transaction before it publishes reusable idle or starts the captured successor. The ACP session ID
and binding generation stay unchanged; the child generation, child object, and record identity are
replaced.

`BackendTurnCapabilities` owns the exact provider fact as
`requires_fresh_child_after_requested_cancel`. It defaults to false. The locked Claude definition is
true, while Hermes and Codex remain false without backend-name branching.

## Implementation

- The normal terminal-response path checks the active definition's capability before resuming the
  requested-cancel quarantine.
- A capable requested Stop or Send Now enters the same replacement path already used when a
  requested-cancel prompt unwinds exceptionally. The helper was generalized to accept the optional
  returned response.
- The recovered tracked turn now settles as `TrackedTurnResult("interrupted", response=response)`.
  Exceptional recovery still supplies `None`; normal capable recovery preserves the exact returned
  `PromptResponse`.
- Existing transition begin, exact-child replacement, unchanged-session private load, replay/ready
  commit, queued-handle retargeting, Send Now human echo, successor start, and failure handling were
  reused unchanged.

## Proof

- A Stop regression returns a normal cancelled response, proves the quarantine is committed rather
  than resumed, preserves binding/session and tracked response, and advances the queued prompt only
  on the fresh child.
- A Send Now regression proves the captured successor and human boundary move to the fresh child and
  start exactly once after commit, with the predecessor's response preserved.
- The false-capability control proves normal requested cancellation still resumes the quarantine and
  advances the queue on generation N without replacement.
- Claude definition coverage freezes the capability true. Hermes and Codex definition coverage
  freezes it false.
- The selected requested-cancel broker/hub tests, provider-definition tests, scoped Ruff, strict
  Mypy, and scoped diff check pass. Exact commands and output are in `focused-checks.txt`.

## Scope

Only the contract's backend capability, Claude definition, broker, focused tests, and ticket evidence
files changed. No provider package, SDK child, hub/registry implementation, public wire or browser
code, schema, generated distribution, live server, or browser session was touched. Canonical
`./verify` was intentionally not run; ACP-10 retains that final gate.
