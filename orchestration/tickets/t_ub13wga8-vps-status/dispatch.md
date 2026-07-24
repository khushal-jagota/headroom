# Dispatch — t_ub13wga8 lightweight VPS status and safe cleanup

## Current seams

- `src/planner/environments/backup.py` already owns verified-snapshot proof and
  retention. `RETENTION_COUNT` is currently 3; `create_database_backup()` calls
  `_retain_verified_snapshots()` only after atomic publication. Reuse that proof,
  change the retention policy to 7, and do not create a second directory scanner.
- `src/planner/core/config.py` owns runtime paths. The server already has
  `db_path`, `logs_dir`, and `release_sha`; `src/planner/core/server.py` owns
  `/api/*` composition. `PLAN_RELEASE_ROOT` is launcher-provided path context;
  `Config.release_sha` is the runtime release-identity fact.
- `src/planner/environments/cli.py` already owns host-local environment operations.
  The SSH path must use the environment-domain collector directly so it still works
  when the Panels HTTP server is down; the API composes the same collector and
  serialises the same immutable snapshot contract.
- The header status cluster is `web/src/App.svelte` plus `.shell-statuses` in
  `assets/app.css`. The resource catalogue is event-driven and eagerly fetches
  opened handles, so it is deliberately not the status-popover owner.
- Current environment kinds are only `live` and `staging`; current preview support
  is file preview, not a runtime environment (`test_preview_is_not_an_environment_kind`).

## File scope

Production:

- `src/planner/environments/vps_status.py` (new): typed, framework-free snapshot,
  evidence collectors, bounded render data, and the one ephemeral cleanup inventory.
- `src/planner/environments/backup.py`: expose/reuse verified-snapshot enumeration
  and retention planning/apply; set retention to seven.
- `src/planner/core/config.py`, `config.yaml`, and
  `src/planner/environments/release_launcher.py`: the minimal configured backup
  directory needed by the server snapshot/cleanup, including release-launch
  allowlisting; no credentials are read or returned.
- `src/planner/core/server.py`: read-only `GET /api/vps-status`, composed from the
  same config and status module as the host-local CLI.
- `src/planner/environments/cli.py`: `panels environment status --json` and
  dry-run-by-default `panels environment cleanup`; cleanup is a local filesystem
  operation admitted by OS permissions and has no HTTP mutation route.
- `web/src/lib/types.ts`, `web/src/components/VpsStatusPopover.svelte` (new),
  `web/src/App.svelte`, and `assets/app.css`: typed, local-only selected-header
  popover and its compact state treatment.

Coverage and live docs:

- `tests/unit/test_vps_status.py` (new), `tests/unit/test_database_backups.py`,
  `tests/unit/test_server_events.py`, `tests/unit/test_cli_entrypoints.py`, and
  `tests/unit/test_config.py`.
- `web/tests/vps-status.test.mjs` (new), added to `web/package.json`'s existing
  `test` script; `tests/e2e/test_vps_status.py` (new).
- `docs/environments.md`, `docs/backups.md`, `docs/cli.md`, and
  `ops/panels-environments/backup-restore.md`.

Do not alter database schema/migrations, event kinds, environment manifests,
deployment transaction semantics, process supervision, or file-preview code.

## TDD sequence

1. **Red: status contract and evidence.** Add unit tests first for one JSON-safe,
   immutable snapshot with exact top-level fields `collected_at`, `overall_state`,
   `environment`, `release`, `backup`, `disk`, `workloads`, `worktrees`, `logs`,
   `cleanup_candidates`, and `resources`. Section state uses one vocabulary:
   `healthy`, `warning`, `critical`, `unavailable`, or `review_needed`. `resources`
   contains nullable `cpu_percent`, `load_averages`, `ram`, and `swap`; on Darwin
   all values are null and the section is `unavailable`. Every unavailable or
   unprovable fact is explicit, never a guessed zero or omitted section.

   Add a pure `VpsStatusPolicy` beside the snapshot contracts. Disk is warning when
   free space is below 15% or 10 GiB and critical below 8% or 5 GiB. A verified
   backup is warning after 36 hours and critical after 72 hours. Configured logs are
   warning at 25 MiB; abandoned operation temporaries become eligible after 24 hours.
   These constants are one policy value used by collectors, cleanup, and fixtures.
   Inject filesystem, clock, process, and Git probes so the tests prove malformed
   manifests, unreadable roots, non-worktrees, missing release metadata, and probe
   failures remain unavailable/review-needed rather than becoming fabricated facts.
   On Darwin assert all four resource fields are null/unavailable; do not begin
   Linux collection or thresholds in this ticket.

2. **Red: sanitisation and bounded correlation.** Specify fixtures containing API
   keys, credential-file contents, and very long process arguments. Assert the
   serialised snapshot contains none of them. Workload records may expose only a
   recognised Panels role plus bounded identity/state/age evidence, never a command
   line or arbitrary child arguments. Parse `git worktree list --porcelain` only
   through a fixed-argv, bounded-output probe; display worktrees only when Git
   proves them. Resolve every reported file candidate under its configured root and
   retain an explicit unknown/review-needed result for ambiguity.

