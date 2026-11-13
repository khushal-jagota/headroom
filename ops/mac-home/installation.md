# A home on Mac or Linux

One command installs a home on an Apple Silicon Mac or a Linux x86_64 server.
The host holds the record. The Mac home runs in your graphical login session.
Logout stops the Mac home. Sleep makes it unreachable. Linux uses a systemd user service.
Linux setup requires user lingering so that the home and its backup stay available after logout.
Tailscale supplies remote HTTPS access.
The source path below remains available. A release home also supports updates and rollback.
Previews, native metrics, maintenance schedules, and watchdog schedules remain separate work.
One Panels instance can own the account skills in one OS account.
Use a separate OS account for another instance, including an executor with a different record.

## Install with one command

Install Python 3.11.8 or newer and Node.js 22 first. The Mac requires macOS 15 or newer.
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
The home binds to loopback port 8767. Select another port with `--port`.
Use `--root` and `--service` for a separate installation.
On the Mac, run the bootstrap with a stable external Python interpreter.
The command records that interpreter for updates. It must stay outside the home and remain available.

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


## Prepare the Mac source

Install Xcode Command Line Tools, Homebrew, and Tailscale. Sign in to your tailnet.
Use a separate checkout and state folder. Keep this checkout at the chosen commit after setup.
Do not use another Panels home's source, environment, state folder, or launchd labels.

```sh
xcode-select --install
brew install python@3.13 node icu4c pkgconf git
git clone https://github.com/khushal-jagota/headroom.git "$HOME/panels-source"
cd "$HOME/panels-source"
git checkout <approved-commit>
export PATH="$(brew --prefix python@3.13)/libexec/bin:$(brew --prefix icu4c)/bin:$PATH"
export PKG_CONFIG_PATH="$(brew --prefix icu4c)/lib/pkgconfig"
mkdir -p "$HOME/.panels-build-tmp"
export TMPDIR="$HOME/.panels-build-tmp"
scripts/dev setup
scripts/dev npm --prefix web run build
```

Choose an unused loopback port and two unused launchd labels. The command refuses an existing state folder or loaded label.
It writes configuration and plists only. It does not start a service.

```sh
export PANELS_ROOT="$HOME/Library/Application Support/PanelsHome"
export PANELS_LABEL=com.panels.home
export PANELS_PORT=8767
.venv/bin/python scripts/mac_home_setup.py \
  --root "$PANELS_ROOT" --label "$PANELS_LABEL" --port "$PANELS_PORT"
```

The source and record remain separate. Configuration is at `$PANELS_ROOT/data/config.yaml`.
The database, skills, and settings live under `data/`.
Local Ticket worktrees live under `data/files/tickets/<ticket-id>/worktree`.
The configured workspace root is `workspaces/`.
Private sockets and the port-scoped lease live under `/var/tmp/panels-<uid>/`, with mode 0700.
These short runtime paths do not depend on the length of the state path.

## Use your existing login and start

Panels uses the provider logins already on your Mac. No second login is required.
The generator preserves your real `HOME` for the macOS login keychain.
It retains explicitly configured `CODEX_HOME`, `CLAUDE_CONFIG_DIR`, and `PLAN_HERMES_HOME` from the setup shell.
Without those variables, it uses `~/.codex`, `~/.claude`, and `~/.hermes`.
If you use custom provider folders, export their existing paths before the setup command.

The source home sets `provision_account_skills: true` and provisions its own managed skills into the native provider homes.
This is the same account-wide skill delivery that an installed Linux home uses.
Setup and startup check existing Panels skill links before any account skill write.
If another instance owns those links, setup prints a warning and startup repeats it.
The server can still start, but its Workers cannot own their skills until you use a separate OS account.
Neither setup nor startup replaces the other instance's account links.

For a disposable test, add `--isolated-providers` to the setup command.
This explicit test option creates empty provider folders under the state root. It does not create a login.
It sets `provision_account_skills: false` and does not provision account-wide skills.
A test can read another instance's existing skills. This exception is for tests only and does not establish its own skill authority.
Do not copy or symlink credentials, perform a second login, or change an existing executor's skill setup for a test.

For Hermes, add `PLAN_HERMES_PYTHON` to the home plist's environment with its installed interpreter path.
See the repository README for provider prerequisites. Select the provider in Panels before the first Worker.

```sh
launchctl bootstrap "gui/$(id -u)" "$PANELS_ROOT/agents/$PANELS_LABEL.plist"
curl --fail --connect-timeout 1 --max-time 2 --retry-max-time 30 \
  --retry 20 --retry-connrefused --retry-delay 1 \
  "http://127.0.0.1:$PANELS_PORT/api/health?expected_sha=$(git rev-parse HEAD)"
launchctl bootstrap "gui/$(id -u)" "$PANELS_ROOT/agents/$PANELS_LABEL.backup.plist"
```

