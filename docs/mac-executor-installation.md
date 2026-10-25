# Mac executor installation and updates

The executor uses one native Python environment, one source checkout, and one GUI LaunchAgent.
The operator installs and updates it with ordinary package and service commands.
This procedure preserves the existing persistent executor root.
It supplies no automatic updater, deployment transaction, or automatic rollback.

Run host commands only under separate authorization for the target Mac.
Code review and this runbook grant no live authority.
Linux tests establish software behavior, not installed credentials, login behavior, reboot, or pre-unlock operation.

## Record inputs before changes

Use the signed-in owner's normal HOME and GUI session.
Record the installed revision, candidate full lowercase SHA, launch arguments, old plist, and existing installation intent.
Retain the old app, permanent runtimes, repair evidence, and pending bytes.
Record prior dependency inputs and source before each later update.
Use an operator-selected evidence directory outside the replaceable source and environment.

The fixed paths are:

```sh
INSTALL_ROOT="$HOME/Deployments/PanelsExecutor"
SOURCE="$INSTALL_ROOT/source"
VENV="$INSTALL_ROOT/venv"
EXECUTOR_ROOT="$HOME/Library/Application Support/Panels/executor"
PLIST="$HOME/Library/LaunchAgents/com.panels.executor.plist"
SERVICE="gui/$(id -u)/com.panels.executor"
```

If the installed executor uses an explicit different persistent root, preserve that root in every command and plist.
Never substitute a new empty root to bypass existing work or incompatible evidence.

Set operator-reviewed values for these inputs:

- `REVIEWED_REPOSITORY`: an absolute local checkout with the exact reviewed commit available.
- `CANDIDATE_SHA`: the full lowercase 40-character commit SHA.
- `NATIVE_PYTHON`: the absolute native ordinary CPython 3.14.3 executable.
- `BACKEND_PATH`: explicit absolute directories for native Node and the installed provider commands, plus required system commands.
- `EVIDENCE`: an absolute private directory outside the source, environment, and persistent executor root.
- SSH host, port, user, authentication mode, known-hosts path, and server URL.
- Either the public-key identity path or the Tailscale executable path.
- The remote relay command with the deployed VPS executable and configured absolute VPS socket.

