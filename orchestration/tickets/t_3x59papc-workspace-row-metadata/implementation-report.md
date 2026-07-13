# Implementation Report

## Scope

- Added focused Workspace row metadata coverage in `tests/e2e/test_board_stage_indicators.py` for registered type labels, active and terminal stage labels, exactly one byline `StageMark`, current-running, user-takeover markers, and the selected title/byline/left-metadata/right-mark geometry.
- Replaced the Workspace board row rendering in `web/src/routes/BoardRoute.svelte` with a domain-specific button row so the title is first line and the byline owns the type/stage text plus the existing `StageMark`.
- Scoped row layout CSS to Workspace board rows in `assets/app.css`.

## RED

The leaf sandbox could not launch Chromium, so the orchestrator reconstructed the baseline after implementation: it reverse-applied only the `BoardRoute.svelte` and `app.css` production diff, rebuilt the baseline frontend, ran the new test, then restored the production diff and rebuilt through a shell trap.

Command:

```bash
PYTHONPATH="$PWD/src" .venv/bin/pytest \
  'tests/e2e/test_board_stage_indicators.py::test_workspace_row_byline_shows_registered_type_active_stage_and_mark' -q
```

Decisive output:

```text
F                                                                        [100%]
>       assert page.text_content(f"{card} .board-workspace-row-byline").strip() == (
E       playwright._impl._errors.TimeoutError: Page.text_content: Timeout 30000ms exceeded.
E       - waiting for locator("[data-card] ... .board-workspace-row-byline")
RECONSTRUCTED_RED_EXIT=1
```

The baseline failed because the selected byline did not exist, which is the intended missing behavior.

## GREEN

After restoring the production diff and building the worktree frontend:

```bash
PYTHONPATH="$PWD/src" .venv/bin/pytest tests/e2e/test_board_stage_indicators.py -q
```

Decisive output:

```text
...                                                                      [100%]
```

All three focused Workspace tests pass, including the new worker-type/stage/byline slice and the existing marker plus grouping/filter regressions.

Frontend check:

```bash
cd web && npm run check
```

Decisive output:

```text
svelte-check found 0 errors and 3 warnings in 1 file
```

The warnings are pre-existing `TicketRoute.svelte` state-reference warnings, not from this change.

## Independent review

- The pre-implementation Codex review identified the manifest-label, terminal-label, byline-placement, marker-independence, and missing-state-test constraints; all were implemented.
- The first completed-diff review returned `NO VIOLATIONS`.
- A hardened geometry review then found three integration issues: the new Vite bundle was not yet tracked, the manifest label assertion could race, and one comment still claimed the board made no manifest fetch. The generated bundle is now staged, the test waits for the registered label, and the comment is corrected.
- The final staged-plus-unstaged Codex review confirmed all three fixes and returned `NO VIOLATIONS`.

## Canonical verification

Command:

```bash
PYTHONPATH="$PWD/src" ./verify
```

Result:

```text
All checks passed!
Success: no issues found in 119 source files
655 passed, 10 warnings in 11.24s
svelte-check found 0 errors and 3 warnings in 1 file
75 passed in 111.36s (0:01:51)
[verify] gate ruff: ok
[verify] gate mypy: ok
[verify] gate unit suite: ok
[verify] gate build check: ok
[verify] gate frontend: ok
[verify] gate e2e suite: ok
VERIFY: PASS
```

The three Svelte warnings and ten Python warnings are pre-existing; the canonical gate passed every required check.

## Notes

- Registered type labels are read through `manifestResource()` plus `lifecycleFor()`, with `labelize()` only as the loading/unknown fallback.
- Active row stage text uses `gating_field_label`.
- Terminal row stage text uses `state_label`; the `StageMark` still keeps the existing closeout field fallback for done/dropped rows.
- `StageMark` state and marker logic remains driven only by board card fields, not manifest loading.
