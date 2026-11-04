# Panels single-user operating inputs

These checked-in files describe the Linux and macOS operating surface. They do not
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

## Dedicated GitHub Actions runner

Install the repository's self-hosted runner beneath
`~/Deployments/Panels/deployment-runner`, alongside but outside `current`, then use
`configure-deployment-runner.sh` with the
repository URL and a short-lived registration token. The script registers the custom
labels `production` and `panels-deploy`; GitHub adds `self-hosted` and `linux`. The
unit requires that configured runner and runs its `run.sh`; the deploy workflow
requests exactly those four labels.

The workflow builds the requested exact commit in runner-temporary storage and deploys
only `~/Deployments/Panels/current/app`. It controls live with `systemctl --user` and
leaves `current/data` and `current/logs` in place.

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

## Worker resource units

Linux Ticket agents run in transient user services under `panels-workers.slice`.
Their tools inherit that unit. Each service limits memory to 6 GiB and stops its
whole cgroup on OOM. The slice limits all Workers to 24 GiB and gives them
CPU weight 10. Its parent `panels.slice` also uses weight 10, below the
foreground `app.slice`. Panels uses weight 100 and a 1 GiB memory reservation.
`MemoryLow` protects memory under pressure only when every ancestor supplies
the same protection. It does not preallocate RAM.

Worker temporary files use a private `~/.pw-XXXXXXXX/t` directory on disk for the agent process
lifetime, including successive conversation turns. Shutdown removes the directory
after the tools stop. Worker OOM errors appear in the existing conversation error
path. The unit boundary controls resources, not access to files or credentials.
Worker units retain `NoNewPrivileges=yes` from the previous live-service launch path.

Activation has three required steps. Complete them in this order.

1. **Install the slices before installing the application release.** Copy the two
   slice files from the prepared release source, then reload the user manager:

   ```sh
   mkdir -p "$HOME/.config/systemd/user"
   cp panels.slice panels-workers.slice "$HOME/.config/systemd/user/"
   systemctl --user daemon-reload
   ```

   The new code has no uncontained fallback. It cannot launch Workers until the
   slices exist with the required limits. Updates restarts Panels on the new code
   during installation, so this step must precede an Updates install.

2. **Install the application release.** Use Updates or the approved release route
   only after the slices are installed and the user manager reload is complete.
   Updates performs its application restart during this step.

3. **Install the live-service change and restart Panels.** After the release install
   finishes, copy the prepared live-service file, reload the user manager, and
   perform the operator-owned restart:

   ```sh
   cp panels-live.service "$HOME/.config/systemd/user/"
   systemctl --user daemon-reload
   systemctl --user restart panels-live.service
   ```

   This applies `OOMPolicy=continue` and the live service's memory protection request.
   Workers must not perform these live operations. Check that no old Worker tools
   remain after shutdown.

The scratch verification record records measured peaks and the selected budgets.
These limits leave about 7 GiB plus swap outside the Worker budget on the 31 GiB
VPS. Future changes must preserve headroom for Panels and the host.

### Optional extra memory protection

The following ancestor allocations are optional extra protection. They do not gate
Worker isolation or release installation. The independent Worker cap preserves
headroom without them.

If the operator wants an effective 1 GiB `MemoryLow` reservation, apply the same
protection through the full cgroup hierarchy on this UID-1000 VPS:

```sh
sudo systemctl set-property user.slice MemoryLow=1G
sudo systemctl set-property user-1000.slice MemoryLow=1G
sudo systemctl set-property user@1000.service MemoryLow=1G
systemctl --user set-property app.slice MemoryLow=1G
```

The three system-manager commands protect existing ancestors. They do not create
another service or account. The user-manager command protects the foreground slice.
Each command persists its property. These optional host changes require operator
authority. Without them, the live unit's `MemoryLow` alone does not supply an
effective reservation.

After activation, check that each Worker cgroup is outside `panels-live.service`.
Check `memory.max`, `memory.oom.group`, CPU weights, and disk `TMPDIR`. If optional
memory protection was applied, also check `memory.low` along the ancestor chain.
