# T3 — Task pill strip (plan progress)

Implements DESIGN.md §6. Mockup: `orchestration/chat-redesign/mockup.html`
(Working / Permission / Compacting scenarios — pill, spinner rule, hover
popover; note the strip reserves space in every state).

Wave 2: runs after T1 (header) and T2 (transcript) have landed — read their
changed files as they now are; T2 removed the transcript's inline plan
rendering and left plan data in the snapshot.

## Files owned
- New component `web/src/components/acp/` (name it for what it is, e.g.
  TaskProgressStrip)
- `web/src/components/acp/AcpConversationPane.svelte` — only the insertion of
  the strip between thread shell and composer region
- `web/src/lib/acp/conversationState.ts` — only if plan state isn't already
  exposed on the snapshot in a consumable form
- `assets/app.css` — strip/pill styles if placed there rather than component-scoped

## Behavior
- In-flow strip, fixed height (34px), centred content, present in every state
  (space always reserved; nothing floats over the thread).
- Pill visible only while a turn is active (activity thinking / compacting /
  waiting_for_permission) AND a plan with ≥1 entry exists: `{done} / {total}
  tasks`, tabular numerals, mono xs, pill outline per mockup.
- Spinner beside the count only when activity is thinking or compacting — not
  while waiting_for_permission. Reduced-motion safe.
- Hover or keyboard focus reveals a popover above the pill listing entries in
  order: `✓` done (struck through, faint), `›` in_progress (strong), `○`
  pending. Popover styling per mockup (raised surface, shadow, radius-md).
- Plan replaced whole on each `plan` update; `plan_removed` clears it (pill
  disappears, strip stays).

## Out of scope
Composer internals (T5), header (T1, done), transcript (T2, done). No new
deps/tokens; 4px grid. Do not edit test files.

## Gates
`cd web && npm run check` (no new errors) and `npm run build` pass. Run
`npm test` once, record newly broken test files, do not fix.
