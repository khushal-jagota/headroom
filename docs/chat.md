# Chat

Chat is how you talk to a ticket's worker directly, and how you reach the underlying
AI system's catalogue of commands and skills. The live chat surface is on a ticket:
a real conversation with that ticket's employee.

## The ticket chat

The chat panel on a ticket is a real conversation with that ticket's employee. When
you send a message it starts a server-owned chat turn. The browser reads one
`ChatState` resource: durable visible messages plus the active turn, if one is
running. The same resource survives navigation, remounts, reloads, WebSocket
misses, and simple polling.

Hermes is still the transport. The planner stores the durable `chat_session_key`
before submitting a prompt, so tools inside a worker turn can resolve their ticket
immediately. The visible transcript and live activity indicator belong to Panels:
`chat_messages` records the product-facing lines, and `chat_turns` records the
current phase, partial output, session key, error, and completion.

That split matters: writing a row to Panels chat state does not append anything to
the worker's Hermes conversation. A normal chat send reaches the worker because it
goes through the gateway/session path and is then mirrored into Panels chat. Direct
`chat_messages` or `chat_turns` writes are only UI/audit state unless the same text
is also delivered through Hermes.

System B uses the same chat state. When it starts an automatic worker step, it writes
a worker line and an active turn. Gateway deltas and future tool/activity events
update that turn. When the worker settles, the assistant reply is recorded as a
message and the active turn disappears.

Human sends and commands follow the same pre-prompt session-key rule. If the ticket
is already at `agent_running_step`, the send or command returns `already_running`
instead of creating a competing turn. The chat state remains readable.

The visible active turn can be paused from the chat panel. While a turn is active,
the composer's send button becomes the pause button; pressing it interrupts that
chat session and settles the `chat_turns` row as interrupted with any partial output
kept. Pause does not change the ticket's runtime status or dispatch ownership.

Earlier the chat could only ever say "Gateway Offline", because it was wired to the
wrong thing and, after a gateway restart, kept trying to resume a session that no
longer existed. It now reaches the actual worker, and if the link is ever lost the
system quietly starts a fresh session for that ticket instead of giving up — so the
chat keeps working without you noticing the hiccup. The same repair is used when
history is loaded: if Hermes says the durable key has rotated, the planner stores
the fresh key and logs a `chat_session_created` event.

The Chief of Staff page uses the same chat state shape with its top-level entity id.
Only the gateway routing differs: chief messages go to the `panels-chief-of-staff`
role, while ticket and day chat keep the worker gateway.

_Code paths:_ `src/planner/chat/` (the gateway-backed chat service and state writer),
`web/src/components/ChatPanel.svelte` and `web/src/components/ChatComposer.svelte`
(the panel).

## Slash commands and skills

Type a "/" in a ticket's chat and a menu opens listing everything the underlying
agent system can do: a long catalogue of built-in commands and, in their own section,
**skills** — short instruction documents that teach a worker a particular job. The
catalogue is fetched once from the gateway and remembered, so the menu opens
instantly after the first time.

The menu hides commands that do not make sense in a web chat, such as quitting the
underlying worker. The commands it does show run on the ticket's own worker:

- **Skills run.** Picking a skill runs it straight into that ticket's employee: the
  worker reads those instructions and acts on them for this ticket.
- **Display commands render as system lines.** Picking or typing a command such as
  `/status` executes it and shows the gateway's command output in the chat trace.

## Handoffs

- **The employee runtime** (`employee-runtime.md`) — the live worker the chat talks
  to, and the role skills it can be handed.
- **Days** (`days.md`) — the day no longer has its own chat surface; loose capture
  is a future day/rollover capability, not current UI.

## Deferred

- **Chat is not queued behind an active worker step.** Sending chat or running a
  command while the worker step is active returns `already_running`; history remains
  readable.

---

_Last verified: 2026-07-09._
