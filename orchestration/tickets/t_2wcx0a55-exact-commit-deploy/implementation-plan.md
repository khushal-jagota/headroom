# Implementation plan — t_2wcx0a55

## Slice 1: release identity and runtime proof

1. Add focused tests for a typed full-SHA release identity carried from a validated manifest through
   the scrubbed live launch environment into `/api/meta`. Production release launch rejects missing or
   malformed identity; ordinary development/test startup remains explicit and does not invent one.
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
   read the prior SHA from its validated release manifest, back up with that revision, switch pointer,
   restart, prove requested SHA, record success. Remove every live-Git-checkout revision lookup.
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
   `current` release launcher. The service sees the release root read-only; database, managed files,
   Hermes/provider state, configuration, credentials, logs, and backups are explicit external writable
   paths. Only the operator/deploy identity can write releases or deployment controls.
2. Add matching macOS launchd inputs and one stable operator service-control entrypoint. Keep the
   production runner identity separate from the running live identity. Treat both platform assets as
   install intent; Closeout establishes and proves actual ownership and permissions.
3. Add GitHub workflows: PR verification reports `./verify`; `main` push checks out `github.sha`
   explicitly, proves the checkout's full `HEAD` equals it, re-runs the release gate, and then serially
   invokes deployment on the one production self-hosted runner with that same SHA. Document that the
   current GitHub plan cannot make PR checks branch-required.
4. Update environment, backup, release, and operator docs to remove stale live-checkout revision
   lookup and explain local-to-VPS relocation.

Focused gate: workflow/static asset tests, environment Linux/CLI tests, docs links, Ruff, strict Mypy.

## Integration and handoff

1. Sweep for stale live-checkout, `PANELS_LIVE_REPOSITORY`, `/opt/panels/live`, and Git-derived deployed
   revision assumptions. Preserve staging's repository checkout behavior.
2. Run independent contract and security review. Address every finding and re-review concrete fixes.
3. Stage only Ticket-owned changes; run `git diff --cached --check` and inspect untracked files.
4. After the implementer stops, the parent/orchestrator runs the one canonical `./verify` in the
   isolated worktree and preserves its full transcript under `data/verify/`. The implementer runs only
   the focused gates named above.
5. Commit the verified Ticket branch. Do not merge, deploy, restart live, install service definitions,
   register a runner, or change GitHub settings during Implementation.
