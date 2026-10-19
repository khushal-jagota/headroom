# Production deployment

Production runs one Git-free application as the existing UID-1000 `vps` user:

```text
~/Deployments/Panels/current/
├── app/     deployed executable application
├── data/    database, managed files, configuration, deployment lifecycle, and runtime state
└── logs/    application logs
```

Only `app` changes during deployment. `data` and `logs` remain in place through code
changes, failed deployments, and code rollback. Serving, deployment, rollback, backup,
and user-service control need no sudo.

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

## Services and runner

Linux uses systemd user services. `panels-live.service` launches the deployed app,
`panels-deployment-runner.service` owns the dedicated GitHub Actions runner used by the
deployment workflow, and the backup timer invokes the deployed app's backup command.
The `vps` user controls them with `systemctl --user`.

The live service receives provider secrets from protected environment files outside the
deployment root. Voice transcription uses a mode-`0600` file at
`~/.config/panels/panels-live.env`, loaded by a `panels-live.service` drop-in. The file
contains `PLAN_GROQ_API_KEY` and persists when a deployment replaces `current/app`.

macOS retains its launchd path. Both service controllers operate in the signed-in
user's service-manager domain, and both preserve the user's normal Hermes, Codex, and
Claude homes.

## Exact commit path

A push to `main` deploys `${{ github.sha }}`. The owner-triggered workflow input accepts
one lowercase, full 40-character SHA, including an earlier commit selected for rollback.
Both routes use the same transaction:

```text
workflow checkout
        ▼
publish Preparing for the requested SHA
        │
        ▼
exact temporary checkout → prove requested HEAD
        │
        ▼
observe served release → select and run related source checks
        │
        ▼
build candidate once → isolated installed-launcher smoke → seal digest proof
        │ stage candidate while production remains live
        ▼
stop service → create verified database + managed-files snapshot
        │ hard cutover
        ▼
replace current/app → restart + prove requested SHA
        │
        ▼
app healthy → reconcile managed skills into Hermes, Codex, and Claude homes
        │
        ▼
publish workflow success

or stop candidate → restore snapshot + prior app → prove prior SHA
```

The self-hosted runner proves Python is at least 3.12 and Node is version 22. It builds
under runner-temporary storage and removes that state when the job ends. The resulting
app contains no Git metadata and carries a validated identity for the requested commit.
Runtime validation requires both executable entrypoints: `bin/panels` is the
root-relative interactive CLI that preserves caller context, while
`bin/panels-launcher` is the isolated launcher for services and other managed runtime
operations.

The workflow uses the fixed VPS contract:

- current root: `~/Deployments/Panels/current`
- live database: `~/Deployments/Panels/current/data/planner.db`
- backups: `~/Deployments/Panels/current/data/backups`
- deployment lifecycle: `~/Deployments/Panels/current/data/deployment-lifecycle.json`
- health: loopback port 8767
- service manager: `systemctl`
- service: `panels-live.service`
- managed skills: `~/Deployments/Panels/current/data/skills`
- agent homes: `~/.hermes`, `~/.codex`, and `~/.claude`

The server puts the deployed app SHA in each root document and requires that document
to revalidate. A retained installed app checks `/api/meta` without cache reuse. If the
running SHA changes, the app offers a user-controlled page update. This path does not
replace the push-only service worker or its subscriptions.

After the app deployment succeeds, the current workflow deployment tool invokes
`environment provision-skills` with all four paths explicitly. Using the workflow tool
means an intentional rollback to an older app still applies the current skill-retirement
policy. The command reconciles Panels skills into each agent home, removes explicitly
retired Panels skills, and leaves unrelated custom skills in place. This post-deploy step
is idempotent. If it fails after app cutover, the app remains deployed and the workflow
reports failure; rerunning the deployment safely retries skill reconciliation.

## Related checks before deployment

Deployment runs related checks before any app transaction. Pull requests and staging
pushes do not start a suite workflow. Agents still run focused checks before a push.
The current workflow revision supplies control tooling. The requested checkout supplies
its own editable Python environment, pinned dependencies, source, and test configuration.
Both Node lockfiles supply build prerequisites. A rollback uses these same boundaries.

