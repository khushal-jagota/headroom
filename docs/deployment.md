# Production deployment

Panels publishes checked applications separately from their installation. A push to
`main` creates a retained release on a disposable hosted runner. An owner update
request sends that exact release to the existing deployment runner.

[Executable archive runtime](archive-runtime.md) defines the supported host contract,
sealed target proof, and compatibility refusal before service actions.

```text
main → complete checks → build once → seal archive → smoke → disposable rehearsal
                                                               ↓
                                                   retained archive and proof
                                                               ↓
owner request → dedicated runner → stop → verified backup → replace → health
                                                               ↓
                                             admit work → skills → published HTTP/2
```

Production runs one Git-free application as the existing `vps` user:

```text
~/Deployments/Panels/current/
├── app/     executable application
├── data/    database, configuration, managed files, and deployment records
└── logs/    application logs
```

Only `app` changes during replacement. Persistent data and logs remain in place.
New backups contain the database and existing managed configuration roots. They
exclude uploads and Ticket artifacts under `data/files`. See [backups](backups.md).

[Owner-controlled updates](updates.md) describes the buttons, authorization,
configuration, retained releases, and startup admission boundary.

Code paths: `.github/workflows/deploy.yml`, `src/planner/environments/`.

## Recover an older database with cancelled Tickets

An upgrade refuses an older database that contains cancelled Tickets or would produce
them while applying historical migrations. The refusal leaves its schema and records
unchanged. The owner chooses whether to preserve those Tickets through an
older supported version or archive and delete them with the version being installed.
The recovery command works directly on the database even when the server cannot start.

Stop the service first. Use the same database configuration as that service and a new
archive directory outside the application tree. Its parent directory must already exist.
For an explicit database path:

```sh
systemctl --user stop panels-live.service
PLAN_DB_PATH=/absolute/path/to/panels.db panels maintenance archive-cancelled-tickets --archive-dir /absolute/path/to/cancelled-ticket-archive --yes
```

Run this as the owner, without a Worker identity. The command first saves and verifies a
complete snapshot of the original database, including unrelated records and history.
Keep the private archive directory protected as you would the database itself.

Some older databases need historical migrations before their cancelled Tickets exist.
Only this explicit recovery action prepares those migrations, stopping before the
retirement-removal migration. It does not start the server or initialize the current app.
The archive also contains a readable manifest, the selected Tickets' complete records,
field values and artifact references, and SQL evidence of the prepared database.
Everything is verified durable and readable before deletion. Historical preparation and
Ticket deletion share one transaction; any failure rolls both back, leaving the original
database unchanged. The verified original snapshot remains available in the archive.

Files under `/files/tickets/<ticket-id>` remain where they are; the archive retains their
references rather than copying or deleting them.

After successful recovery, retry the upgrade and restart the service. The upgrade removes
obsolete retirement fields from the remaining Tickets. It does not change their Stage,
ordinary fields, proposals, or approval ownership.

## Services and host prerequisites

Linux uses systemd user services. `panels-live.service` launches the deployed app.
`panels-deployment-runner.service` owns the dedicated GitHub Actions runner.
The backup timer invokes the deployed application's backup command.
The `vps` user controls them with `systemctl --user`; these operations need no sudo.

The installer must run outside the service it replaces. Service control refuses an
installer in the target unit's cgroup before it can change deployment state. `nohup`
and shell backgrounding do not leave that cgroup. Owner adoption uses a separate
`panels-adoption-<run>-<attempt>` user unit, so stopping Panels cannot kill its installer.

Development worktrees must never link dependencies into the deployed application.
Development and verification run in independent worktrees, never in the installed tree.
Matching lockfiles do not make a shared directory safe: test and build tools write
caches into `node_modules`, invalidating the deployed app's integrity proof. Use a
development-owned dependency tree. Use the guarded development commands below.

The operator establishes the filesystem layout, units, runner, and private route
before adoption. The workflow does not provision the host or change Tailscale Serve.
It keeps these existing paths:

- Application root: `~/Deployments/Panels/current`.
- Database: `current/data/planner.db`.
- Backups: `current/data/backups`.
- Lifecycle record: `current/data/deployment-lifecycle.json`.
- Health: `http://127.0.0.1:8767/api/health`.
- Managed skills: `current/data/skills`.
- Agent homes: `~/.hermes`, `~/.codex`, and `~/.claude`.

