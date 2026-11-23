# A home on Mac or Linux

One command installs the server and machine code on Apple Silicon or Linux x86_64.
The server holds the record. Machine code runs as a separate service on the same account.
The installer connects the two through the local executor link, without a credential prompt. The Mac home runs in your graphical login session.
Logout stops the Mac home. Sleep makes it unreachable. Linux uses a systemd user service.
Linux setup requires user lingering so that the home and its backup stay available after logout.
Tailscale supplies remote HTTPS access.
A release home runs the TypeScript server and supports updates and rollback.
The home supplies native previews, metrics, maintenance, and stall alarms.
One Panels instance can own the account skills in one OS account.
Use a separate OS account for another instance, including an executor with a different record.

## Install with one command

The server release carries its own Node. No separate Node installation is required. The download script needs Python 3.11.8 or newer. The Mac requires macOS 15 or newer.
Linux requires x86_64, an active systemd user manager, and user lingering.
If lingering is absent, setup refuses before installation. Run `loginctl enable-linger "$USER"`, then repeat the command.
The sealed release also checks its native runtime requirements before the service starts.

Mac release jobs use bundled tools, resolved provider CLI directories, common user bins, and standard system directories.
The bundled Node directory precedes provider directories.
Jobs do not inherit the entire installation shell PATH. Direct use of the packaged installer preserves an explicit `PLAN_CLAUDE_EXECUTABLE`.

Authenticate the GitHub CLI for the release repository, then run this command:

```sh
panels_setup=$(mktemp) && gh api repos/khushal-jagota/headroom/contents/scripts/panels_install.py --header 'Accept: application/vnd.github.raw+json' > "$panels_setup" && python3 "$panels_setup" home
```

The command selects a successful publication from the trusted release workflow on `main`.
It checks the publication identity, target, and archive checksum before it executes the sealed installer.
It refuses an existing installation root, service name, or occupied port.
It starts only the new server and machine code.
It checks the exact release health and an accepted machine connection before it reports its address.
A private repository requires GitHub access to its Release assets. Legacy update attempts also require access to Actions artifacts.
The command uses the GitHub CLI login for release access. It keeps that token in `data/secrets/update-token` for updates. It refuses an existing `~/.local/bin/panels`, then writes a small `panels` command there that runs the installed app.

The Mac default root is `~/Library/Application Support/PanelsHome`. The service name is `com.panels.home`.
Linux uses `~/.local/share/panels-home` and the user service `com.panels.home.service`.
Both hosts schedule the backup at 02:30 each day, maintenance at 03:15 each day, and the stall watchdog every 30 seconds.
Linux uses a user service and timer for each job, for example `com.panels.home.backup.service` and `com.panels.home.backup.timer`.
The server service and its separate machine service restart after an exit.
Apple Silicon registers the server and machine jobs for this installation and future logins.
The machine job uses the server label plus `.machine`, for example `com.panels.home.machine`.
Linux adds `.service` to that label.
The machine state stays under `machine/data` in the installation root.
Its runtime snapshot stays under `machine/runtime`, separate from the server app and data.
The home binds to loopback port 8767. Select another port with `--port`.
Use `--root` and `--service` for a separate installation.
Updates copy their installer and bundled Node outside the app that they replace. No manual Node step is required.

For an already downloaded, trusted publication, use the same command with local files:

```sh
python3 "$panels_setup" home --archive /private/panels-app.tar.gz --receipt /private/publication.json
```

Use the Mac home publication on Mac and the Linux home publication on Linux.
The receipt must match the exact archive. Do not use an executor archive for a home.
The server archive supplies both programs at the same revision. A second download is not required.
Use `--isolated-providers` for disposable checks. It prevents account skill links and uses private provider folders.
If setup fails, it stops its new machine job before the server jobs.
It removes only its created root, links, and unit files.
Existing roots and services remain untouched. If cleanup cannot confirm a stopped job, setup retains its files and reports that job.
No existing home is restarted or replaced. Updates still use the owner Update action.
Mac updates stop this installation's home and auxiliary jobs before replacement.
They restore the previously loaded auxiliary jobs after exact release health passes.
An update also supplies maintenance and watchdog jobs to older two-job homes, with the same login persistence choice.
Foreign login links cause an error and remain unchanged.
An auxiliary restart failure after accepted health does not restore the previous database.

## After the install

The installer prints the local address. Do these steps in this order. Each step names how to check it.

### 1. Sign in to a provider

Provider logins belong to this machine. The server never holds them. Sign in as the account that runs the home:

```sh
codex login          # Codex
claude auth login    # Claude
```

Antigravity uses the sign-in of the `agy` command. Check a login with `codex login status` or `claude auth status`.

### 2. Choose the model of the Chief and the Managers

A new home starts the Chief and the Managers on Codex. If you use a different provider, open Settings → Agents. Choose a model for the Chief of Staff and for the Manager role. Workers take their model from their Worker type in Settings → Workers.

### 3. Make the first project

A new home has no projects. The browser app shows projects but does not make them. Make one with the `panels` command:

```sh
panels project create --name "My project" --repository-url https://github.com/OWNER/REPO.git
```

The installer puts `panels` in `~/.local/bin`. If the installer said that this folder is not on your path, add it first. If you chose a `--port`, set `PLAN_SERVER_URL=http://127.0.0.1:PORT` before you use `panels`. Check with `panels project list`.

