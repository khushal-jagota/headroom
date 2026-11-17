# A home on Mac or Linux

One command installs a home on an Apple Silicon Mac or a Linux x86_64 server.
The host holds the record. The Mac home runs in your graphical login session.
Logout stops the Mac home. Sleep makes it unreachable. Linux uses a systemd user service.
Linux setup requires user lingering so that the home and its backup stay available after logout.
Tailscale supplies remote HTTPS access.
A release home runs the TypeScript server and supports updates and rollback.
The home supplies native previews, metrics, maintenance, and stall alarms.
One Panels instance can own the account skills in one OS account.
Use a separate OS account for another instance, including an executor with a different record.

## Install with one command

Install Node.js 22 or newer first. The download script needs Python 3.11.8 or newer. The Mac requires macOS 15 or newer.
Use `/opt/homebrew/bin/node` on the Mac. Linux requires x86_64, an active systemd user manager, and user lingering.
If lingering is absent, setup refuses before installation. Run `loginctl enable-linger "$USER"`, then repeat the command.
The sealed release also checks its native runtime requirements before the service starts.

Mac release jobs use bundled tools, resolved provider CLI directories, common user bins, and standard system directories.
The Homebrew directory precedes provider directories so that provider installs do not replace the required Node runtime.
Jobs do not inherit the entire installation shell PATH. Direct use of the packaged installer preserves an explicit `PLAN_CLAUDE_EXECUTABLE`.

Authenticate the GitHub CLI for the release repository, then run this command:

```sh
panels_setup=$(mktemp) && gh api repos/khushal-jagota/headroom/contents/scripts/panels_install.py --header 'Accept: application/vnd.github.raw+json' > "$panels_setup" && python3 "$panels_setup" home
```

The command selects a successful publication from the trusted release workflow on `main`.
It checks the publication identity, target, and archive checksum before it executes the sealed installer.
It refuses an existing installation root, service name, or occupied port.
It starts only the new home and checks the exact release health before it reports its address.
A private repository requires GitHub access to its contents and Actions artifacts.
The command uses the GitHub CLI login for release access. It does not persist a new token or authentication setting.

The Mac default root is `~/Library/Application Support/PanelsHome`. The service name is `com.panels.home`.
Linux uses `~/.local/share/panels-home` and the user service `com.panels.home.service`.
Both hosts schedule the existing backup command at 02:30 each day.
Linux uses the separate `com.panels.home.backup.service` and `com.panels.home.backup.timer` user units.
The Mac also registers its maintenance and stall-watchdog jobs for this installation and future logins.
Maintenance runs at 03:15 each day. The watchdog checks stalls every 30 seconds.
The home binds to loopback port 8767. Select another port with `--port`.
Use `--root` and `--service` for a separate installation.
On the Mac, the command records the external Node at `/opt/homebrew/bin/node` for updates. It must stay outside the home and remain available.

Provider login remains a separate step. Authenticate Codex, Claude, or Hermes before the first Worker starts.
For another device, configure Tailscale Serve HTTPS and the owner login through the existing trusted ingress.
The local address does not grant remote access. See [deployment](../../docs/deployment.md) and [authority](../../docs/authority.md).

For an already downloaded, trusted publication, use the same command with local files:

```sh
python3 "$panels_setup" home --archive /private/panels-app.tar.gz --receipt /private/publication.json
```

Use the Mac home publication on Mac and the Linux home publication on Linux.
The receipt must match the exact archive. Do not use an executor archive for a home.
Use `--isolated-providers` for disposable checks. It prevents account skill links and uses private provider folders.
If setup fails, it stops only its new jobs and removes only its created root, links, and unit files.
Existing roots and services remain untouched. If cleanup cannot confirm a stopped job, setup retains its files and reports that job.
No existing home is restarted or replaced. Updates still use the owner Update action.
Mac updates stop this installation's home and auxiliary jobs before replacement.
They restore the previously loaded auxiliary jobs after exact release health passes.
An update also supplies maintenance and watchdog jobs to older two-job homes, with the same login persistence choice.
Foreign login links cause an error and remain unchanged.
An auxiliary restart failure after accepted health does not restore the previous database.

## Stop, restart, and backup

Set the installation's root and label. These are the defaults:

```sh
export PANELS_ROOT="$HOME/Library/Application Support/PanelsHome"
export PANELS_LABEL=com.panels.home
```

Before stop, record the supervisor and app identities for this home.
Stop the exact jobs, with the auxiliary jobs first:

```sh
for suffix in maintenance stall-watchdog backup; do
  launchctl bootout "gui/$(id -u)/$PANELS_LABEL.$suffix"
done
launchctl bootout "gui/$(id -u)/$PANELS_LABEL"
```
Wait until `launchctl print "gui/$(id -u)/$PANELS_LABEL"` reports that the job is absent.
Check that the recorded supervisor and app ended and that the port has no listener.
If the stop exceeds 30 seconds, record the output and stop. Do not bootstrap a replacement until these checks pass.
Apply the same job-absence and recorded-process checks to every auxiliary label before reload.
Start them again, the home first. This is also the ordinary service restart route.

```sh
launchctl bootstrap "gui/$(id -u)" "$PANELS_ROOT/agents/$PANELS_LABEL.plist"
for suffix in backup maintenance stall-watchdog; do
  launchctl bootstrap "gui/$(id -u)" "$PANELS_ROOT/agents/$PANELS_LABEL.$suffix.plist"
done
```

The supervisor remains in launchd custody. Its hard death also removes the app and permits a fresh start.

To remove the installation, first complete those stop checks for all four labels.
Remove only login symlinks whose targets equal this installation's generated plists.
Do not remove foreign entries, provider folders, or data during job removal.
Keep the installation folder until all recorded jobs and processes retire.

Run a backup now with `launchctl kickstart "gui/$(id -u)/$PANELS_LABEL.backup"`.
Check `logs/backup.stderr.log` and `logs/backup.stdout.log`, then inspect the snapshot under `data/backups/`.
The snapshot contains the database, Worker settings, and managed skills. It excludes uploads, Ticket files, and provider homes.
Retain a separate copy of those files when you need them. Database restore does not restore provider authentication.

## Jobs, sleep, and logs

Logs live in `$PANELS_ROOT/logs/`. Mac cleanup truncates oversized live logs in place because launchd retains their open descriptors.
The login session owns all four jobs. Logout stops them. Sleep makes the home unreachable.
Calendar jobs missed during sleep run after wake. Several missed calendar events coalesce into one run.
The watchdog skips intervals during sleep. It resumes at a later interval and does not replay each missed check.
After login or a missed watchdog stretch, the first interval defers only a stale-checker alarm.
The next interval still alarms if the checker does not recover.
An absent login session or powered-off Mac provides no execution promise or catch-up guarantee.

Check `logs/maintenance.stdout.log`, `logs/maintenance.stderr.log`, and the corresponding `stall-watchdog` logs after a failure.
Use `launchctl print "gui/$(id -u)/$PANELS_LABEL.maintenance"` or the `.stall-watchdog` label to inspect the last exit status.
A job failure does not broaden cleanup permissions. Preserve the logs and repair the reported cause before another manual run.
