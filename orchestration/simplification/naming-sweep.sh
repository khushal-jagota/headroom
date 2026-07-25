#!/usr/bin/env bash
# Naming sweep for the orchestration rework.
#
# Run and read, not a committed forever-test. It greps the live tree for the vocabulary
# the rework retired and prints whatever is left, so each remaining hit can be checked
# by eye against the frozen list below.
#
# Two blocks. The first must come back clean: those names describe machinery that no
# longer exists anywhere. The second prints every hit, because a large frozen surface
# legitimately keeps these words:
#
#   - SQL and column names (employee_session_id, employee_backend, employee_launch_*,
#     employee_conversations, employee_configuration_catalog_cache, and the
#     employee_step_runs table itself) — renaming or dropping a column or table needs a
#     migration and this package ships none;
#   - the HTTP wire fields bound to those columns, the /api/employee-configuration*
#     routes, and the Python/TS identifiers that ARE those wire names;
#   - managed settings JSON keys (renaming them breaks settings.json already on disk);
#   - the ACP conversation layer (src/planner/conversation/**, web/src/lib/acp/**,
#     web/src/components/acp/**, AcpConversation.svelte) and the types it defines,
#     which die at the swap;
#   - the EventKind value "employee_session_changed";
#   - the CLI flag --employee-backend and `ticket set employee-backend`, which post the
#     frozen wire field;
#   - e2e selectors and the CSS hooks they match (data-employee-configuration-*).
#
# Two ordinary-English spots are excluded by name rather than reworded, because they
# belong to unrelated systems: "no eligible cleanup" in the VPS status summary, and
# "catalog discovery" in the ACP backend-probing tests (a different sense of the word,
# not the retired loop).
#
# Skips: .venv, node_modules, web/dist, orchestration/, src/planner/skills/ (parked),
# alembic versions/, tests/fixtures/ (frozen schema snapshot).
#
# Usage: orchestration/simplification/naming-sweep.sh   (from anywhere)

set -uo pipefail
cd "$(dirname "$0")/../.."

TARGETS=(src web/src assets docs tests config.yaml AGENTS.md)
EXCLUDE_DIRS=(--exclude-dir=.venv --exclude-dir=node_modules --exclude-dir=dist
  --exclude-dir=__pycache__ --exclude-dir=skills --exclude-dir=versions
  --exclude-dir=fixtures --exclude-dir=.git --exclude-dir=planner.egg-info)

status=0

# sweep <label> <grep-flags> <pattern> [named-allowlist-path...]
sweep() {
  local label="$1" flags="$2" pattern="$3"
  shift 3
  local hits
  hits="$(grep -rn $flags "${EXCLUDE_DIRS[@]}" -- "$pattern" "${TARGETS[@]}" 2>/dev/null)"
  local allowed
  for allowed in "$@"; do
    hits="$(printf '%s\n' "$hits" | grep -v "^${allowed}:" || true)"
  done
  hits="$(printf '%s\n' "$hits" | grep -c . >/dev/null && printf '%s\n' "$hits" | grep . || true)"
  if [ -z "$hits" ]; then
    printf '%-24s clean\n' "$label"
  else
    printf '%-24s %s hit(s)\n' "$label" "$(printf '%s\n' "$hits" | grep -c .)"
    printf '%s\n' "$hits" | sed 's/^/    /'
    status=1
  fi
}

echo "== retired names: must be clean =="
sweep "Automatic Employee" -F "Automatic Employee"
sweep "AutomaticEmployee" -F "AutomaticEmployee"
sweep "EmployeeStepRunner" -F "EmployeeStepRunner"
sweep "discovery loop" -Fi "discovery loop"
sweep "resolution engine" -Fi "resolution engine"
sweep "eligib" -Ei "eligib" src/planner/environments/vps_status.py

# Paths whose "employee" vocabulary is frozen wholesale. Anything hit outside these is
# printed in full so it can be read line by line.
FROZEN_PATHS='^(src/planner/conversation/|web/src/lib/acp/|web/src/components/acp/|web/src/components/AcpConversation\.svelte|tests/unit/test_acp_|tests/unit/test_conversation_|tests/unit/test_claude_|tests/unit/test_codex_|tests/unit/test_hermes_|tests/unit/test_employee_configuration_catalog|tests/unit/test_role_skill_kickoff|tests/unit/test_in_place_compaction_strategy|tests/support/acp_|tests/e2e/test_acp_conversation\.py)'

echo
echo "== frozen surface: classify each against the list in this script's header =="
hits="$(grep -rn -Ei "${EXCLUDE_DIRS[@]}" -- "employee" "${TARGETS[@]}" 2>/dev/null |
  grep -v ":.*catalog discovery")"

echo "-- the ACP conversation layer and its tests (frozen wholesale, dies at the swap) --"
printf '%s\n' "$hits" | grep -E "$FROZEN_PATHS" | cut -d: -f1 | sort | uniq -c |
  sort -rn | sed 's/^/    /'

echo
echo "-- everywhere else, line by line --"
printf '%s\n' "$hits" | grep -Ev "$FROZEN_PATHS" | sed 's/^/    /'
printf '\n    %s line(s) outside the ACP layer\n' \
  "$(printf '%s\n' "$hits" | grep -Ev "$FROZEN_PATHS" | grep -c .)"

exit $status
