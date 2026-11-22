#!/usr/bin/env bash
# PROBE ONLY. Run the screens probe: sequence 1 with the default cache headers, then
# sequence 2 with no-store (run 1 and the cold-start run 2). Args: <app command...>
# Env: UD (data folder), SERVER (payload server).
set -uo pipefail
cfg() { printf '{"server":"%s","run":%s,"noStore":%s}\n' "$SERVER" "$1" "$2" > "$UD/screens-probe.json"; }
step() { echo "== run $1 noStore=$2"; cfg "$1" "$2"; "${@:3}"; echo "exit $?"; }
rm -rf "$UD/screens"; step 1 false "$@"
rm -rf "$UD/screens"; step 1 true "$@"; step 2 true "$@"
rm -f "$UD/screens-probe.json"
