# Separate TypeScript Mac executor

The package producer assembles a separate Node executor and unsigned installation assets from supplied Darwin arm64 inputs.
No command in this document ran on the owner's Mac during this Ticket.
The shared codec embeds its schema as `WIRE_SCHEMA` in `server/executor/wire.generated.ts`. It does not read `wire.schema.json` at runtime.
Use a separate app, supervisor, job, and state root. Keep the Python installation idle for rollback after cutover.

The TypeScript default state root is `~/Library/Application Support/Panels/executor-typescript`.
The Python default is its sibling `executor`. TypeScript refuses that Python root, its descendants, and resolved aliases.
An explicit state root still needs an operator review against the actual Python installation.
Provider homes and credentials remain account resources. Separate roots do not solve shared skill publication.
The owner must choose the shared-publication policy before concurrent native use.

## Produce the reviewed package

Use the exact approved source revision. Supply an official Darwin arm64 Node distribution and production dependencies for that Node version.
Use Node 22.18 or later in the Node 22 series, or Node 24.
Prepare dependencies with `npm ci --omit=dev` from the approved `server/package-lock.json` on Darwin arm64.
The producer requires the installed lock metadata and exact direct dependency versions.
It checks Mach-O headers for Node, better-sqlite3, and Koffi. These checks do not establish provenance or ABI compatibility.
The Manager must arrange genuine Darwin package construction and native addon load checks before installation.

The Manager can dispatch `.github/workflows/typescript-lifecycle-native.yml` with `build_mac_package=true` and the four reviewed target inputs.
It uses standard `macos-15` arm64 and official Node 22, then uploads the archive, assets, build metadata, and this document.
The workflow does not install, sign, launch, or contact the configured peer.

Run the producer with production dependencies under its own `server/node_modules` directory. Node strips TypeScript without a compiler.

```sh
node server/executor/mac/packaging/main.ts \
  --source "$APPROVED_SOURCE" \
  --node-distribution "$DARWIN_ARM64_NODE" \
  --node-modules "$DARWIN_PRODUCTION_NODE_MODULES" \
  --output "$NEW_PACKAGE_DIRECTORY" \
  --revision "$APPROVED_REVISION" \
  --home "$TARGET_MAC_HOME" \
  --executor-id "$APPROVED_EXECUTOR_ID" \
  --server-url "$APPROVED_HTTPS_PEER" \
  --backend-path "$REVIEWED_BACKEND_PATH"
```

All input paths must identify reviewed inputs. The output parent must exist, and the output directory must be new.
`--home` is the target account's absolute home, even when another machine assembles the package.
`--backend-path` contains absolute directories for the installed provider commands and ordinary system tools.
The producer does not download, execute, sign, or install its inputs.

The output contains these artifacts:

| Artifact | Purpose |
| --- | --- |
| `panels-mac-runtime.tar.gz` | Runtime archive for initial installation and later updates. |
| `runtime/` | Expanded archive with `node/bin/node`, `bin/panels`, production dependencies, and TypeScript source. |
| `runtime/mac-runtime.json` | Exactly `schema_version`, `application_revision`, `platform`, and `architecture`. |
| `assets/` | Candidate app metadata, native launcher source, manual console, LaunchAgent, and target paths. |
| `build.json` | Source revision, Node version, Node digest, archive digest, and target paths. |
| `INSTALLATION.md` | This handoff document. |

The wire schema remains embedded in `wire.generated.ts`. The runtime excludes the Python server and Python interpreter.
The two plain Hermes scripts remain provider assets. The installed Hermes environment supplies their interpreter.
The package also includes static skills, Worker seed data, CLI metadata, and the shell startup asset.

## Install from the owner's native session

These commands are a future owner procedure. They do not authorize this Ticket to install or launch anything.
Keep the Python installation idle. Review any nondefault Python root before the first TypeScript start.
Resolve the shared provider skill policy before the first real provider session.

