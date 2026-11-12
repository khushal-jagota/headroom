# Environment port, Part 2

This package ports update state, retained VPS metrics, and VPS status behind an explicit per-server runtime.
[main.ts](../main.ts) does not enable that runtime. Part 4 must supply the real staging, installer, and Mac adapters first.

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
A second proof forces an update while the real core is busy. Its external installer adapter requests actual application shutdown.
It holds the accepted provider return through shutdown and ownership transfer, with detach released explicitly.
Its later return performs no store/publication access or database mutation. It also checks the shared loop, metrics, and update stop deadline.
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
| Real core: 2 | Existing server/database fixtures and real conversation core with scripted backends/adapters. Proves idle completion/admission, refused writes, failure reopening, forced-now update through application shutdown with held detach/provider return, ownership, and late-return fencing. | Bodies 1975 / 813 ms; combined one-worker process 6.23 s. |
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
