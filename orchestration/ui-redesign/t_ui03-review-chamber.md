# t_ui03 — Review chamber

Reference: `orchestration/daily-redesign/review.html`; notes.md "Owner rulings"
(Review is one thing at a time — locked), "Fidelity audit", proposals 2–3, and
the rejected queue-position note.

## Outcome
The review screen is the approved chamber: one centered column, Skip ›/Open
ticket › top-right, labelled recap + notes above the ask, the ask with depth
and entrance rhythm, send-back row, serif voice, the re-set empty state, and
keyboard shortcuts.

## Contract
1. **Layout** (`ReviewRoute.svelte`): centered ~680px column. Top line: right-
   aligned `Skip ›` (button, keeps `data-skip`) and `Open ticket ›` (link,
   keeps `data-open-ticket`). NO queue position, NO bottom outside-actions row
   (delete `review-outside-actions` usage on this screen — grep tests for
   `data-skip`/`data-open-ticket`: they must still resolve).
2. **Context block**: serif title (`--type-serif-display`, hover amber, still
   the ticket link — `review-ticket-title` is a class only, no data attribute
   exists; keep the class and the href behavior). Below it a small
   RECAP label + serif recap, then a collapsed NOTES disclosure when the field
   has user notes (this content comes from the existing TicketStageSection
   review variant — restyle, don't re-plumb: the recap/notes/ask composition
   stays inside the existing component tree; achieve mockup structure by
   styling those pieces, restructuring markup inside the components only as
   needed while keeping their data contracts).
3. **The ask**: depth treatment identical to t_ui02 (shared CSS, one
   definition). Approve + "until … then …" picker as-is functionally.
4. **Send-back row**: textarea + `Send back` per mockup; `data-review-revision`,
   `data-review-revision-input`, `data-review-revision-send` preserved; ⌘↩
   submit inside the textarea preserved.
5. **Entrance rhythm**: staggered fade-up (~320–480ms, the mockup's `arrive`
   keyframes) on queue-line/title/context/ask/revise; respects
   `prefers-reduced-motion`; keyed to the current entry so advancing re-runs it.
6. **Keyboard shortcuts** (approved addition): when focus is NOT in an
   editable/input, `s` = skip, `o` = open ticket, and ⌘↩ (ctrl↩) = approve the
   current ask (same code path as the Approve button; disabled states
   respected). A faint hint line at the bottom center per mockup. No global
   shortcut may fire while typing anywhere (inputs, textareas,
   contenteditable).
7. **Empty state**: the real copy ("There is nothing to review right now." in
   serif, "N agents in progress" sub) with the existing rising-disc mark
   restyled per mockup; `data-review-empty` preserved.
8. Old review styles replaced by this wave are deleted.

## e2e / selector notes
Grep for `data-review-card`, `data-review-empty`, `data-skip`,
`data-open-ticket`, `data-review-revision`, **and the scope selectors inside
the review card** — `tests/e2e/test_flows_a.py` asserts
`[data-review-card] [data-scope-ceiling]` / `[data-scope-atcap] select` values
(~lines 194, 337): both wrappers must survive inside the ask exactly.
Tests click Skip/Open wherever they
live in the DOM — moving them to the top is fine as long as attributes survive.
New shortcuts must not break existing keyboard tests (grep for `keyboard` /
`press` in e2e).

## Acceptance
Implementer slice green (+ the e2e file(s) covering review — identify by grep);
design review against review.html (including empty state via its toggle);
full `./verify` green at integration.
