# t_hs02 implementation report

## Result

Chief, Ticket, and Day human chat now submit through one ordered live-session ingress. Human
message, model-backed command, image, and Stop operations no longer open a raw per-call session
drain. The existing transcript, activity, token, system, and completion projection is unchanged.

The session boundary now returns Hermes's immutable `streaming`, `queued`, or `steered` receipt
with a bounded consequence stream. An interrupted turn remains registered until its late terminal
observation arrives, so an immediate next send cannot consume the old completion.

Employee `run_ticket_step()` deliberately keeps its compatibility resume/drain behavior for
`t_hs03`.

## RED / GREEN evidence

- RED: the first consequence contract test failed during collection because
  `AcceptedSubmission` did not exist.
- GREEN: the session-layer Stop A -> submit B -> late interrupted A -> B lifecycle regression
  passes for B dispositions `streaming` and `queued`.
- GREEN: the same sequence through `SharedGateway` proves the human path writes only
  `session.create`, A submit, interrupt, and B submit. It does not resume, locally wait for idle,
  or consume the next session-wide completion.
- GREEN: FastAPI/SQLite regressions pass for Ticket, canonical Day, and Chief. The paused A turn
  stays interrupted, the old completion is absent, and B has exactly one assistant reply.
- GREEN: message, model-backed command, and image paths acknowledge the exact worker-context
  key/revision pair once for native `streaming`, `queued`, and `steered` acceptance.
- GREEN: transport-unknown message/image delivery retains context, sends one prompt only, and
  performs no speculative image detach.
- GREEN: concurrent same-session image submissions preserve `image.attach` immediately followed
  by their own `prompt.submit` write.
- GREEN: a known image prompt rejection attempts detach exactly once before admitting B; even when
  detach fails, A preserves the original prompt RPC error, Stop stays available, and B proceeds.
- GREEN: the slow-fake browser regression pauses A, immediately sends B, remounts the ticket, and
  observes exactly one B reply with the current UI.

Focused gates before full verification:

- `tests/unit/test_minds_sessions.py tests/unit/test_minds.py`: 69 passed.
- `tests/unit/test_chat*.py tests/unit/test_return_for_revision.py`: 90 passed.
- Ticket/Day/Chief FastAPI race: 3 passed.
- Browser pause/send/remount regression: 1 passed.
- Ruff, Mypy, and `git diff --check`: passed.

## Full verification

The first full run correctly caught an employee-scope regression:

- failing test: `test_settlement_doorbell_drives_the_auto_advance_chain`
- cause: live-session reuse had been applied to employee steps before `t_hs03`, removing the
  expected second-step `session.resume`
- correction: live-session reuse is now explicit for human chat only; the focused employee
  auto-advance regression passed afterward

Final `PYTHONPATH="$PWD/src" ./verify` output:

```text
ruff: All checks passed!
mypy: Success: no issues found in 104 source files
unit: 374 passed, 3 warnings
build check: ok
frontend: ok
e2e: 57 passed

[verify] gate ruff: ok
[verify] gate mypy: ok
[verify] gate unit suite: ok
[verify] gate build check: ok
[verify] gate frontend: ok
[verify] gate e2e suite: ok

VERIFY: PASS
```

The warnings are the repository's existing Starlette/httpx, `TestClock` collection, and Svelte
route-capture warnings.
