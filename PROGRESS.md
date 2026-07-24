# PROGRESS

## Current work cycle (2026-07-24): Bare Panels CLI for agent shells (`t_80u04jna`)

Implementation is isolated on `ticket/t_80u04jna-bare-panels-cli`. The branch now includes
current `staging` (`a2c0ee0`) and provisions a host-level `/usr/local/bin/panels` wrapper
that enters the canonical `/opt/panels/current/bin/panels-launcher`, while deployment and
rollback remain responsible only for switching the `current` pointer.

The wrapper asset, Linux setup install contract, static and hermetic deployment-asset
tests, and environment documentation are implemented. The hermetic test substitutes the
one canonical target in a copied wrapper, exercises a scrubbed agent-like PATH outside
the checkout, and proves exact arguments and a simulated `current` switch across two
releases. The focused deployment-asset module passes (13 tests), and the already-installed
live wrapper passed `--help` plus a read-only Ticket query from `/tmp` with a scrubbed
agent-like environment. Independent implementation review found no code, shell-safety,
test-strength, documentation, scope, or decision violations. Canonical implementation
verification passed before approval: Ruff and MyPy clean, 1,451 unit tests passed, frontend
checks passed, and 127 e2e tests passed (`VERIFY: PASS`).

Current stage: Closeout. The current staging merge had conflicts only in the two memory files;
the product, tests, assets, and docs merged cleanly. Current hypothesis: preserving this Ticket's
current state in `PROGRESS.md` and both Tickets' durable decisions is the complete integration
repair. Next step: independently review the combined diff, run the canonical `./verify` gate on
the prospective staging revision, then advance and push `staging`, confirm the rolling pull
request, and clean up the Ticket worktree and branch. Blockers: none.
