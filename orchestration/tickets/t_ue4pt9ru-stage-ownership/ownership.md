# Slice ownership

All slices implement `contract.md`. No slice may change the frozen public contract.

## Backend

Owns `src/planner/**`, `tests/unit/**`, `tests/support/**`, `tests/typing/**`, and backend/CLI e2e coverage. It defines storage/migration, domain contracts, one ownership resolver and writer path, runtime eligibility/settlement, Chief reconciliation, API/CLI, views, events, and backend tests. It must not edit `web/**`, `skills/**`, or `docs/**`.

## Frontend

Owns `web/src/**`, `web/tests/**`, and `tests/e2e/test_stage_ownership_frontend.py`. It consumes the frozen wire shape, preserves the Ticket layout, and owns the visible owner/execution-route controls, Continue wording, paired status, Workspace/Review presentation, resource-event completeness, and browser proof. It must not edit backend, skills, or docs.

## Skills and docs

Owns `skills/**` and `docs/**`. It describes the frozen lifecycle and updates new-worker authoring guidance without inventing backend or UI behavior.

## Parent integration

The parent owns `contract.md`, review records, `PROGRESS.md`, `decisions.md`, serial integration, cross-slice repairs, complete-diff review, final `./verify`, and the Ticket implementation proposal. Slices do not commit or run `./verify`.
