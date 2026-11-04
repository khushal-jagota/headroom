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
whole process group on OOM. The slice limits all Workers to 20 GiB and gives them
CPU weight 10. Its parent `panels.slice` also uses weight 10, below the
foreground `app.slice`. Panels uses weight 100 and a 1 GiB memory reservation.
`MemoryLow` protects memory under pressure. It does not preallocate RAM.

Worker temporary files use a private directory on disk for the agent process
lifetime, including successive conversation turns. Shutdown removes the directory
after the tools stop. Worker OOM errors appear in the existing conversation error
path. The unit boundary controls resources, not access to files or credentials.

The operator must install `panels.slice` and `panels-workers.slice` alongside `panels-live.service`
before activation. The operator then reloads the user manager and restarts Panels.
Workers must not reload or restart the live service. Existing agents remain in
their original units until the operator restarts Panels. The operator must check
that no old Worker tools remain before activation.

The scratch verification record for the containment change records measured peaks
and the selected budgets. These limits leave about 11 GiB outside the Worker
budget on the 31 GiB VPS. Future changes must preserve headroom for Panels and
the host.

For activation, the operator runs these commands from this directory after the
application release is ready:

```sh
cp panels.slice panels-workers.slice panels-live.service "$HOME/.config/systemd/user/"
systemctl --user daemon-reload
systemctl --user restart panels-live.service
```

These commands stop the live app and its existing children. Do not run them from
a Worker. Keep the prepared files with the release until the operator selects
the activation time.
