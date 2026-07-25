# t_w5gmk3cq — Open Workspace selections as pages on mobile

## Accepted outcome

At viewport widths of 960px or less, selecting a Workspace Ticket opens the existing
standalone `#/ticket/<ticket-id>` page and selecting Chief of Staff opens the existing
standalone `#/chief` page. At wider widths, both selections retain the current two-pane
Workspace routes and inspector behavior.

No new page, breakpoint, navigation state, or layout is introduced.

## Implementation boundary

- `web/src/routes/BoardRoute.svelte`
- `tests/e2e/test_chief_of_staff.py`
- `docs/frontend.md`
- generated `web/dist/**`

The parent orchestrator owns this contract, review records, `PROGRESS.md`, `decisions.md`,
ticket artifacts, the canonical `./verify`, and the Panels implementation proposal.

## Required behavior

1. Both Workspace selection handlers evaluate the same `(max-width: 960px)` condition
   already used by the CSS layout at interaction time.
2. Narrow Ticket selection uses the encoded standalone Ticket hash.
3. Narrow Chief of Staff selection uses `#/chief`.
4. Wider Ticket and Chief of Staff selections preserve `#/workspace/<ticket-id>` and
   `#/workspace`, including existing inspector restoration and history behavior.
5. Plain-language frontend documentation describes the responsive split.

## Acceptance evidence

- A focused Playwright test is observed failing before the production edit and passing
  after it.
- Existing desktop Workspace selection and history coverage remains green.
- Svelte checks and the frontend build pass.
- Real 390px-wide screenshots show the complete standalone Ticket and Chief of Staff
  pages reached from Workspace selection.
- Independent implementation review reports no unresolved contract or regression finding.
- One final settled-tree `./verify` reports `VERIFY: PASS`.