Video attachment requires `ffprobe` on the host's executable path. Its absence
refuses new video uploads without preventing ordinary application use.
Provider secrets remain in protected service environment files outside the app.
The existing transcription configuration uses `~/.config/panels/panels-live.env`.
Replacing `current/app` does not replace that file.

A systemd-launched supervisor accepts ordinary shutdown signals only from its
verified manager. The operator stops the unit through `systemctl --user`.
If shutdown cannot finish, the unit's stop timeout and kill policy apply.
Check its effective timeout and kill properties before an operator recovery.
Workers never stop the service that hosts them.

The direct operator tools retain their Linux systemd and macOS launchd controllers.
The owner-update workflow uses the existing Linux deployment runner.

Code paths: `ops/panels-environments/`, `src/planner/environments/deployment.py`,
`src/planner/environments/deployment.py`.

## Publication checks and exact archive

Publication uses GitHub-hosted Ubuntu, Python 3.12, and Node 22. It runs full Ruff,
mypy, Svelte checks, typechecked Vitest, and the complete browser catalog.
Unit, integration, and E2E suites run in separate pytest processes.
Nested test directories participate. Documentation-only changes use the same checks.
The requested checkout supplies its dependencies, test configuration, and source.
The trusted workflow checkout supplies the orchestration and report validators.

The existing report checks reject empty proof, failures, and unexpected skips.
Named paid-provider opt-ins receive no default coverage credit.
The temporary Codex setup follows the requested source's exact CLI pin.
It provisions no provider credentials.

Connected checks use the existing bubblewrap shadow for deployment artifacts.
The source, reports, and temporary tools remain reachable through explicit mounts.
Browser entries share the existing build, static server, and Chromium process.
Cases retain separate contexts. Their receipts bind the requested revision,
invocation, entries, and required cases. No unisolated fallback exists.

Publication builds the target application once. It seals `panels-app.tar.gz` before
smoke starts. Smoke and rehearsal each extract that same archive into separate trees.
Extraction checks the archive hash, safe paths and links, Unix modes, manifest,
application tree, executable entrypoints, and installed Python runtime.
Smoke checks exact-SHA health, built assets, and an initialized API with fresh state.
It receives an explicit environment without provider credentials.
A hosted runtime failure fails publication. It does not trigger a candidate rebuild.

The release contains an interactive CLI and a service launcher. Their paths resolve
relative to the application root. The executable archive contains no Git metadata.
The proof receipt binds its source SHA, manifest digest, archive hash, schema floor,
publication run, artifact identity, and expiry. It does not bind a future installation
to the live SHA observed during publication.

Publication uploads one executable archive and a separate proof artifact, both with
30-day retention. Reports include separate JUnit files, Vitest and browser receipts,
smoke evidence, rehearsal evidence, and workflow timing observations.
The final job duration remains available in GitHub after the last timing snapshot.
These reports describe their own execution. Local implementation checks do not prove
that a hosted publication or production installation succeeded.

Code paths: `.github/workflows/deploy.yml`, `scripts/update_publication.py`,
`scripts/deployment_check_report.py`, `src/planner/environments/update_artifacts.py`,
`src/planner/environments/deployment_gate.py`.

## Disposable rehearsal

Publication rehearses replacement on a disposable hosted VM. Manual publication also
accepts `rehearsal: true`; this input cannot select the production runner.
The default baseline is the pinned source
`2c0e05cefc82de100bfc576c1a8f3eda7a931f2d`, whose schema head is
`video_upload_ledger`. A manual rehearsal can name another distinct baseline.
The baseline builds separately; the target still builds once.

The VM receives its own user service, home, ports, database, files, and lifecycle.
It uses synthetic records and sentinel files only. Before and after evidence records
schema revisions, the synthetic Project, and hashes for excluded uploads and artifacts.
The rehearsal uses the existing backup, replacement, health, and recovery boundaries.
It adds no failure-injection rehearsal or broader rollback system.

Hosted prerequisites include bubblewrap, FFmpeg, a systemd user manager, and browser
runtime dependencies. Offline Hermes packaging uses its pinned core and ACP package
in a separate temporary interpreter. It enables no paid-provider tests.
Cleanup removes the temporary service, worktrees, interpreters, and state after
proof collection. The published-route HTTP/2 check stays outside this private VM.

