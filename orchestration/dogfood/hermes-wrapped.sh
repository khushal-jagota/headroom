#!/bin/sh
# Dogfood Level C shim (§18.5 "prompt shape" attempt latitude, disclosed in DOGFOOD.md).
# The real spawn adapter invokes: hermes -p <profile> --skills <skill> chat -q "<message>".
# The planning-worker skill is NOT installed in ~/.hermes, so --skills would not resolve.
# This wrapper drops the --skills flag and prepends a read-this-file instruction to the
# message, then execs the real hermes. Everything else passes through untouched.
# (Recreated: the Level B session deleted the first copy with rm -rf orchestration/dogfood.)

REAL_HERMES="/Users/khushaljagota/.local/bin/hermes"
SKILL_FILE="/Users/khushaljagota/.hermes/planning-v2/skills/planning-worker.md"

args=""
msg=""
expect_msg=0
skip_next=0
for a in "$@"; do
  if [ "$skip_next" = 1 ]; then skip_next=0; continue; fi
  if [ "$expect_msg" = 1 ]; then msg="$a"; expect_msg=0; continue; fi
  case "$a" in
    --skills) skip_next=1 ;;
    -q) expect_msg=1 ;;
    *) args="$args \"$a\"" ;;
  esac
done

enriched="Read $SKILL_FILE, then $msg. Act only through the plan CLI; do not run ./verify, do not modify or delete any repository files."
eval "exec \"$REAL_HERMES\" $args -q \"\$enriched\""
