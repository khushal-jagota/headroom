# Panels single-user operating inputs

These checked-in files describe the Linux operating surface. They do not
modify a host by themselves.

The VPS has one Panels identity: the existing UID-1000 `vps` user. Source lives at
`~/projects/Panels/Panels`; the deployed app and live state live at
`~/Deployments/Panels/current`. Do not create `panels-live`, `panels-worker`, or
`panels-deploy` accounts and do not install these units as system services.

## Linux user services

Install the unit files in `~/.config/systemd/user/`, then reload and enable them as
`vps`:

```sh
mkdir -p "$HOME/.config/systemd/user"
cp panels-live.service panels-deployment-runner.service \
  panels-db-backup.service panels-db-backup.timer \
  panels-maintenance.service panels-maintenance.timer \
  "$HOME/.config/systemd/user/"

systemctl --user daemon-reload
systemctl --user enable --now panels-live.service
systemctl --user enable --now panels-deployment-runner.service
systemctl --user enable --now panels-db-backup.timer
systemctl --user enable --now panels-maintenance.timer
```

Enable user lingering once during the host migration if these services must start
before an interactive login. That host-level migration step is outside this repository
change.

`panels-live.service` binds the app to loopback port 8767 through its explicit live
configuration. The existing Tailscale Serve route remains unchanged.

Worker checks use temporary databases and scratch servers, never the live database.

## Stall watchdog

`panels-stall-watchdog.timer` runs `panels stalls watchdog` every 30 seconds, outside the
live service, so a stuck server can still raise an alarm. Each run opens the live
database read-only, alarms once on each stall the server's checker has recorded and on
a checker heartbeat older than 90 seconds, pushes to Khushal's subscribed browsers and
devices unless the Panels "Stalled work" notification is switched off, and messages the
Chief through the live server (best effort, 10 seconds). Its record of what it has
already alarmed on is `data/stall-watchdog/stall-watchdog-state.json`. Native push needs
the same `PLAN_APNS_*` inputs as the live service, added to this unit's environment.

Install it once, after the release that ships it is current:

```sh
cd "$HOME/Deployments/Panels/current/app/ops/panels-environments"
cp panels-stall-watchdog.service panels-stall-watchdog.timer "$HOME/.config/systemd/user/"
systemctl --user daemon-reload
systemctl --user enable --now panels-stall-watchdog.timer
```

Check it with `systemctl --user list-timers panels-stall-watchdog.timer` and
`journalctl --user -u panels-stall-watchdog.service`.

## Dedicated GitHub Actions runner

Install the repository's self-hosted runner beneath
`~/Deployments/Panels/deployment-runner`, alongside but outside `current`, then use
`configure-deployment-runner.sh` with the
repository URL and a short-lived registration token. The script registers the custom
labels `production` and `panels-deploy`; GitHub adds `self-hosted` and `linux`. The
unit requires that configured runner and runs its `run.sh`; the deploy workflow
requests exactly those four labels.

Panels now installs its own updates. The runner remains for one release only: the
installed 5622d2a81 server installs the first self-installing release through the
workflow's bridge `adopt` job (see `docs/deployment.md`). The next release deletes that
job; then stop and disable `panels-deployment-runner.service` and unregister the runner.

## Commands and non-production

The checked-in `panels` wrapper follows the deployed application's `bin/panels`
command and forwards the caller's arguments and environment:

```sh
install -d "$HOME/.local/bin"
install -m 0755 panels "$HOME/.local/bin/panels"
```

That command is the interactive CLI and works from any directory. The deployed command
sets its own application root and exact SHA while leaving the rest of the caller
environment intact. It is separate from `bin/panels-launcher`, which rebuilds an
isolated environment for the live service, scheduled maintenance, and backups.

Staging is not a service. Prepare its fake state under
`~/projects/Panels/Panels/data/environments/staging`, start it from
`~/projects/Panels/Panels` on demand,
and stop it after active work. Ticket servers follow the same on-demand rule from their
isolated worktrees.

The live and backup units use the `vps` user's ordinary `HOME`, Hermes, Codex, and
Claude homes. They contain no `User=` switch, privileged ownership choreography,
tmpfiles rules, or root-owned environment-manager path.

Backup and restore instructions are in [backup-restore.md](backup-restore.md).

## Worker processes

One supervisor owns every local agent process on the home (Workers, the Chief,
Managers, and generic and smoke-test conversations) and the Worker processes on
executors. It records process identities and bounds shutdown. Only Workers lower
their CPU priority to nice 10 and raise their OOM score. On Linux, each new supervised
process gets its own session and enters a delegated cgroup v2 child group before
backend execution. Stop kills that group and requires proof that it is empty. Detached descendants remain in the group. Requests to an existing external daemon
do not make that daemon or its children supervised descendants. This is not a security sandbox.
The durable record binds cleanup to that group across an application restart.

