# Implementation plan: isolated Panels runtime environments

Ticket: `t_2r1u7f39`

## Boundary

Do not restart, inspect, reset, remove, or mutate the currently running Panels instance. All runtime tests and manual commands use a temporary caller-selected environment root under pytest `tmp_path` or `/tmp`, with fresh ports and fresh Hermes homes.

Implementation ends at an approval-ready Implementation proposal and reviewed/verified commit on `ticket/t_2r1u7f39-isolated-runtime`. Closeout remains separate: no merge to `main`, no production promotion, no installed systemd units/accounts, no credential configuration, no public ingress, no backup/monitoring/cleanup automation, and no removal of this ticket worktree.

The existing `panels serve` foreground supervisor remains the single server entry point. The new environment runner prepares a scrubbed environment and `exec`s the same interpreter into `python -m planner serve`; it does not introduce a daemon.

## File ownership

Implementation may own only these production/documentation paths:

- `src/planner/environments/__init__.py`
- `src/planner/environments/contracts.py`
- `src/planner/environments/logic/validation.py`
- `src/planner/environments/logic/credentials.py`
- `src/planner/environments/logic/registry.py`
- `src/planner/environments/logic/launch_env.py`
- `src/planner/environments/materialize.py`
- `src/planner/environments/fake_fixture.py`
- `src/planner/environments/hermes_smoke.py`
- `src/planner/environments/cli.py`
- `src/planner/cli/main.py` only to register `panels environment`
- `src/planner/server_lifecycle/supervisor.py` only if a narrow helper beside the existing `PortScopedServerLifecycleLease` is required; do not change `panels serve` behavior
- `src/planner/minds/config.py` only if the existing `provision_planner_home_skills` needs a no-credential assertion or explicit environment-home helper
- `deployment/panels-runtime/README.md`
- `deployment/panels-runtime/setup-accounts.sh`
- `deployment/panels-runtime/panels-live.service`
- `deployment/panels-runtime/panels-nonproduction@.service`
- `deployment/panels-runtime/live.env.example`
- `deployment/panels-runtime/nonproduction.env.example`
- `docs/cli.md`
- `docs/systems.md`
- `docs/environments.md`
- `orchestration/tickets/t_2r1u7f39-isolated-runtime/red-green.md`
- `PROGRESS.md`
- `decisions.md`

Implementation may own these focused tests:

- `tests/unit/test_environment_contracts.py`
- `tests/unit/test_environment_credentials.py`
- `tests/unit/test_environment_fake_fixture.py`
- `tests/unit/test_environment_cli.py`
- `tests/unit/test_environment_lifecycle.py`
- `tests/unit/test_environment_linux_assets.py`
- `tests/unit/test_environment_hermes_smoke.py`
- `tests/e2e/test_environment_runtime.py`

Do not touch frontend files, HTTP route behavior, database migrations, existing runtime data under `data/`, global Hermes homes, production credentials, or any unrelated orchestration/memory files unless the implementation owner explicitly widens the ticket.

## Interfaces to introduce

`src/planner/environments/contracts.py` owns framework-free shapes:

- `EnvironmentKind = Literal["live", "staging", "preview"]`
- `ResolvedEnvironmentInstance`
  - `kind`
  - `instance_id`
  - `environment_root: Path`
  - `instance_root: Path`
  - `db_path: Path`
  - `managed_files_root: Path`
  - `hermes_home: Path`
  - `logs_dir: Path`
  - `dispatcher_lock_path: Path`
  - `server_control_socket_path: Path`
  - `port: int`
  - `credentials_env_file: Path | None`
  - `allowed_repository_roots: tuple[Path, ...]`
  - `expected_linux_account: Literal["panels-live", "panels-worker"]`
  - `fixture_version: str | None`
  - `prepared: bool`
  - `running: bool`
- `EnvironmentManifest`
  - persisted resolved settings plus `fixture_version`, `prepared_at`, `repository_roots`
  - never credential contents
- `EnvironmentPortRange(start: int, end: int)`
- `EnvironmentDefaults(live_port=8767, staging_port=8768, preview_ports=EnvironmentPortRange(...))`
- `EnvironmentCredentialPolicy`
- `EnvironmentValidationError`