Before checks, the gate records the installed manifest and exact health response. A
baseline is trustworthy only when the manifest and `/api/health?expected_sha=<SHA>`
agree. The selector compares that served commit with the requested commit across the
whole release. Deleted and renamed paths, changed tests, accumulated releases, and
rollback all affect selection. The checked-in domain map expands related coverage into
explicit existing test files and browser hosts. Default scheduling API/CLI checks and
HTTP authority checks remain required for their affected domains.

An unavailable baseline, shared or unknown path, or change to selection/dependencies
selects broad default coverage. Missing mapped historical coverage broadens or stops.
Documentation-only and empty diffs omit source suites but still require candidate proof.
The selection records its control revision, requested SHA, baseline, paths, and reasons.

Ruff, mypy, three separate pytest processes, Svelte checks, and typechecked Vitest
run as separate checks. Selected browser entries share one multi-entry Vite build,
static server, and Chromium process. Independent cases retain fresh contexts. Check steps report
failures even when another check fails. Missing tools, empty selected collection, and
unexpected required skips fail the gate. Seven named paid-provider opt-ins remain visible
without default coverage credit. No provider opt-in is enabled by this workflow.
Before pytest, the workflow resolves the exact Codex CLI pin from the requested source's
installed Python package. It checks PATH and the user's local CLI, then installs that
exact npm version into run-temporary tools only if neither matches. It logs the required
version, selected executable and actual version, publishes its directory through
`GITHUB_PATH`, and verifies it again before every selected pytest suite. No credentials
are provisioned and provider opt-ins stay disabled. Rollbacks read the requested source's
pin while using the current workflow's setup helper.

The required bwrap mount redirects deployment artifacts into temporary storage for
E2E and Node browser hosts. The source, dedicated report directory, and temporary Codex
tools are rebound after that shadow mount. Thus a runner directory nested beneath the
deployment path still writes the E2E report to the same host-visible file that the
strict report checker reads. There is no unisolated fallback.

After source checks pass, the workflow builds one exact candidate. Its installed launcher
starts with fresh state, dispatch disabled, and no provider credentials. Smoke checks
prove exact-SHA health, the root document and its built assets, and an initialized API
read. The smoke process and state are removed on exit. The gate revalidates candidate
bytes and records their digest with the served-base observation.

The deployment transaction requires this evidence. Under its lock it rechecks the
requested SHA, artifact digest, and exact baseline observations. It also checks copied
candidate bytes against the pinned digest before service stop or installation. A
changed baseline or candidate refuses deployment before production mutation, including
first-install and same-SHA paths. Existing backup and recovery behavior remains in place.

The workflow removes temporary source, environments, candidates, gate reports, and
artifact shadows on every outcome. The lifecycle finalizer reports failures from checks,
selection, build, smoke, cutover, or provisioning through the existing record.

Repository landing on staging does not activate a changed production workflow. The
owner must merge the reviewed revision to main. No production dispatch is required to
review these checks. Any required-check setting must retain effective protection until
the replacement is available. Production deployment and restart remain owner actions.


### Shared browser selection and receipts

`npm --prefix web run test:browser -- workspace` runs one selected entry. Omit the entry
name to run the eight-entry catalog. Multiple entry names share one Vite build, HTTP
server, and Chromium process. Each case retains its own context and network fixtures.
The catalog lives in `web/tests/browser/catalog.json`.

The deploy selector reads capability from the tracked requested-source catalog. It
runs all selected entries in one invocation. The control checkout validates that
invocation's receipt against the requested-source catalog and exact revision. Missing
entries, duplicate identities, absent cases, empty proof, skips, and failures refuse
release admission. A receipt from another revision or invocation also refuses admission.
Workspace motion additionally requires the Node projection cases and the compiler for
the TypeScript test project. A successful browser process cannot replace that proof.

Rollback source without the catalog retains the original independent commands and exit
checks. That older format has no per-case receipt obligation. A source that declares the
new catalog never falls back after a missing, malformed, or failed receipt. An old
control checkout discovers the new entry wrappers through its existing file inventory.
The retained direct rail alias can repeat workspace under old control. This is a
compatibility cost, not a ninth catalog entry.