Use a new private package directory on the Mac. Set `PACKAGE` to its absolute path.
Compare `build.json` and the archive digest with the Manager's approved revision and digest through a separate trusted record.
Check that `assets/installation.json` names this account's actual `HOME`, reviewed executor ID, HTTPS peer, and provider PATH.
If any target differs, regenerate the package assets before installation.

```sh
shasum -a 256 "$PACKAGE/panels-mac-runtime.tar.gz"
cat "$PACKAGE/build.json"
cat "$PACKAGE/assets/installation.json"
```

The fixed paths are:

| Item | Path or identity |
| --- | --- |
| Install | `~/Deployments/PanelsExecutorTypeScript` |
| Fixed Node | `~/Deployments/PanelsExecutorTypeScript/supervisor/node/bin/node` |
| Fixed bootstrap | `~/Deployments/PanelsExecutorTypeScript/supervisor/server/executor/mac/entrypoint.ts` |
| Manual console | `~/Deployments/PanelsExecutorTypeScript/bin/panels` |
| Signed app | `~/Applications/Panels TypeScript.app` |
| App executable | `~/Applications/Panels TypeScript.app/Contents/MacOS/Panels` |
| Bundle identity | `com.panels.executor.typescript.app` |
| LaunchAgent | `~/Library/LaunchAgents/com.panels.executor.typescript.plist` |
| Job label | `com.panels.executor.typescript` |
| State | `~/Library/Application Support/Panels/executor-typescript` |
| Unix socket | `~/Library/Application Support/Panels/executor-typescript/run/executor.sock` |
| Launch logs | `~/Library/Application Support/Panels/executor-typescript/logs/launch-agent.{stdout,stderr}.log` |
| Updated runtime | `<state>/updates/releases/<revision>-<archive-sha256>` |

The native app executes the fixed supervisor Node and bootstrap with the original arguments.
The supervisor launches the selected versioned runtime as a child. It retains its own Node across runtime updates.
The versioned `bin/panels` uses its own adjacent Node and entrypoint. The supervisor prepends this directory to agent PATH.
The manual console always enters through the signed app.

Check that the install, app, and job destinations are absent before this first-install procedure.
Do not overwrite an existing installation with these commands.

```sh
set -eu
TS_INSTALL="$HOME/Deployments/PanelsExecutorTypeScript"
TS_ROOT="$HOME/Library/Application Support/Panels/executor-typescript"
TS_APP="$HOME/Applications/Panels TypeScript.app"
TS_PLIST="$HOME/Library/LaunchAgents/com.panels.executor.typescript.plist"
test ! -e "$TS_INSTALL" && test ! -L "$TS_INSTALL"
test ! -e "$TS_APP" && test ! -L "$TS_APP"
test ! -e "$TS_PLIST" && test ! -L "$TS_PLIST"
umask 077
mkdir -p "$HOME/Deployments" "$HOME/Applications" "$HOME/Library/LaunchAgents"
mkdir "$TS_INSTALL"
mkdir "$TS_INSTALL/supervisor"
tar -xzf "$PACKAGE/panels-mac-runtime.tar.gz" -C "$TS_INSTALL/supervisor"
mkdir "$TS_INSTALL/bin"
install -m 755 "$PACKAGE/assets/bin/panels" "$TS_INSTALL/bin/panels"
ditto "$PACKAGE/assets/Panels TypeScript.app" "$TS_APP"
mkdir -p "$TS_APP/Contents/MacOS"
clang -arch arm64 -O2 "$PACKAGE/assets/launcher.c" -o "$TS_APP/Contents/MacOS/Panels"
```

Choose the owner's Apple Development signing identity. Do not substitute ad hoc signing.
Sign this separate app and retain its identity across later updates.

