# Release prep scripts

These are the scripts of the 26 Nov rehearsal of release e40325811 on a copy of live. They are copied unchanged, so they still name that rehearsal's paths, port and build run. The release prep Worker type (`panels-worker-release-prep`) reuses them. Its first Ticket adapts them for general use, and later Tickets fix them here.

The method: copy live read-only (`copy_and_sanitize.py`), admit the hosted build (`unpack_and_admit.ts`), launch the copy on a private port (`launch_copy.sh`), time the schema steps and snapshot flow, check readiness and owner sends, load the largest conversations in a browser (`browser_check.mjs`), compare live before and after (`live_snapshot.py`, `compare_live.py`), stop by exact PID (`stop_copy.py`), and write the report (`make_report.py`).
