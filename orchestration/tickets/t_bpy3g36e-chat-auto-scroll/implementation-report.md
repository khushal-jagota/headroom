# Implementation report

## Changed

- `ChatPanel` now records whether the reader is following from their distance to the bottom.
- Initial load and rendered chat growth pin to the bottom only while following.
- A `Latest ↓` button appears whenever the bottom is more than the near-bottom threshold away, independent of whether content is new.
- Clicking the button or manually returning near the bottom restores follow mode.
- Shared chat layout styles and `docs/chat.md` describe the behavior.

## TDD and focused proof

- RED: `.venv/bin/python -m pytest tests/e2e/test_flows_a.py::test_e26_chat_panel_echo_and_offline -q` failed waiting for the absent `[data-chat-jump]` control after a distance-only scroll.
- GREEN: the same command passed after implementation.
- `npm --prefix web run check` passed with 0 errors and the three existing `TicketRoute.svelte` warnings.
- `npm --prefix web run build` passed and refreshed the shared generated bundle.

The browser test covers initial positioning, distance-only control visibility, click resume, following through send/live output/settlement, preserving scroll-back through those same stages, and manual-return resume.

## Full verification

`./verify` passed: Ruff and Mypy passed, 211 unit tests passed, frontend check/build passed, 48 E2E tests passed, and the run ended with `VERIFY: PASS`.
