# Separate TypeScript Mac executor

This document starts the native handoff for the TypeScript executor. Packaging and the complete installation procedure remain pending.
No command in this document ran on the owner's Mac during this Ticket.
Use a separate app, supervisor, job, and state root. Keep the Python installation idle for rollback after cutover.

The TypeScript default state root is `~/Library/Application Support/Panels/executor-typescript`.
The Python default is its sibling `executor`. TypeScript refuses that Python root, its descendants, and resolved aliases.
An explicit state root still needs an operator review against the actual Python installation.
Provider homes and credentials remain account resources. Separate roots do not solve shared skill publication.
The owner must choose the shared-publication policy before concurrent native use.

## Preserve a failed first initialization

An existing outbox with SQLite `user_version=0` is incompatible. TypeScript refuses it before a lock or storage mutation.
A failed fresh initialization can leave this file. The refusal deliberately preserves evidence and possible work.
Version zero alone does not establish that the directory contains no work.
Do not delete the database, its WAL, its SHM, or the state root to force another start.

The owner must fill these values from the reviewed TypeScript installation before any recovery command:

```sh
TS_ROOT='/ABSOLUTE/REVIEWED/TypeScript/state-root'
TS_PANELS='/ABSOLUTE/REVIEWED/TypeScript/bin/panels'
TS_NODE='/ABSOLUTE/REVIEWED/TypeScript/bin/node'
TS_PACKAGE='/ABSOLUTE/REVIEWED/TypeScript/package-with-node_modules'
TS_JOB='com.panels.executor.typescript'
PYTHON_ROOT="$HOME/Library/Application Support/Panels/executor"
EVIDENCE='/ABSOLUTE/PRIVATE/recovery-evidence'
```

These are placeholders, not installation defaults. The final package must supply its exact executable and package paths.
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

Use the later reviewed TypeScript start procedure only after this recovery review.
Do not restart or change the Python installation. Preserve the evidence and moved files until the owner closes recovery.

## Native checks that Linux cannot prove

Run these checks in the later separate native Ticket, with a fresh isolated peer and synthetic data first.
The operator must record the reviewed revision, Node version, arm64 architecture, package digest, exact paths, job, and app identity.
The Koffi bridge can load only the operating system's `libSystem.B.dylib` on macOS.
Missing symbols or failed calls must refuse the operation. A path-based fallback is forbidden.

| Call or boundary | Required real Mac check |
|---|---|
| `openat(int,const char*,int,...)` | Variadic mode passed as explicit promoted `int`, created-file mode 0600, directory/no-follow and Darwin close-on-exec flags, FIFO refusal, and ancestor-swap containment |
| `mkdirat(int,const char*,uint16_t)` | Darwin 16-bit `mode_t`, 0700 creation, existing-directory behavior, and parent-relative placement |
| `unlinkat(int,const char*,int)` | Correct zero flags, retained-parent cleanup, and missing-entry/error behavior |
| `renameat(int,const char*,int,const char*)` | Retained source/target parents, atomic publication, and failure cleanup |
| `fsync(int)` | Directory flushes, error propagation, and successful commits on the selected native filesystem |
| `fcntl(int,int,...)` with `F_FULLFSYNC=51` and explicit `int` zero | Variadic Darwin ABI and defined third slot, strict full-flush success, and refusal without a weaker fallback |
| `flock(int,int)` | Exclusive/nonblocking/unlock flags, contention refusal, crash release, and interrupted-call behavior |
| `getpwnam` and Darwin `passwd` layout | Field offsets, pointer decoding, named-user expansion, and missing-account refusal |
| Packaged Koffi addon | arm64 Mach-O architecture, pinned addon load from the relocated package, and no extra native library |
| Native app and LaunchAgent | Signed requester attribution, startup/restart, exact Unix socket path/mode, consent, and permission retention |
| Root aliases | Refusal of the Python root and descendants through symlinks and native case-insensitive aliases |
| Process ownership | Darwin PID/start-time observations, priority, descendants, direct-child exit, and no leftover owned child |
| Provider homes and shared skills | Fresh Claude/Codex/Hermes sessions, selected model/effort, canonical bytes, and owner-approved link restoration |
| Sleep, files, and updates | Sleep/wake order and receipts, both file directions, failed-readiness rollback, and zero-work quiescence |

Physical power-loss durability and permission persistence remain native evidence limits.
Synthetic Linux checks prove neither Darwin ABI nor native disk behavior.

## Measure the main-thread database budget

The export-only `spool_burst(parent)` helper in `server/executor/mac/storage/testing/parity.ts` writes 256 near-1 MiB items.
It acknowledges each item and yields between every append and acknowledgement. A 10 ms timer measures actual event-loop delay.
The caller supplies a fresh private real-disk parent and synthetic homes. The helper starts no network host or provider.
Record maximum and p99 timer delay, maximum operation time, rows, bytes, elapsed time, and peak RSS.
Do not run this measurement in the live state root or on a memory-backed temporary filesystem.
The owner repeats it on the actual Mac filesystem before native admission.
If maximum timer delay exceeds 50 ms on the real Mac, move database work to an owned worker before admission.
Record a Linux result above 50 ms as a finding. Do not hide it behind the later Mac check or call it a pass.