Make the first project before step 4. After step 4, the home trusts only the Tailscale login, and a terminal on this machine is no longer the owner. Then ask the Chief to make each new project.

### 4. Reach the home from a different device

The home listens only on this machine. Panels uses Tailscale Serve for HTTPS from your other devices. Tailscale also tells the home who is calling.

Do not open the home to other devices before you set the allowed login. Without these settings, every caller is the owner.

1. Install Tailscale and sign in. Turn on HTTPS certificates for your tailnet in the Tailscale admin console.
2. Publish the home: `tailscale serve --bg http://127.0.0.1:8767`. Use your port if you chose one.
3. Read the address with `tailscale serve status`. It looks like `https://MACHINE.TAILNET.ts.net`.
4. Add three settings to `data/config.yaml` in the installation root. The file is JSON, so keep it valid JSON:

   ```json
   "trusted_ingress_provider": "tailscale",
   "trusted_ingress_allowed_login": "you@example.com",
   "trusted_ingress_canonical_origin": "https://MACHINE.TAILNET.ts.net"
   ```

   The allowed login is your Tailscale login name. Only that login is the owner.
5. Restart the home. On Linux, run `systemctl --user restart com.panels.home.service`. On a Mac, use the stop and start steps below.

Open the HTTPS address on a different device to check it. See [authority](../../docs/authority.md) for what the login decides.

### 5. Updates

Updates are on from the install. The installer keeps the GitHub CLI token in `data/secrets/update-token` with private access, and the home uses it to read new releases. When a release is ready, the app shows **Update available**. Only the owner presses **Update**. See [deployment](../../docs/deployment.md).

If you sign out of the GitHub CLI or the token expires, the home cannot see new releases. Sign in again, then run `gh auth token > data/secrets/update-token` from the installation root.

### 6. Add a second machine

A second machine runs Workers for this home. It needs step 4 first, because it reaches the home at its HTTPS address. Open Settings → Machines and add the machine, or run:

```sh
panels machine join-command "Second machine" --home https://MACHINE.TAILNET.ts.net
```

Run the printed command on the other machine. The same command supports Linux x86_64 and Apple Silicon.
See the [Linux installation notes](../linux-executor/installation.md) or [Apple Silicon installation notes](../mac-executor/typescript-installation.md).
The new machine signs in to its own providers, as in step 1.

## Stop, restart, and backup

Set the installation's root and label. These are the defaults:

```sh
export PANELS_ROOT="$HOME/Library/Application Support/PanelsHome"
export PANELS_LABEL=com.panels.home
```

Before stop, record the server and machine process identities.
Stop the exact jobs, with the machine job first:

```sh
for suffix in machine maintenance stall-watchdog backup; do
  launchctl bootout "gui/$(id -u)/$PANELS_LABEL.$suffix"
done
launchctl bootout "gui/$(id -u)/$PANELS_LABEL"
```
Wait until `launchctl print "gui/$(id -u)/$PANELS_LABEL"` reports that the job is absent.
Check that the recorded server and machine processes ended and that the port has no listener.
If the stop exceeds 30 seconds, record the output and stop. Do not bootstrap a replacement until these checks pass.
Apply the same job-absence and recorded-process checks to every machine and auxiliary label before reload.
Start the server first, then the machine and auxiliary jobs. This is also the ordinary service restart route.

```sh
launchctl bootstrap "gui/$(id -u)" "$PANELS_ROOT/agents/$PANELS_LABEL.plist"
for suffix in machine backup maintenance stall-watchdog; do
  launchctl bootstrap "gui/$(id -u)" "$PANELS_ROOT/agents/$PANELS_LABEL.$suffix.plist"
done
```

The supervisor remains in launchd custody. Its hard death also removes the app and permits a fresh start.

To remove the installation, first complete those stop checks for all five labels.
Remove only login symlinks whose targets equal this installation's generated plists.
Do not remove foreign entries, provider folders, or data during job removal.
Keep the installation folder until all recorded jobs and processes retire.

Run a backup now with `launchctl kickstart "gui/$(id -u)/$PANELS_LABEL.backup"`.
Check `logs/backup.stderr.log` and `logs/backup.stdout.log`, then inspect the snapshot under `data/backups/`.
The snapshot contains the database, Worker settings, and managed skills. It excludes uploads, Ticket files, and provider homes.
Retain a separate copy of those files when you need them. Database restore does not restore provider authentication.

## Jobs, sleep, and logs

Logs live in `$PANELS_ROOT/logs/`. Mac cleanup truncates oversized live logs in place because launchd retains their open descriptors.
The login session owns all five jobs. Logout stops them. Sleep makes the home unreachable.
Calendar jobs missed during sleep run after wake. Several missed calendar events coalesce into one run.
The watchdog skips intervals during sleep. It resumes at a later interval and does not replay each missed check.
After login or a missed watchdog stretch, the first interval defers only a stale-checker alarm.
The next interval still alarms if the checker does not recover.
An absent login session or powered-off Mac provides no execution promise or catch-up guarantee.

Check `logs/maintenance.stdout.log`, `logs/maintenance.stderr.log`, and the corresponding `stall-watchdog` logs after a failure.
Use `launchctl print "gui/$(id -u)/$PANELS_LABEL.maintenance"` or the `.stall-watchdog` label to inspect the last exit status.
A job failure does not broaden cleanup permissions. Preserve the logs and repair the reported cause before another manual run.
