# Cancel the retained initial Mac prompt

This procedure belongs to the native verification Worker. This coding Ticket executes none of these commands against a service.
The procedure replaces only the retained isolated VPS app, then requests ordinary shutdown of the original conversation.
Local cancellation does not prove native termination or undo earlier effects.

The Manager restricted replacement to the isolated verification app. If fresh evidence identifies production or another target, stop and report a target mismatch.
Do not substitute a production service operation. Do not signal the Panels server that hosts the Worker.

## Retained inputs and required evidence

The observations below are historical. They do not identify a current process without fresh checks.

| Item | Retained value |
| --- | --- |
| Old app | `/home/vps/panels-session-evidence/t_jmzfmp3d/protocol8/current/app` |
| Ownership record | `/home/vps/panels-session-evidence/t_jmzfmp3d/protocol8/server-owned.json` |
| Configuration | `/home/vps/panels-session-evidence/t_jmzfmp3d/protocol8/app-config.yaml` |
| Verification database | `/home/vps/panels-session-evidence/t_kvj3689g/recovery-ecb8e182/current/data/planner.db` |
| Isolated listener | `127.0.0.1:8793` |
| Retained Serve route | `https://panels-vps.tail687c57.ts.net:9443` → `http://127.0.0.1:8793` |
| Logical executor | `mac-native-verification` |
| Historical supervisor / child PID | `3121341` / `3121455` |
| Historical VPS / Mac revision | `a81c5223cd3cfe863db35be48923676c686dbf33` |
| Protocol / journal | `8` / `2` |
| Original conversation | `conv_649ca4a9292b43b5b8ce54010cf32055` |
| Original child | `1e089637-fbf9-492c-a2c2-768e96d493b6` |
| Original Mac incarnation | `c6354144-6164-4c55-86da-a828870515c5` |

The database is outside the old app directory. Preserve that exact database and its managed files.
The old database contains initial intent and an open assignment without `executor_core_turns` metadata.
The successful start outcome does not prove prompt acceptance. An absent prompt outcome does not prove non-delivery.
The private registry does not survive VPS replacement. Do not invent its missing prompt command identity.
Older records also lack original sender bytes. Preserve unknown lifecycle without a fabricated transcript message.

Retain the earlier `supported-cancellation-assessment.md`, timeout, installed package evidence, journal comparisons, and `final-runtime-disposition.json`.
Those files remain under the original Ticket's `artifacts/protocol8` directory and its private evidence directory.

## Prepare the isolated candidate

Use an ordinary host session with the existing verification authority. Disable shell tracing before private configuration access.
Select a new private evidence directory. Select the exact published repair revision from the Implementation evidence.
Do not substitute the earlier compression-only revision `66e72f9346e1091942297ede9793c55a27206061` for the completed repair.

```sh
set -eu
set +x
umask 077
export PRIOR=/home/vps/panels-session-evidence/t_jmzfmp3d/protocol8
export VERIFY_DB=/home/vps/panels-session-evidence/t_kvj3689g/recovery-ecb8e182/current/data/planner.db
export EVIDENCE=/home/vps/panels-session-evidence/t_q9kpcm9h/cancellation-application
export ORIGINAL_CONVERSATION=conv_649ca4a9292b43b5b8ce54010cf32055
# Set these two values from the reviewed publication handoff.
: "${REVIEWED_SOURCE:?Set the absolute reviewed checkout path}"
: "${REPAIR_SHA:?Set the full reviewed repair SHA}"
export REVIEWED_SOURCE REPAIR_SHA
mkdir -p "$(dirname "$EVIDENCE")"
mkdir -m 0700 "$EVIDENCE"
mkdir "$EVIDENCE/current"
git -C "$REVIEWED_SOURCE" rev-parse HEAD > "$EVIDENCE/source-head.txt"
test "$(cat "$EVIDENCE/source-head.txt")" = "$REPAIR_SHA"
git -C "$REVIEWED_SOURCE" diff --exit-code > "$EVIDENCE/source-diff.txt"
git -C "$REVIEWED_SOURCE" diff --cached --exit-code > "$EVIDENCE/source-index-diff.txt"
"$REVIEWED_SOURCE/.venv/bin/panels" environment app-build \
  --source-root "$REVIEWED_SOURCE" --requested-sha "$REPAIR_SHA" \
  --candidate-app "$EVIDENCE/current/app" > "$EVIDENCE/build.json"
"$REVIEWED_SOURCE/.venv/bin/panels" environment app-identity \
  --app "$EVIDENCE/current/app" > "$EVIDENCE/candidate-identity.json"
```