```sh
security find-identity -v -p codesigning
codesign --force --sign "$APPLE_DEVELOPMENT_IDENTITY" "$TS_APP"
codesign --verify --strict --verbose=2 "$TS_APP"
codesign -dv --verbose=4 "$TS_APP"
plutil -lint "$TS_APP/Contents/Info.plist"
plutil -lint "$PACKAGE/assets/com.panels.executor.typescript.plist"
```

Complete the native load and consent checks below before the first service start.
The signed app must receive any required file permissions through the normal macOS consent flow.
Install only the separate TypeScript LaunchAgent:

```sh
mkdir -p "$TS_ROOT/logs"
chmod 700 "$TS_ROOT" "$TS_ROOT/logs"
install -m 600 "$PACKAGE/assets/com.panels.executor.typescript.plist" "$TS_PLIST"
launchctl bootstrap "gui/$(id -u)" "$TS_PLIST"
launchctl print "gui/$(id -u)/com.panels.executor.typescript"
"$TS_INSTALL/bin/panels" executor health
```

Check the selected revision, socket path, health response, and launch logs before real work.
Record the package digest, Node version, signing identity, bundle identity, and job label with the native handoff.
Use `executor quiesce --exit-when-ready` before a planned stop.
Require committed exit, zero work counts, and exit of the recorded runtime before unloading this job.
If startup fails, retain the state and logs. Use the recovery procedure below when SQLite reports version zero.

## Preserve a failed first initialization

An existing outbox with SQLite `user_version=0` is incompatible. TypeScript refuses it before a lock or storage mutation.
A failed fresh initialization can leave this file. The refusal deliberately preserves evidence and possible work.
Version zero alone does not establish that the directory contains no work.
Do not delete the database, its WAL, its SHM, or the state root to force another start.

Use these exact default paths for recovery. Review any explicit state-root override before use:

```sh
TS_ROOT="$HOME/Library/Application Support/Panels/executor-typescript"
TS_PANELS="$HOME/Deployments/PanelsExecutorTypeScript/bin/panels"
TS_NODE="$HOME/Deployments/PanelsExecutorTypeScript/supervisor/node/bin/node"
TS_PACKAGE="$HOME/Deployments/PanelsExecutorTypeScript/supervisor/server"
TS_JOB='com.panels.executor.typescript'
PYTHON_ROOT="$HOME/Library/Application Support/Panels/executor"
EVIDENCE="$HOME/Deployments/PanelsExecutorTypeScript/recovery-evidence-$(date -u +%Y%m%dT%H%M%SZ)"
```

The evidence directory must be new and outside both state roots. Its parent must already exist.
Never substitute the live Python console or its job label.
Check both resolved roots and the console before a service action:

```sh
"$TS_NODE" --input-type=module - "$TS_ROOT" "$PYTHON_ROOT" "$TS_PANELS" "$EVIDENCE" <<'JS'
import { existsSync, realpathSync, statSync } from 'node:fs';
import { basename, dirname, isAbsolute, resolve } from 'node:path';
const [root, python, consolePath, evidencePath] = process.argv.slice(2);
if (![root, python, consolePath, evidencePath].every(isAbsolute)) throw Error('Absolute reviewed paths required.');
const live = realpathSync(python), state = realpathSync(root), consoleReal = realpathSync(consolePath);
const inside = (base, value) => {
  const expected = statSync(base, { bigint: true });
  for (let path = value; ; path = dirname(path)) {
    const actual = statSync(path, { bigint: true });
    if (expected.dev === actual.dev && expected.ino === actual.ino) return true;
    if (path === dirname(path)) return false;
  }
};
if (inside(live, state) || inside(live, consoleReal)) throw Error('Refusing Python installation alias.');
const evidenceParent = realpathSync(dirname(evidencePath));
if (inside(state, evidenceParent) || inside(live, evidenceParent) || existsSync(resolve(evidenceParent, basename(evidencePath))))
  throw Error('Evidence must be a new destination outside both state roots.');
console.log({ state, python: live, console: consoleReal });
JS
case "$TS_JOB" in com.panels.executor|'') printf '%s\n' 'Refusing live or empty job.'; exit 1;; esac
```

