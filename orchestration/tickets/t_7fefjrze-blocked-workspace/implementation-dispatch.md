# Implementation dispatch

## Allowed files

- `web/src/routes/BoardRoute.svelte`
- `tests/e2e/test_blockers_frontend.py`

## Required behavior

- Use the existing stage-section model to make synthetic Blocked groups start closed.
- Keep ordinary active stages open and leave shared Disclosure behavior unchanged.
- In the blocker browser test, wait for a visible Workspace element, assert Blocked is
  closed, open it, prove its Tickets are visible, and assert an ordinary active stage
  remains open.

## Gate

Run the focused `tests/e2e/test_blockers_frontend.py` suite. Do not commit.
