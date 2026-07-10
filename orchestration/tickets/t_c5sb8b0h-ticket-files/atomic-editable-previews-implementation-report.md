# Atomic Editable Previews Implementation Report

## Summary

Implemented the atomic editable preview contract in the current worktree without committing and without running `./verify`.

- Markdown fields are again one continuous `contenteditable` surface.
- Source/edit/save/cancel controls were removed from production Markdown editing paths.
- Renderer-authored anchors now carry their supported Markdown source token.
- Editable hydration replaces rendered anchors with `contenteditable="false"` atomic preview slots.
- Serialization emits each atomic slot's stored source token and ignores generated preview descendants.
- Markdown edit dirtiness is driven by real `input` events, so async preview loading does not trigger a save.
- Read-only and editable preview mounting now share `web/src/lib/filePreviewMount.ts`.

## RED/GREEN Log

1. RED: continuous editable browser assertion

Command:

```sh
.venv/bin/pytest tests/e2e/test_ticket_file_previews.py::test_editable_markdown_file_links_round_trip_as_raw_markdown -q
```

Result: blocked before test execution by local Playwright/Chromium launch failure:

```text
BrowserType.launch: Target page, context or browser has been closed
FATAL: ... bootstrap_check_in org.chromium.Chromium.MachPortRendezvousServer... Permission denied (1100)
```

2. RED: renderer source token + serializer atomic slot tests

Command:

```sh
node web/tests/markdown-renderer.test.mjs && node web/tests/markdown-edit.test.mjs
```

Result: failed as expected because rendered anchors did not carry `data-markdown-source-token`.

3. GREEN: renderer token + serializer atomic slot

Command:

```sh
node web/tests/markdown-renderer.test.mjs && node web/tests/markdown-edit.test.mjs
```

Result: passed after adding renderer source tokens, protecting generated anchors from later emphasis parsing, and teaching the serializer to emit atomic-slot tokens.

4. GREEN: Svelte type check after restoring continuous `InlineEdit`

Command:

```sh
node web/tests/markdown-renderer.test.mjs && node web/tests/markdown-edit.test.mjs && npm --prefix web run check
```

Result: passed. `svelte-check` reported 0 errors and the existing 3 `TicketRoute.svelte` warnings about capturing the initial `id`.

5. RED/GREEN edge serializer slice

Command:

```sh
node web/tests/markdown-renderer.test.mjs && node web/tests/markdown-edit.test.mjs
```

Result: passed after adding adjacent atomic slot and selected-deletion serializer coverage.

6. Focused checks

Commands:

```sh
node web/tests/markdown-renderer.test.mjs && node web/tests/markdown-edit.test.mjs && node web/tests/file-preview.test.mjs
npm --prefix web run check
.venv/bin/python -m py_compile tests/e2e/test_ticket_file_previews.py tests/e2e/test_flows_a.py tests/e2e/test_flows_b.py
git diff --check
```

Results:

- Node tests passed.
- `svelte-check` passed with 0 errors and the existing 3 `TicketRoute.svelte` warnings.
- Python e2e files compiled.
- `git diff --check` passed.

7. Affected e2e module attempt

Command:

```sh
.venv/bin/pytest tests/e2e/test_ticket_file_previews.py tests/e2e/test_flows_a.py tests/e2e/test_flows_b.py -q
```

Result: blocked before test execution. All reported errors share the same Playwright/Chromium launch failure:

```text
BrowserType.launch: Target page, context or browser has been closed
FATAL: ... bootstrap_check_in org.chromium.Chromium.MachPortRendezvousServer... Permission denied (1100)
```

Firefox fallback was attempted:

```sh
.venv/bin/pytest tests/e2e/test_ticket_file_previews.py::test_editable_markdown_file_links_round_trip_as_raw_markdown --browser firefox -q
```

Result: blocked because Firefox is not installed in the Playwright cache.

8. Frontend build

Command:

```sh
npm --prefix web run build
```

Result: passed. Vite emitted `web/dist` with the usual external `/assets/*.css` and non-module `/assets/markdown.js` warnings, plus the existing 3 `TicketRoute.svelte` warnings.

## E2E Status

The affected e2e modules did not pass in the implementation agent's sandbox because no Playwright browser could launch there. The failure happened during the session `browser` fixture before app code or test bodies executed. No `./verify` run was attempted by that agent.

## Integrator follow-up

The integrator ran the browser suite outside the agent sandbox and fixed the failures it exposed:

- Added editable caret guards around atomic blocks and normalized browser-inserted non-breaking spaces.
- Restored proposal Escape to the original proposal even after an earlier local blur/save.
- Proved direct active keyboard edit → Approve, no-edit loaded-preview approval without `edited_body`, exact entity-bearing token persistence, adjacent typing/paste, Delete/Backspace/selected deletion, focused preview actions, pending-fetch abort, and iframe cleanup.
- Removed the edit/source controls and the Day placeholder workaround, updated current docs/decision text, and rebuilt the production bundle.

The focused Node checks, Svelte check, production build, and 26 affected Playwright tests pass in the integrator environment. The final repository-wide `./verify` passed: Ruff, Mypy, 209 unit tests, frontend check/build, 46 E2E tests, and `VERIFY: PASS`.
