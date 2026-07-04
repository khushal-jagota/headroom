# T15 — Day and Review screens (stage 5)

## Scope

SPEC §10 screens 1 and 2 on the T14 foundation, composing D11 components only (new ones need orchestrator sign-off — request via report, don't invent silently).

- **Day (#/day, home)**: brief (Markdown block); day-plan tree (D11 #9 — per-node Accept/Invalidate, top Accept-all/Reject-all, calling the four plan routes); ordered day ticket list (Entity rows, remove action = defer); Review entry with pending count; day chat panel (D11 #10 via /api/chat/day_<date>/*, offline notice, pluggable input source seam).
- **Review (#/review)**: one-at-a-time (D11 #16), oldest first, from /api/queues approvals: gating-field proposals (Proposal card + mandatory Grant-pair picker — Accept disabled until both halves picked, "no further" included), sprint-item status proposals (accept without grant), needs_review results (approve action), quick-edit prefilled with proposal body (edit-accept), Skip (stays queued, move on), link to full Ticket. Resolved items leave immediately (refetch on action).

## Files owned

- `assets/screens-day.js`, `assets/screens-review.js` (+ the plan-tree and chat-panel and proposal-card/grant-picker component additions to `assets/components.js` — coordinate: this ticket OWNS components.js additions for D11 items 7, 8, 9, 10, 16; T16/T17 must not touch them).

## Acceptance for integration

Manual-scripted smoke against a demo-seeded test server: day shows brief/plan/tickets; accept-all adds plan children to the list; review walks pending items oldest-first; accept without grant pair impossible (button disabled until picked); edit-accept stores altered text; chat echoes via fake gateway; offline fake shows notice. node --check green; no literal styles outside tokens.
