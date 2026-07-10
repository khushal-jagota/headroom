# t_ui05 — Sprint restructure

Reference: `orchestration/daily-redesign/sprint.html` + `sprint-docs.html`;
notes.md Sprint rev 3 (+ the loose-tickets-as-group and collapsible-groups
directions, and the priority·title·state row order).

## Outcome
One sprint tracking page (no tabs) and a separate sprint-documents page.
Items grouped by project in collapsible groups; loose tickets are the same
group primitive; item rows carry status word + done-fraction; ticket rows read
priority · title · state.

## Contract
1. **Routing** (`App.svelte` + `SprintRoute.svelte`): `#/sprint` renders the
   tracking page; `#/sprint/documents` renders the documents page; legacy
   `#/sprint/overview` → replace-redirect to `#/sprint/documents`, legacy
   `#/sprint/tracking` → replace-redirect to `#/sprint`. The tabs nav is
   deleted.
2. **Tracking page**: serif sprint name (InlineEdit preserved); meta line:
   `date_start – date_end · day N of M · X of Y done · [Sprint documents ›]`
   — day-of-sprint derived from the dates (clamped: before start = day 0,
   after end = day M), done count = done items over total items (current
   `health` logic). The bet (`primary_bet`) as serif lead, read-only as today.
3. **Project groups**: items grouped by their `project` field, groups sorted
   alphabetically, itemless projects omitted, no-project items under
   "No project"; each group a collapsible disclosure (open by default) with
   uppercase label + count. **Loose tickets** = the same group primitive at the
   end, count = loose ticket count, ticket rows directly inside.
4. **Item rows**: chevron · title · status word (`in_progress` shown as amber
   "in progress"; `todo`/`blocked` faint; `done` green with the row dimmed) ·
   done-fraction ("1/3 done" from the item's ticket states; "—" when no
   tickets). Each item keeps `data-item-id` and **gains
   `data-item-status={status}`**. Expanded body: chips (priority · due ·
   blocked-by titles · blockers-cleared; project chip dropped — the group says
   it) then ticket rows.
5. **Ticket rows**: priority · title · state word; `data-ticket-id` preserved;
   still links to the ticket. Colour rule: green when `done`, amber when the
   ticket needs a human — the sprint item ticket projection
   (`src/planner/sprints/views.py` ~line 101) currently returns only
   id/title/state/priority, so **extend it with `has_pending_proposal` and
   `ticket_status`** (same semantics as the board card projection), and colour
   amber when `has_pending_proposal` or `state == "needs_review"`. Update the
   affected unit tests for the sprint views (grep tests/unit for the
   projection) and list them in the report.
6. **Documents page**: back link to the sprint, serif "Sprint documents" title,
   then the three phase disclosures exactly as today's overview fields
   (kickoff/mid/review field lists, InlineEdit editing, refline).
   `data-phase` values (`kickoff`/`mid`/`review`) and `data-field` per field
   preserved on this page.
7. Replaced sprint styles (tabs, status-group headings) deleted this wave.

## e2e / selector translations (this wave has real ones — list each in the report)
- `tests/e2e/test_flows_b.py` asserts membership in
  `[data-status-group="todo"]` / `[data-status-group="in_progress"]`
  containers and that an item leaves the todo group when work starts. Status
  groups no longer exist → translate to equivalent assertions on
  `[data-item-id=…][data-item-status="todo"→"in_progress"]` (same state
  transitions, same waits — assert the attribute value changes, and that the
  item is present exactly once).
- `#/sprint/overview` (test_flows_b.py:490) → `#/sprint/documents` (also
  proves the legacy redirect once: navigating to `#/sprint/overview` lands on
  documents).
- `[data-phase]` order/click assertions stay valid on the documents page.
- `test_flows_b.py:492` asserts the current tab via `.tabs .tab.cur` — tabs are
  deleted; translate to an equivalent assertion that the documents page is
  active (e.g. its `data-screen`/heading), not a weakened one.
- `test_flows_b.py:432` asserts loose tickets under `[data-loose]` — keep
  `data-loose` on the loose-tickets group body.
- Grep also for `data-status-group` in unit tests and any `tab` selectors.

## Acceptance
Implementer slice green (+ `tests/e2e/test_flows_b.py`); design review against
sprint.html and sprint-docs.html; full `./verify` green at integration.
