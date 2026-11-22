# Apple Silicon machine code

An Apple Silicon machine joins with the same command as a Linux machine.
The owner gets the command and a private credential from Settings → Machines, or from `panels machine join-command`.
The machine needs macOS 15 or newer, a login to its graphical session, the GitHub CLI with a login, and Python 3.11 or newer.
The Python that comes with macOS is 3.9. Install a newer one with Homebrew: `brew install python gh`.
The machine needs no Node, no Xcode tools and no signing identity.

## What the join does

The command downloads `scripts/panels_install.py` at the server's released revision.
The script checks the machine, then selects the newest unexpired release from `main` that matches that revision.
It downloads the `panels-mac-runtime` artifact and checks its SHA-256 against the publication receipt.
It starts the join step `server/environments/mac_executor/join_main.ts` on the Node inside that release.
The credential passes on private standard input. It never appears in a process argument.

The join step refuses an existing installation, state folder, LaunchAgent file or loaded job.
It then writes these fixed paths:

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
| Credential | `<state>/machine-credential.json` |
| Unix socket | `<state>/run/executor.sock` |
| Launch logs | `<state>/logs/launch-agent.{stdout,stderr}.log` |
| Updated runtime | `<state>/updates/releases/<revision>-<archive-sha256>` |

It loads the job in the `gui/<uid>` domain and polls `executor health` through the console.
The join ends when the server accepts a fresh lease from this revision.
If a step fails, the join stops its job and removes only what it made. Its error includes the last launch error.

Provider commands get a fixed PATH: `~/.local/bin`, `/opt/homebrew/bin`, `/usr/local/bin`, then the system folders.
The supervisor puts the runtime's own `bin` first.

## The launcher and its signature

The launcher app is the same for every account. It reads the account's home from the user database, not from `HOME`.
It runs the fixed Node and bootstrap below that home with the original arguments.
The supervisor keeps its own Node across runtime updates and launches the selected versioned runtime as a child.

The release workflow builds the launcher on its Apple Silicon runner with `server/executor/mac/packaging/launcher_main.ts`.
It signs the app with the project's Developer ID Application identity, the identity of the desktop app.
The app travels inside the runtime archive at `launcher/Panels TypeScript.app`, so the archive digest covers it.
The publication proof checks the signature of the unpacked copy. The join copies it with `ditto` and checks it again.

macOS keys folder permissions on the designated requirement of the app.
For a team signature, that requirement names the bundle identity and the team. For an ad-hoc signature, it names the exact code hash.
So folder permissions stay when a later release changes the launcher.
The launcher is not notarized. The join downloads it through the GitHub API, so it carries no quarantine flag, and Gatekeeper does not assess it.

The hosted rehearsal `typescript-mac-executor.yml` signs its launcher ad hoc, because a pull request build has no release identity.
Its join case runs the server's own join command on a clean runner with a local release.
Folder consent and permission retention under the Developer ID need a real second Apple Silicon machine. That check remains the owner's.

## Operate an installed machine

Use the manual console for executor commands, for example `~/Deployments/PanelsExecutorTypeScript/bin/panels executor health`.
Use `executor quiesce --exit-when-ready` before a planned stop.
Require committed exit, zero work counts, and exit of the recorded runtime before you unload the job with `launchctl bootout`.
If startup fails, retain the state and logs. Use the recovery procedure below when SQLite reports version zero.

An installation from the earlier manual procedure keeps its own launcher, which the owner's Apple Development identity signed.
Runtime updates do not change the launcher, so that installation keeps working.

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
EVIDENCE="$HOME/Deployments/PanelsExecutorTypeScript/recovery-evidence-$(date -u +%Y%m%dT%H%M%SZ)"
```

The evidence directory must be new and outside the state root. Its parent must already exist.
Check the resolved root, the console, and the evidence path before a service action:

```sh
"$TS_NODE" --input-type=module - "$TS_ROOT" "$TS_PANELS" "$EVIDENCE" <<'JS'
import { existsSync, realpathSync, statSync } from 'node:fs';
import { basename, dirname, isAbsolute, resolve } from 'node:path';
const [root, consolePath, evidencePath] = process.argv.slice(2);
if (![root, consolePath, evidencePath].every(isAbsolute)) throw Error('Absolute reviewed paths required.');
const state = realpathSync(root), consoleReal = realpathSync(consolePath);
const inside = (base, value) => {
  const expected = statSync(base, { bigint: true });
  for (let path = value; ; path = dirname(path)) {
    const actual = statSync(path, { bigint: true });
    if (expected.dev === actual.dev && expected.ino === actual.ino) return true;
    if (path === dirname(path)) return false;
  }
};
const evidenceParent = realpathSync(dirname(evidencePath));
if (inside(state, evidenceParent) || existsSync(resolve(evidenceParent, basename(evidencePath))))
  throw Error('Evidence must be a new destination outside the state root.');
console.log({ state, console: consoleReal });
JS
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
Preserve the evidence and moved files until the owner closes recovery.

## Native checks that Linux cannot prove

The hosted rehearsal checks the packaged runtime with a private peer and synthetic data.
Use the later owner session for the remaining checks below, especially consent, sleep/wake, and permission retention.
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
| Root aliases                                                       | Refusal of the earlier `executor` root and descendants through symlinks and native case-insensitive aliases                                                                |
| Process ownership                                                  | Darwin PID/start-time observations, priority, descendants, direct-child exit, and no leftover owned child                                                                |
| Provider homes and shared skills                                   | Fresh sessions on each built-in backend, selected model/effort, canonical bytes, and owner-approved link restoration                                                     |
| Sleep, files, and updates                                          | Sleep/wake order and receipts, both file directions, failed-readiness rollback, and zero-work quiescence                                                                 |

Physical power-loss durability and permission persistence remain native evidence limits.
Synthetic Linux checks prove neither Darwin ABI nor native disk behavior.

## Canonical skills in the native session

Run these steps with synthetic snapshots, a private TypeScript state root, and a private provider home before any real provider starts.
The packaged entrypoint creates `MacSkillsPublisher` from `server/executor/mac/skills/index.ts` through the trusted host.

1. Publish a snapshot with non-ASCII text and CRLF bytes. Check its generation digest, `SKILL.md` bytes, and account links.
2. Publish the same snapshot again, then replace one source. Check generation reuse, owned retired links, stale generation links, and preserved unrelated entries.
3. Corrupt one existing generation. Check that the Mac preserves it under `.invalid-*` and rebuilds the authoritative bytes. Check the other snapshot validation diagnostics.
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

The Mac host reuses each built-in driver's child factory with the invocation's shared `child_processes` and `one_shot_children` owners.
Canonical skills publish before factory creation. The trusted host supplies the real account paths under the restrictions above.

Each Codex native-start factory receives an explicit fresh `RememberedCodexLoginRejection`.
Rejection memory belongs to that factory and disappears with it.
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

## Directory operations for the native skills check

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
