# Chat

Chat is how you talk to a ticket's worker directly, and how you reach the underlying
AI system's catalogue of commands and skills. The live chat surface is on a ticket:
a real conversation with that ticket's employee.

## The ticket chat

The chat panel on a ticket is a real conversation with that ticket's employee. When
you send a message it goes to the live worker over the real gateway, and the worker
answers in its own words — plain prose, no chat-bubble decoration, with token
streaming and a few "thinking" dots while it works, because a real round trip takes
several seconds.
When you open or return to a ticket, the panel reloads the durable Hermes session
history for that ticket through a lazy gateway resume, so the read path does not
build an agent just to display old turns. This is the full employee trace: human
messages, assistant replies, system or command output, and worker-step prompts
and replies all come back into the rail. The browser only keeps a local
transcript while an active message is streaming; Hermes history is the source for
a reopened ticket.

System B uses that same durable session for automatic worker steps. When it creates
or resumes a session, it stores the `chat_session_key` before submitting the prompt,
so tools inside the worker turn can resolve their ticket immediately and the prompt
and reply are visible in chat history after the turn.

Human sends and commands follow the same pre-prompt session-key rule. If the ticket
is already at `agent_running_step`, the send or command returns `already_running`
instead of creating a competing turn; the history route still reads the existing
transcript.

Earlier the chat could only ever say "Gateway Offline", because it was wired to the
wrong thing and, after a gateway restart, kept trying to resume a session that no
longer existed. It now reaches the actual worker, and if the link is ever lost the
system quietly starts a fresh session for that ticket instead of giving up — so the
chat keeps working without you noticing the hiccup. The same repair is used when
history is loaded: if Hermes says the durable key has rotated, the planner stores
the fresh key and logs a `chat_session_created` event.

_Code paths:_ `src/planner/chat/` (the gateway-backed chat service),
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

_Last verified: 2026-07-08._