`logic/validation.py` owns pure validation:

- `validate_instance_id(kind, instance_id)`
- `validate_absolute_environment_root(environment_root)`
- `validate_repository_roots(requested_roots, allowed_roots)`
- `validate_resolved_registry(instances)`
- `reject_overlapping_paths(paths_by_label)`
- `reject_duplicate_ports(instances)`
- `reject_nonproduction_nested_under_live(candidate, live)`
- credential-file references participate in overlap and live-nesting validation exactly like instance state paths

`logic/credentials.py` owns credential/env-file parsing:

- `parse_environment_file(path) -> dict[str, str]`
- rejects malformed lines, duplicate keys, unknown keys, forbidden keys, and production-only keys in staging/preview
- rejects all overrides of contract-owned `PLAN_*`, `HERMES_HOME`, `PYTHONPATH`, actor/ticket identity, launch-root, and process identity keys

`logic/launch_env.py` owns the scrubbed process environment:

- `build_environment_run_env(instance, credentials, ambient) -> dict[str, str]`
- starts from a small allowlist (`LANG`, `LC_*`, `PATH`, `TERM`, `TMPDIR`, `HOME` only if required by the interpreter/tooling)
- adds credential-file values after validation
- adds contract-owned `PLAN_DB_PATH`, `PLAN_PORT`, `PLAN_LOGS_DIR`, `PLAN_DISPATCHER_LOCK_PATH`, `PLAN_SERVER_CONTROL_SOCKET`, and `PLAN_HERMES_HOME`
- never forwards arbitrary ambient `PLAN_*`, `HERMES_*`, provider credentials, `PYTHONPATH`, `PLAN_ACTOR`, `PLAN_TICKET_ID`, or `HERMES_SESSION_KEY`
- an explicit hidden test-only launch seam, never ambient input or credential-file content, may additionally set `PLAN_TEST_MODE=1`, deterministic clock/timing values, and `PLAN_GATEWAY_ADAPTER=fake` for subprocess acceptance tests

`materialize.py` owns filesystem effects:

- `prepare_environment_instance(...)`
- `reset_environment_instance(...)`
- `remove_environment_instance(...)`
- `inspect_environment_instance(...)`
- atomic manifest writes via temp file + `os.replace`
- staging/preview reset builds a sibling temporary data tree, integrity-checks it, then replaces the old data tree
- live prepare creates empty layout only and never calls the fixture builder

`fake_fixture.py` owns the canonical seed:

- `FAKE_FIXTURE_VERSION`
- `build_fake_environment_database(db_path, *, now) -> FakeFixtureReport`
- uses `core.db.create_schema`, current domain writers in `projects.data`, `sprints.data`, `days.data`, `tickets.data`, and managed-file path conventions from `files.logic.paths`
- creates fictional projects, a sprint, day membership, tickets using several existing registered Worker-type ids and representative stages/statuses reached through current writers, managed Markdown, and one managed image/file placeholder; it never creates Worker-type definitions or rows
- never imports from `planner.seed`

`hermes_smoke.py` owns opt-in real-session smoke:

- `smoke_prepared_nonproduction_instances(staging, preview, hermes_python) -> HermesSmokeReport`
- the official ACP smoke prompts one unrelated real session in each home, closes the first child,
  and loads each durable session from a fresh child before reporting its id; it does not delete
  those sessions
- fake-gateway unit tests cover this path; no model call runs in `./verify`

`cli.py` owns the Click group registered from `src/planner/cli/main.py`:

- `inspect`
- `prepare`
- `run`
- `reset`
- `remove`
- `smoke-hermes-homes`

## RED-to-GREEN slices

For every slice: first add the focused failing tests, run the command and paste the decisive failing output into `orchestration/tickets/t_2r1u7f39-isolated-runtime/red-green.md`; then implement only that slice, rerun the same command, and paste the decisive passing output. Do not run `./verify` until the final gate.

### Slice 1: contract resolution and registry isolation

Own:

- `src/planner/environments/contracts.py`
- `src/planner/environments/logic/validation.py`
- `src/planner/environments/logic/registry.py`
- `tests/unit/test_environment_contracts.py`

Acceptance:

