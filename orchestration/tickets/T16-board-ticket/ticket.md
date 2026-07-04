# T16 — Board and Ticket screens (stage 5)

## Scope

SPEC §10 screens 3 and 4 on the T14 foundation, composing D11 components.

- **Board (#/board)**: one column per ticket state, `dropped` hidden; cards are Entity rows showing title, priority, deadline, project, pending-proposal marker, running-claim marker; click opens Ticket; no drag-and-drop.
- **Ticket (#/ticket/<id>)**: four field sections (value as Markdown block; pending proposal via Proposal card with Accept/Edit carrying the onward grant; notes inline via Field editor); recap section; State control (D11 #11: human jump + drop); Grant control (D11 #12: plain ceiling/at-cap pickers); **Copy ticket** button (GET /api/tickets/<id>/copy-text → clipboard with textarea fallback); links section (add/remove via /api/links, kinds per §3.6); day/sprint assignment controls; run history (D11 #15); event log (D11 #14); chat panel (shared component from T15).

## Files owned

- `assets/screens-board.js`, `assets/screens-ticket.js`, plus components.js additions ONLY for D11 items 11, 12, 14, 15 (State control, Grant control, Event log, Run history). Items 7/8/10 are T15's — reuse, never redefine.

## Acceptance for integration

Scripted smoke against a demo-seeded server: board renders six columns with correct cards and markers; ticket screen renders a mid-flow ticket (pending proposal visible, accept with grant works); copy-text returns the §10 block; unblock/drop/grant actions round-trip; event log and run history render. node --check green; tokens-only styling.