First, quiesce only the reviewed TypeScript installation:

```sh
PLAN_EXECUTOR_ROOT="$TS_ROOT" "$TS_PANELS" executor quiesce --exit-when-ready
```

Require committed exit, zero work counts, and exit of the recorded TypeScript process.
If the command refuses or the socket is absent, stop the recovery procedure.
A missing socket is not proof that no work exists.
For a failed first launch with no ready service, the owner must review startup logs and the exact job state instead.
Proceed only after the owner confirms that this is that failed first initialization, no process owns the root, and no work was admitted.
Do not force an unknown process to exit.
After that confirmation, unload only this TypeScript job to prevent a restart during recovery:

```sh
launchctl bootout "gui/$(id -u)/$TS_JOB"
```

Preserve the entire stopped root, including all SQLite WAL and SHM files, before inspection.
Use a new private evidence directory. Refuse an existing backup destination.

```sh
umask 077
mkdir "$EVIDENCE"
ditto --rsrc --extattr "$TS_ROOT" "$EVIDENCE/state-before-version0"
ditto --rsrc --extattr "$EVIDENCE/state-before-version0" "$EVIDENCE/inspection-copy"
```

Inspect the second copy only. A read-only SQLite connection can still need SHM state.
The first copy remains the untouched recovery baseline.
Read the logical database with its copied WAL, rather than inspect the main file alone:

```sh
"$TS_NODE" --input-type=module - "$TS_PACKAGE" "$EVIDENCE/inspection-copy/spool/outbox.sqlite3" <<'JS'
import { createRequire } from 'node:module';
import { lstatSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
const [packageRoot, path] = process.argv.slice(2);
if (!lstatSync(dirname(path)).isDirectory() || lstatSync(dirname(path)).isSymbolicLink()) throw Error('Refusing non-directory inspection spool.');
for (const suffix of ['', '-wal', '-shm']) {
  try { if (!lstatSync(path + suffix).isFile()) throw Error('Refusing non-regular inspection file.'); }
  catch (error) { if (error.code !== 'ENOENT' || suffix === '') throw error; }
}
const Database = createRequire(resolve(packageRoot, 'package.json'))('better-sqlite3');
const db = new Database(path, { readonly: true, fileMustExist: true });
try {
  const version = db.pragma('user_version', { simple: true });
  const tables = db.prepare("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name").all().map(row => row.name);
  const items = tables.includes('items') ? db.prepare('SELECT COUNT(*) AS count FROM items').get().count : 0;
  console.log({ version, tables, items });
  if (version !== 0 || items !== 0) throw Error('Not an empty version-zero initialization. Preserve all work.');
} finally { db.close(); }
JS
```

The owner must also review the full root inventory and first-launch logs.
Unknown tables, blobs, command journals, ready markers, admitted work, or incomplete evidence stop this procedure.
A zero item count does not authorize removal of unrelated files.
After the owner confirms an empty failed initialization, move its database files aside together. Do not delete them.
Keep the recovery directory outside `spool`, because the spool refuses unknown legacy contents.

```sh
FAILED="$TS_ROOT/recovery/version0-$(date -u +%Y%m%dT%H%M%SZ)"
if test -L "$TS_ROOT/recovery"; then printf '%s\n' 'Refusing recovery symlink.'; exit 1; fi
mkdir -p "$TS_ROOT/recovery"
mkdir "$FAILED"
for suffix in '' '-wal' '-shm'; do
  source="$TS_ROOT/spool/outbox.sqlite3$suffix"
  if test -e "$source"; then mv "$source" "$FAILED/"; fi
done
```

Use the reviewed TypeScript start procedure above only after this recovery review.
Do not restart or change the Python installation. Preserve the evidence and moved files until the owner closes recovery.

## Native checks that Linux cannot prove

