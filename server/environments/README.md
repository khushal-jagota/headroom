# Environment runtime

This package owns update state, retained VPS metrics, and VPS status behind an explicit per-server runtime.
Production composition supplies the staging, installer, and Mac adapters. Tests supply isolated external operations.

Updates keep one attempt file outside SQLite and backups. OS `flock` protects compare-and-swap.
Notifications go out after unlock, so a synchronous subscriber can re-enter without deadlock.
Atomic replacement preserves unknown fields as native JSON values. GitHub IDs use safe integer numbers.
A lock wait blocks Node's event loop.
Strict UTF-8 reads treat invalid bytes and BOM input as an unreadable record.

The provider reads GitHub publications. It checks the repository, workflow, branch, event, conclusion,
SHA, pinned artifacts, Mac artifact, expiry, and runtime metadata. Downloads stream with a 60-second timeout for each network read.
Disk writes consume no read deadline. Proof ZIP reads check size and CRC.
Credentials stay outside records and public errors. Installation requires a running SHA, canonical installed app root, and this server's own unit.

Update preparation first closes new starts while accepted work can still save its results.
Local child retirement retains final callbacks until their writes settle. Cancel invalidates that preparation before it reopens work.
The final save barrier lives outside SQLite and rollback snapshots. It retains accepted executor saves through actual settlement.
The installer holds a separate lifetime lock through installation and rollback. Startup with that lock and a durable barrier admits health checks while normal producers wait.
An abandoned attempt releases only its own admission generation. Bounded probes retry recovery even when no file changes.
Preparation audits current definitions, skills, and Tickets through the existing write door before the transition to `preparing`.
All six routes require the owner and retain configured Mac health/update reads. Deployment body and revision share one captured read.

Startup and owner actions can schedule advancement; the readiness hook supplies later polls.
A 250 ms observer watches for external attempt-file changes.
There is no separate advancement timer. Shutdown fences continuations synchronously and joins only until the shared absolute deadline.
A standalone stop without that deadline uses the existing 30-second grace.
Late stage, backup, and installer settlements cannot write records or publish readiness after ownership ends.
Node releases admit a database at their head or earlier on their own schema-step chain. Schema steps in `core/database/schema_steps` run only in the installer, on the stopped database after the swap, with a backup.
Release creation and extraction can require an explicit OS, architecture, and role.
Default home admission refuses executor markers. Explicit targets require a matching runtime marker, including Darwin home targets.
Darwin home metadata uses a version 2 Node contract with an inventory digest and the Mac OS and library boundaries.

Startup recovery scopes workspace admission to each pass and retains pending recoveries through bounded backoff.
A denied starts lease ends that boot pass without deleting its durable recovery rows. Recovery uses the shared admission owner for supervisor priority, pacing, and outcome settlement.
Executor update reads select a cached service for each machine through the registered platform.
Each machine keeps separate request IDs and reports. Darwin machines share archive bytes, and Linux storage stays separate.
Native first-home installation lives in `server/installation/`. It admits sealed Node home releases,
creates private state and a new service family, and requires exact ready health before backup jobs start.
Before activation, the admitted candidate creates a fresh database with its own schema seed and bootstrap.
Any existing database entry causes refusal. Updates never call this initializer.
Rollback authenticates created files and loaded service identities. Uncertain stop retains the installation.
The flat JSON stdin entry is `server/installation/home_main.ts`.
Linux executor archive production and low-level installation use the shared native builder, target admission, and process owners in `linux_executor/`.
The low-level installer writes a private credential and exclusive user unit after admission. It does not start services.
Real Darwin ABI acceptance runs in the hosted lifecycle workflow. It exercises sealed archive admission,
foreground boot, launchd installation, and copied update rollback.

## Retained metrics and status

Metrics use one auxiliary writer in a sampler worker and two read children, each with a separate watchdog.
Native SQL deadlines and cleanup survive a blocked APP loop or APP death; HTTP replies still wait for APP scheduling.
A later read retries a failed replacement after a 50 ms backoff. The original request deadline includes that wait and cold startup.
The auxiliary writer uses the existing engine without application projections. The sampler runs independently of dispatch and retains an in-flight pass through settlement.
Test mode supplies `collector: null` and performs no host collection. The sampler worker, both read children, and watchdogs still start.
Pinned Pako 3.0.2 with `legacyHash` keeps the stored compressed bytes stable. Node's built-in zlib differs on the retained payload corpus.

The recorded empty-collector runtime starts in **349 ms**. Summed APP/read-child RSS is **363.5 MiB**;
that sum counts shared pages more than once. [Runtime costs](evidence/metrics-runtime.json) and
[native-query lifetime evidence](evidence/metrics-native-lifetime.json) retain the measurements.

The collector reads proc, sysfs, cgroups, and bounded service output without process arguments or environments.
Baselines include boot, PID/start time, device membership, and cgroup inode. Slow evidence stays cached between slow passes.
The Linux clock stays continuous across process replacements.
Darwin reads `mach_absolute_time` through Koffi, with integer timebase conversion.
The Darwin collector uses the existing native bridge for host and process observations.
Linux-only pressure, cgroup, thermal, device, and systemd readings stay unavailable on Darwin.