Keep the candidate at this final path. Its environment contains absolute paths.
Check the candidate manifest against the reviewed full SHA. Check its protocol and journal against the installed Mac evidence.
If compatibility differs, stop and return the peer dependency. Do not change the Mac package during VPS preparation.

## Capture and bind the old isolated process

Capture route and listener evidence. Do not modify the retained Serve route.

```sh
tailscale serve status --json > "$EVIDENCE/serve-before.json"
ss -H -ltnp 'sport = :8793' > "$EVIDENCE/listener-before.txt"
python3 - <<'PY'
import json, os, re
from pathlib import Path

prior = Path(os.environ['PRIOR'])
evidence = Path(os.environ['EVIDENCE'])
record = json.loads((prior / 'server-owned.json').read_text())
environment = record['environment']
pid = int(record['new_launcher'])
app = prior / 'current/app'
expected = {
    'PLAN_DB_PATH': os.environ['VERIFY_DB'],
    'PLAN_PORT': '8793',
    'PLAN_CONFIG_PATH': str(prior / 'app-config.yaml'),
    'PLAN_SERVER_CONTROL_SOCKET':
        '/home/vps/panels-session-evidence/t_kvj3689g/recovery-ecb8e182/current/data/control.sock',
}
assert record['command'] == str(app / 'bin/panels-launcher'), 'target mismatch'
assert all(environment.get(k) == v for k, v in expected.items()), 'target mismatch'

def process(identity):
    root = Path('/proc') / str(identity)
    stat = (root / 'stat').read_text().rsplit(')', 1)[1].split()
    argv = (root / 'cmdline').read_bytes().split(b'\0')
    argv = [part.decode() for part in argv if part]
    env = dict(part.split(b'=', 1) for part in
               (root / 'environ').read_bytes().split(b'\0') if b'=' in part)
    assert all(env.get(k.encode()) == v.encode() for k, v in expected.items()), 'target mismatch'
    assert argv[0] == str(app / '.venv/bin/python'), 'target mismatch'
    return {'pid': identity, 'parent': int(stat[1]), 'start_ticks': stat[19],
            'argv': argv, 'cwd': str(root.joinpath('cwd').resolve())}

supervisor = process(pid)
assert supervisor['argv'][1:] == ['-m', 'planner', 'serve'], 'target mismatch'
assert supervisor['cwd'] == str(prior / 'current'), 'target mismatch'
btime = next(int(row.split()[1]) for row in Path('/proc/stat').read_text().splitlines()
             if row.startswith('btime '))
started = btime + int(supervisor['start_ticks']) / os.sysconf('SC_CLK_TCK')
assert abs(started - float(record['time'])) < 10, 'ownership timestamp mismatch'
listener = (evidence / 'listener-before.txt').read_text()
assert listener.count('127.0.0.1:8793') == 1, 'listener mismatch'
pids = set(map(int, re.findall(r'pid=(\d+)', listener)))
assert len(pids) == 1, 'listener identity is ambiguous'
application = process(pids.pop())
assert application['parent'] == pid, 'listener belongs to another supervisor'
assert application['argv'][1:] == ['-m', 'planner.server_lifecycle.application'], 'target mismatch'
(evidence / 'old-process.json').write_text(json.dumps(
    {'supervisor': supervisor, 'application': application}, indent=2))
PY
```

If the ownership record or process differs, stop. Do not select another PID from a process search.
The process record contains an allowlisted identity only. Do not print the complete environment or private configuration.

Use the retained loopback interface for reads. The original harness uses that interface without credentials.
If the authorized interface requires authentication, add its existing private curl configuration with `--config "$CURL_IDENTITY_CONFIG"`.
Do not print credentials, add synthetic trusted headers, or change authentication settings.