Run these checks in the later separate native Ticket, with a fresh isolated peer and synthetic data first.
The operator must record the reviewed revision, Node version, arm64 architecture, package digest, exact paths, job, and app identity.
The Koffi bridge can load only the operating system's `libSystem.B.dylib` on macOS.
Missing symbols or failed calls must refuse the operation. A path-based fallback is forbidden.

| Call or boundary                                                   | Required real Mac check                                                                                                                                                  |
| ------------------------------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `openat(int,const char*,int,...)`                                  | Variadic mode passed as explicit promoted `int`, created-file mode 0600, directory/no-follow and Darwin close-on-exec flags, FIFO refusal, and ancestor-swap containment |
| `mkdirat(int,const char*,uint16_t)`                                | Darwin 16-bit `mode_t`, 0700 creation, existing-directory behavior, and parent-relative placement                                                                        |
| `unlinkat(int,const char*,int)`                                    | Correct zero flags for files and Darwin `AT_REMOVEDIR=0x80` for empty directories, retained-parent cleanup, nonempty-directory refusal, and missing-entry/error behavior |
| `renameat(int,const char*,int,const char*)`                        | Retained source/target parents, atomic publication, and failure cleanup                                                                                                  |
| `fsync(int)`                                                       | Directory flushes, error propagation, and successful commits on the selected native filesystem                                                                           |
| `fcntl(int,int,...)` with `F_FULLFSYNC=51` and explicit `int` zero | Variadic Darwin ABI and defined third slot, strict full-flush success, and refusal without a weaker fallback                                                             |
| `flock(int,int)`                                                   | Exclusive/nonblocking/unlock flags, contention refusal, crash release, and interrupted-call behavior                                                                     |
| `getpwnam` and Darwin `passwd` layout                              | Field offsets, pointer decoding, named-user expansion, and missing-account refusal                                                                                       |
| Packaged Koffi addon                                               | arm64 Mach-O architecture, pinned addon load from the relocated package, and no extra native library                                                                     |
| Packaged better-sqlite3 addon and journal worker                   | arm64 addon load inside the journal worker, full-flush commits, interrupted transaction recovery, and native connection cleanup before joined worker exit                |
| Native app and LaunchAgent                                         | Signed requester attribution, startup/restart, exact Unix socket path/mode, consent, and permission retention                                                            |
| Root aliases                                                       | Refusal of the Python root and descendants through symlinks and native case-insensitive aliases                                                                          |
| Process ownership                                                  | Darwin PID/start-time observations, priority, descendants, direct-child exit, and no leftover owned child                                                                |
| Provider homes and shared skills                                   | Fresh Claude/Codex/Hermes sessions, selected model/effort, canonical bytes, and owner-approved link restoration                                                          |
| Sleep, files, and updates                                          | Sleep/wake order and receipts, both file directions, failed-readiness rollback, and zero-work quiescence                                                                 |

Physical power-loss durability and permission persistence remain native evidence limits.
Synthetic Linux checks prove neither Darwin ABI nor native disk behavior.

## Canonical skills in the native session

Run these steps with synthetic snapshots, a private TypeScript state root, and a private provider home before any real provider starts.
The packaged entrypoint creates `MacSkillsPublisher` from `server/executor/mac/skills/index.ts` through the trusted host.

1. Publish a snapshot with non-ASCII text and CRLF bytes. Compare its generation digest, `SKILL.md` bytes, and account links with Python.
2. Publish the same snapshot again, then replace one source. Check generation reuse, owned retired links, stale generation links, and preserved unrelated entries.
3. Corrupt one existing generation. Check that the Mac preserves it under `.invalid-*` and rebuilds the authoritative bytes. Python refuses this case. Check other snapshot validation diagnostics against Python.
4. Hold the private `.install.lock` from another owner. Stop the publisher with one absolute deadline. Check its actual worker exit and lock cleanup separately.
5. Exercise empty-directory cleanup through `unlinkat` with Darwin `AT_REMOVEDIR=0x80`. Check refusal for a nonempty directory and for a substituted symlink.