VPS status and backup evidence use the existing response shapes. Cleanup rechecks targets before deletion and keeps live references.
The operator CLI calls `apply_cleanup_inventory`. On Linux, the collector factory calls `build_linux_collector`.

**Integer boundary:** the ten metrics integer overrides refuse values outside ±9,007,199,254,740,991.
[DEVIATIONS.md](../DEVIATIONS.md#vps-metrics-integer-configuration) lists the keys and rationale.
[Configuration boundary](evidence/metrics-config.json), [store compatibility](evidence/metrics-store.json), and
[command/clock proof](evidence/collector-command-clock.json) hold the small durable reports.

## Backup, fixtures, and operator functions

Backup captures a snapshot, and restore writes it to a stopped destination.
The native full-copy step is synchronous on its calling Node event loop. It requests the largest positive SQLite page budget instead of 100-page turns.
Snapshot metadata and managed-file manifests use sorted, ASCII-escaped, two-space JSON bytes.
UTC timestamps use `+00:00`, with six fractional digits when nonzero.
New snapshots include skills and Worker settings. They leave Ticket files outside the snapshot.
Legacy snapshots still restore their recorded files. A later managed-root failure does not undo the database replacement.

The build-time [schema generator](../core/database/tools/generate_seed.ts) writes the schema seed and migration graph: the frozen Python baseline in `core/database/python_baseline/` plus the TypeScript schema steps.
Runtime bootstrap creates fresh identities and uses the existing domain writers. It requires no Python interpreter.
The staging fixture uses those writers for its fictional projects, Managers, Tickets, notes, and image.
Its navigation starts at the prepared schema version. Normal server publication performs the runtime upgrade.
Bootstrap creates only new isolated fixture/test databases and never upgrades an existing application database.

Regenerate and check the live seed from the repository root with:

```sh
node server/core/database/tools/generate_seed.ts
node server/core/database/tools/generate_seed.ts --check
```

CI runs `--check`. It refuses a hand edit of the seed or graph, Python migration drift (the baseline graph no longer matches `src/planner/core/migrations/versions`), and a chain that does not start at the baseline head. With no steps the live files equal the baseline byte for byte.
The baseline is frozen, and nothing in the repository rewrites it. Schema changes are TypeScript schema steps only.
The JSON stores DDL and the exact revision, without seeded data or shared identities.

The registry uses blocking kernel `flock`. Its wait blocks the Node event loop.
Use this registry layer only from a command process, never from a server request or background loop.

The [command table](commands.ts) exports `environment_commands` for the CLI.
Each entry supplies its command path, option types/defaults, and a plain [handler](cli.ts).
Handlers return separate stdout, stderr, and exit-code values. They do not parse arguments or write to process streams.

Ten handlers cover status, cleanup, backup, backup-current, skill provisioning, restore, prepare, inspect, reset, and remove.
Cleanup remains a dry run unless the caller supplies apply. Each invocation collects a fresh inventory.
Backup-current requires the real app validator as an explicit dependency. It has no successful default validator.

Ticket workspace cleanup starts outside test mode, with a ten-minute initial wait independent of dispatcher admission.
The matching-database gate selects legacy scratch roots for inventory. Those roots remain for owner review.
The workspace owner inventories complete Ticket folders before removal and applies the seven-day lifecycle clock.
CLI and scheduled cleanup retain a runtime-unavailable fence until the conversation supervision owner supplies custody and its retirement operation.
Linux folder removal uses held directory handles and no-follow opens. Mac cleanup inventories and preserves folders with an unsupported-safe-removal reason.
Its close fences new work at the shared deadline. An already dispatched filesystem removal or a Git child that ignores TERM can outlive that join.

## Operator factory

`commands.ts::operator_commands` reuses the canonical CLI catalog for all seventeen
operator leaves. Each handler loads only when selected. The executable owns argv,
help, stream output, and the final error renderer.
`operator_dependencies.ts::create_production_operator_dependencies` takes the
selected name, explicit `undefined` facts, and an immutable invocation environment
snapshot. It assembles only that command's production adapters. Status and cleanup
share one invocation child owner, join actual closes, and preserve the first signal
through cleanup failures. Path expansion uses that invocation's home.

Test admission combines the invocation snapshot with the actual process test markers.
Mutation builders use the returned checked paths before filesystem access. Link removal
retains the checked parent and link name, so it removes the entry itself. Configuration
selectors come from `core/config.ts`; the default `data/planning.db` is relative to cwd.

All seventeen leaves are available, including app-build, run, serve, and restart.

Operational failures carry raw `failure_message` and `stderr_prefix` fields.
Success and warning streams stay separate. The accepted hidden diagnostic retains
its literal unsupported transcript through `server/cli/main.ts`. That executable
binds the table and factory.
