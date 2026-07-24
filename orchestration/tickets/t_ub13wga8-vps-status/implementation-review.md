# Implementation review — t_ub13wga8 lightweight VPS status and safe cleanup

## Findings

1. **HIGH — a symlinked snapshot is accepted as verified status/retention evidence.**

   `verified_snapshots()` accepts every `snapshot-*` directory for which
   `is_verified_snapshot()` succeeds, but the latter uses `is_dir()`, `is_file()`,
   `read_text()`, and `_sha256()` without rejecting symlinks
   ([backup.py:295](../../../src/planner/environments/backup.py#L295),
   [backup.py:313](../../../src/planner/environments/backup.py#L313)). Those calls
   follow a `snapshot-*` symlink and symlinked metadata/database children. As a
   result, an external, otherwise-valid snapshot is counted for backup recency and
   can enter the retention plan, even though it is outside the configured root. This
   breaks the contract's “never follows a symlink” and evidence-only invariants; the
   later cleanup check avoids deleting the symlink but does not repair the false
   healthy/recency evidence.

   **Correction:** make the shared verification predicate reject a symlink at the
   snapshot directory and every proof-bearing entry (metadata, database, and managed
   tree/manifest), and make enumeration require a direct child of the non-symlink
   configured root before reading it. Add regressions with a valid external snapshot
   linked into `backup_dir`, plus linked metadata/database entries, asserting they
   are absent from status recency/count, the seven-snapshot retention plan, cleanup
   inventory, and deletion. The existing tests cover a configured *logs* root
   replaced by a symlink, but not snapshot-proof symlinks.

2. **HIGH — the new systemd maintenance job does not receive the paths that cleanup
   is supposed to maintain.**

   `panels-maintenance.service` loads `backup.env` and invokes cleanup with no
   `PLAN_CONFIG_PATH`, `PLAN_BACKUP_DIR`, or `PLAN_LOGS_DIR`
   ([panels-maintenance.service:8](../../../ops/panels-environments/panels-maintenance.service#L8)).
   That environment file provides `PANELS_BACKUP_DIRECTORY`, which `load_config()`
   does not read ([backup.env.example:3](../../../ops/panels-environments/backup.env.example#L3));
   it reads `PLAN_BACKUP_DIR` instead. With systemd's normal working directory this
   falls back to relative `data/backups`/`data/logs`, so the scheduled `--apply` can
   inspect an unrelated location rather than the configured backup and live logs.
   This also means the new backup-dir release/config propagation is not effective
   for the repository-owned Linux schedule.

   **Correction:** give maintenance an explicit, operator-owned status/maintenance
   environment that supplies an absolute config path or all three absolute
   `PLAN_DB_PATH`, `PLAN_LOGS_DIR`, and `PLAN_BACKUP_DIR` values. Bridge the existing
   `PANELS_BACKUP_DIRECTORY` only through an explicit shell/wrapper or replace it
   with the `PLAN_*` names; do not rely on a relative working directory. Ensure the
   live launch input likewise supplies the selected backup path for the server
   snapshot. Add a focused operator-input/config propagation test that starts from
   the unit's declared environment and proves the collector/cleanup receives the
   intended absolute backup and log roots, never `data/backups` by fallback.

3. **MEDIUM — a normal process-probe failure turns the read-only status endpoint
   into a 500 instead of an explicit unavailable section.**

   `_default_process_lines()` uses `subprocess.run(..., check=True, timeout=1)`,
   which raises `CalledProcessError` or `TimeoutExpired`, but
   `_workloads_section()` catches only `OSError`
   ([vps_status.py:392](../../../src/planner/environments/vps_status.py#L392)). The
   exception escapes `collect_vps_status()` and therefore `GET /api/vps-status`,
   contrary to the dispatched requirement that probe failures preserve an explicit
   unavailable/review-needed fact rather than fabricate or omit it.

   **Correction:** catch `subprocess.SubprocessError` as well as `OSError` at this
   adapter boundary and return the existing unavailable workloads section. Add
   separate timeout and non-zero-`ps` tests that exercise the actual collector and
   API response, asserting a sanitized 200 snapshot with `workloads.state ==
   "unavailable"`. Current coverage only tests an unreadable log directory.

4. **MEDIUM — production workload evidence cannot recognise the documented live
   service command.**

   The live launcher executes the venv Python as `python -m planner serve`, while
   the collector asks `ps` for `comm` and accepts only a command whose first token is
   literally `planner` or `panels` ([vps_status.py:392](../../../src/planner/environments/vps_status.py#L392)).
   `comm` is the executable name, so the launcher-owned service is reported as
   Python and discarded. The response then says “recognised Panels workloads” is
   healthy with an empty list, which is not honest evidence about the actual running
   Panels server.

   **Correction:** use one bounded, fixed-argv probe that can recognise the precise
   launcher command form (and any supported console-script form), discard the raw
   arguments immediately, and serialize only the bounded role/pid/state/age fields.
   Add a regression using the real `python -m planner serve` shape and one unrelated
   Python process, proving the former appears as a sanitized Panels workload and the
   latter does not. Keep the long-argument secret fixture to prove no inspected raw
   argument reaches the snapshot.

## Evidence reviewed

- Read the complete working-tree implementation, tests, frontend source/build output,
  live docs, and operator inputs against `staging` (the ticket `HEAD` equals
  `staging`; all implementation changes are uncommitted).
- Confirmed there is no cleanup HTTP route, cleanup UI action, status route, or
  popover polling in the reviewed source.
- A direct temporary-fixture check showed `verified_snapshots()` returns a
  `snapshot-*` symlink to an external valid snapshot.
- Focused reviewer run: `.venv/bin/python -m pytest -q tests/unit/test_vps_status.py
  tests/unit/test_database_backups.py tests/unit/test_config.py tests/unit/test_release.py`
  passed (with the existing FastAPI/Starlette deprecation warning). This does not
  cover the findings above.

## Resolution review

**PASS.** Re-reviewed only the four findings above against the corrected tree.

- Snapshot enumeration and proof now reject symlinked snapshot directories,
  metadata, databases, managed-tree roots, and manifests. The new backup and status
  regressions cover external snapshot links and all of those proof entries; none can
  affect recency, retention, inventory, or deletion.
- The Linux maintenance unit now reads the dedicated, owned
  `maintenance.env` with absolute `PLAN_DB_PATH`, `PLAN_LOGS_DIR`, and
  `PLAN_BACKUP_DIR`; the live input carries the same backup root. The regression
  proves the collected inventory uses those absolute roots rather than fallback
  `data/backups`, and the install assets create/protect the new environment file.
- The workloads collector now converts `OSError` and subprocess failures to an
  unavailable section. The API regression covers both `TimeoutExpired` and
  `CalledProcessError` and receives a sanitized 200 response.
- The fixed, bounded `ps` command now reads the command form needed to recognize
  the release launcher’s `python -m planner serve` process, discards arguments, and
  returns only sanitized workload facts. The regression excludes an unrelated Python
  process and proves a token in the recognized command is not serialized.

Verification run fresh for this resolution review:

```text
.venv/bin/python -m pytest -q tests/unit/test_database_backups.py tests/unit/test_vps_status.py tests/unit/test_config.py tests/unit/test_release.py
63 passed (existing FastAPI/Starlette deprecation warning)

.venv/bin/python -m ruff check src/planner/environments/backup.py src/planner/environments/vps_status.py tests/unit/test_database_backups.py tests/unit/test_vps_status.py tests/unit/test_config.py
All checks passed!
```
