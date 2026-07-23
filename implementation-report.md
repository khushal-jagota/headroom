# Implementation report — t_2wcx0a55

## Result

Implemented all three dispatched slices in three coherent commits:

- `a3e3e0d3` — exact-SHA release identity and runtime proof.
- `ff184f54` — serialized deployment transaction and code-only recovery.
- Slice 3 — host service assets and GitHub handoff (commit below).

The branch never ran canonical `./verify`, installed services, registered a runner, changed live,
changed GitHub settings, merged, or pushed.

## Design

`ReleaseManifest` is the production identity source. It validates a lowercase full 40-character
SHA and a digest of the exported tree. `build_exported_release` proves the source checkout `HEAD`,
exports tracked files with `git archive`, writes a stable launcher, and leaves `.git` out of the
release. The launcher validates the manifest and injects only the validated identity into the
scrubbed environment. Development and test startup remains explicit through an optional config
identity; `/api/meta` reports it and `/api/health` proves it.

Deployment validates both candidate and current release manifests, backs up with the current
manifest SHA before changing `current`, switches the pointer atomically, restarts, and polls a
bounded health seam. Candidate failure rolls back the code pointer and proves the prior SHA. It
does not restore database or other application state. Results are durable JSONL records, and an
already-selected SHA is a no-op.

Linux live service input and macOS launchd input use `/opt/panels/current/bin/panels-launcher` and
external state paths. Staging keeps its checkout-based environment contract. GitHub deploy checks
out `github.sha`, proves `git rev-parse HEAD`, runs `./verify`, and passes the same SHA-derived
release path to the operator CLI.

## TDD evidence

Slice 1 RED:

```text
.venv/bin/pytest -q tests/unit/test_release.py
ModuleNotFoundError: No module named 'planner.environments.release'
```

Slice 1 GREEN:

```text
.venv/bin/pytest -q tests/unit/test_release.py
..... [100%]
```

Slice 2 RED:

```text
.venv/bin/pytest -q tests/unit/test_deployment.py
ModuleNotFoundError: No module named 'planner.environments.deployment'
```

Slice 2 GREEN:

```text
.venv/bin/pytest -q tests/unit/test_deployment.py
.... [100%]
```

Slice 3 focused GREEN:

```text
.venv/bin/pytest -q tests/unit/test_deployment_assets.py
... [100%]
```

Final focused combined gate (not canonical verification):

```text
.venv/bin/ruff check ...
All checks passed!
.venv/bin/python -m mypy ...
Success: no issues found in 7 source files
.venv/bin/pytest -q tests/unit/test_release.py tests/unit/test_deployment.py \
  tests/unit/test_environment_linux.py tests/unit/test_environment_cli.py \
  tests/unit/test_environment_repository_runtime.py tests/unit/test_environment_contracts.py \
  tests/unit/test_database_backups.py tests/unit/test_server_events.py \
  tests/unit/test_deployment_assets.py
......................................................... [100%]
```

The focused suite includes one expected FastAPI/httpx deprecation warning from the existing test
dependency; it does not fail the gate.

The stale-assumption sweep found only negative assertions in the new asset tests that explicitly
prove old live-Git names are absent; no production or operator input retains those assumptions.
