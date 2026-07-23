# Implementation report — t_2wcx0a55 correction pass

## Scope

This second correction pass addresses the remaining executable-path blockers without deploying,
installing host services, registering runners, changing GitHub settings, or running canonical
`./verify`.

Production release validation now has an explicit runtime mode requiring `.venv/bin/python`, an
executable `bin/panels-launcher`, built `web/dist`, and `agent_backends/node_modules`; low-level
fixture-manifest validation remains available without that mode. Build, launcher, deployment,
and same-SHA reuse use the runtime mode.

Initial deployment now accepts an explicitly absent/new database, but an existing database
requires an operator-established baseline SHA and is backed up before switching `current`.
Baseline backup failure leaves no pointer or restart and records `initial_failed`. Existing valid
same-SHA releases remain idempotent; incomplete same-SHA artifacts are rejected.

The verify workflow pins setup-python/setup-node and installs Python editable plus both npm trees
before `./verify`. The deploy workflow is one serial `[self-hosted, production]` job with no
artifact transfer; it proves `github.sha` and uses the exact release path through deployment.
Checked-in host setup expresses non-root `panels-deploy` ownership for release controls, while
`panels-live` only reads and executes releases. `current` remains a deploy-created symlink.

## Review disposition

| Finding | Disposition |
| --- | --- |
| P0 fresh runner/build/publish/validate, environment loss, modes, launchd verbs | Corrected in `.github/workflows/deploy.yml`, `release-build`, launcher allowlist, service assets, and `service-control.sh`. |
| P1 initial deployment and failed state reporting | Corrected in `deploy_release`; initial failure removes `current`, records `initial_failed`, and reports the operator state path. |
| P1 process-local lock | Corrected with `fcntl.flock` in `deployment_lock`; a forked-process regression proves blocking. |
| P1 containment, symlinks, exact SHA, expected health SHA | Corrected in manifest/deployment validation and `/api/health`; regressions cover exact directory/root and missing health expectation. |
| P1 shell manifest parsing and source digest meaning | Corrected with `release-identity`/`backup-current` CLI and separate `source_digest`/`artifact_digest`. |
| P1 validation/switch failure records | Corrected with durable failed records around validation and pointer switching; regression covers pointer failure. |

## Exact RED/GREEN evidence

1. RED: after adding release/deployment/runtime/asset regressions, the focused command reported
   8 failures, including missing `digest_release_artifact`, missing `release_root`/lock APIs,
   missing expected health SHA, unpinned workflow/build step, and invalid launchd/backup assets.
2. GREEN: `.venv/bin/pytest -q tests/unit/test_deployment_assets.py tests/unit/test_release.py tests/unit/test_deployment.py tests/unit/test_environment_linux.py tests/unit/test_environment_cli.py` — `41 passed`.
3. GREEN: `.venv/bin/ruff check src/planner/environments/release.py src/planner/environments/release_launcher.py src/planner/environments/deployment.py src/planner/environments/cli.py tests/unit/test_release.py tests/unit/test_deployment.py tests/unit/test_deployment_assets.py` — `All checks passed!`.
4. GREEN: `.venv/bin/mypy --strict src/planner/environments/release.py src/planner/environments/release_launcher.py src/planner/environments/deployment.py src/planner/environments/cli.py` — `Success: no issues found in 4 source files`.
5. GREEN: `sh -n ops/panels-environments/setup-accounts.sh ops/panels-environments/service-control.sh ops/panels-environments/pre-deploy-backup.sh` and `git diff --check` — no output/errors.
6. Parent spot-check correction: both workflows now create the repository `.venv`, install with
   `.venv/bin/python`, and use that interpreter for `./verify` and release construction. GREEN:
   `.venv/bin/pytest -q tests/unit/test_deployment_assets.py && .venv/bin/ruff check
   tests/unit/test_deployment_assets.py && git diff --check` — `7 passed`; Ruff passed; diff check
   emitted no errors.

Canonical `./verify` remains reserved for the parent/orchestrator.
