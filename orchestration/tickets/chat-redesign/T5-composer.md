# T5 — Composer: queue tray, delivery segment, send⇄stop

Implements DESIGN.md §7. Mockup: `orchestration/chat-redesign/mockup.html`
(Working / Permission scenarios for tray + segment + stop; Idle for armed send).

Wave 2: runs after T1 — the old lifecycle row's "New conversation" now lives in
the header (T1); this ticket removes the entire lifecycle row from the
composer.

## Files owned
- `web/src/components/acp/AcpComposer.svelte`
- `web/src/components/acp/ConversationComposer.svelte`
- `assets/app.css` — composer-related `.chat-*` blocks only

## Behavior
- **Queue tray** replaces the current `.acp-queue` list: fused to the top of
  the sending box — same sunken surface, horizontally inset (≈space-2 margin),
  top corners `--radius-lg`, no bottom border; the sending box keeps its full
  native rounding in all states. Rows: mono index, one-line prompt summary
  (existing promptLabel logic), `×` cancel per row (existing cancel-queued
  path). Hairline separator between rows. `aria-label="Queued prompts"`.
- **Delivery segment**: the current three-button `.acp-delivery` row is
  replaced by a compact segmented pill control in the composer footer, left of
  send, visible only while a turn is active: `queue` (default) · `send now` ·
  `steer`. Steer disabled (not hidden) with the existing unavailable title
  when `supportsSteer` is false; drop the separate help line. Selected segment:
  overlay bg + strong text per mockup. Idle → control absent, sends `normal`.
- **Send ⇄ Stop**: one control in the send position. Idle: `↑`, accent-filled
  when text/images present, disabled when empty. Turn active: red-outline `■`
  (title "Stop the turn") calling the existing cancel path. Keyboard behavior
  (Enter/Shift+Enter/Escape), slash menu, image attach/preview/drop: unchanged.
- **Receipt line**: only failures — composer errors and rejected deliveries
  render as the existing mono line (error color); receipts for
  accepted/queued/started render nothing. Remove the old always-on receipt.
- **Lifecycle row**: delete (`Stop` moved into send; New conversation is in
  the header).
- Placeholder: `Message {employeeLabel}...` where the label passed in is now
  the worker-only name (T1 changed the derivation; just consume it).

## Out of scope
Header (T1), transcript (T2), task strip (T3), permission (T4). No new
deps/tokens; 4px grid. Do not edit test files.

## Gates
`cd web && npm run check` (no new errors) and `npm run build` pass. Run
`npm test` once, record newly broken test files, do not fix.
