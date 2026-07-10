# Slice ownership

All slices implement `contract.md`; no slice changes the contract.

## Backend and migration

Owns `src/planner/**` and backend/unit/CLI tests. It defines the canonical wire shape, lifecycle logic, schema migration, runtime behavior, queues, CLI, seed/import behavior, and strict Chief external-work prefixes. It must not edit `web/src/**`, `skills/**`, or `docs/**`.

## Frontend

Owns `web/src/**` and lifecycle-specific browser assertions. It consumes the frozen backend shape: five fields (`success`, `approach`, `plan`, `implementation`, `closeout`) and the linear states ending in `done`. It must preserve existing controls and layout rather than redesigning the ticket or review screens.

## Skills and docs

Owns `skills/**`, `docs/**`, and skill-content/provisioning assertions. It describes separate Implementation and Closeout work without adding assignment rules or changing the established artifact-planning guidance.

## Integration

The parent integrates against the shared contract, sweeps the merged tree for obsolete lifecycle symbols, runs focused cross-slice checks, sends the complete diff to Codex, resolves findings, and runs `./verify` once after the tree settles.