3. **Green: one environment-domain snapshot owner.** Implement
   `vps_status.py` with immutable contracts and pure classification helpers,
   separating OS/filesystem adapters from assessment/render data. Collect disk facts
   from the configured runtime path, release identity from `Config.release_sha`,
   backup recency only from the existing verified-snapshot
   predicate, configured-log facts from `logs_dir`, current-process-tree facts, and
   Git-proven worktrees. Add the minimally necessary configured backup directory;
   its checked-in default is the existing local `data/backups` convention and its
   `PLAN_*` override is carried by the release launcher. `PLAN_RELEASE_ROOT` remains
   only launcher-provided path context; it is not identity proof unless explicitly
   passed through the existing release-manifest validator. The collector must never
   open a credential file or serialise ambient environment values.

4. **Red then green: retention and proof-bound cleanup.** First change the current
   backup tests from three to seven and add cases proving the newest seven *verified*
   snapshots survive, invalid/ambiguous `snapshot-*` directories are neither counted
   nor removed, and a retention failure preserves recovery points. Promote the
   existing verified-snapshot enumeration and retention plan in `backup.py` to the
   public source used by status; `create_database_backup()` and cleanup must use the
   same plan/apply helper.

   Build one in-memory `CleanupInventory` per invocation. Dry-run serialises its
   fresh inventory without authorising later work. `--apply` builds its own fresh
   inventory once and re-proves each selected target
   immediately before mutation (regular/non-symlink target, resolved containment,
   recognised operation prefix, expiry, and no live reference). It may only:

   - rotate regular configured log files under `logs_dir` through the inventory;
   - prune the verified backup paths selected by the seven-snapshot retention plan;
   - remove expired `.backup-*` directories under the configured backup directory,
     the only abandoned temporary root the current backup writer creates.

   A candidate that loses any proof is retained and reported review-needed. There is
   no stored preview or dry-run authorisation token. Add repository-owned daily
   launchd/systemd schedule inputs that execute a fresh apply invocation under the
   operator identity; they do not create an in-process scheduler.

5. **Red then green: server and SSH CLI share one read shape.** Add API and CLI
   tests that compare decoded `panels environment status --json` output with
   `GET /api/vps-status` from the same injected collector fixture. Both invoke the
   same environment-domain collector and serialiser; the CLI does not depend on HTTP.
   Test cleanup's per-request inventory, failed immediate re-proof no-op, and all
   excluded paths. Keep cleanup host-local (`panels environment cleanup` reports a
   fresh dry run; only `--apply` mutates), add no cleanup API, and assert frontend
   source contains no cleanup request.

6. **Red then green: selected header popover.** Add the shared TypeScript snapshot
   type once in `web/src/lib/types.ts`. `VpsStatusPopover` owns only component-local
   response/loading/error/open state: opening it and its Refresh control call
   `/api/vps-status`; there is no `onMount` read, timer, event invalidation, resource
   catalogue entry, or persisted preview model. Mount it in the existing
   `.shell-statuses` header cluster, not as a route. Render the concise evidence
   states healthy, warning, unavailable, and review-needed with accessible button /
   popover semantics; preserve detailed paths/candidate evidence for the CLI.
   Frontend source tests forbid polling. Playwright route-fulfils literal sanitized
   `/api/vps-status` payloads for healthy, warning, unavailable, and review-needed
   rendering and manual refresh, while one server unit test proves the real endpoint
   returns the same contract. Also prove no Status route or preview environment exists.

7. **Docs and focused gates.** Update the environment/CLI docs with status and
   dry-run/apply usage, required filesystem authority, manual-refresh behaviour, and
   the explicit Linux follow-up boundary. Update backup docs/runbook to seven verified
   snapshots and existing nightly/pre-deploy schedules; do not claim status changes
   those schedules. Run only after the settled change:

   ```sh
   .venv/bin/pytest tests/unit/test_vps_status.py tests/unit/test_database_backups.py tests/unit/test_server_events.py tests/unit/test_environment_cli.py tests/unit/test_config.py tests/unit/test_release.py
   npm --prefix web run check
   node web/tests/vps-status.test.mjs
   .venv/bin/pytest tests/e2e/test_vps_status.py
   ```

   The ticket's repository-wide completion gate is one final `./verify` on the
   settled branch, not a substitute for the focused gates.

## Safety invariants

- One sanitized snapshot contract serves API, CLI, and popover; no parallel shape
  or browser canonical store exists.
- Verified backup proof remains checksum/metadata/managed-file proof from
  `backup.py`; only verified paths can enter retention, recency, or deletion.
- Cleanup is dry-run first and inventory-bound. It never kills a process, removes a
  worktree/cache/prepared environment/selected release, follows a symlink, deletes
  ambiguous state, or acts outside a configured root.
- Mac CPU/load/RAM/swap remains honestly unavailable. Linux resource collection and
  warning thresholds are reserved for `t_qrdamx8z`.
- No credential value, environment dump, raw process argument, or unbounded command
  output reaches logs, API, CLI, or browser.
- The popover is manually fetched and ephemeral; it has no polling, event kind,
  database table, monitoring history, alerting, or persistent preview environment.
