#!/usr/bin/env bash
# Run the matrix once per condition. Each condition applies its probes, builds the
# frontend when a probe touched it, measures, then puts the tree back.
set -u
cd "$(dirname "$0")/.."
for condition in "$@"; do
  python3 measure/probes/apply.py --revert >/dev/null
  probes="${condition//+/ }"
  if [ "$condition" != "baseline" ]; then
    python3 measure/probes/apply.py $probes >/dev/null
  fi
  case "$condition" in
    *C1*|*M1*) npm run build --prefix web >/dev/null 2>&1 ;;
  esac
  PANELS_CONDITION="$condition" .venv/bin/python -m pytest measure/test_matrix.py -s -q 2>&1 \
    | grep -E "condition:|cold open|press Full|press play|seek |warm reopen|repeated:|played to|now at|FAILED|Error"
done
python3 measure/probes/apply.py --revert >/dev/null
npm run build --prefix web >/dev/null 2>&1
echo "tree restored"
