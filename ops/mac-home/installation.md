# A source home on an Apple Silicon Mac

This path runs one Panels home in your graphical login session. The Mac holds its record.
Logout stops the home. Sleep makes it unreachable. Tailscale supplies remote HTTPS access.
This slice has no release install, update, previews, metrics, maintenance schedule, or watchdog schedule.

## Prepare the source

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

The source home currently projects managed skills under `$PANELS_ROOT/workspaces/.agents/skills`.
Local Ticket Workers start under `$PANELS_ROOT/data/files/tickets/` and do not receive that projection.
A Worker can read another instance's account skills, or find no Panels skills at all.
The install is not yet a usable Worker home until its own skill authority reaches the actual Worker context.
Source startup does not provision account-wide skill links in the shared provider homes.
For a coexistence check, use the canonical provider home and compare the live executor's setup and skills before and after.
Do not copy `auth.json` or change login, provider configuration, or global skill links for that check.

For a disposable test, add `--isolated-providers` to the setup command.
This explicit test option creates empty provider folders under the state root. It does not create a login.
For one real Codex Worker in that test, set only its home plist's `CODEX_HOME` to the existing canonical provider directory.
Use that directory directly. Do not copy or symlink credentials, perform a second login, or provision account-wide skills.

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
  PLAN_PORT=8768 PLAN_DISPATCH_ENABLED=0 PLAN_TEST_MODE=1 \
  .venv/bin/python -m planner serve
)
```

In another terminal, check port 8768 health and read one known record through that scratch server.
Use Ctrl-C in the proof terminal to stop it. Check that the scratch listener ended.
Missing artifact links on a fresh restore are expected. A deliberate home restore uses the same command only after its home job stops.
