# Chat

Chat is how you talk to a ticket's worker directly, and how you reach the underlying
AI system's catalogue of commands and skills. The live chat surface is on a ticket:
a real conversation with that ticket's employee.

## The ticket chat

The chat panel on a ticket is a real conversation with that ticket's employee.
Ordinary messages and commands both enter through one request:
`POST /api/chat/{entity_id}/turns`. That request starts a server-owned chat turn,
then the gateway stream delivers it to Hermes and reports its progress back to the
turn. There is no separate send, command, or browser streaming route. The browser
reads one `ChatState` resource: durable visible messages plus the active turn, if
one is running. The same resource survives navigation, remounts, reloads, WebSocket
misses, and simple polling.

When the panel first loads, it starts at the latest message. New messages and live
output stay in view while the reader is at or near the bottom. An upward scroll
immediately leaves the viewport in the reader's control as the conversation grows.
The Latest button appears whenever the bottom of the conversation is meaningfully
below the viewport, even when no new content has arrived. Using it, or manually
returning near the bottom, resumes following.

The small image button directly beside `/` can add one or more images to a message.
Paste and drop use the same intake path. Pending images appear as a compact row in
the composer, and each one can be removed before sending. A message may contain text
and images or only images. Panels keeps the selected images in the composer until the
turn starts successfully, so an upload or start error can be retried without choosing
the files again. Invalid selections show the normal quiet chat error and do not start
a turn.

Chat images for tickets, days, and the Chief of Staff share the managed
`files/chats/<entity-id>/` tree. The visible human line remains ordinary Markdown
with managed-file links in the selected order, and the shared file preview renders
those links inline both immediately and after a reload. Delivery to the AI is
separate: Panels attaches each saved image to the live Hermes session in order,
then submits one prompt for that turn. A transcript preview by itself is not proof
that the AI received the image.

Hermes is still the transport. The planner stores the durable `chat_session_key`
before submitting a prompt, so tools inside a worker turn can resolve their ticket
immediately. The visible transcript and live activity indicator belong to Panels:
`chat_messages` records the product-facing lines, and `chat_turns` records the
current phase, partial output, session key, error, and completion.

While a turn is running, the quiet activity row shows the latest safe summary. Its
chevron opens an ordered list of thinking phases, tool use, and commands for that
turn. Panels stores only the category, short label, state, identity, and timing
needed to keep that list current. It never stores reasoning text, tool arguments,
or tool and command output in the activity list. Repeated updates change the same
entry where Hermes supplies an identity, the list keeps at most 100 entries, and
the entries are removed when the turn settles.

That split matters: writing a row to Panels chat state does not append anything to
the worker's Hermes conversation. A normal chat send reaches the worker because it
goes through the gateway/session path and is then mirrored into Panels chat. Direct
`chat_messages` or `chat_turns` writes are only UI/audit state unless the same text
is also delivered through Hermes.

The EmployeeStepRunner uses the same chat state. When it starts an automatic worker step, it writes
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

Stop changes the visible Panels turn immediately. It does not guess that Hermes has
finished unwinding the interrupted work. A following send goes straight to Hermes
and follows Hermes's native `streaming`, `queued`, or `steered` result. The gateway
keeps each accepted send tied to its own consequence, so delayed interruption or
completion events cannot finish the wrong visible turn.

The stored chat session key names the durable Hermes conversation. A gateway restart
resumes that key and does not replay prior input. If Hermes rotates the durable key,
Panels stores the new key and logs a `chat_session_created` event. If delivery becomes
uncertain, Panels reports the gateway outcome honestly and does not guess or retry the
prompt automatically.

The Chief of Staff page uses the same chat state shape with its top-level entity id.
Only the gateway routing differs: chief messages go to the `panels-chief-of-staff`
role, while ticket and day chat keep the worker gateway. The hosted
`POST /api/messages/chief` endpoint is a narrow shell over the same human-turn
service, not a second delivery path.

Hosted clients can send a plain HTTP Chief message without using the local Panels
CLI:

```
curl -X POST https://<tailscale-serve-name>/api/messages/chief \
  -H 'Content-Type: application/json' \
  --data '{"text":"What should I look at next?"}'
```

Python scripts use the same JSON body:

```
import requests

response = requests.post(
    "https://<tailscale-serve-name>/api/messages/chief",
    json={"text": "What should I look at next?"},
    timeout=30,
)
response.raise_for_status()
turn = response.json()
```

The body must be exactly one nonblank string field: `{"text": "..."}`. Panels sends
that text through the real Chief Hermes session with message mode and returns the
created chat turn. If a Chief turn is already running, the route returns
`already_running` with HTTP 409. Browser clients in hosted mode must use the
configured canonical HTTPS origin; script clients may omit `Origin`.

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

Typing exactly `/new` starts a fresh underlying Hermes conversation for that chat.
Panels stores the new durable session key, shows `New session started.` as a system
line, and sends the next ordinary message through the new session's live handle. The
visible Panels transcript stays in place. Commands with arguments, such as
`/new title`, continue through the ordinary command path.

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

_Last verified: 2026-07-10._
