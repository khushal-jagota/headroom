# Stop authorization for the TS supervisor

The Manager accepted the pure TS stop protocol for managed Linux services on November 12, 2026.
This satisfies the design acceptance condition. The implementation and its proofs remain separate work.
The Manager requested this comparison after approval of the four-part implementation split.
No supervisor implementation accompanies this document.

## What the existing check protects

An agent can accidentally match the live server with a broad process-stop command.
On Linux, Python ignores SIGINT and SIGTERM unless the kernel identifies the verified systemd manager as sender.
It rechecks the launch parent and manager identity before acceptance.
An unknown sender or unverifiable manager fails closed.
A foreground launch accepts ordinary signals.
On macOS, Python also accepts ordinary signals. It does not implement the Linux provenance guard there.

This protects the supervisor signal boundary, not uninterrupted service.
A broad stop can also terminate the application child. Python then exits with failure and systemd can restart the service.
Refusal of the supervisor signal alone does not prevent that interruption.

This protects against accidental signals, not hostile processes under the same Unix account.
Those processes can send SIGKILL, request a service stop, or read that account's files.
The existing restart socket also permits local application replacement. It is not a hostile-user isolation boundary.

The Panels worker skill records live outages on August 15 and October 20, 2026, from broad process stops.
Git records the Linux guard on October 20 in commit `a8be4c50d`.
These establish two historical incidents, not a measured rate of prevented signals.
I did not inspect live logs. The number of subsequent refusals is unknown.

## Options and platform costs

| Option | Protection and limitation | Linux cost | Mac and Electron cost |
| --- | --- | --- | --- |
| Accept every signal | Loses the Linux accidental-stop protection. Preserves present Mac behavior. | No extra component. | No extra component. |
| Native wrapper before Node | Preserves kernel sender provenance and the Linux sender policy. Cannot stop same-user SIGKILL. | Compiler, wrapper binary, native signal handling, packaging and relocation proof. | Separate builds for each architecture, signing and relocation. Mac manager identity needs a separate policy because Python has none. |
| Authenticated stop intent, then signal | Ignores accidental bare signals. Authorizes intentional control through possession of a private capability. Does not establish signal-sender identity. | Pure TS command and socket extension. New unit needs an explicit stop command. | No extra binary or signing unit. Foreground behavior stays unchanged. An Electron owner can use the same control operation for Quit. Unattended launchd stop needs its own integration decision. |

A Node addon or helper after signal delivery cannot recover the lost sender identity.
The native option requires the wrapper to receive signals before Node.
That extra platform surface conflicts with the migration's simpler Electron and Mac packaging goal.

## Accepted design: intent-gated managed stop

Use the third option for managed Linux services. Keep ordinary signals for foreground launches, as Python does.
Fail closed if the launch mode or required managed-stop capability cannot be established.
Do not infer an ordinary launch from a failed manager check.

The supervisor creates a random capability and incarnation identifier at startup.
It writes the capability to an explicit private runtime path with mode 0600 beneath a mode-0700 directory.
It never places the capability in logs, process arguments, backup roots, or application/agent environments.
This is possession authentication against accidental callers. It does not exclude a malicious same-user process.

The new unit uses an explicit TS stop command in `ExecStop`.
That command reads the capability from its explicit path and opens the supervisor's Unix control socket.
It requests one stop intent for the current incarnation and receives the exact supervisor PID and an acknowledgment.
It then sends SIGTERM to that PID only. It does not use a process-name pattern.

The supervisor consumes the intent on the first stop signal and starts its existing ordered shutdown.
An intent expires after five monotonic seconds and cannot survive restart or replay in another incarnation.
A bare SIGTERM or SIGINT logs a refusal and leaves the managed supervisor alive.
A signal during an authorized stop is harmless because an intentional stop is already pending.
The command fails visibly if the socket refuses or the incarnation changes. It does not silently fall back to an unauthenticated signal.

The control protocol preserves the existing restart operation and adds a versioned stop-intent operation.
The implementation must bound message size and connection time, and use constant-time capability comparison.
Cleanup removes only this incarnation's capability and socket after the child exits and ownership ends.

For the accidental-signal threat, this gives the same refusal without a platform binary.
It deliberately changes the authorization fact: prior intent replaces kernel sender identity.
A same-user process that obtains the capability can authorize a stop, just as that user can already request systemd stop.
Do not describe this as equal authentication of the signal sender.

## Changes to service operation and failure behavior

Only a separately named TS unit receives the new `ExecStop`. Existing units remain untouched.
A normal `systemctl stop` runs that command before systemd's remaining termination steps.
The command must finish only after the supervisor exits.
The new unit will use a 60-second stop timeout: a 30-second application shutdown budget plus a 30-second margin.
The loop deadline is 30 seconds today. The total real-core settlement bound remains an open proof.
The installer still stops its own identified unit through systemd.

If the supervisor socket or stop command fails, systemd can reach its timeout and SIGKILL fallback.
Python already permits that fallback after a provenance refusal.
A startup failure before successful service activation can skip `ExecStop`, so graceful shutdown is not guaranteed there.
These are explicit failure limits. The hosted-runner proof must exercise them before acceptance of the packaged service.

The foreground Mac server keeps Python's ordinary SIGINT/SIGTERM behavior.
This proposal adds no change to the Mac executor, its LaunchAgent, or the live Mac installation.
A future managed Mac-only server needs an explicit authenticated Quit/stop integration.
An unannounced launchd or OS shutdown cannot supply prior intent automatically.
The Manager must accept that policy before any Mac service adopts this guard.

## Proof required after design acceptance

- Compare bare-signal refusal, valid intent plus signal, wrong capability, expired intent, and old-incarnation replay on isolated subprocesses.
- Distinguish a supervisor-only signal from signals to both child and supervisor. Preserve the child-death failure and restart contract.
- Preserve foreground shutdown and restart behavior, including a stalled application child.
- Prove that child and agent environments contain no stop capability.
- Prove ordered final writes and lock retention with one shutdown deadline.
- On a hosted Linux runner, prove actual new-unit `ExecStop`, installer stop, and failed-control timeout behavior.
- Prove a startup failure before activation, where systemd can skip `ExecStop`.
- Prove `systemctl restart`: stop intent, clean stop, and a new supervisor incarnation and capability.
- Prove an accepted stop during an application restart through the existing restart socket. Stop wins without a replacement child afterward.
- Prove mode 0600 for the capability file and mode 0700 for its parent directory.
- Prove that neither capability nor capability path reaches the application child or any agent process environment.
- Prove that the capability path is outside every snapshot and backup root.
- Start and relocate the packaged supervisor on the Node-only target root. No compiler or Python can be required there.

The real-core hung-provider shutdown proof remains open until the conversation routes pull request merges.
The design does not claim that an in-memory stop proves that boundary.

## Source evidence

- `src/planner/server_lifecycle/signals.py`: Linux `sigtimedwait` sender capture and sender-blind non-Linux behavior.
- `src/planner/server_lifecycle/logic/shutdown.py`: guarded admission and ordinary foreground policy.
- `src/planner/server_lifecycle/process_identity.py`: manager identity and non-Linux policy.
- `src/planner/server_lifecycle/supervisor.py`: refusal diagnostics, child ownership, and restart socket.
- `ops/panels-environments/panels-live.service`: existing unit, read from the repository only.
- Panels worker skill: the two historical incidents. No incident frequency estimate comes from runtime telemetry.
