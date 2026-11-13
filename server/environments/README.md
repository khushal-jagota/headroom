# Environment port, Part 2

This package ports update state, retained VPS metrics, and VPS status behind an explicit per-server runtime.
[main.ts](../main.ts) requires the real production composition in normal mode and fails before startup until it exists.
Its existing test-mode path remains bare. Part 4 must supply the real staging, installer, and Mac adapters first.

Implementation differences that the Manager accepted as implementation details:

- Python emits attempt changes inside the file lock. TS emits after unlock to allow synchronous subscriber re-entry.
- TS observes external attempt-file changes every 250 ms. Python has no matching observer.
- Even with no collector, TS starts a sampler worker and two read processes, each with a watchdog worker. The recorded summed RSS is 363.5 MiB, with shared pages counted more than once.

Updates keep one attempt file outside SQLite and backups. OS `flock` protects compare-and-swap.
Python emits notifications inside the lock. TS emits after unlock to permit synchronous subscriber re-entry without deadlock.
Atomic replacement preserves unknown fields and numeric representations. A 50 ms Python lock hold blocks Node's event loop for 57.4 ms.
Strict UTF-8 reads retain Python's unreadable-record behavior, including invalid bytes and BOM input.

The provider keeps Python's current publication contract. It checks the repository, workflow, branch, event, conclusion,
SHA, pinned artifacts, Mac artifact, expiry, and runtime metadata. Downloads stream with a 60-second timeout for each network read.
Disk writes consume no read deadline. Proof ZIP reads check size and CRC.
The direct provider comparison includes runtime metadata's exact-match and Unicode decimal patch-version boundaries.
Credentials stay outside records and public errors. Installation requires a running SHA, canonical installed app root, and this server's own unit.

Workspace admission audits current definitions, skills, and Tickets through the existing write door and a temporary registry.
Commit steps finish before the guarded file transition to `preparing`; refusal keeps work open. The application registry stays unchanged.
All six routes require the owner and retain configured Mac health/update reads. Deployment body and revision share one captured read.

The updater follows Python: startup and owner actions can schedule advancement; the readiness hook supplies later polls.
TS adds a 250 ms external-file observer; Python has no matching observer at this composition boundary.
There is no separate advancement timer. Shutdown fences continuations synchronously and joins only until the shared absolute deadline.
A standalone stop without that deadline uses the existing 30-second grace. Python does not join its daemon prefetch thread.
Late stage, backup, and installer settlements cannot write records or publish readiness after ownership ends.
The Node release admission bridge belongs to Part 4.

The real-core proofs now cover idle-update admission after a native turn completes, HTTP write refusal without database mutation,
continued reads, and admission reopening after installer launch failure. They use ordinary routes and scripted external operations.
A second case forces an update while the real core is busy. Its external installer adapter requests actual application shutdown.
The Manager's later stop policy supersedes its original ownership-transfer expectation.
The revised case holds a raw provider beyond its shared 0.5-second grace. Ownership stays held while actual work remains.
After actual provider and child settlement, the original close flight finalizes automatically and permits a successor.
The revised forced case is included in the focused lifecycle run; its exact owned children close before exit.
The real installer/systemd path, vendor SDK behavior, loop-provider cancellation, and an absolute total shutdown bound remain unproven.

## Retained metrics and status

Metrics use one auxiliary writer in a sampler worker and two read children, each with a separate watchdog.
Native SQL deadlines and cleanup survive a blocked APP loop or APP death; HTTP replies still wait for APP scheduling.
A later read retries a failed replacement after a 50 ms backoff. The original request deadline includes that wait and cold startup.
The auxiliary writer uses the existing engine without application projections. The sampler runs independently of dispatch and retains an in-flight pass through settlement.
Test mode supplies `collector: null` and performs no host collection. The sampler worker, both read children, and watchdogs still start.
Pinned Pako 3.0.2 with `legacyHash` retains Python's compressed bytes. Node's built-in zlib differs on the retained payload corpus.

