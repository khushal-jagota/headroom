# Release prep scripts

These are the scripts of the 26 Nov rehearsal of release e40325811 on a copy of live. They are copied unchanged, so they still name that rehearsal's paths, port and build run. The release prep Worker type (`panels-worker-release-prep`) reuses them. Its first Ticket adapts them for general use, and later Tickets fix them here.

The method: copy live read-only (`copy_and_sanitize.py`), admit the hosted build (`unpack_and_admit.ts`), launch the copy on a private port (`launch_copy.sh`), time the schema steps and snapshot flow, check readiness and owner sends, load the largest conversations in a browser (`browser_check.mjs`), compare live before and after (`live_snapshot.py`, `compare_live.py`), stop by exact PID (`stop_copy.py`), and write the report (`make_report.py`).

## Settings and scripts added for the 27 Nov release

The scripts no longer hold a fixed commit. Set `CANDIDATE_SHA` (`readiness.py`, `snapshot_flow.py`), `LIVE_SHA` (`live_snapshot.py`) and optionally `UNIT_PREFIX` (`launch_copy.sh`) to full SHAs and a private worker unit prefix. The scripts still refuse the live port 8767.

`ios_probe.mjs` sends the requests of the installed iPhone build, of the next build and of a build below the floor to a private test server. `run_probe_server.mjs` starts `server/test_host.ts` on a fresh database from a checkout, runs a probe against it and stops that server by its PID. Run it once for the live commit and once for the candidate, then compare the two JSON files. Set `INSTALLED_BUILD` and `NEXT_BUILD` for the probe. The test host has no trusted ingress, so owner-only routes such as `/api/notifications/apns/live-activity` answer 400 `agent_forbidden`. That proves that the route exists, not that it works.

Still missing: `snapshot_flow.py` needs a `release.json` in the rehearsal root. Copy the `release` object from the live `update-attempt.json` (read only). `make_report.py` is specific to the 26 Nov rehearsal and was not reused.

## Added for the 28 Nov release (candidate f0182e368)

- `copy_and_sanitize.py` now copies the metrics sidecar read-only and removes the rows that ts_0019 added: Live Activity tokens, the web-push identity and its deliveries, credentials and their requests, and agent launch tokens. Check the list against the schema again when a release adds tables that hold secrets.
- `snapshot_flow.py` sets the attempt record aside after the late check. Without that, the copy reports an update in progress, and the full-page update pause covers every button in the browser pass.
- `prepare_attempt.py` writes a record without `deadline_at`. Live's update records carry one, and the page shows "Updating…" through a dropped connection only while the deadline is in the future. Use `restart_with_deadline.py` with `restart_watch.mjs` to see what an open page shows during the restart. A restart without a deadline shows "Can't reach Panels" at once.
- `browser_check.mjs` prints the cause of each failed target, watches the shell status card, and checks the `retry: 1000` frame on each stream. `browser_diag.mjs` diagnoses one pane. Playwright is not installed in the checkout: install the locked version from `tests/e2e/package-lock.json` under `TMPDIR` and set `PLAYWRIGHT_FROM`.
- `new_routes_probe.mjs` checks stream framing and the routes `POST /api/client-events` and `GET /api/connection-records` through `run_probe_server.mjs`. `sidecar_open.mjs` opens a copy of the sidecar with a checkout's `Store`.
- The hosted candidate package comes from a manual dispatch of `typescript-first-install.yml` on staging (`first-install-packages-ubuntu-24.04-<run>`). Owner sends to a copy with no machine are accepted and held, not refused.