Unknown impact and a missing selector map broaden to all default suites. Invalid
catalog declarations refuse admission. Required local tools, including bubblewrap for
browser selections, remain mandatory. Only the exact provider opt-in skip allowlist
excludes paid proof. Gate changes require the disposable hosted rehearsal before
integration into the release branch.

The integration bootstrap case remains. It runs the requested revision's builder
with real dependencies and checks its installed result. The release gate uses the
control revision's builder. These can differ during rollback, so the actual candidate
smoke does not replace that source-builder proof.


## Disposable workflow rehearsal

The same `Deploy exact commit` workflow accepts `rehearsal: true` on manual dispatch.
Supply `commit_sha` and an explicit known-good `baseline_sha`; both are full SHAs and
must differ. The rehearsal runs on a fresh GitHub-hosted Ubuntu VM with its own
concurrency group. Production runs remain on the dedicated self-hosted runner and only
accept the workflow control revision from `main`.

The hosted setup installs Python 3.12, Node 22, Chromium's runtime dependencies,
bubblewrap, FFmpeg, and a real systemd user manager and bus. The required offline Hermes
extension-packaging checks use a separate interpreter in the runner's home, provisioned
from official `NousResearch/hermes-agent` revision
`76d832d3857551a029c4b39c23945eb47c16fe5b` (Hermes 0.20.0), with only its core and ACP
extra. This matches the deployed Hermes source used to validate that packaging boundary;
it enables no provider tests and receives no credentials. Installation logs and the exact
revision are retained with the rehearsal proof, and cleanup removes that interpreter.
This test prerequisite is separate from the disposable app's private agent homes. It creates a unique user service,
available loopback port, private home, fresh database with dispatch disabled, backup
root and agent homes. No production secrets or private-network access are used.
Temporary paths deliberately sit below `/home/vps/Deployments/Panels` to exercise the
report-shadow collision rather than avoid it.

The setup builds and serves the exact baseline app before observation and selection.
The normal source checks, candidate build, installed-launcher smoke, digest evidence,
`app-deploy`, real backup/replacement and exact-SHA health checks, and skill provisioning
then run against those disposable paths. They use the same fail-closed gates as
production. An unchanged baseline/candidate pair is refused so the rehearsal proves
replacement and backup rather than only the same-SHA fast path.

On every outcome, the workflow first finalizes the lifecycle and collects reports,
gate evidence, baseline and final health, deployed manifest, backup metadata, service
unit and logs into an Actions artifact. It then stops and removes the temporary service
and removes worktrees and temporary state. The artifact remains available after the VM
is discarded. Production runs do not upload deployment data.

## Test admission and coordinated guidance

`PRINCIPLES.md` holds the standing admission policy. `AGENTS.md` references it during
existing plan and implementation review. The operational paragraph also lives in the
managed `panels-worker-coding` skill and its repository fresh-install seed at
`src/planner/skills/panels-worker-coding/SKILL.md`.

Chief confirmed the live rule at 02:13:38Z. Coding Workers read it on their next step,
without a service deployment. Repository policy applies when checkout agents use the
revision that contains it. The seed supplies fresh installations. Provisioning copies
the database-managed rule outward to agent homes and does not replace it from the seed.
Future revisions must supply Chief exact corresponding wording for the live skill,
`PRINCIPLES.md`, and the seed, plus the `AGENTS.md` reference. These are coordinated
revision destinations. Repository edits do not authorize managed-store writes.

The prior instructions required focused execution, material E2E risk, cheapest faithful
layers, and small connected proofs. They did not require every added test to name its
incremental coverage gap, reuse hosts, replace duplicate matrices, or record connected
cost. That gap permits individually justified tests to repeat existing proof. It does
not establish the cause of all historical growth. Existing review now checks admission
against the suite cut's retained proofs and intentional retirements.

The cut at `f9849945` is the comparison baseline: 22 E2E cases and 20 frontend hosts.
Its reported combined browser time changed from 516.9 to 356.6 seconds (160.3 seconds,
31.0%) in one comparison. The earlier result included three failures. Its coverage
ledger records replacements and intentional retirements. This deployment change adds
no browser host or browser E2E case. New checks must document distinct risks and costs
under the admission policy.

