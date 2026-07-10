# Frontend implementation dispatch

Start only after the backend contract is green and read its actual `ChatActivityEntry` field names from `web/src/lib/types.ts`/API state before editing.

## Scope

Own only:

- `web/src/lib/types.ts`
- `web/src/components/ChatPanel.svelte`
- the chat-activity rules in `assets/app.css`
- `tests/e2e/test_live_chat_state.py`
- `docs/chat.md`

Do not change backend files, the composer, transcript roles, navigation, or managed-file behavior.

## Strict TDD behavior

Add the focused Playwright test first and capture expected RED before production changes. Seed ordered activity entries through the real SQLite schema and assert the API returns them.

Prove:

1. A running ticket-worker turn and a running Chief turn both show the existing latest activity label and a native disclosure button, collapsed by default.
2. The button has a stable hook, accessible name, `aria-expanded`, and `aria-controls`; Enter/Space can open and close it.
3. Expansion renders ordered display-safe category/label/status details inline under the current row. No raw payload, argument, result, or output test string appears in the DOM.
4. A live activity update refreshes the expanded timeline without clearing the composer draft.
5. Collapse removes only the timeline; the dots/current label, pause action, input state, transcript, and draft remain unchanged.
6. Remount starts collapsed again and still reads current server state.
7. Timeline growth participates in the existing pre-growth follow decision: it follows only while already near the bottom. If the reader scrolled upward, growth preserves position and `Latest` visibility remains distance-based. Include the activity list as an explicit reactive dependency so a status-only row update cannot bypass this contract.
8. Reduced-motion behavior stays the current dots-only animation suppression; the chevron should use a static glyph/state rather than a new looping animation.

## Visual boundary

Keep the current bubble-less pending row. The row becomes a quiet full-width disclosure target only when activity entries exist; dots and latest label retain their exact role. The expanded list is indented, compact, and subordinate, with a thin guide and muted metadata. Use existing spacing/type/color/border/motion tokens. Do not add avatars, timestamps to transcript messages, cards, tabs, headers, or composer controls.

Reference: `/Users/khushaljagota/.hermes/planning-v2/data/files/tickets/t_zafkz8jy/artifacts/agent-activity-approach.png`.

Write RED/GREEN evidence to `orchestration/tickets/t_zafkz8jy-agent-activity/frontend-implementation-report.md`. Do not commit or merge.
