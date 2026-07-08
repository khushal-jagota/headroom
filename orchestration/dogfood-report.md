# Dogfood report — live app, end-to-end

**Tree:** `68b2c9a` + uncommitted working-tree changes (as handed off).
**How it was run:** isolated `plan serve` on a temp DB, `PLAN_TEST_MODE=1` (fake adapters →
echo chat gateway, no real hermes spawn), `PLAN_FAKE_NOW=2026-07-04T12:00:00`, seeded with
`plan seed --demo`. Driven by Playwright (headless Chromium), every screen visited and
interacted with. The real `~/.hermes/planning/` dir was never touched.

**Screenshots:** `<scratchpad>/dogfood/shots/` (referenced per screen below). Driver scripts
and raw `report.json` are in `<scratchpad>/dogfood/`.
Full path: `/private/tmp/claude-501/-Users-khushaljagota--hermes-planning-v2/df06c49b-4be9-4274-addd-da37a626407c/scratchpad/dogfood/`

## Headline

Everything the handoff asked to verify **works**. Zero console errors/warnings, zero
JS/page errors, zero HTTP ≥400, zero server-side 500s or tracebacks (191 log lines, all
`200 OK`). Chat is **online via echo** on tickets and both inline-edits persisted across
reload. One genuine visual defect (Board card layout) and a couple of low-severity nits.

## Screen by screen — what works

| Screen | Result | Notes |
|---|---|---|
| `#/day` (Today Overview) | **Works** | Four fields render: focus hero + Brief take + Watchout (amber label) + If today lands (green label). Calm, correct. `01_day.png` |
| `#/board` | **Works, but janky** | 6 state columns (needs success/approach/plan, in progress ×2, needs review, done). Cards carry title + priority/deadline/project chips. Horizontal scroll for overflow columns is by design. **Card layout defect — see Issue 1.** `02_board.png` |
| `#/review` | **Works** | 1 approval ("Review the analytics dashboard numbers") with RESULT + review notes + Approve / Skip / open-ticket. Nav badge shows "Review 1". `03_review.png` |
| `#/sprint` (Tracking) | **Works** | Header + "Sprint Overview · Sprint Tracking" tab pair; primary-bet line; "1 of 3 done"; ACTIVE/TODO/BLOCKED/DONE groups + LOOSE TICKETS. Expanding an item reveals its ticket rows (state · title · priority). `04_sprint_tracking_expanded.png` |
| `#/sprint/overview` | **Works** | Kickoff (limiting factor / primary bet / supports / premortem) open; Mid-sprint Review + Sprint Review collapsed, phase-open derived from content. `05_sprint_overview.png` |
| `#/backlog` | **Works** | Empty at seed. Compose ("+ New backlog item") opens; created item landed in the **P3** group with a Vylo chip and the unscheduled count updated. `10_backlog_created.png` |
| `#/ideas` | **Works** | Capture-first hero. Title-only capture → flat row; capture-with-body → expandable disclosure whose body renders markdown (bold). `11_ideas_with_body_expanded.png` |
| `#/ticket/<id>` | **Works** | Header = title + priority + due + project + sprint (+ "blocked" when blocked) + Copy. Recap → (approval block on needs_review: RESULT + review notes + amber **Approve**) → collapsible fields (SUCCESS/APPROACH/PLAN/RESULT with ✓/● markers) → chat rail. `08_ticket_inprogress.png`, `09_ticket_needs_review.png` |

## Chat — ONLINE (echo), confirmed

- `GET /api/chat/{id}/status` → `{"available": true}`; no `gateway offline` notice on any
  ticket.
- Typed + sent a message on two tickets → the echo reply (`echo: <message>`) rendered live
  in the rail each time. `08_ticket_inprogress_chat.png`, `09_ticket_needs_review_chat.png`

## Inline edits — persist across reload, confirmed

- **Day focus** field edited → reloaded → new value present. ✓
- **Sprint "limiting factor"** field edited → reloaded → new value present. ✓
- Both are the shared contenteditable `inlineEdit` hook committing on blur → single-field
  PATCH; the WS flush re-renders from the saved value.

## Console / JS / HTTP / server errors

None. No `console.error`/`warning`, no `pageerror`, no response ≥400 across all screens and
interactions; server log is 191 lines of `200 OK` with no traceback.

## Issues — ranked

**1. [Medium · visual] Board cards clip chips and wrap titles one word per line.**
`#/board` reuses the `.entity-row` layout (flex, `justify-content: space-between`, chips
`flex-shrink: 0`) inside a narrow fixed-`min-width` column. When priority + deadline +
project are all present, the **project chip is clipped** ("Vy…", "Lea…") and the title, with
no `min-width: 0`/ellipsis, **wraps a word per line** ("Fix / the / flaky / login / test.").
Functional (cards click through; all columns reachable by horizontal scroll) but reads
broken. Fix: give the title `min-width: 0` + ellipsis, or stack title over a chip row, or
widen the column. This is the one thing a user would call a bug on sight. `02_board.png`

**2. [Low · UX friction] Backlog compose does not submit on Enter.** The title field enables
the "Add to backlog" button, but pressing Enter in it does nothing — you must click the
button. The **Ideas** capture, by contrast, *does* submit on Enter. Inconsistent between the
two create surfaces; consider Enter-to-submit on the backlog title for parity.

**3. [Low · cosmetic, test-mode artifact] Idea relative date reads "1d" for ideas created
"today".** `relDate()` uses the real browser clock while the server clock is frozen at
`PLAN_FAKE_NOW`; the gap makes "today" render as a day old. This is a harness artifact, not a
product bug — in production both clocks are real time.

**4. [Nit] Chat rail is very sparse.** No header/label, empty until the first send, and the
"planner" reply is plain text while the "you" message is a bubble. Works; just minimal enough
that it can read as "is chat even here?" before you type.

## Bottom line

Ship-healthy on behavior. The only fix worth doing before showing this off is **Issue 1 (Board
card layout)**; Issues 2–4 are polish.
