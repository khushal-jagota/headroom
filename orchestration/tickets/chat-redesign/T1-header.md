# T1 — Header: silent identity row + overflow

Implements DESIGN.md §1 (and the placeholder's sibling change stays in T5).
Mockup reference: `orchestration/chat-redesign/mockup.html` (header region; see
the Offline / Permission / Compacting scenarios).

## Files owned
- `web/src/components/acp/AcpConversationPane.svelte` (header region + removal
  of the ConversationStatus mount)
- `web/src/components/acp/ConversationStatus.svelte` (retire or reduce to the
  invisible screen-reader alert; delete if nothing remains)
- `assets/app.css` — only the `.chat-head` / header-related block
- Worker display name derivation: wherever `employeeLabel` is computed for
  ticket routes (e.g. `conversationEmployeeLabel`), the chat header must show
  the worker name only — no ticket number. Do not change what other screens
  show; derive/strip for the chat panel.

## Behavior
- Left: worker name (mono, xs, tracking-mono). A 7px red dot appears left of
  it only when the connection is in trouble (connection state `closed`/`error`
  or a recoverable connection error present). No dot otherwise.
- Mid: exception word only — `waiting for you` (accent-text) when a permission
  is pending; `compacting` when compaction in flight. Otherwise nothing.
- Right: usage `41.2k / 200k · $0.34` — compact k-format ≥1000, tabular-nums,
  cost only when the wire sent one, whole segment absent if no usage yet.
- Right of usage: `⋯` ghost button → small popover menu with one item,
  "New conversation"; choosing it shows an inline confirm (second click on
  "Confirm" style, or equivalent) before calling the controller's
  new-conversation action. Keyboard accessible, focus-visible.
- Preserve an invisible `role="alert"` for new protocol rejections /
  recoverable errors (screen-reader parity with today).

## Out of scope
Composer (T5), transcript (T2), task strip (T3), permission (T4). Do not touch
their files. No new dependencies, no new tokens; 4px-grid spacing only.

## Gates
`cd web && npm run check` (no new errors) and `npm run build` pass. Existing
node tests may fail on old DOM assertions — do NOT edit test files (T6 owns
them); list which tests broke in your report.