The live unit and generated Linux executor unit set `Delegate=yes`. The supervisor
does not enable controllers in `cgroup.subtree_control`. The server stays in the parent
group, and each supervised process occupies a child. The parent must keep an empty
`cgroup.subtree_control`: cgroup v2 permits resident processes there because no domain
controllers are enabled for its children. Existing resource settings apply to the whole
service subtree. No separate Worker units or resource limits return.

Without delegation, writable cgroup v2, or an empty parent `cgroup.subtree_control`,
every conversation still launches. Cleanup reports the unavailable support and retains its fence.
A process inventory alone cannot prove that unseen detached descendants ended.
Old records retain their original evidence and do not acquire a new containment claim.

For the existing live installation, add only delegation through a user-unit drop-in.
Do not replace the installed unit with the repository unit for this update.
At the planned update, run these commands as `vps`:

```sh
mkdir -p "$HOME/.config/systemd/user/panels-live.service.d"
cat > "$HOME/.config/systemd/user/panels-live.service.d/delegate.conf" <<'UNIT'
[Service]
Delegate=yes
UNIT
systemctl --user daemon-reload
systemctl --user restart panels-live.service
systemctl --user show panels-live.service -p ActiveState -p Delegate -p KillMode -p ControlGroup
```

Expect `ActiveState=active`, `Delegate=yes`, and `KillMode=control-group`. If the normal
update already supplies that exact restart, create the drop-in and reload before it.
Do not restart twice. Do not migrate active Workers between groups. The existing
service stop covers the entire subtree, including old Workers. After that restart,
new supervised processes use delegated groups. Generated executor units receive the same setting
at installation. Restart them through their ordinary update route. This ticket does
not apply either change to a live service.

| Home service property | Supervised process behavior |
|---|---|
| `CPUWeight=100` | Shared by the home and every supervised process. Each Worker uses nice 10; other conversations keep the home's nice value. |
| `OOMScoreAdjust=-500` | The home keeps its protection. At spawn, each Worker raises its own score to at least 0. Higher inherited scores stay higher. Tools inherit that score. The Chief, Managers, and other conversations keep the home's score. |
| `MemoryAccounting=yes`, `MemoryLow=1G` | Apply to the combined home and supervised group. There is no separate memory reservation against Workers. |
| `OOMPolicy=continue` | Remains the home service policy. Workers have no independent unit policy or memory cap. |
| `NoNewPrivileges=yes`, `PrivateTmp=yes` | Inherited from the home service. These are not portable per-Worker containment. |
| Service stop and restart | Apply to the home service and its processes. The supervisor reconciles its records after restart. On the home, each new launch deletes finished records that no conversation still names; executors delete none. |
| Environment and working directory | Each backend preserves its explicit launch inputs and role identity. |

The Linux adapter writes the Worker OOM score before the backend executes. A failed
write refuses launch. The removed Bash startup hook raised tool shells and their
children to 1000, above the agent. That tool-first preference no longer exists.
macOS has no equivalent OOM score, so that primitive does nothing there.

`WorkerScratch` supplies Workers, and only Workers, with short disk directories and
private tmux and screen socket directories, independently of supervision. Scratch
survives successive turns and closes only after confirmed process cleanup. Account
and login probes retain their separate lifetime.

### Operator installation

Install the home service through the ordinary release route. Workers must not reload
or restart the live service. Existing unit-backed custody remains readable until
exact retirement proves cleanup. Do not erase old process records to clear a fence.

After old Worker units retire, the operator can remove the obsolete slice files from
the user service directory. New Worker launches need neither slice. Keep the home's
existing OOM protection and service properties. No root OOM or memory configuration
change is part of Worker supervision.

At release, stop installing `panels.slice` and `panels-workers.slice` from this folder
into `~/.config/systemd/user/`. Remove their old copy, enable, and start steps from
local installation instructions. Do not create per-Worker transient services or add
Worker slice assignments. These services had no static Worker template to remove.
Retain exact-unit evidence until old unit-backed custody retires. Then remove the
obsolete slice files and perform any user-manager reload through the operator's
release procedure. Keep `panels-live.service` and the backup, maintenance, watchdog,
and deployment-runner units. This repository change performs no live unit operation.

On a disposable Linux home, verify that the home retains its configured OOM score,
that each Worker uses at least 0, and that its tools inherit the Worker score. Check
home responsiveness under heavy builds and low memory. These checks must not run on
the live home. Native Mac checks require an authorized test machine.