- live resolves to `<root>/live`, port `8767`, account `panels-live`
- staging resolves to `<root>/staging`, port `8768`, account `panels-worker`
- preview `<id>` resolves to `<root>/previews/<id>`, account `panels-worker`
- validation rejects unsafe ids, non-absolute roots, duplicate/overlapping state paths, Hermes homes, sockets, and ports
- validation rejects staging/preview paths nested under live
- validation rejects staging/preview credential-file references nested under or overlapping live paths
- repository roots must resolve to existing allowed repositories/worktrees

Focused RED/GREEN command:

```sh
.venv/bin/pytest tests/unit/test_environment_contracts.py -q
```

### Slice 2: credential files and scrubbed launch environment

Own:

- `src/planner/environments/logic/credentials.py`
- `src/planner/environments/logic/launch_env.py`
- `tests/unit/test_environment_credentials.py`
- `tests/unit/test_environment_cli.py` for `run` env-builder/exec seam only

Acceptance:

- credential files reject malformed, duplicate, unknown, forbidden, and nonproduction production-only keys
- contract-owned runtime values cannot be overridden by file or ambient env
- launch env strips hostile ambient `PLAN_*`, `HERMES_*`, provider credentials, `PYTHONPATH`, `PLAN_ACTOR`, `PLAN_TICKET_ID`, `HERMES_SESSION_KEY`
- launch env keeps only approved ambient basics and adds validated credentials plus contract-owned values
- CLI `run` requires a prepared manifest and calls an injectable `exec_fn` with `[sys.executable, "-m", "planner", "serve"]`
- the hidden test-only run seam injects fake/test settings itself; ambient and credential-file attempts to set those values remain rejected

Focused RED/GREEN command:

```sh
.venv/bin/pytest tests/unit/test_environment_credentials.py tests/unit/test_environment_cli.py -q
```

### Slice 3: materialization and canonical fake fixture

Own:

- `src/planner/environments/materialize.py`
- `src/planner/environments/fake_fixture.py`
- `src/planner/minds/config.py` only if needed to assert skill-only Hermes provisioning
- `tests/unit/test_environment_fake_fixture.py`
- `tests/unit/test_environment_cli.py` for `prepare`, `inspect`, and `reset`

Acceptance:

- `prepare staging` and two `prepare preview` calls create distinct SQLite DBs, managed-file trees, Hermes homes, logs, locks, sockets, and manifests
- all non-live instances are built from the same `FAKE_FIXTURE_VERSION` and have the same logical fixture report but independently generated ids/database bytes
- live prepare creates layout/manifest only and never invokes `build_fake_environment_database`
- Hermes homes are empty except repository-owned skill symlinks from `provision_planner_home_skills`; no `auth.json`, provider config, or session state is copied
- `inspect --json` reports paths, port, account, repository roots, fixture version, prepared/running state, and credential-file path without exposing values

Focused RED/GREEN command:

```sh
.venv/bin/pytest tests/unit/test_environment_fake_fixture.py tests/unit/test_environment_cli.py -q
```

### Slice 4: lifecycle safety, preview allocation, reset/remove

Own:

- `src/planner/environments/materialize.py`
- `src/planner/environments/logic/registry.py`
- `src/planner/server_lifecycle/supervisor.py` only for a narrow lifecycle-lease helper beside the existing lease class if required
- `tests/unit/test_environment_lifecycle.py`
- `tests/unit/test_environment_cli.py` for destructive command behavior

Acceptance:

- prepare is idempotent for the exact same manifest
- mismatched re-prepare fails without mutating prior state
- concurrent preview prepares under the environment-root registry lock allocate distinct durable ports from the configured preview range
- reset is staging/preview only, preserves instance id/port/Hermes home/credential reference, and leaves the old data tree intact if fixture build or integrity check fails
- remove is staging/preview only, refuses live, requires stopped lifecycle lease, deletes only that instance root, and releases preview registry allocation
- reset/remove fail while `PortScopedServerLifecycleLease(resolve_server_lifecycle_lease_path(port), port)` is held; no process inspection or signaling is used
- reset/remove themselves retain that same lease from before the first filesystem mutation until success or failure cleanup completes, preventing a server-start check/delete race

