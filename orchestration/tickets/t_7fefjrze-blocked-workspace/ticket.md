# Ticket `t_7fefjrze`: Collapse Blocked Workspace sections by default

## Contract

- `web/src/routes/BoardRoute.svelte` owns the Workspace stage-section default state.
- `tests/e2e/test_blockers_frontend.py` owns the acceptance proof.
- Do not change canonical Ticket stages or the shared Disclosure component.

## Acceptance

- Every Blocked section starts closed.
- Opening a Blocked section reveals its Tickets normally.
- An ordinary active stage remains open by default.
