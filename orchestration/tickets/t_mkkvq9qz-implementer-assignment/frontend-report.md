# Frontend, worker-skill, and docs implementation report

## Changed

- Added the nullable fixed `Implementer` union to `TicketDetail`.
- Added one existing `EnumPill` to the Ticket facts row with `keyLabel="implementer"`, the stable `data-implementer` hook, exact frozen labels, and direct set/change/clear PATCH behavior through the existing invalidation path. No component, CSS, layout, or edit/save/cancel flow was added.
- Added focused browser coverage for the unassigned value, Khushal assignment, change to Hermes with Codex, clear, reload persistence, and unchanged Ticket state and `ticket_status` after every edit.
- Added the agreed execution-route guidance to `skills/panels-worker/SKILL.md` and kept `docs/tickets-and-gates.md` to assignment, prompt-visibility, ordinary-edit, and Plan-acceptance mechanics.

## Test-first evidence

Production source and documentation were unchanged when the focused test was first added and run.

**RED**

```text
.venv/bin/python -m pytest tests/e2e/test_flows_a.py::test_ticket_implementer_assignment_edits_in_facts_without_changing_workflow -q
```

Failed for the intended missing behavior:

```text
assert page.locator(implementer).count() == 1
E       AssertionError: assert 0 == 1
selector='.ticket-facts [data-implementer]'
```

**GREEN**

```text
.venv/bin/python -m pytest tests/e2e/test_flows_a.py::test_ticket_implementer_assignment_edits_in_facts_without_changing_workflow -q
# .                                                                        [100%]
```

The same command passed again after the final production build.

## Verification

- `npm --prefix web run check` — passed with 0 errors and the three existing `TicketRoute.svelte` initial-`id` capture warnings.
- `npm --prefix web run build` — passed; only the existing runtime asset and initial-`id` warnings were reported.
- `npm --prefix web test` — passed the frontend event-mapping static test.
- `.venv/bin/ruff check tests/e2e/test_flows_a.py` — `All checks passed!`
- `git diff --check` — passed with no output.
- Per dispatch, `./verify` was not run.

## Integration boundary

This slice consumes the backend-owned nullable `implementer` field and direct `GET/PATCH /api/tickets/{id}` contract but does not modify backend files. The focused Playwright GREEN crossed the current merged frontend/backend boundary successfully, and the observed backend files remained stable through the final run. There is no current blocker. If backend-owned files change after this handoff, the parent should rerun the exact focused Playwright command against the final merged tree before integration.