The jobs remain registered for this login session. For automatic starts at later logins, copy these two plists into `~/Library/LaunchAgents/`.
Do not overwrite an existing plist. Keep the source and state paths unchanged.
Logs live in `$PANELS_ROOT/logs/`. The backup job runs at 02:30 while your login session exists.
No job promises a backup while logged out or asleep. A missed time can run after wake; check the backup log.

For phone access, run `tailscale serve --bg "http://127.0.0.1:$PANELS_PORT"` on a clean Tailscale Serve configuration.
Set these entries in `config.yaml` with your actual login and the HTTPS origin that Serve reports:

```yaml
trusted_ingress_provider: tailscale
trusted_ingress_allowed_login: your-tailnet-login
trusted_ingress_canonical_origin: https://your-mac.your-tailnet.ts.net
```

Restart after a configuration change. Open the HTTPS address and check owner access.
For an existing Serve configuration, preserve its routes and choose an unused HTTPS port.

## Stop, restart, and backup

Before stop, record the supervisor and app identities for this home.
Stop the exact jobs with `launchctl bootout "gui/$(id -u)/$PANELS_LABEL.backup"` and `launchctl bootout "gui/$(id -u)/$PANELS_LABEL"`.
Wait until `launchctl print "gui/$(id -u)/$PANELS_LABEL"` reports that the job is absent.
Check that the recorded supervisor and app ended and that the port has no listener.
If the stop exceeds 30 seconds, record the output and stop. Do not bootstrap a replacement until these checks pass.
Apply the same job-absence wait before reload of the backup label.
Start them again with the bootstrap commands above. This is also the ordinary service restart route.
The supervisor remains in launchd custody. Its hard death also removes the app and permits a fresh start.

Run a backup now with `launchctl kickstart "gui/$(id -u)/$PANELS_LABEL.backup"`.
Check `logs/backup.stderr.log` and `logs/backup.stdout.log`, then inspect the snapshot under `data/backups/`.
The snapshot contains the database, Worker settings, and managed skills. It excludes uploads, Ticket files, and provider homes.
Retain a separate copy of those files when you need them. Database restore does not restore provider authentication.

## Prove a restore on an isolated copy

Choose a verified snapshot and a fresh scratch folder with its own unused port. Do not stop the real home for this check.
Copy the snapshot into scratch. Restore only into the stopped scratch destination:

```sh
export PANELS_RESTORE="$HOME/PanelRestoreProof"
mkdir -m 700 "$PANELS_RESTORE"
mkdir -p "$PANELS_RESTORE/logs" "$PANELS_RESTORE/workspaces" \
  "$PANELS_RESTORE/providers/codex" "$PANELS_RESTORE/providers/claude" \
  "$PANELS_RESTORE/providers/hermes"
printf '{}\n' > "$PANELS_RESTORE/config.yaml"
cp -R "$PANELS_ROOT/data/backups/<snapshot-name>" "$PANELS_RESTORE/snapshot"
.venv/bin/python -m planner environment restore \
  --snapshot "$PANELS_RESTORE/snapshot" \
  --destination-db "$PANELS_RESTORE/data/planner.db" --live-stopped
```

Start a foreground proof in a subshell with every inherited `PLAN_*` variable cleared.
Set only the explicit scratch paths below. Keep dispatch disabled and test mode enabled.

```sh
(
  for panels_variable in $(env | sed -n 's/^\(PLAN_[A-Za-z0-9_]*\)=.*/\1/p'); do
    unset "$panels_variable"
  done
  env CODEX_HOME="$PANELS_RESTORE/providers/codex" \
  CLAUDE_CONFIG_DIR="$PANELS_RESTORE/providers/claude" \
  PLAN_HERMES_HOME="$PANELS_RESTORE/providers/hermes" \
  PLAN_CONFIG_PATH="$PANELS_RESTORE/config.yaml" \
  PLAN_WORKSPACE_ROOT="$PANELS_RESTORE/workspaces" \
  PLAN_DB_PATH="$PANELS_RESTORE/data/planner.db" \
  PLAN_LOGS_DIR="$PANELS_RESTORE/logs" \
  PLAN_BACKUP_DIR="$PANELS_RESTORE/data/backups" \
  PLAN_DISPATCHER_LOCK_PATH="$PANELS_RESTORE/dispatcher.lock" \
  PLAN_SERVER_LIFECYCLE_LEASE_PATH="$PANELS_RESTORE/lifecycle.lock" \
  PLAN_SERVER_CONTROL_SOCKET= \
  PLAN_APP_SHA="$(git rev-parse HEAD)" \
  PLAN_PROVISION_ACCOUNT_SKILLS=0 \
  PLAN_PORT=8768 PLAN_DISPATCH_ENABLED=0 PLAN_TEST_MODE=1 \
  .venv/bin/python -m planner serve
)
```

In another terminal, check port 8768 health and read one known record through that scratch server.
Use Ctrl-C in the proof terminal to stop it. Check that the scratch listener ended.
Missing artifact links on a fresh restore are expected. A deliberate home restore uses the same command only after its home job stops.
