# Implementation dispatch

Implement the reviewed plan in the current shared worktree using strict TDD.

## Ownership

Ticket-owned files are `web/src/components/ChatPanel.svelte`, the chat region of `assets/app.css`, the chat flow in `tests/e2e/test_flows_a.py`, `docs/chat.md`, and generated `web/dist` output.

Unrelated concurrent edits in `tests/e2e/test_ticket_hard_delete_e2e.py` and `web/src/routes/TicketRoute.svelte` must remain untouched.

## Required proof

- First extend the browser test and run it to an expected RED caused by the missing behavior/control.
- Prove initial bottom positioning, following at the bottom, preservation after scroll-back, distance-based affordance visibility without new content, click-to-resume, and manual-return-to-resume.
- Run the focused chat e2e test, Svelte check, and frontend build after implementation.
- Do not run `./verify`; the integrator owns the single canonical full run after independent diff review.