The recorded empty-collector runtime starts in **349 ms**. Summed APP/read-child RSS is **363.5 MiB**;
that sum counts shared pages more than once. [Runtime costs](evidence/metrics-runtime.json) and
[native-query lifetime evidence](evidence/metrics-native-lifetime.json) retain the measurements.

The collector reads proc, sysfs, cgroups, and bounded service output without process arguments or environments.
Baselines include boot, PID/start time, device membership, and cgroup inode. Slow evidence stays cached between slow passes.
A held old Node process, a new process, and Python prove Linux clock continuity across replacements.
Darwin matches CPython's `mach_absolute_time` through existing Koffi, with integer timebase conversion.
Native Linux statistics and all Darwin native branches remain unexecuted here. Hosted platform verification is still required.

VPS status and backup evidence use the existing response shapes. Cleanup rechecks targets before deletion and keeps live references.
This part reads backup evidence. Part 3's operator CLI will call `apply_cleanup_inventory`.
Part 4's production activation will call `build_linux_collector` and supply the required real update adapters.

## Evidence and test admission

These cases cover boundaries absent from existing pure rules and empty responses. Costs describe test bodies or named operations.
Review removed five repeated state vectors and sixteen repeated caller combinations. The existing cases now cover the requested timeout, stop, and reader-recovery repairs.

| Cases | Distinct risk and cheapest faithful proof | Recorded cost |
| --- | --- | --- |
| Updates: 7 | Direct Python/TS file/rule parity, real lock, existing Fastify/FastAPI hosts, scripted transport/real ZIP. Proves unknown metadata, invalid UTF-8/BOM recovery, settlement, admission, stop fences, provider trust, and all owner doors. | File 3.18 s; service 2.19 s; rules 0.27 s; continuations 2.85 s; HTTP 1.01 s; provider 0.92 s; lock blocks 57.4 ms. |
| Metrics: 5 | Existing samples/sidecar fixture: 82 operations compare rows/compressed bytes. Real sampler/readers/native SQLite prove cadence, ownership, deadline and death cleanup; existing hosts compare 28 HTTP answers. | Store 1.89 s; sampler 2.70 s; native 2.93 s; runtime 0.56 s before the 0.80 s recovery extension; HTTP 2.01 s. |
| Status: 3 | Direct parity on synthetic app/backup trees and injected probes: sanitized evidence, cleanup races, backup ordering/link/I/O failure. | 0.27 / 0.38 / 0.96 s. |
| Collector: 3 | Existing fake proc/sys fixture: full sample sequences and PID race. Scripted child: output/time bounds, retained pipe, exact settlement, and process clock continuity. | Sequence 0.42 s; PID 0.24 s; command/clock 2.33 s including cleanup; clock extension 0.22 s. |
| Configuration: 1 | Direct grammar/path parity and accepted TS integer boundary; Python's extreme-value answer remains informational. | 0.14 s. |
| Auxiliary: 7; composition: 2 | Existing Vitest database/server fixtures: schema/rollback/maintenance scope, resources, both poll hooks, ownership through re-entrant startup/shutdown. | Bodies 1.456 s + 0.765 s; prior shared 24-test process 3.43 s. |
| Real core: 2 | Existing server/database fixtures and real conversation core with scripted backends/adapters. The forced-now case proves ownership after grace expiry and automatic finalization after actual settlement. | Earlier idle body 1975 ms. Revised forced case 1458 ms; focused runner 4.99 s. |
| Historical browser: 1, outside default CI | Existing host/frontend, Chromium, 21 Python-written samples, actual queries/chart/workload. | 3.24 s. |

Small durable reports: [store compatibility](evidence/metrics-store.json), [command/clock proof](evidence/collector-command-clock.json),
and [configuration boundary](evidence/metrics-config.json). Historical browser output:
[screenshot](/files/tickets/t_3ud37ykx/artifacts/part2-evidence/vps.png) and
[manifest](/files/tickets/t_3ud37ykx/artifacts/part2-evidence/manifest.json).
[The archive](/files/tickets/t_3ud37ykx/artifacts/part2-evidence/historical-profile-and-browser.zip) contains the original browser receipt, helper, and raw rebuild reports.
That run predates the Chief's current rule and the Goals merge.
Its two out-of-VPS `/api/goals` resource 404s describe the earlier build. It reports no page errors and confirms that its owned server stopped.

