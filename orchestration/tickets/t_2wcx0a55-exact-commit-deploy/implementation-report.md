# Implementation report — t_2wcx0a55 correction pass

## Scope

This pass addresses the independent P0–P2 review findings without deploying, installing host
services, registering runners, changing GitHub settings, or running canonical `./verify`.

The release manifest now carries both the digest of the exported tracked-source input and a
separate final artifact digest. Release validation checks the exact SHA directory, release-root
containment, regular manifest, and symlink containment. Existing valid same-SHA releases are
idempotently reused.

Deployment now supports a safe initial deployment, records validation and pointer-switch failures,
uses an operator-owned `flock` lock across processes, and records the operator state path when an
initial health proof fails. Candidate/current identity is validated end to end. Production health
requires the expected SHA; test-mode development health remains explicit.

The release-build CLI and workflow prepare Python/Node prerequisites on the production runner,
prove `github.sha`, build and validate the host-native release, publish it, and deploy that exact
artifact. Third-party actions are pinned to full commit SHAs. The launcher preserves only the
explicit external runtime allowlist. Linux/macOS service assets use readable release paths and
valid launchd domain verbs. Backup inputs use the shared validated identity CLI.

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
2. GREEN: `.venv/bin/python -m pytest tests/unit/test_release.py tests/unit/test_deployment.py tests/unit/test_server_events.py tests/unit/test_deployment_assets.py tests/unit/test_database_backups.py tests/unit/test_environment_cli.py tests/unit/test_environment_linux.py --tb=no` — `54 passed, 1 warning`.
3. GREEN: `.venv/bin/ruff check src/planner/environments src/planner/core/server.py tests/unit/test_release.py tests/unit/test_deployment.py tests/unit/test_server_events.py tests/unit/test_deployment_assets.py tests/unit/test_environment_linux.py` — `All checks passed!`.
4. GREEN: `.venv/bin/mypy --strict src/planner/environments/release.py src/planner/environments/deployment.py src/planner/environments/release_launcher.py src/planner/environments/backup.py src/planner/environments/cli.py src/planner/core/server.py` — `Success: no issues found in 6 source files`.

Canonical `./verify` remains reserved for the parent/orchestrator.
