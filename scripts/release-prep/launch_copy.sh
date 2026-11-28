#!/bin/bash
# Start the sealed candidate as a private foreground home on the copy.
# setsid --fork: own session, survives the tool call. exec: the launcher IS the recorded PID,
# and the launcher execve's into foreground_main, so that PID is the supervisor we signal.
# Usage: [UNIT_PREFIX=panels-rehearsal-<tag>-] launch_copy.sh REHEARSAL_ROOT APP PORT
set -eu
R=$1 APP=$2 PORT=$3
mkdir -p "$R/home/.codex" "$R/home/.claude" "$R/home/.gemini" "$R/scratch" "$R/workspace" "$R/s" "$R/secrets" "$R/data/logs" "$R/tmp"
chmod 700 "$R/s" "$R/secrets"
[ -f "$R/secrets/update-token" ] || printf 'inert-rehearsal-token\n' > "$R/secrets/update-token"
chmod 600 "$R/secrets/update-token"
cat > "$R/config.yaml" <<YAML
port: $PORT
tick_seconds: 60
dispatch_enabled: false
notifications_enabled: false
boundary_hour: 5
YAML
setsid --fork /usr/bin/env -i \
  HOME="$R/home" USER=vps LOGNAME=vps SHELL=/bin/bash LANG=C.UTF-8 PATH=/usr/bin:/bin TMPDIR="$R/tmp" \
  CODEX_HOME="$R/home/.codex" CLAUDE_CONFIG_DIR="$R/home/.claude" GEMINI_HOME="$R/home/.gemini" \
  PLAN_CONFIG_PATH="$R/config.yaml" PLAN_DB_PATH="$R/data/planner.db" PLAN_PORT="$PORT" \
  PLAN_DISPATCH_ENABLED=false PLAN_NOTIFICATIONS_ENABLED=false \
  PLAN_LOGS_DIR="$R/data/logs" PLAN_BACKUP_DIR="$R/data/backups" \
  PLAN_DISPATCHER_LOCK_PATH="$R/data/dispatcher.lock" \
  PLAN_SERVER_CONTROL_SOCKET="$R/s/c.sock" PLAN_SERVER_LIFECYCLE_LEASE_PATH="$R/s/lease.lock" \
  PLAN_UPDATE_TOKEN_FILE="$R/secrets/update-token" \
  PLAN_WORKER_UNIT_PREFIX="${UNIT_PREFIX:-panels-rehearsal-}" \
  PLAN_WORKSPACE_ROOT="$R/workspace" PLAN_TICKET_SCRATCH_ROOT="$R/scratch" PLAN_TICKET_SCRATCH_DATABASE="$R/data/planner.db" \
  PLAN_CREDENTIAL_KEY_PATH="$R/data/credential-secrets/key" \
  /bin/bash -c 'echo $$ > "$0/launcher.pid"; date +%s.%N > "$0/launch.time"; exec "$1/bin/panels-launcher" serve >>"$0/serve.log" 2>&1 </dev/null' "$R" "$APP"
sleep 1
echo "launcher pid $(cat "$R/launcher.pid")"