```sh
"$REVIEWED_SOURCE/.venv/bin/panels" environment app-identity \
  --app "$PRIOR/current/app" > "$EVIDENCE/old-app-identity.json"
curl --fail --silent --show-error --max-time 10 \
  http://127.0.0.1:8793/api/meta > "$EVIDENCE/meta-before.json"
curl --fail --silent --show-error --max-time 10 \
  http://127.0.0.1:8793/api/executor/health > "$EVIDENCE/executor-before.json"
curl --fail --silent --show-error --max-time 10 \
  "http://127.0.0.1:8793/api/conversation/conversations/$ORIGINAL_CONVERSATION" \
  > "$EVIDENCE/conversation-before.json"
```

Preserve a failed read as unknown state. A wrong URL or HTTP 404 does not prove conversation absence.
Check the route target, logical executor, process ownership, and database path before retirement.
Check that the old manifest and served revision match the historical VPS revision above.
If the revision differs, stop and return the fresh identity for review.
Do not require Mac quiescence before this VPS retirement. That order can preserve the stalled dependency indefinitely.

## Retire only the recorded isolated supervisor

Open a PID handle and recheck identity before the signal. A stale or reused PID refuses this step.
The observation below has a finite 60-second bound. It does not create a service retry policy.

```sh
python3 - <<'PY'
import json, os, select, signal
from pathlib import Path

evidence = Path(os.environ['EVIDENCE'])
identity = json.loads((evidence / 'old-process.json').read_text())['supervisor']
pid = identity['pid']
handle = os.pidfd_open(pid)
try:
    root = Path('/proc') / str(pid)
    stat = (root / 'stat').read_text().rsplit(')', 1)[1].split()
    argv = [part.decode() for part in (root / 'cmdline').read_bytes().split(b'\0') if part]
    assert stat[19] == identity['start_ticks'], 'PID identity changed'
    assert argv == identity['argv'], 'process command changed'
    assert str(root.joinpath('cwd').resolve()) == identity['cwd'], 'process directory changed'
    requested = os.environ.get('ISOLATED_RETIRE_SIGNAL', 'TERM')
    assert requested in ('TERM', 'KILL'), 'unsupported signal'
    chosen = signal.SIGTERM if requested == 'TERM' else signal.SIGKILL
    signal.pidfd_send_signal(handle, chosen)
    exited = bool(select.select([handle], [], [], 60)[0])
    (evidence / 'retirement.json').write_text(json.dumps({'pid': pid, 'observed_exit': exited}))
    assert exited, 'Preserve the refusal or timeout and stop this attempt'
finally:
    os.close(handle)
PY
ss -H -ltnp 'sport = :8793' > "$EVIDENCE/listener-after-stop.txt"
```

Check that both recorded processes exited and that the listener file is empty.
If guarded shutdown refuses TERM, retain its diagnostic from `$PRIOR/server.log`.
Use its exact-PID SIGKILL guidance only for the same owned supervisor after another identity check.
Set `ISOLATED_RETIRE_SIGNAL=KILL` for that invocation of the same PID-handle block.
Do not set this value before the recorded TERM refusal or timeout.
Do not use broad process patterns, process-group signals, or an inferred listener PID as authority.
If the application child survives, stop and return that exact dependency. Do not start another app over it.

## Preserve state and start the patched isolated app

Create the supported coherent snapshot after old process exit. The backup command uses SQLite's backup API.
It includes configuration roots but excludes Ticket artifacts and uploads. Preserve the complete managed files tree separately.

```sh
"$EVIDENCE/current/app/bin/panels" environment backup \
  --source-db "$VERIFY_DB" --backup-dir "$EVIDENCE/database-backup" \
  --deployed-revision a81c5223cd3cfe863db35be48923676c686dbf33 \
  > "$EVIDENCE/backup-path.txt"
if test -d "$(dirname "$VERIFY_DB")/files"; then
  cp -a "$(dirname "$VERIFY_DB")/files" "$EVIDENCE/managed-files"
fi
cp -a "$PRIOR/app-config.yaml" "$EVIDENCE/prior-app-config.yaml"
```

Do not copy only a live SQLite main file. Retain its WAL and SHM until the supported snapshot completes.
Leave the original database and files at their existing paths. Preserve all prior evidence and installation inputs.
On the Mac, retain the executor root at `~/Library/Application Support/Panels/executor`, including spool, snapshots, logs, and receipts.
Do not clear pending entries or replace that root with an empty directory.

Under the existing Mac authority, capture local health and a coherent journal snapshot in a separate private directory.
Execute the following block on the Mac. It sends no signal or shutdown request.

