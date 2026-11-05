#!/usr/bin/env sh
set -eu

: "${PANELS_CURRENT_ROOT:?set PANELS_CURRENT_ROOT}"
: "${PANELS_BACKUP_SOURCE_DB:?set PANELS_BACKUP_SOURCE_DB}"
: "${PANELS_BACKUP_DIRECTORY:?set PANELS_BACKUP_DIRECTORY}"

current_app=$PANELS_CURRENT_ROOT/app
export PLAN_HERMES_HOME="$HOME/.hermes"

exec "$current_app/bin/panels-launcher" environment backup-current \
  --source-db "$PANELS_BACKUP_SOURCE_DB" \
  --backup-dir "$PANELS_BACKUP_DIRECTORY" \
  --current-app "$current_app"
