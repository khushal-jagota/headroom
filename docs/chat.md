# Chat

Chat is how you talk to a ticket's worker directly, and how you reach the underlying
AI system's catalogue of commands and skills. There are two chat surfaces — the one
on a ticket (a real conversation with that ticket's employee) and the one on the day
(quick capture, covered in `days.md`).

## The ticket chat

The chat panel on a ticket is a real conversation with that ticket's employee. When
you send a message it goes to the live worker over the real gateway, and the worker
answers in its own words — plain prose, no chat-bubble decoration, with a few
"thinking" dots while it works, because a real round trip takes several seconds.
Earlier the chat could only ever say "Gateway Offline", because it was wired to the
wrong thing and, after a gateway restart, kept trying to resume a session that no
longer existed. It now reaches the actual worker, and if the link is ever lost the
system quietly starts a fresh session for that ticket instead of giving up — so the
chat keeps working without you noticing the hiccup.

_Code paths:_ `src/planner/chat/` (the gateway-backed chat service), `assets/screens-ticket.js`
(the panel).

## Slash commands and skills

Type a "/" in a ticket's chat and a menu opens listing everything the underlying
agent system can do: a long catalogue of built-in commands and, in their own section,
**skills** — short instruction documents that teach a worker a particular job. The
catalogue is fetched once from the gateway and remembered, so the menu opens
instantly after the first time.

The two kinds behave differently today:

- **Skills run.** Picking a skill runs it straight into that ticket's employee: the
  worker reads those instructions and acts on them for this ticket.
- **Display commands are insert-only.** The other (non-skill) commands are dropped
  into the message box as text you can choose to send; actually executing them is
  left for later.

## Handoffs

- **The employee runtime** (`employee-runtime.md`) — the live worker the chat talks
  to, and the role skills it can be handed.
- **Days** (`days.md`) — the day chat, where loose messages are captured as tickets
  or ideas.

## Deferred

- **Display commands don't execute yet** — insert-only for this first slice.
- **No token streaming** — the reply arrives whole after the thinking dots.
- **Chat isn't behind the per-step queue** the runtime uses for a ticket's work.

---

_Last verified: 2026-07-07._