Focused RED/GREEN command:

```sh
.venv/bin/pytest tests/unit/test_environment_lifecycle.py tests/unit/test_environment_cli.py -q
```

### Slice 5: concurrent runtime through `panels environment run`

Own:

- `src/planner/environments/cli.py`
- `tests/e2e/test_environment_runtime.py`
- any missing seams from earlier environment modules

Acceptance:

- two or three test-mode subprocesses launched through `panels environment run` become healthy concurrently on independent ports
- test mode, deterministic clock/timing settings, and the fake gateway come only from the explicit hidden test seam, not ambient state or credential files
- each process exposes `/api/meta` and uses its own database, managed files, logs directory, dispatcher lock, server control socket, and Hermes home
- writes to one instance do not appear in another
- subprocess termination leaves no owned child process running
- tests never touch the current operator instance and never reuse its database/Hermes home/supervisor

Focused RED/GREEN command:

```sh
.venv/bin/pytest tests/e2e/test_environment_runtime.py -q
```

### Slice 6: Linux runtime assets

Own:

- `deployment/panels-runtime/README.md`
- `deployment/panels-runtime/setup-accounts.sh`
- `deployment/panels-runtime/panels-live.service`
- `deployment/panels-runtime/panels-nonproduction@.service`
- `deployment/panels-runtime/live.env.example`
- `deployment/panels-runtime/nonproduction.env.example`
- `tests/unit/test_environment_linux_assets.py`

Acceptance:

- live unit runs as `User=panels-live`
- nonproduction template runs as `User=panels-worker`
- both call the common `panels environment run` entry point
- units name expected working directories, environment files, restart behavior, and instance roots
- examples contain names only, never secret values
- tests parse/render files locally and assert account/path/env/command/mode expectations without claiming macOS enforces Linux users or permissions

Focused RED/GREEN command:

```sh
.venv/bin/pytest tests/unit/test_environment_linux_assets.py -q
```

### Slice 7: Hermes smoke contract and docs

Own:

- `src/planner/environments/hermes_smoke.py`
- `tests/unit/test_environment_hermes_smoke.py`
- `docs/cli.md`
- `docs/systems.md`
- `docs/environments.md`
- `src/planner/environments/cli.py` for `smoke-hermes-homes`

Acceptance:

- fake-gateway tests prove two explicit homes produce distinct stored session ids; official ACP
  tests prove exact prompt success and fresh-child loading, while the durable sessions remain in
  their owning homes
- real smoke command is opt-in, takes two already-authenticated nonproduction homes, fails clearly when either home is not independently configured, and is not part of `./verify`
- docs provide copyable examples for `inspect`, `prepare`, `run`, `reset`, `remove`, and the opt-in smoke command
- docs state live vs staging/preview isolation, Linux account boundary expectations, and the Implementation/Closeout boundary

Focused RED/GREEN command:

```sh
.venv/bin/pytest tests/unit/test_environment_hermes_smoke.py tests/unit/test_environment_cli.py -q
```

## Independent review

After all focused slice commands are green and before the canonical verification run, request an independent read-only Codex review of the implementation diff and contract fit:

```sh
codex exec --model gpt-5.5 --sandbox read-only --reasoning-effort high "Review the diff for ticket t_2r1u7f39 against orchestration/tickets/t_2r1u7f39-isolated-runtime/contract.md. Focus on environment isolation, credential scrubbing, lifecycle lease safety, fake fixture independence, Hermes-home no-credential provisioning, Linux assets, test strength, and whether the current running Panels instance could be mutated. Return concrete violations with file:line references; say 'no violations found' only if none." < /dev/null
```

Paste the full review output into `orchestration/tickets/t_2r1u7f39-isolated-runtime/red-green.md`, then either fix each finding with focused tests or explicitly refute it in the same file with code references. If fixes are made, rerun only the affected focused commands before proceeding.

## Canonical verification

Run exactly one full verification after the implementation diff and review responses are complete:

```sh
./verify
```

Paste the full output into `orchestration/tickets/t_2r1u7f39-isolated-runtime/red-green.md`. The completion claim requires `VERIFY: PASS`; do not rerun `./verify` just to re-quote output unless code changes after the first full run.