Do not derive authentication mode from the presence of a key.
Public-key mode requires an absolute private key. Tailscale mode forbids that key and requires port 22.
See [SSH prerequisites](executor.md#ssh-prerequisites-and-boundaries) for host pins and peer requirements.

Check the actual native host before installation:

```sh
"$NATIVE_PYTHON" - <<'PY'
import platform
import sys
assert platform.system() == "Darwin"
assert platform.machine() == "arm64"
assert platform.python_implementation() == "CPython"
assert sys.version_info[:3] == (3, 14, 3)
assert int(platform.mac_ver()[0].split(".")[0]) >= 11
import sysconfig
assert not sysconfig.get_config_var("Py_GIL_DISABLED")
print(sys.executable, platform.mac_ver()[0], platform.machine())
PY
PATH="$BACKEND_PATH" node -p 'JSON.stringify({platform:process.platform,arch:process.arch,version:process.versions.node})'
```

Require Node platform `darwin`, architecture `arm64`, and major version 22 or newer.
Intel and free-threaded Python remain outside this target.
Do not copy a VPS virtual environment to the Mac.

## Prepare exact source and compare compatibility

Prepare a temporary source copy without changing installed code:

```sh
mkdir -p "$EVIDENCE"
git -C "$REVIEWED_REPOSITORY" rev-parse --verify "$CANDIDATE_SHA^{commit}"
git -C "$REVIEWED_REPOSITORY" archive --format=tar "$CANDIDATE_SHA" > "$EVIDENCE/candidate-source.tar"
CANDIDATE_SOURCE="$(mktemp -d)"
tar -xf "$EVIDENCE/candidate-source.tar" -C "$CANDIDATE_SOURCE"
PYTHONPATH="$CANDIDATE_SOURCE/src" "$NATIVE_PYTHON" - <<'PY'
from planner.executor.configuration import PROTOCOL_VERSION, SPOOL_FORMAT_VERSION
print(PROTOCOL_VERSION, SPOOL_FORMAT_VERSION)
PY
```

Check that the resolved commit equals the reviewed full SHA.
Record the candidate constants: wire protocol 6 and journal format 2.
Both Mac and VPS peers must match protocol 6 before work. Coordinate the peer
transition through the Manager and installation Worker before any application.

Read fresh installed Mac health through the installed console:

```sh
PLAN_EXECUTOR_ROOT="$EXECUTOR_ROOT" "$INSTALLED_PANELS" executor health > "$EVIDENCE/installed-health.json"
launchctl print "$SERVICE" > "$EVIDENCE/installed-service.txt"
cp "$PLIST" "$EVIDENCE/prior.plist"
```

Set `INSTALLED_PANELS` to the actual installed console before the first command.
For later updates, it is `$VENV/bin/panels`.
Read identity, revision, protocol, spool format, and PID from the results.
The health response carries no work counts. The quiescence receipt supplies those counts.
An unavailable health result is unknown state, not proof of first installation.

Independently read the intended VPS package constants on the VPS:

```sh
cd /absolute/intended/vps/app
/absolute/intended/vps/app/.venv/bin/python - <<'PY'
from planner.executor.configuration import PROTOCOL_VERSION, SPOOL_FORMAT_VERSION
print(PROTOCOL_VERSION, SPOOL_FORMAT_VERSION)
PY
```

Use the actual deployed package interpreter and working directory for that VPS layout.
Record its exact app revision alongside the constants.
`GET /api/executor/health` reports the connected Mac identity, not independent VPS compatibility.
If either host's version evidence is absent or incompatible, defer installation.
The handshake remains the final protocol guard.

A genuine fresh installation has no existing executor, journal, service, or retained installation intent.
Only that established case skips the installed-process shutdown steps.
It still requires intended VPS evidence and read-only spool preflight before startup.

## Obtain successful graceful exit

Use the identity and PID from fresh local health:

```sh
PLAN_EXECUTOR_ROOT="$EXECUTOR_ROOT" "$INSTALLED_PANELS" executor quiesce \
  --expected-incarnation "$OLD_INCARNATION" \
  --expected-generation "$OLD_GENERATION" \
  --expected-revision "$OLD_REVISION" \
  --expected-pid "$OLD_PID" \
  --exit-when-ready > "$EVIDENCE/exit-receipt.json"
```

Check that the returned identity and PID match the request.
Require `exit_committed=true`, stopped admission, stopped idle children, and every work count zero.
Active turns, callbacks, uncertainty, transfers, source writes, or unsent entries prevent exit.

A refused exit still stops admission. Outbound replay continues, but ordinary admission does not automatically resume.
Defer the update until existing work settles or the operator resolves the exact dependency.
Obtain fresh health before another identity-bound request.
Do not interpret a stopped admission flag as successful exit.

After a matching committed exit, observe the service and process:

```sh
launchctl print "$SERVICE" > "$EVIDENCE/service-after-exit.txt"
ps -p "$OLD_PID" -o pid=,ppid=,lstart=,command=
```

Require observed successful exit, no live old process, and no replacement executor under that service.
If the service restarts or evidence is ambiguous, stop the procedure and obtain fresh identity and work evidence.
An old receipt grants no authority over a new process.
Only after successful exit, unload the exact job:

```sh
launchctl bootout "$SERVICE"
```

Do not use bootout, kickstart with `-k`, or a signal as the shutdown method.

## First transition from installed revision 30ce7317

The retained installation evidence names `30ce73179782a9203ab82a15ac5ac77a3f264a1a` as the Mac revision.
Check fresh evidence before action. That retained statement is not a current host observation.

This installed revision checks all work counts, including unsent entries, before it stops idle native producers.
An idle catalog producer can therefore prevent the queue from draining after ordinary task completion.
Correction `442c38c4` stops idle producers before queue drain and accounts for in-flight source work.
The correction exists only in the candidate before installation.
Do not assume corrected shutdown behavior in the installed process.
Do not install the candidate to obtain permission to stop that process.

If ordinary quiescence cannot commit exit, stop installation and preserve the exact returned evidence.
Retain the installed app, runtimes, plist, intent records, spool, and repair evidence.

The earlier authorized seam operation documents an existing operator disposition:

1. Obtain fresh non-exit quiescence and bind it to the current executor identity.
2. Establish zero active turns, callbacks, and uncertain operations.
3. Establish no source write or transfer in flight and identify the exact idle native child.
4. Under separate explicit seam authority, terminate only that exact idle child with ordinary TERM.
5. Preserve every queued byte and let outbound replay drain normally.
6. Obtain fresh normal quiescence with matching committed successful exit and all work counts zero.

That prior operation sent no signal to the executor, helper, server, or process group.
It grants no general signal authority and authorizes no new operation from this coding Ticket.
If current seam authority does not cover the observed state, return the exact dependency to the Manager.
Do not discard the queue, force executor exit, or add another lifecycle mechanism.

The existing evidence remains in the owning Ticket artifacts:
`/files/tickets/t_kvj3689g/artifacts/finite-replay-update-result.md` and
`/files/tickets/t_kvj3689g/artifacts/finite-replay-update-dependency.md`.
Keep those historical procedures intact.

## Install at the final paths

Proceed only after successful exit and job removal, or after an established genuine fresh-install determination.
Create the fixed source checkout once:

```sh
mkdir -p "$INSTALL_ROOT"
git clone --no-checkout "$REVIEWED_REPOSITORY" "$SOURCE"
```

For subsequent updates, reuse that checkout and fetch the reviewed commit from the explicit reviewed repository:

```sh
git -C "$SOURCE" fetch "$REVIEWED_REPOSITORY" "$CANDIDATE_SHA"
```

Check for local source modifications before checkout. Preserve them and stop if their disposition is unclear.
Retain the prior exact source and dependency inputs for recovery.
Then select the exact commit:

```sh
git -C "$SOURCE" checkout --detach "$CANDIDATE_SHA"
git -C "$SOURCE" rev-parse HEAD
```

Require the printed revision to equal the full requested SHA.
For the first installation, create the environment at its final path:

```sh
"$NATIVE_PYTHON" -m venv "$VENV"
```

For later updates, reuse that fixed environment. Never move or copy it.
Install from the selected source:

```sh
cd "$SOURCE"
"$VENV/bin/python" -m pip install -r requirements-build.txt
"$VENV/bin/python" -m pip install --no-build-isolation \
  --only-binary=:all: --no-binary=http-ece -r requirements-executor.txt
rm -rf -- "$SOURCE/build"
"$VENV/bin/python" -m pip install --force-reinstall --no-deps --no-build-isolation "$SOURCE"
"$VENV/bin/python" -m pip check
PATH="$BACKEND_PATH" npm ci --prefix "$SOURCE/agent_backends"
```

Require every command to succeed before continuation.
Pinned `http-ece` is the sole ordinary source-build exception.
The generated `source/build` cache can retain removed modules, so clear only that cache before each source reinstall.
This command preserves the source inputs, persistent data, and retained evidence.
Forced source replacement matters because successive revisions share project version `2.0.0`.
No editable installation or source-path wrapper participates in runtime launch.

Check the stopped persistent spool with the candidate's read-only inspector:

```sh
PLAN_EXECUTOR_ROOT="$EXECUTOR_ROOT" "$VENV/bin/python" - <<'PY'
from planner.executor.configuration import SPOOL_FORMAT_VERSION, executor_paths
from planner.executor.preflight import check_spool_compatibility
check_spool_compatibility(executor_paths().root, SPOOL_FORMAT_VERSION)
print("spool compatible")
PY
```

If inspection refuses, preserve the root and stop. Do not repair or convert it through this installation procedure.
Legacy conversion has its own stopped-process boundary in [the executor reference](executor.md#fresh-installation-and-offline-legacy-conversion).

## Render and start one LaunchAgent

The renderer prints XML to stdout. It does not install packages, write files, or control services.
Render with explicit public-key inputs:

```sh
"$VENV/bin/python" -m planner.environments.mac_executor.cli \
  --home "$HOME" --user-id "$(id -u)" \
  --executor-root "$EXECUTOR_ROOT" \
  --application-revision "$CANDIDATE_SHA" \
  --backend-path "$BACKEND_PATH" \
  --ssh-auth-mode public_key \
  --ssh-host "$SSH_HOST" --ssh-port "$SSH_PORT" --ssh-user "$SSH_USER" \
  --ssh-identity-file "$SSH_IDENTITY" --ssh-known-hosts "$SSH_KNOWN_HOSTS" \
  --panels-server-url "$PANELS_SERVER_URL" \
  --remote-command "$REMOTE_RELAY_COMMAND" > "$EVIDENCE/candidate.plist"
plutil -lint "$EVIDENCE/candidate.plist"
```

For Tailscale mode, use `--ssh-auth-mode tailscale` and port 22.
Replace `--ssh-identity-file` with `--tailscale-binary /absolute/tailscale`.

Check the rendered arguments and environment before installation.
The program is `$VENV/bin/panels executor run` with the full explicit SHA.
The environment includes the owner's HOME, persistent root, and PATH with `venv/bin` first.
`PLAN_CLAUDE_EXECUTABLE` names the absolute
`source/agent_backends/node_modules/@anthropic-ai/claude-code/bin/claude.exe` executable.
Check that this executable and the reviewed provider commands exist.
The noneditable Python installation cannot discover that source path itself.

One `com.panels.executor` GUI job uses RunAtLoad and `KeepAlive={SuccessfulExit:false}`.
Abnormal exit requests restart. Successful maintenance exit stays stopped absent another demand.
The job retains a throttle and separate stdout/stderr files beneath the persistent `logs` directory.
No daemon or extra demand trigger supplies reboot or pre-unlock operation.

Install and start the checked plist:

```sh
mkdir -p "$HOME/Library/LaunchAgents" "$EXECUTOR_ROOT/logs"
install -m 0600 "$EVIDENCE/candidate.plist" "$PLIST"
launchctl bootstrap "gui/$(id -u)" "$PLIST"
launchctl kickstart "$SERVICE"
PLAN_EXECUTOR_ROOT="$EXECUTOR_ROOT" "$VENV/bin/panels" executor health > "$EVIDENCE/candidate-health.json"
launchctl print "$SERVICE" > "$EVIDENCE/candidate-service.txt"
```

Check the expected revision, protocol, journal format, PID, and fresh identity locally.
Check that VPS reconciliation reaches ready for that identity and revision.
Retain both observations before the operator declares success.
Do not claim unattended restart, reboot, credentials, or physical outage behavior from these checks alone.

## Deferred update and failed installation

If preparation fails before shutdown, preserve logs and the installed code.
If quiescence refuses exit, leave code unchanged and record that admission remains stopped.
Do not automatically restart a process with unresolved work.

If installation fails after job removal, leave the service unloaded.
Retain failure output and all persistent data. Finish an explicit compatible reinstall before startup.
A partial pip or npm installation supplies no permission to bootstrap the job.

If candidate startup fails, obtain fresh candidate identity and work evidence before service removal or recovery.
If it runs, use its own identity-bound graceful exit procedure.
Never reuse the old process's receipt against the candidate.
If fresh state is unavailable or uncertain, stop and return the exact dependency to the operator.

For an explicitly approved compatible recovery, select the recorded prior SHA in the fixed source checkout.
Repeat the pinned pip commands, build-cache removal, forced source reinstall, pip check, and npm ci sequence above.
Check spool compatibility with that prior package before startup.
Render a plist for the prior SHA and its recorded inputs, then follow the same bootstrap and proof procedure.
The first transition retains the old app and runtimes as recovery inputs, not as an automatic fallback command.
An incompatible prior package must not open the current journal.
Never delete pending work or automatically convert the journal for rollback.

File transfers require the standard SFTP v3 subsystem and shared-home access on
the same authenticated SSH connection. Preview reverse Unix forwarding remains
required. Endpoint capability evidence does not prove concurrent relay and SFTP
behavior; that proof belongs to the reviewed candidate tests. This procedure
grants no authority to change live SSH configuration.

Code paths: `src/planner/environments/mac_executor/`, `src/planner/executor/cli.py`,
`src/planner/executor/preflight.py`, `requirements-build.txt`, `requirements-executor.txt`,
`agent_backends/package-lock.json`.

_Last verified: 2026-10-25. Commands checked against source; no live Mac operation occurred._
