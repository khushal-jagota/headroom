# ACP-05 compaction capture deadline implementation review

Status: **READY**

## Finding

### [P1] Preserve the cause and phase of immediate non-timeout compaction failures

The five-minute timeout path now has exact phase, budget, binding disposition, invalidation, logging,
and visible-boundary evidence. The ordinary failure path still hides the information the owner asked
to retain. When private fork load fails and exact-original restoration also fails,
`employee_registry.py:1086-1116` discards the original `capture_error`, raises a generic
`ConversationRuntimeGenerationFatal`, and retains neither `capture_phase` nor either concrete failure
in the surfaced reason. `turn_broker.py:791-799` then catches that exception without binding it, does
not log it, and publishes `Conversation runtime generation failed during compaction`. The same generic
collapse occurs for other immediate capture failures at `turn_broker.py:800-807`. Existing assertions
at `tests/unit/test_conversation_turn_broker.py:1857-1861` and `:1961-1967` currently lock in that opaque
result.

This means extending the deadlock breaker to five minutes does not deliver the owner requirement that
a rare non-hang failure say *why* it failed. Preserve an exact phase plus the concrete backend/restore
cause in the server log and a truthful cause in the tracked result and failed compaction boundary;
keep the existing fail-closed child invalidation and durable-binding disposition. Add a regression for
an immediate private-load or normalization failure proving it is not reported as the generic runtime
generation message.

## Verified by inspection

- Production/default capture timeout is 300 seconds and shutdown remains 10 seconds.
- The production actor mints one absolute deadline and passes it through hub admission, fork/private
  load, normalization, CAS/resolve, restore/adoption, actor rekey, browser commit, and completion.
- Deadline expiry distinguishes stayed N, committed N+1, and unresolved N/N+1 dispositions.
- Deadline-fatal paths invalidate the uncertain runtime, and registry settlement releases the employee
  gate in `finally`.
- Successful capture still installs the N+1 binding and preserves the original shutdown behavior.

No tests were run for this read-only review.

## Finding disposition

**Addressed.** `ConversationCompactionCaptureFailed` now carries the exact phase, concrete primary
exception type/message, optional required restore/abort cause, and generation-fatal disposition.
Registry recovery preserves both failures without a new deadline; the strategy proxy aborts only a
pre-commit normalization transaction and does not manufacture a stale post-CAS abort. The broker
publishes and logs the same concrete reason at hub admission, capture, actor rekey, and browser commit
instead of substituting generic capture/generation text.

Deterministic regressions cover an immediate normalization failure, normalization plus failed abort,
CAS plus failed restore, and a primary timeout plus non-timeout restore failure. The existing
successful N→N+1 adoption path consumes one transition and remains green. The corrected focused gate
passes Ruff, strict Mypy, and all 134 named Python tests; exact output is in `focused-checks.txt`.

## Correction check

Status: **ADDRESSED**

The original generic-message finding is mostly addressed: immediate `RuntimeError` paths now retain
phase, exception type/message, paired restore cause, boundary/tracked/log equality, and the successful
post-CAS path consumes its transition once without a false proxy abort. The 300-second default and one
absolute deadline are unchanged.

One concrete P1 remains. `AcpEmployeeRegistry._await_before_deadline()` returns `task.result()` at
`employee_registry.py:1585`, so an operation's own immediate `TimeoutError` is indistinguishable from
the helper's actual deadline expiry at `:1581-1584`. `prepare_compaction_capture()` then treats every
such exception as budget exhaustion at `:1095-1106`. The new regression demonstrates the false report:
it injects `TimeoutError("backend private load timed out")` while the deadline is still 300 seconds away
at `tests/unit/test_acp_employee_registry.py:1321-1329`, yet expects `exceeded its configured 300-second
budget` and drops the primary exception type/message. This directly contradicts the requirement to
know that a rare compaction failed for a backend reason rather than because Panels' five-minute
deadlock breaker elapsed.

Use a distinct internal deadline-expiry signal (or otherwise distinguish helper expiry from an
operation-raised `TimeoutError`) and make this regression expect the exact primary `TimeoutError` plus
the paired restore failure, without deadline-expiry wording. Apply the same distinction to the
normalization and actor deadline helpers so phase classification is truthful throughout.

No tests were run for this correction check.

### Correction-check disposition

**Addressed.** Panels now raises the internal `ConversationWaitDeadlineExpired` only when one of its
absolute-deadline wait helpers wins. Compaction conversion catches only that signal. A backend,
protocol, or persistence coroutine that finishes by raising ordinary `TimeoutError` therefore flows
through `ConversationCompactionCaptureFailed` with its exact phase and `TimeoutError: <message>`;
unrelated callers that already catch `TimeoutError` keep the same behavior because the internal signal
is a subclass.

The corrected public registry regression uses a deadline nearly 300 seconds in the future and proves
an immediate private-load `TimeoutError` plus failed original restore retains both causes and never
uses configured-budget wording. The separate injected wait-expiry regression remains green. The full
134-test focused gate, scoped Ruff, and strict Mypy pass unchanged.

## Final narrow check

Status: **READY**

`ConversationWaitDeadlineExpired` is raised only by the registry, strategy-proxy, and actor wait
helpers when their timer wins. All compaction deadline conversions catch that typed signal rather
than raw `TimeoutError`. An operation-raised raw `TimeoutError` therefore reaches
`ConversationCompactionCaptureFailed` with exact phase, exception type/message, and paired recovery
cause; the corrected 300-seconds-remaining regression explicitly rejects configured-budget wording.
The existing hung fork, private-load, CAS, restore, and browser-commit regressions continue to require
the dedicated deadline exception and exact budget diagnostics. No remaining violation was found in
this narrow check.

No tests were run for this final read-only check.
