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

One supervisor owns Worker processes on the home and on executors. It records process
identities, lowers CPU priority to nice 10, and bounds shutdown. Cleanup proves that
identified processes ended. A tool that detaches before observation can survive.
Unknown cleanup retains ownership and blocks replacement and folder removal.

Linux Workers share the home service resource domain. New launches do not use
`panels-workers.slice`, `panels.slice`, or transient Worker services. Nice priority
does not replace their old aggregate CPU share or control-group cleanup.

| Home service property | Worker behavior |
|---|---|
| `CPUWeight=100` | Shared by the home and Workers. Each Worker uses nice 10. |
| `OOMScoreAdjust=-500` | The home keeps its protection. At spawn, each Worker raises its own score to at least 0. Higher inherited scores stay higher. Tools inherit that score. |
| `MemoryAccounting=yes`, `MemoryLow=1G` | Apply to the combined home and Worker group. There is no separate memory reservation against Workers. |
| `OOMPolicy=continue` | Remains the home service policy. Workers have no independent unit policy or memory cap. |
| `NoNewPrivileges=yes`, `PrivateTmp=yes` | Inherited from the home service. These are not portable per-Worker containment. |
| Service stop and restart | Apply to the home service and its processes. The supervisor reconciles Worker records after restart. |
| Environment and working directory | Each backend preserves its explicit launch inputs and role identity. |

The Linux adapter writes the Worker OOM score before the backend executes. A failed
write refuses launch. The removed Bash startup hook raised tool shells and their
children to 1000, above the agent. That tool-first preference no longer exists.
macOS has no equivalent OOM score, so that primitive does nothing there.

`WorkerScratch` supplies short disk directories independently of supervision. Scratch
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

On a disposable Linux home, verify that the home retains its configured OOM score,
that each Worker uses at least 0, and that its tools inherit the Worker score. Check
home responsiveness under heavy builds and low memory. These checks must not run on
the live home. Native Mac checks require an authorized test machine.