Coverage savings and runner capacity remain separate evidence. This change removes
PR-wide execution without an empty-diff guard or a replacement staging run. It adds no
rolling-run capacity setting, so there is no such setting's capacity measurement or
restoration condition to claim. Reverting the workflow change restores the former PR
placement. Main workflow activation remains separate from staging landing.

## Deployment status evidence

The runner owns one small versioned lifecycle file in persistent `current/data`.
Checkout is the unavoidable bootstrap because it supplies the canonical stdlib-only
writer. Immediately after checkout, the workflow reads the current deployment ID and
starts the new record before proving or building the requested source. A checkout
failure and a requested value that is not a lowercase full SHA therefore cannot be
recorded; later Git proof, runtime, build, deployment, and provisioning failures can.
The app-deploy transaction advances the same deployment ID
through restart, exact-SHA verification, app health, and rollback. The workflow records
success only after app deployment and production skill provisioning have both
completed. Its finalizer turns interrupted work into a public-safe failure and does not
overwrite a proved rollback.

Writes are serialized beside the record. Starting a run compares the deployment ID the
runner read—including an explicit expectation that no readable ID existed—with the ID
observed while holding the write lock. Transitions compare their own deployment ID.
A stale runner therefore cannot overwrite a newer deployment. Each bounded JSON record is written
to a same-directory temporary file, synced, atomically replaced, and followed by a
directory sync. Panels rejects links, non-regular files, oversized files, unsupported
versions, malformed fields, and invalid SHAs rather than guessing.

`GET /api/deployment-status` projects that evidence into `idle`, `preparing`,
`restarting`, `back_up`, `problem`, or `unknown`. Preparation and restart evidence
expires if the runner disappears. A proved success appears as `back_up` only briefly
and only when the running app SHA exactly matches the requested SHA. Failure and
rollback remain problems until a later deployment supersedes them. The server watches
the lifecycle path and sends the existing contentless change signal after create,
replace, change, or deletion, so connected browsers re-read the canonical file.

An app deployed before this lifecycle feature cannot expose the new status endpoints or
observe runner updates while that old app is serving. The current workflow tool and
finalizer still record the rollback durably. The status becomes visible again when a
compatible app is restored or deployed; this is the bootstrap limitation when crossing
the feature boundary.

## Replacement and recovery

Deployment is serialized. It fully stages and validates the candidate while production
remains live, then stops the service and runs the deployed application's
`backup-current` command. That command publishes a verified snapshot containing both
the database and managed files. Deployment then retains the prior app as a temporary
fallback, installs the candidate at `current/app`, restarts Panels, and requires health
to report the requested commit.

Every candidate, including its staged copy, must contain the complete current runtime
and both executable entrypoints. The existing app still receives manifest, artifact,
and safe-tree validation, but it may predate the interactive `bin/panels` entrypoint.
This allows an intact older production app to upgrade while still rejecting a tampered
one before downtime, backup, restart, or filesystem replacement begins.

If replacement, restart, or health proof fails, deployment stops the candidate, restores
the verified database-and-files snapshot, restores the fallback app, restarts it, and
proves the prior SHA healthy. If recovery cannot be completed and proved, the error
reports the exact retained filesystem paths for operator continuation. A successful
transaction removes the fallback.

Code rollback uses the same workflow with an earlier full SHA. Because database
migrations may be destructive, automatic failure recovery restores the pre-cutover
snapshot; a later intentional rollback deploy does not independently rewind data.

## Existing-host precondition

The operator must establish the single-user filesystem layout, user units, runner, and
persistent paths before enabling automatic deployment. Repository deployment does not
perform that host setup or change the existing Tailscale Serve route.

Code paths: `.github/workflows/deploy.yml`, `src/planner/environments/app.py`,
`src/planner/environments/deployment.py`,
`src/planner/environments/deployment_lifecycle.py`,
`src/planner/environments/cli.py`, and `src/planner/skill_sources.py`.

---

_Last verified: 2026-10-18 (local gate implementation; main activation remains separate)._
