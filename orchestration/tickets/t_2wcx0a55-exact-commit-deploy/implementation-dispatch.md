# Implementation dispatch — t_2wcx0a55

You are the leaf implementer. Work directly in this Ticket worktree; do not delegate. Implement
`implementation-plan.md` against `contract.md` and the accepted repository Ticket fields.

## Required sequence

1. Read `AGENTS.md`, `CLAUDE.md`, the contract, plan, current environment/backup/runtime code, and
   neighboring tests before editing.
2. Follow strict vertical TDD. For each tracer behavior, edit the focused test first, run it to a
   meaningful missing-behavior RED, add the minimum production code, and rerun the exact test to GREEN.
   Preserve exact RED/GREEN commands and decisive output in `implementation-report.md`.
3. Complete Slice 1, run its focused gate, and commit it.
4. Complete Slice 2, run its focused gate, and commit it.
5. Complete Slice 3, run its focused gate, and commit it.
6. Run the whole-tree stale-assumption sweep and focused combined gates. Do not run canonical
   `./verify`; the parent owns independent review and the one final verifier.
7. Write `implementation-report.md` with changed files, design choices, exact evidence, and any
   remaining blocker. Leave the worktree clean with coherent commits.

## Boundaries

- Live deploys only an exact full `main` SHA. Never read or deploy `staging` after merge.
- The release is a host-native application tree with no Git metadata, branch, remote, or development
  workflow. GitHub Actions' temporary source checkout is not a product environment.
- The current Mac and later VPS use the same release/deploy protocol. Do not make the artifact
  Linux-only; platform service inputs may differ.
- Staging remains a persistent development checkout. Do not remove its repository-root behavior.
- Integrate the existing database backup command; do not reimplement backups or automatic state
  restore.
- A production service identity may write persistent runtime paths, never release roots or deployment
  controls. Repository assets describe installation intent but do not claim installed enforcement.
- Current GitHub plan cannot require PR checks. Report PR verification and enforce the gate again on
  `main` before deployment; do not claim branch protection exists.
- Do not install services, register runners, modify GitHub settings, restart live, alter live state,
  merge, push, or run Closeout.
- Do not edit unrelated frontend product behavior, Tickets, migrations, or other worktrees.

Stop and report if the contract cannot be satisfied without crossing one of these boundaries.
