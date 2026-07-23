#!/usr/bin/env sh
set -eu

: "${PANELS_ENVIRONMENT_MANAGER_PYTHON:?set PANELS_ENVIRONMENT_MANAGER_PYTHON}"
: "${PANELS_BACKUP_SOURCE_DB:?set PANELS_BACKUP_SOURCE_DB}"
: "${PANELS_BACKUP_DIRECTORY:?set PANELS_BACKUP_DIRECTORY}"
: "${PANELS_CURRENT_RELEASE:?set PANELS_CURRENT_RELEASE}"

revision=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]+"/manifest.json"))["release_sha"])' "$PANELS_CURRENT_RELEASE")

exec "$PANELS_ENVIRONMENT_MANAGER_PYTHON" -m planner environment backup \
  --source-db "$PANELS_BACKUP_SOURCE_DB" \
  --backup-dir "$PANELS_BACKUP_DIRECTORY" \
  --deployed-revision "$revision"