Linux covers the synthetic publication flow and a failed descriptor close with successful retry. It does not prove Darwin flags or native filesystem behavior.
The shared server publisher retains its API. Its workspace `openat` now uses a variadic declaration and an explicit promoted `int` argument.
The Mac publisher imports the neutral account module and uses native operations only through the Mac platform module.
The Mac requires an absolute, existing provider home and an existing real `skills` directory before it writes any state.
The trusted adapter host supplies that configured home separately from the command snapshot. A different resolved path refuses.
The Mac never converts a legacy `skills` symlink. Refuse linked homes, linked `skills` entries, and linked state-root ancestors before the session.
Native `/tmp` and `/var` are aliases. Use their reviewed canonical paths or a private path under the real account home.
Add negative checks for real files/directories at managed names, foreign links, case-only collisions, and link loops.
Compare every provider entry before and after each refusal. Include bytes and raw targets of dangling user links.
Generation files and directories flush before rename. This host does not collect unreferenced generations or abandoned stages.
Only recognizable owned temporary account links receive a sweep here. No provider directory receives recursive deletion.
The held directory and immediate leaf rechecks prevent a swapped folder from redirecting deletion into its replacement.
They do not coordinate same-user programs that change entries concurrently. There is no atomic ownership comparison in rename.
The root-local lock does not serialize a second installation's writes into the same account home.
The owner must choose that policy before concurrent real use. Never snapshot or restore provider credentials or databases.

## Adapter and catalog handover

The Mac host reuses the Claude, Codex, and Hermes factories with the invocation's shared `child_processes` and `one_shot_children` owners.
Canonical skills publish before factory creation. The trusted host supplies the real account paths under the restrictions above.

Each Codex native-start factory receives an explicit fresh `RememberedCodexLoginRejection`.
This matches Python Mac `MacRunner._factory`: rejection memory belongs to that factory and disappears with it.
The VPS uses durable database memory across factories. The independent machine catalog reads saved login standing without that local rejection memory.

The existing snapshot API materializes the SDK snapshot on the main thread.
Canonical serialization, retained result bodies, and bounded readers belong to the catalog worker.
This slice does not put all catalog bulk work off the main thread.

Failed catalog cleanup faults the invocation and retains the native close capability for retry.
Shutdown retries that close before it joins a dependent process-close barrier.
The actual join remains incomplete until resource closure. After closure, it still reports the retained failure.

Real Darwin and provider checks remain part of the later separate native Ticket:

1. Check the real account homes, canonical skills, selected model and effort, and fresh provider sessions.
2. Stop a provider during startup. Check direct-child exit, pipe cleanup, and the shared owners' actual joins.
3. Exercise a failed catalog close and its retry. Check that neither an optional catalog answer nor a shutdown deadline certifies resource closure.

## Measure the main-thread database budget

The export-only `spool_burst(parent)` helper in `server/executor/mac/storage/testing/parity.ts` writes 256 near-1 MiB items.
It acknowledges each item and yields between every append and acknowledgement. A 10 ms timer measures actual event-loop delay.
The caller supplies a fresh private real-disk parent and synthetic homes. The helper starts no network host or provider.
Record maximum and p99 timer delay, maximum operation time, rows, bytes, elapsed time, and peak RSS.
Do not run this measurement in the live state root or on a memory-backed temporary filesystem.
The owner repeats it on the actual Mac filesystem before native admission.
If maximum timer delay exceeds 50 ms on the real Mac, move database work to an owned worker before admission.
Record a Linux result above 50 ms as a finding. Do not hide it behind the later Mac check or call it a pass.


### Directory operations for the native skills check

