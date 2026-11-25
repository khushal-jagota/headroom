# A home on Mac or Linux

One command installs the server and machine code on Apple Silicon or Linux x86_64.
The server holds the record. Machine code runs as a separate service on the same account.
The installer connects the two through the local executor link, without a credential prompt. The Mac home runs in your graphical login session.
Logout stops the Mac home. Sleep makes it unreachable. Linux uses a systemd user service.
Linux setup requires user lingering so that the home and its backup stay available after logout.
Panels requires Tailscale for supported use from other devices and for extra machines.
A release home runs the TypeScript server and supports updates and rollback.
The home supplies native previews, metrics, maintenance, and stall alarms.
One Panels instance can own the account skills in one OS account.
Use a separate OS account for another instance, including an executor with a different record.
A disposable test home can share an account with `--isolated-providers --skip-command-link`, a separate root, service prefix, and port.
Use only its private provider folders. Use its absolute `ROOT/app/bin/panels` command.
The test home does not create, replace, or remove the account command.

## Install with one command

The server release carries its own Node. The installer needs `curl` and Python 3.11.8 or newer. The Mac requires macOS 15 or newer.
Linux requires x86_64, an active systemd user manager, and user lingering.
If lingering is absent, setup refuses before installation. Run `loginctl enable-linger "$USER"`, then repeat the command.
The sealed release also checks its native runtime requirements before the service starts.

Mac release jobs use bundled tools, resolved provider CLI directories, common user bins, and standard system directories.
The bundled Node directory precedes provider directories.
Jobs do not inherit the entire installation shell PATH. Direct use of the packaged installer preserves an explicit `PLAN_CLAUDE_EXECUTABLE`.
On Linux, use the machine account's `~/.local/bin` or the system bin directories for provider commands you install yourself.

Setup uses the host's time zone for attention and daily measures. To choose a different IANA time zone, add an option like `--time-zone Asia/Tokyo` after `home` in the command.

<!-- panels-desktop-install-command -->
```sh
(panels_setup=$(mktemp) && trap 'rm -f -- "$panels_setup"' EXIT && curl --disable --fail --silent --show-error --location --proto '=https' --proto-redir '=https' --output "$panels_setup" 'https://raw.githubusercontent.com/khushal-jagota/headroom/main/scripts/panels_install.py' && python3 "$panels_setup" home)
```

The command uses the official public releases. Anyone can download them without a GitHub login.
It selects a publication from the trusted release workflow on `main`.
It checks the publication identity, target, and archive checksum before it executes the sealed installer.
It refuses an existing installation root, service name, or occupied port.
It starts only the new server and machine code.
It checks the exact release health and an accepted machine connection before it reports its address.
It refuses an existing `~/.local/bin/panels`, then writes a small command there that runs the installed app.

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
The installer runs with the bundled Node from the retained release. Its service can restore that release if the new home fails its trial.

For an already downloaded publication you trust, download the installer to a file. Run it with the local archive and receipt:

```sh
python3 ./panels_install.py home --time-zone America/New_York --archive /path/to/panels-app.tar.gz --receipt /path/to/publication.json
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

For a private Project repository, configure Git access for each machine account that runs its Tickets. Check that account's access with `git ls-remote` before starting a Ticket. Configure push access only on machines that need it for approved work.

### 4. Reach the home from a different device

The home listens on loopback. Tailscale Serve supplies HTTPS and the caller's login from your other devices.

Configure owner access before you start Serve. Until the settings take effect, the server treats loopback callers as the owner.

1. Install Tailscale and sign in. Turn on MagicDNS and [HTTPS certificates](https://tailscale.com/docs/how-to/set-up-https-certificates) for your tailnet.
2. Find the home's full DNS name in the Tailscale admin console. Use `https://` and that name as the origin.
3. Add the following entries inside the JSON object in the installation's `data/config.yaml`. Keep the existing entries.

   ```json
   "trusted_ingress_provider": "tailscale",
   "trusted_ingress_allowed_login": "you@example.com",
   "trusted_ingress_canonical_origin": "https://MACHINE.TAILNET.ts.net"
   ```

   Set the allowed login to the exact Tailscale login for the owner.
4. Restart the home. On Linux, run `systemctl --user restart com.panels.home.service` with your home service name. On Mac, use the stop and start steps below.
5. Check that the owner-only endpoint below returns HTTP 403 on loopback. The empty request cannot create a machine.

   ```sh
   curl --silent --show-error --output /dev/null --write-out '%{http_code}\n' \
     --header 'Content-Type: application/json' --data '{}' \
     http://127.0.0.1:8767/api/machines/join-command
   ```

   Use your home port if it differs. If the result differs from 403, keep the route closed and check the settings.
6. Run `tailscale serve --bg http://127.0.0.1:8767` with your home port. Check the origin with `tailscale serve status`.

Open the HTTPS address on a different device signed in to the allowed login. See [authority](../../docs/authority.md) for what the login decides.

### 5. Updates

Only the owner can press **Update** when the app shows a release. A fresh install supplies no GitHub credential. It needs a home release with public update support to fetch updates anonymously. See [deployment](../../docs/deployment.md).

### 6. Add a second machine

A second machine runs Workers for this home. Finish step 4 first. Sign in to Tailscale on the second machine. On the home, open Settings → Machines and add the machine.

From a terminal with owner authority, the equivalent command is:

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