```sh
set +x
umask 077
export EXECUTOR_ROOT="$HOME/Library/Application Support/Panels/executor"
export MAC_EVIDENCE="$HOME/panels-session-evidence/t_q9kpcm9h/cancellation-application"
mkdir -p "$(dirname "$MAC_EVIDENCE")"
mkdir -m 0700 "$MAC_EVIDENCE"
PLAN_EXECUTOR_ROOT="$EXECUTOR_ROOT" "$HOME/Deployments/PanelsExecutor/venv/bin/panels" \
  executor health > "$MAC_EVIDENCE/executor-health.json"
"$HOME/Deployments/PanelsExecutor/venv/bin/python" -I - <<'PY'
import os, sqlite3, time
from pathlib import Path

source = Path(os.environ['EXECUTOR_ROOT']) / 'spool/outbox.sqlite3'
destination = Path(os.environ['MAC_EVIDENCE']) / 'outbox.sqlite3'
assert source.is_file() and not destination.exists()
deadline = time.monotonic() + 10

def progress(status, remaining, total):
    if time.monotonic() > deadline:
        raise TimeoutError('Retain the incomplete snapshot and original journal')

with sqlite3.connect(source.as_uri() + '?mode=ro', uri=True, timeout=5) as original:
    with sqlite3.connect(destination) as snapshot:
        original.backup(snapshot, pages=128, progress=progress)
        assert snapshot.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
PY
```

Keep immutable snapshots and the original executor root in place. A journal copy alone does not preserve referenced file bytes.
Retain local command failures. Do not retry an unknown operation or infer idle state from a failed health read.

Start the reviewed isolated package with the existing private environment and a new copy of its YAML configuration that enables the same logical executor. Keep the prior configuration as evidence. Copy no credentials into public output.

```sh
"$EVIDENCE/current/app/.venv/bin/python" -I - <<'PY'
import json, os, subprocess, time
import yaml
from pathlib import Path

evidence = Path(os.environ['EVIDENCE'])
prior = Path(os.environ['PRIOR'])
old = json.loads((evidence / 'old-process.json').read_text())
assert all(not Path('/proc', str(item['pid'])).exists() for item in old.values()), 'old process remains'
assert not subprocess.check_output(['ss', '-H', '-ltn', 'sport = :8793'], text=True).strip()
app = evidence / 'current/app'
assert json.loads((app / 'manifest.json').read_text())['app_sha'] == os.environ['REPAIR_SHA']
configuration = yaml.safe_load((evidence / 'prior-app-config.yaml').read_text())
configuration['executor_id'] = 'mac-native-verification'
configuration_path = evidence / 'app-config.yaml'
configuration_path.write_text(yaml.safe_dump(configuration))
record = json.loads((prior / 'server-owned.json').read_text())
environment = record['environment'].copy()
assert environment['PLAN_DB_PATH'] == os.environ['VERIFY_DB']
assert environment['PLAN_PORT'] == '8793'
assert environment['PLAN_CONFIG_PATH'] == str(prior / 'app-config.yaml')
environment['PLAN_CONFIG_PATH'] = str(configuration_path)
command = str(app / 'bin/panels-launcher')
launched_at = time.time()
with (evidence / 'server.log').open('ab') as log:
    child = subprocess.Popen([command, 'serve'], cwd=evidence / 'current', env=environment,
                             stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
root = Path('/proc') / str(child.pid)
start_ticks = root.joinpath('stat').read_text().rsplit(')', 1)[1].split()[19]
(evidence / 'server-owned.json').write_text(json.dumps(
    {'new_launcher': child.pid, 'command': command, 'environment': environment,
     'time': launched_at, 'start_ticks': start_ticks,
     'namespace': os.readlink(root / 'ns/mnt')}, indent=2))
(evidence / 'new-process.json').write_text(json.dumps(
    {'pid': child.pid, 'start_ticks': start_ticks, 'launch_time': launched_at,
     'command': command}, indent=2))
PY
```

Keep `server-owned.json` private. Capture the new PID, start time, executable, listener, manifest, and served revision separately.

```sh
curl --fail --silent --show-error --max-time 10 \
  http://127.0.0.1:8793/api/meta > "$EVIDENCE/meta-after.json"
curl --fail --silent --show-error --max-time 10 \
  http://127.0.0.1:8793/api/executor/health > "$EVIDENCE/executor-after.json"
ss -H -ltnp 'sport = :8793' > "$EVIDENCE/listener-after-start.txt"
```