The platform module opens `.` relative to the retained directory with close-on-exec, then copies each `readdir` name before the next call.
It clears `errno` before `readdir`, distinguishes EOF from failure, and retains failed-close capabilities for retry.
Check the descriptor-relative stream open, `fdopendir`, `readdir`, `closedir`, `readlinkat`, and `symlinkat` on the actual arm64 package.
Check repeated enumeration, dangling links, loop refusal, retained-folder replacement, atomic link replacement, and cleanup after a failed close.
Linux uses the 64-bit `dirent` name offset 19. Darwin inode64 uses offset 21, with `d_namlen` at offset 18.
Record lengths above 1048 bytes on Darwin or 280 bytes on Linux refuse before name decoding. Unusual filesystems with larger Linux records are unsupported here.
The native code follows Apple's [inode64 layout](https://raw.githubusercontent.com/apple-oss-distributions/xnu/main/bsd/sys/dirent.h),
[Libc declarations](https://raw.githubusercontent.com/apple-oss-distributions/Libc/main/include/dirent.h), and
[ABI aliases](https://raw.githubusercontent.com/apple-oss-distributions/xnu/main/bsd/sys/cdefs.h).
Darwin arm64 uses the unsuffixed symbols. Darwin x86_64 selects the inode64 aliases.
These are source-based ABI choices. No Darwin call or native durability check ran on this Linux VPS.

## Command maintenance before storage closure

The command service retains known outcomes across journal and output failures. A retry resumes settlement without another provider call.
Planned replay seals admission. A canceled waiter does not cancel the shared gate, and replay completion does not resume admission.
The host calls `resume_after_cancelled_update()` separately when it cancels an update.
Update preparation stops native processes before it joins commands that can hold a child lock.
Its synthetic stop proof uses the same outcome settlement path without a command journal record.
The host supplies the update request ID and a cancellation signal. Retries reuse each exact child's stop proof identity.
Quiesce also accepts cancellation. A canceled observer leaves admitted work in the runner's actual cleanup custody.
This module does not download, install, or activate a release.

The host must stop the runner before it closes the journal and outbox.
Pass one absolute shutdown deadline, then capture that attempt's `settled()` promise.
Only a successful actual attempt certifies completion. A rejected attempt retains work for a later stop retry.
A deadline error on `stop()` does not mean that the work stopped.
A successful retry clears the failures that it resolves. Earlier diagnostics remain in the log.
Unresolved source and settlement failures still reject actual completion. Keep storage open while required settlement remains unfinished.

Exact command stop joins every selected actual command before native cleanup.
The Manager accepted continued native cleanup after a failed command join. The outcome remains uncertain and has no successful stop certificate.
The exact child's command and cleanup errors remain retained across retries. A later stop retries cleanup without erasing that history.
Final retirement discards that child's in-memory join history. Its durable command outcomes remain unchanged.

Mailbox command calls require an `AbortSignal`. Queue shutdown supplies its original absolute deadline in `MailboxRunCancelled`.
Cancellation withdraws preparation and starts permanent cleanup with that same deadline. A failed drain rejects its actual attempt and returns the canceled call.
An already-aborted signal refuses before command effects. Each call removes its abort listener when it finishes.
Other aborts reject only the observer and supply no shutdown deadline. The command service retains the admitted work.
The host supplies its real deadline for later cleanup. An observer timeout never certifies that resources closed.

## Local event composition

`MacRunner` in `server/executor/mac/runner/index.ts` connects command, native, source, event, maintenance, and lifetime owners before admission opens.
Its live port carries text deltas, thinking, and tool progress through the host's lossy HTTP sender.
Live payloads validate before send. Retired children do not send; a failed sender drops the notification and logs once.
Other native events enter durable source custody. Completed messages, finished tools, and ended turns capture Ticket artifacts through the outbox.
Exact stop joins admitted sources and final native events before it publishes the stop certificate.
Permanent stop refuses new command, replay, and ordinary source work, and drops late live events.
The host still owns transport, the Unix socket, update installation, and storage closure after the runner's actual join.
