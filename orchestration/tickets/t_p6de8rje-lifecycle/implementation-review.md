# Independent implementation review

Reviewer: Codex CLI, `gpt-5.5`, read-only sandbox, high reasoning.

## First pass

- **High — `src/planner/core/db.py`, `tests/unit/test_db.py`:** awaiting-approval migration wrote both a settled Implementation value and a pending proposal. Correction: keep the candidate only in the pending proposal and assert `implementation.value is None` in both current-schema and old project-column migration paths.
- **Medium — `frontend-report.md`:** the interrupted initial browser run did not provide decisive RED evidence. Correction: reproduce the new board assertion against the pre-frontend baseline or record an owner waiver.
- **Low — `assets/app.css`:** stale `.approval-result` selectors remained while the report claimed a clean remnant sweep. Correction: remove them and rerun the sweep.

## Disposition

All findings accepted.

- Awaiting-approval migration now produces `value = None` plus a pending proposal. Value-only legacy rows synthesize a proposal; existing proposal metadata is preserved; unusable rows fail loudly. Eight focused migration tests pass.
- RED was reproduced in a clean detached `0cbd2b5` worktree with the current backend and new board assertion but old frontend: Playwright timed out waiting for `[data-stage-field="implementation"]`. The worktree was removed.
- The stale CSS selectors were removed. Remnant sweeps over `web/src` and `assets` return zero matches, and the focused board suite passes.

## Follow-up 1

The first follow-up found three additional items:

- **High — legacy runtime-control ceiling:** accepted. Every non-empty legacy `needs_review` status now caps the migrated ceiling at `needs_implementation`; the active-revision migration expectation proves it.
- **High — stale Chief CLI e2e option:** accepted. The real-server test now supplies both `--implementation-file` and `--closeout-file`; its two tests pass.
- **Medium — generated dist bundle shown as untracked:** refuted as a code violation. This task does not stage or commit files. The production build generated `web/dist/assets/index-pgQkG56R.js`, `web/dist/index.html` references that exact existing bundle, and final verification will exercise the coherent pair. The file is part of the working-tree deliverable despite Git describing a newly generated filename as untracked.

A second follow-up rechecked the full implementation and returned `NO VIOLATIONS`.
