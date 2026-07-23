# Implementation plan — t_2wcx0a55

## Slice 1: release identity and runtime proof

1. Add focused tests for a configured release SHA in the scrubbed environment and `/api/meta`.
2. Add the smallest typed release manifest and validation/build API. Start with an exported-source
   tracer test that rejects a non-matching source `HEAD`, produces no `.git`, and records a verified
   full SHA plus source digest.
3. Extend the host-native build path vertically until a real release contains its own Python runtime,
   frontend build, agent-backend dependencies, and imports `planner` from that release.
4. Generalize the live runtime resolver from “repository checkout” to “application runtime root” while
   preserving staging's checkout contract and all existing wrong-root protections.

Focused gate: release/runtime/config/server unit tests, Ruff, strict Mypy, and an exact real-release
smoke using temporary roots.

## Slice 2: deployment transaction and recovery

1. Add a fake service-controller/health-client tracer test proving the full order: validate candidate,
   backup current revision, switch pointer, restart, prove requested SHA, record success.
2. Add no-action failure cases before the switch, then post-switch health failure with atomic code
   rollback and proof of the prior SHA.
3. Add independent bounded cutoffs, deployment serialization, durable release records, idempotent
   same-SHA behavior, and explicit rollback-failure evidence. Keep state restore absent.
4. Expose the typed operation through a narrow operator CLI. Validate every path and SHA before any
   subprocess or filesystem mutation; never use `shell=True`.

Focused gate: deployment/CLI/backup integration tests, Ruff, strict Mypy, and a temporary-root local
build/deploy/rollback exercise with a fake service boundary.

## Slice 3: host services and GitHub handoff

1. Replace the live checkout assumptions in generated and checked-in Linux inputs with the stable
   `current` release launcher and read-only release path.
2. Add matching macOS launchd inputs and one stable operator service-control entrypoint. Keep the
   production runner identity separate from the running live identity and document the installation
   boundary honestly.
3. Add GitHub workflows: PR verification reports `./verify`; `main` push re-runs the release gate,
   then serially invokes deployment on the one production self-hosted runner with `github.sha`.
   Document that current GitHub plan cannot make PR checks branch-required.
4. Update environment, backup, release, and operator docs to remove stale live-checkout revision
   lookup and explain local-to-VPS relocation.

Focused gate: workflow/static asset tests, environment Linux/CLI tests, docs links, Ruff, strict Mypy.

## Integration and handoff

1. Sweep for stale live-checkout, `PANELS_LIVE_REPOSITORY`, `/opt/panels/live`, and Git-derived deployed
   revision assumptions. Preserve staging's repository checkout behavior.
2. Run independent contract and security review. Address every finding and re-review concrete fixes.
3. Stage only Ticket-owned changes; run `git diff --cached --check` and inspect untracked files.
4. Run one canonical `./verify` in the isolated worktree and preserve its full transcript under
   `data/verify/`.
5. Commit the verified Ticket branch. Do not merge, deploy, restart live, install service definitions,
   register a runner, or change GitHub settings during Implementation.
