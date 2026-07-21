# T4 — Permission prompt redesign

Implements DESIGN.md §9. Mockup: `orchestration/chat-redesign/mockup.html`
(Permission scenario).

## Files owned
- `web/src/components/acp/PermissionPrompt.svelte`

## Behavior
- Borderless: `--accent-surface` background, `--radius-md`, no border.
- Top row: uppercase mono kind chip from the request's tool-call `kind`
  (fallback: omit chip), then title (`toolCall.title` fallback as today), then
  right-aligned mono `m:ss` countdown to `deadlineAt` (omit if absent; stop at
  0:00 — no client-side auto-cancel, the server owns the deadline).
- Diff content renders between top row and options (keep the existing DiffView
  mounting approach).
- Options laid out by ACP `kind`: `reject_once`/`reject_always` on the left as
  ghost text buttons (faint; red on hover/focus only); `allow_*` gather right;
  the LAST allow option is the filled-accent primary (`--accent-bright` bg,
  `--accent-ink` text); other allow options are quiet outline. Labels are the
  server's `name` verbatim, order within each side preserved. Any kind the
  server invents beyond the four falls back to quiet outline on the right.
- Selecting an option calls the existing respond path; while submitting, all
  buttons disable (opacity ~0.7). NO status text ("Waiting for your
  decision" / "Sending permission response" lines are removed).

## Out of scope
Everything else. No new deps/tokens; 4px-grid spacing.

## Gates
`cd web && npm run check` (no new errors) and `npm run build` pass. Do NOT edit
test files (T6 owns them); list which tests broke in your report.