**Accepted integer boundary:** the Manager accepted explicit refusal outside ±9,007,199,254,740,991 for the ten metrics integer overrides.
Python accepts `9007199254740993`; TS returns an exact-range error, which the existing case asserts as a gating result.
The report records `accepted: true`; Python's extreme-value answer remains informational.
[The accepted differences registry](../DEVIATIONS.md#vps-metrics-integer-configuration) lists the keys and rationale. Part 2 source review and merge approval remain separate.

The accepted [rebuild baseline and profile](../lifecycle/REBUILD_PROFILE.md) also lands with this part.
It records Python timing, Python-written projection reuse, repeated TS stalls, and navigation timing. No optimization accompanies it.

## Reproduce and hand off

The Chief's current machine rule sends browser/E2E and full comparison runs to hosted CI only.
Local focused checks run one at a time, with each check's owned processes stopped before the next check.
Use an independent worktree after guarded dependency setup. Remove inherited `PLAN_*` values before repository execution.
Supply private provider homes and explicit test-owned configuration, database, log, and lock paths. Captured runs used the ticket's scrubbed launcher.
One focused local example, with a fresh report directory:

```sh
scripts/dev pytest tests/compare/direct/test_unit21_updates.py \
  -k attempt_store_preserves_versions_and_compare_and_swap \
  --compare-report-dir=/absolute/private/new-report-directory
```

The complete Part 2 driver set runs on hosted CI:

```sh
scripts/dev pytest \
  tests/compare/direct/test_unit21_updates.py \
  tests/compare/direct/test_unit21_metrics.py \
  tests/compare/direct/test_unit21_status.py \
  tests/compare/direct/test_unit21_metrics_collectors.py \
  tests/compare/direct/test_unit21_metrics_config.py \
  --compare-report-dir=/absolute/private/new-report-directory
scripts/dev npm --prefix server run test -- \
  tests/auxiliary_database.test.ts tests/environment_composition.test.ts \
  tests/update_core_lifecycle.test.ts
```

The historical archive preserves the retired browser helper as evidence. This PR provides no runnable browser scenario.
Passing Unit 21 lists use `"candidate": "typescript"`; existing branch CI runs them on Node 22 and Node 24.

Part 3 supplies backup/operator behavior. Part 4 supplies release packaging, the required real adapters, and production enablement.
Real installer/systemd behavior, the absolute production-adapter/core shutdown deadline, loop-provider cancellation, and executor-save recovery remain unproven.
Hosted native-platform proofs remain open. Review and merge do not deploy this package.

## Backup, fixtures, and operator functions

Part 3 adds backup capture and stopped-destination restore across Python and TypeScript.
The native full-copy step is synchronous on its calling Node event loop. It requests the largest positive SQLite page budget instead of 100-page turns.
Snapshot metadata and managed-file manifests use Python's sorted, ASCII-escaped, two-space JSON bytes.
UTC timestamps use `+00:00`, with six fractional digits when nonzero, matching Python's `isoformat()` form.
New snapshots include skills and Worker settings. They leave Ticket files outside the snapshot.
Legacy snapshots still restore their recorded files. A later managed-root failure does not undo the database replacement, as in Python.

The build-time [schema generator](../../tests/compare/direct/unit21_schema_seed.py) records 330 DDL statements and the exact Alembic frontier.
Runtime bootstrap creates fresh identities and uses the existing TS domain writers. Runtime requires no Python interpreter for this operation.
The staging fixture uses those writers for its fictional projects, Managers, Tickets, notes, and image.
Its navigation starts at the prepared schema version. Normal server publication performs the runtime upgrade.
Alembic owns application schema changes until cutover. TS bootstrap creates only new isolated fixture/test databases and never upgrades an existing application database.

The schema drift check runs in this branch's workflow. A merge that changes the Python schema fails until the generated file matches.
Every TS start now requires this exact frontier, so a staging merge with a new migration makes every TS list fail until regeneration.
From the independent worktree, with the private environment described above, regenerate and check it with:

```sh
.venv/bin/python tests/compare/direct/unit21_schema_seed.py
.venv/bin/python tests/compare/direct/unit21_schema_seed.py --check
```

The generator creates its own temporary Python database and migrates it through Alembic.
The JSON stores DDL and the exact revision, without seeded data or shared identities.

The first real drift exercise used target `d42fa18dd` and its `memory_notice_turns_are_maintenance` migration.
Before regeneration, `--check` exited 1 and the existing fresh-schema comparison failed against the saved frontier.
After regeneration, both passed. Only the revision changed; all 330 DDL statements remained identical.
The fresh-schema body took 1.03 seconds and produced zero skill-tree guard receipts.

The registry uses blocking kernel `flock`. Its wait blocks the Node event loop.
Use this registry layer only from a command process, never from a server request or background loop.

The [command table](commands.ts) exports `environment_commands` for the future CLI dispatcher.
Each entry supplies its Python command path, option types/defaults, and a plain [handler](cli.ts).
Handlers return separate stdout, stderr, and exit-code values. They do not parse arguments or write to process streams.
The Manager chose this seam because the CLI unit has no executable dispatcher yet.
Executable binding, parsing, help, and process exit behavior remain open for that unit.

Ten handlers cover status, cleanup, backup, backup-current, skill provisioning, restore, prepare, inspect, reset, and remove.
Cleanup remains a dry run unless the caller supplies apply. Each invocation collects a fresh inventory.
Backup-current requires Part 4's real app validator as an explicit dependency. It has no successful default validator.
App build, app identity, and the final environment launch remain with Part 4's real packaging adapters.

Focused comparisons reuse private paths and existing fixtures:

| Boundary | Evidence and measured local cost |
| --- | --- |
| Fresh DDL, required bootstrap rows, unique identities, atomic failure | Existing fresh-schema case, 0.97 seconds. It also compares page size, auto-vacuum, user version, and journal mode with Python. Regeneration passes after the target integration. |
| Backup/restore in both languages, open WAL, managed bytes/links, legacy files, retention | The same case uses a real Ticket. Ordinary readers and writers preserve its complete payload and persist edits after reopen. Its populated run takes 5.53 seconds and compares exact metadata/manifest bytes. |
| Concurrent source commits during backup | One new case, 0.27 seconds. A second SQLite connection commits after each partial-copy yield. The copy completes with a coherent counter/audit snapshot and integrity check. |
| Restore refusal and failed swaps | Existing filesystem failure case, 2.14 seconds. It includes the early destination guard and the accepted unknown-root error. |
| Registry lifecycle, TS-written fixture, private lease, rollback, launch environment | One connected comparison, 2.43 seconds. |
| Scratch database admission, real Git preservation, timer and cancellation | Three distinct existing comparisons, 2.61 seconds for their process. |
| Scratch ownership during startup and shutdown | Two existing composition cases extended, 5.61 seconds for their process. |
| Command streams, option metadata, and caught/uncaught failures | One function comparison, 2.08 seconds in the body and 5.37 seconds for its process. It includes a real permission refusal and deterministic EINTR retry over the real kernel lock. |

The restore proof covers domain reads and writes. It does not install application commit projections or claim HTTP/server startup.
Part 4's hosted rehearsal supplies the real process, installer, supervisor, and packaged-runtime proofs.
Scratch cleanup starts only for a matching database outside test mode, with a ten-minute initial wait independent of dispatcher admission.
Its close fences new work at the shared deadline. An already dispatched filesystem removal or a Git child that ignores TERM can outlive that join.
Production environment activation remains off until Part 4 supplies the real adapters.
