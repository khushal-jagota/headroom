# ACP-05 implementation review resolution

Both accepted P1 findings are resolved.

## P1 — preparation failure before `session/fork`

Preparation now distinguishes exact source validation from pre-admission ownership failure. Once the
exact live source is validated, missing fork capability and every later preparation failure invalidate
and detach that source, clear any candidate reservation, preserve durable binding N, and report a
generation-fatal failure. A later normal attach starts a fresh child and loads authoritative N. An
already stale or non-owned handle may still fail non-fatally before exact source validation.

The revised regression failed first because missing capability returned `generation_fatal=False`; it now
passes for both missing capability and a fork deadline.

## P1 — publication deadline and fail-closed settlement

Every publication-gate acquisition in the compaction path now uses the transaction's original absolute
deadline: initial and final preparation validation, normal candidate publication, unused-candidate
discard, external-winner publication, abort, and final failed/cancelled settlement.

If the gate cannot be acquired by that deadline, or the operation is cancelled while waiting, settlement
does not wait on the blocked gate again. Under the registry state lock it removes the exact source record
and callback identity, removes candidate callback identity/reservation and prepared state, releases the
lifecycle reservation, and detaches both children. Initial-gate failure performs that retirement only
when the handle still matches the current record; stale or non-owned handles remain non-fatal. The
admitted N sink may then finish independently. Normal publication still acquires the gate and therefore
preserves N-sink quiescence before N+1.

Five held-gate regressions failed before the correction and now pass:

- initial preparation publication validation expires before candidate allocation and retires exact N;
- final preparation revalidation expires and fully settles;
- normal candidate publication is cancelled and fully settles after durable N+1;
- external-winner publication expires and fully settles after durable external N+1;
- abort after the deadline completes and fully settles while N still holds publication.

## Verification

```text
$ .venv/bin/pytest -q --tb=short tests/unit/test_acp_employee_registry.py
....................................................                     [100%]

$ .venv/bin/ruff check src/planner/conversation/employee_registry.py tests/unit/test_acp_employee_registry.py
All checks passed!

$ .venv/bin/mypy --strict src/planner/conversation/employee_registry.py
Success: no issues found in 1 source file
```

The 300-second production constant was not changed. Canonical `./verify` was not run, as required by the
review-fix dispatch.
