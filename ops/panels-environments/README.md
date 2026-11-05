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
Their tools inherit that unit, and it stops them all when the Worker ends. Workers
have no memory cap. The slice and its parent `panels.slice` only give Workers CPU
weight 10, below the foreground `app.slice`, where Panels uses weight 100.

When the machine runs out of memory, Linux kills one process by score and the rest
carry on:

- Every agent's tool shell sets its own score to 1000 through `BASH_ENV`, so tests and
  browsers go first.
- Worker agents keep the user manager's default score of 200.
- The live service asks for -500, so Panels goes last but can still be stopped if it
  leaks itself.
- Worker units and the live service use `OOMPolicy=continue`, so one kill ends only
  the killed process.

A user service cannot go below the user manager's own score, which is 100, without
root. Until the root step below is applied, the live service starts at 100.

Worker temporary files use a private `~/.pw-XXXXXXXX/t` directory on disk for the
agent process lifetime, including successive conversation turns. Shutdown removes
the directory after the tools stop. The unit boundary controls resources, not access
to files or credentials. Worker units retain `NoNewPrivileges=yes`.

### Activation

Run these steps after the release that removes the Worker memory caps is installed.
Older releases refuse to start Workers if the slice lacks its 24 GiB limit, so the
slice file must not be installed before them.

1. **Lower the user manager's floor and pin its default (root).** The drop-in makes it
   persistent. The last command applies it to the running manager without a restart.
   The pinned default keeps every other user unit at today's 200.

   ```sh
   sudo mkdir -p /etc/systemd/system/user@1000.service.d /etc/systemd/user.conf.d
   printf '[Service]\nOOMScoreAdjust=-500\n' | sudo tee /etc/systemd/system/user@1000.service.d/panels-oom.conf
   printf '[Manager]\nDefaultOOMScoreAdjust=200\n' | sudo tee /etc/systemd/user.conf.d/panels-oom.conf
   sudo systemctl daemon-reload
   echo -500 | sudo tee /proc/"$(systemctl show -p MainPID --value user@1000.service)"/oom_score_adj
   ```

2. **Make the 1 GiB reservation effective (root).** `MemoryLow` protects Panels only
   when every ancestor supplies the same protection. It does not preallocate RAM.

   ```sh
   sudo systemctl set-property user.slice MemoryLow=1G
   sudo systemctl set-property user-1000.slice MemoryLow=1G
   sudo systemctl set-property user@1000.service MemoryLow=1G
   systemctl --user set-property app.slice MemoryLow=1G
   ```

3. **Install the unit files and restart Panels.** Workers must not perform this step.

   ```sh
   cd "$HOME/Deployments/Panels/current/app/ops/panels-environments"
   cp panels-live.service panels-workers.slice "$HOME/.config/systemd/user/"
   systemctl --user daemon-reload
   systemctl --user restart panels-live.service
   ```

4. **Check.** The live service reports `OOMPolicy=continue`, `OOMScoreAdjust=-500`
   and `MemoryLow=1073741824`. Its process score is -500. The Worker slice reports
   `MemoryMax=infinity`.

   ```sh
   systemctl --user show panels-live.service -p OOMPolicy -p OOMScoreAdjust -p MemoryLow
   cat /proc/"$(systemctl --user show -p MainPID --value panels-live.service)"/oom_score_adj
   systemctl --user show panels-workers.slice -p MemoryMax
   ```