Check that the same database and canonical origin remain active. Check the Mac identity and reconciliation state through `/api/executor/health`.
Use one bounded readiness observation. If reconciliation remains unavailable, preserve the exact boundary and end the attempt.
Do not issue another start or prompt for the original conversation.

## Request shutdown and distinguish the results

Issue the original conversation shutdown request once. The client bound limits observation, not native work or server settlement.
If it times out, preserve that uncertainty. Do not automatically repeat the mutation.

```sh
curl --silent --show-error --max-time 30 \
  --request POST --dump-header "$EVIDENCE/kill-headers.txt" \
  --output "$EVIDENCE/kill-body.txt" \
  "http://127.0.0.1:8793/api/conversation/conversations/$ORIGINAL_CONVERSATION/kill"
curl --fail --silent --show-error --max-time 10 \
  "http://127.0.0.1:8793/api/conversation/conversations/$ORIGINAL_CONVERSATION" \
  > "$EVIDENCE/conversation-after.json"
curl --fail --silent --show-error --max-time 10 \
  "http://127.0.0.1:8793/api/conversation/conversations/$ORIGINAL_CONVERSATION/events" \
  > "$EVIDENCE/events-after.json"
```

Inspect durable evidence through a query-only connection. The queries below expose no capability or authentication environment.

```sh
python3 - <<'PY'
import json, os, sqlite3, time
from pathlib import Path

conversation = os.environ['ORIGINAL_CONVERSATION']
connection = sqlite3.connect(Path(os.environ['VERIFY_DB']).as_uri() + '?mode=ro', uri=True, timeout=5)
connection.row_factory = sqlite3.Row
connection.execute('PRAGMA query_only=ON')
deadline = time.monotonic() + 5
connection.set_progress_handler(lambda: time.monotonic() > deadline, 1000)
queries = {
    'lifecycle': ('SELECT * FROM conversation_lifecycle_facts WHERE conversation_id=?', (conversation,)),
    'assignments': ('SELECT * FROM executor_child_assignments WHERE conversation_id=?', (conversation,)),
    'runtime': ('SELECT * FROM executor_core_turns WHERE conversation_id=?', (conversation,)),
    'receipts': ('SELECT * FROM mac_inbox_positions WHERE conversation_id=?', (conversation,)),
}
connection.execute('BEGIN')
with connection:
    result = {name: [dict(row) for row in connection.execute(sql, args)]
              for name, (sql, args) in queries.items()}
connection.close()
Path(os.environ['EVIDENCE'], 'durable-after.json').write_text(json.dumps(result, indent=2))
PY
```

Check local disposition and native termination separately:

- Local disposition settles the original send and turn. Unknown older prompt delivery remains unknown.
- Native evidence names a successful ordinary stop for the original child and incarnation. Its command identity differs from the original prompt.
- Assignment closure and quarantine release follow confirmed stop. A completed HTTP request alone proves neither fact.
- Late outcomes require exact retained command evidence. They do not grant renewed execution, callback, event, or CLI authority.

If stop remains unconfirmed, retain quarantine, journal entries, receipts, and the route. End this installation attempt with the exact dependency.
Do not wait for proof that cannot exist. Do not infer non-delivery from absent outcomes or successful start evidence.
Record the original test as canceled. Preserve its timeout and unknown effects. Cancellation does not count as successful delivery.

## Later Mac update, only after confirmed child stop

Follow [the Mac installation procedure](mac-executor-installation.md) under its existing authority.
Obtain fresh local health and process evidence. The quiesce command takes no expected
identity arguments; its receipt identifies the process that handled it.

```sh
PLAN_EXECUTOR_ROOT="$EXECUTOR_ROOT" "$INSTALLED_PANELS" executor quiesce \
  --exit-when-ready > "$EVIDENCE/exit-receipt.json"
```

Require `exit_committed=true`, zero work counts, stopped admission, and observed exit of the receipt's process.
Check that no replacement executor is running before continuing.
Only then run `launchctl bootout "$SERVICE"` and continue the existing compatible package procedure.
If stop or quiescence remains uncertain, end the attempt. Do not force Mac exit or use bootout as shutdown.
Keep the retained route until the Manager confirms that installed peers no longer depend on it.
