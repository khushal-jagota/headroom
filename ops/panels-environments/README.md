# Panels single-user operating inputs

These checked-in files describe the Linux and macOS operating surface. They do not
modify a host by themselves.

The VPS has one Panels identity: the existing UID-1000 `vps` user. Source lives at
`~/Coding/Panels`; the deployed app and live state live at
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
`~/Coding/Panels/.github-runner`, then use `configure-deployment-runner.sh` with the
repository URL and a short-lived registration token. The script registers the custom
labels `production` and `panels-deploy`; GitHub adds `self-hosted` and `linux`. The
unit requires that configured runner and runs its `run.sh`; the deploy workflow
requests exactly those four labels.

The workflow builds the requested exact commit in runner-temporary storage and deploys
only `~/Deployments/Panels/current/app`. It controls live with `systemctl --user` and
leaves `current/data` and `current/logs` in place.

## Commands and non-production

The checked-in `panels` wrapper follows the deployed application and forwards arguments:

```sh
install -d "$HOME/.local/bin"
install -m 0755 panels "$HOME/.local/bin/panels"
```

Staging is not a service. Prepare its fake state under
`~/Coding/Panels/data/environments/staging`, start it from `~/Coding/Panels` on demand,
and stop it after active work. Ticket servers follow the same on-demand rule from their
isolated worktrees.

The live and backup units use the `vps` user's ordinary `HOME`, Hermes, Codex, and
Claude homes. They contain no `User=` switch, privileged ownership choreography,
tmpfiles rules, or root-owned environment-manager path.

Backup and restore instructions are in [backup-restore.md](backup-restore.md).
