# ACP-10 verification report

## Frozen snapshot attempt 1 — FAILED, retained

- Started: `2026-07-21T00:09:19Z`
- Finished: `2026-07-21T00:12:12Z`
- Exact command: `set -o pipefail; ./verify 2>&1 | tee data/verify/acp-10-final.log`
- Exit status: `1`
- Final marker: `VERIFY: FAIL`
- Full retained log: `data/verify/acp-10-final-attempt-1.log` (byte-identical copy made before the
  generic final-log path is reused for a later fresh snapshot)
- Log size: 958 lines, 43,424 bytes
- Log SHA-256: `daa64377bf6ba1c89be8f33b80b7c82ca49dfb602cce246fea228813a55441bf`
- Frozen input manifest: 1,154 entries, 135,424 bytes
- Manifest SHA-256 before and after the run:
  `b1daf130fdeeba793a09d57c82cfcbf21c4bb64183a3404db9ca7a696a149658`
  (byte-for-byte unchanged)

Gate results:

- Ruff: **FAILED** — one unsorted import block and three overlong SQL-string lines.
- Mypy: **PASS** — no issues in 129 source files.
- Unit: **FAILED** — 14 failed, 1,005 passed, 9 warnings.
- Build check: **PASS**.
- Frontend: **PASS** — Svelte diagnostics clean, production build completed, and all 13 scripted
  frontend test files passed.
- E2E: **PASS** — 103 passed, 1 warning.

The unit failures are retained in the full log. They are concentrated in old test fixtures/assertions
that did not carry the now-required `employee_backend` Ticket/Worker-manifest field, plus two
constraint-rebuild expectations using the old manifest shape. No failed gate is hidden or relabelled
as success. This attempt is not the ACP-10 completion claim and `CLOSE-08` remains unproved.

The tree is explicitly returning to audit for a bounded correction of only the four Ruff sites and
14 failing test fixtures/assertions. The failed log will remain at the path above. Focused checks and
a fresh no-writer snapshot must settle before any later full verification decision; `./verify` is not
being rerun immediately.

## Final corrected snapshot — PASS

After the contract-scoped correction, a fresh no-writer snapshot was recorded in
`freeze-report-attempt-2.md`, `freeze-git-status-attempt-2.txt`, and
`verified-input-manifest-attempt-2.sha256`. The correction's exact focused gate passed 76/76 tests
plus Ruff before this freeze. The earlier failed log remains retained unchanged.

- Started: `2026-07-21T00:18:51Z`
- Finished: `2026-07-21T00:21:43Z`
- Exact command: `set -o pipefail; ./verify 2>&1 | tee data/verify/acp-10-final.log`
- Exit status: `0`
- Final marker: `VERIFY: PASS`
- Full final log: `data/verify/acp-10-final.log`
- Log size: 157 lines, 9,051 bytes
- Log SHA-256: `d66e7bfd939d9622ac8673b6cba00937f1124c0b3a95d0d6a37bcda4513a8963`
- Final frozen input manifest: 1,162 entries, 136,601 bytes
- Manifest SHA-256 before and after the run:
  `d7265946efa9bac6a5cee8130ba020dd4827dba8b25a1963491e58f6cd67c611`
  (byte-for-byte unchanged)

Gate results:

- Ruff: **PASS**.
- Mypy: **PASS** — no issues in 129 source files.
- Unit: **PASS** — 1,019 passed, 9 warnings.
- Build check: **PASS**.
- Frontend: **PASS** — Svelte diagnostics found 0 errors and 0 warnings, the production build
  completed, and all 13 scripted frontend test files passed.
- E2E: **PASS** — 103 passed, 1 warning.

## Final verified-build Computer Use confirmation

The server was restarted from the unchanged verified tree, then Safari reloaded
`http://127.0.0.1:8767/#/ticket/t_1xbdpkq0`. The page identified itself as Panels, showed `Connected`,
and rendered the restrained dark Ticket editor/proposal layout beside the narrow Claude conversation
rail. The durable transcript replayed, including the content-free compaction boundary, and the Worker
was idle. No prompt or product mutation was needed. Final frame:

`/var/folders/m1/ghygg_r133nc05srgprf9j5c0000gn/T/com.openai.sky.CUAService/Safari Screenshot 2026-07-21 at 1.23.11 am.jpeg`

The temporary server PID `58655` then shut down cleanly. This report's final disposition is
**PASS**: the final corrected frozen inputs received one clean canonical run, the manifest stayed
exact, and the prior failed snapshot remains visible rather than overwritten or relabelled.