Code paths: `scripts/deployment_rehearsal.py`, `.github/workflows/deploy.yml`.

## Replacement and recovery

The deployment runner downloads the pinned archive and proof. It does not rebuild
the target or rerun publication suites. It validates retained identity and schema
ancestry before downtime. The existing deployment lock serializes installation.
Current-root, candidate, snapshot, and final request admission checks still apply.
Legacy direct operator tools retain their observation-bound gate evidence.

The runner stages the candidate while production remains live. It then stops old
service writers and creates a verified database and configuration snapshot.
Only after that snapshot succeeds does it retain the prior app and replace `app`.
Uploads and Ticket artifacts stay outside this snapshot and replacement path.
Legacy snapshots can contain additional roots; their recorded restore contract remains.

The candidate starts with health and status available while ordinary work stays closed.
Startup migrations and controlled initialization remain allowed. Normal HTTP and CLI
work, conversation recovery, background dispatch, tail writers, and cleanup wait for
the matching runner commit. See [startup admission](updates.md#startup-admission-and-runner-loss).

Each requested-app and prior-app health proof receives the existing 300-second budget
after the service restart command returns. The separate installed smoke budget is
45 seconds with fresh state. Neither budget promises a maximum outage.

Before local commit, replacement or health failure uses the existing recovery path:
stop candidate, restore the verified snapshot and fallback app, restart, and prove
the prior SHA. Restore occurs only while normal candidate work remains closed.
A failed recovery reports retained paths for explicit operator continuation.
The runner never reports an unproved recovery as success.

After local commit, normal work opens. Later lifecycle, skill, or HTTP/2 failures
report the installed SHA and incomplete workflow. They never restore the database.
An intentional later code rollback does not independently rewind database contents.
The update workflow accepts retained, pinned publications through an owner request.
It does not expose an arbitrary-SHA rollback button.

Code paths: `src/planner/environments/deployment.py`,
`src/planner/environments/update_runner.py`, `src/planner/environments/backup.py`.

## Lifecycle and post-health checks

The runner owns a versioned lifecycle file outside the application replacement tree.
Its deployment identity prevents an old runner from overwriting a newer record.
Atomic writes and a lock protect the record. Status reads do not grant permission
to restart, restore, or admit work.

The local commit records application health. Skill reconciliation and published-route
HTTP/2 follow that commit. Workflow success requires those post-health steps.
An always-run finalizer records success or failure with the actual serving SHA.
It preserves a proved rollback. If finalization itself fails, the saved evidence
remains authoritative and the workflow reports failure.

The application observes the lifecycle file through the existing change signal.
Fresh success produces a brief deployment confirmation. A changed app SHA offers
an explicit browser refresh. It does not replace the push service worker.
Expired unfinished evidence requires operator review; expiry never opens startup work.

The publication rehearsal retains its existing foreground command supervisor and
renewal receipts after lifecycle activation. The adoption runner owns its local
transaction and finalization. If that runner disappears, see the explicit recovery
limits in [owner-controlled updates](updates.md#startup-admission-and-runner-loss).

Code paths: `src/planner/environments/deployment_lifecycle.py`,
`scripts/deployment_progress.py`, `.github/workflows/deploy.yml`.

## Published route protocol check

After local adoption and skill reconciliation, the workflow checks the published route.
The operator sets the repository Actions variable `PANELS_PUBLISHED_ORIGIN` to the
private HTTPS origin. The helper has no default. It rejects absent values,
credentials, non-HTTPS URLs, and paths other than `/`.

The helper requests `/api/meta` with `curl --http2`, normal certificate validation,
no redirects, and bounded connection and total timeouts. Only negotiated HTTP/2 passes.
The application health check remains separate. A post-commit route failure leaves the
locally healthy app installed and records an incomplete deployment.
The private-route recovery workflow uses the same helper.

Code paths: `scripts/check_published_http2.py`, `.github/workflows/deploy.yml`,
`.github/workflows/recover-private-route.yml`.

## Interrupted agent sessions

The existing read-only helper captures unresolved turns after a replacement or
recovery stop. Its bounded capture does not initialize or migrate the stopped database.
Capture failure leaves the ordinary backup, health, and recovery checks intact.
Missing evidence never means that no turns stopped.

Interruption evidence stays outside the application and snapshot restore set.
After runtime admission, eligible interrupted conversations use the ordinary send path.
Durable delivery intents prevent automatic replay of an uncertain send.
[The conversation system](conversation-system.md#sessions-that-a-deploy-ends) owns the
warning, admission rules, and visible failure records.

Code paths: `src/planner/environments/`, `src/planner/conversation/`.

## Pull request checks and operator authority

### Development dependency safety

Run setup from a new worktree with Python 3.12 or newer and Node 22 available:

```sh
scripts/dev setup
scripts/dev npm --prefix web ci
scripts/dev npm --prefix agent_backends ci
scripts/dev npm --prefix web run check
scripts/dev pytest tests/unit/test_instrument.py
scripts/dev mypy scripts/tree_environment.py
scripts/dev python -m pytest tests/unit/test_instrument.py
scripts/dev .venv/bin/pytest tests/unit/test_instrument.py
```

The launcher checks dependency storage before npm, pytest, or mypy starts.
Setup requires absent `.venv` and package dependency directories. It refuses existing
destinations without deleting them. It installs the pinned requirements, an editable
Python package, and both Node dependency trees.
Supported npm commands cover install, ci, and registered scripts for `web/` and
`agent_backends/`. Python commands use the worktree's virtualenv. Console and
`python -m` forms receive the same check. Setup creates independent dependencies
and uses a path-only editable Python installation.

The check compares resolved paths, filesystem identities, and Linux mount mappings.
It examines nested links and Python package paths. The normal protected root comes
from the account home and `Deployments/Panels/current`. Explicit deployment context
adds protected roots. It never replaces that normal root.

A refusal distinguishes detected sharing from unavailable safety evidence.
It names the command, examined path, resolved path, protected path, and signal.
Mount evidence includes the device, mount point, and backing path. Missing evidence
includes the operation that failed and its reason. Preserve this diagnostic for repair.

If storage sharing is real, create a fresh worktree and run `scripts/dev setup` there.
Preserve the suspect dependencies until their links or mounts receive deliberate repair.
Never delete dependencies through an unresolved link or mount.

If independent dependencies receive a false refusal, fix the detector and add a
regression check. Do not restructure valid dependencies around the detector error.
If evidence is unavailable, restore it or fix its acquisition before retry.
No flag, environment variable, or approval disables these checks.

Every web script also checks before its dependency executable starts. Native pytest
and mypy hooks check before existing environment identity and dependency-pin checks.
Those hooks run after the tool starts, so they cannot replace the launcher's earlier check.
Ruff and arbitrary direct Python or Node commands remain outside this coverage.

Direct commands outside guarded entry points can still use shared dependencies and
write into the deployed application. General prevention remains uncommissioned.
No other Ticket closes that gap. Startup checks do not provide race-free filesystem
containment or containment of arbitrary startup code. They do not contain a process
that is already running and cannot prevent a write after the check.
General cache writes, Ruff, and other direct tools remain uncommissioned gaps.

The artifact digest detects damage after a write. It does not prevent the write.
These checks do not add cache exclusions or change the digest rules.
If a write reaches the installed tree, report it and leave it in place.
Do not clean the installed tree to make an installation proceed. Preserve the evidence.

The currently installed release contains no enforcement of these checks. Review or
merge is not installed enforcement. No production operation forms part of this work.
The separate installer pull request is neither a prerequisite nor acceptance evidence.

The focused dependency safety workflow checks the proposed merge on an `ubuntu-24.04`
hosted runner. It records environment evidence, admits independent dependencies, and
checks refusal with disposable aliases, including a real bind mount. An unavailable
mount namespace fails that proof. Host evidence and hosted evidence remain separate.
Neither result proves installed enforcement. The check starts no browser or server.

Code paths: `scripts/dev`, `scripts/development_safety/`,
`.github/workflows/dependency-safety.yml`.

### Release checks

The separate pull request workflow checks the complete proposed merge tree with mypy.
It does not replace publication checks. Focused local checks still follow each change.
Follow the branch and merge procedure in `AGENTS.md`. Publication workflow changes
reach the active release path only after their reviewed revision reaches `main`.

Host setup, protected credentials, service control, and production adoption remain
operator actions or the explicitly authorized owner-request runner path.
Workers receive no dispatch credential or authorized restart operation.

Code paths: `.github/workflows/typecheck.yml`, `AGENTS.md`.

---

_Last verified: 2026-10-28 against source. No hosted publication or production adoption claimed._
