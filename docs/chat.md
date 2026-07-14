# Chat

Chat is how you talk to a ticket's worker directly, and how you reach the underlying
AI system's catalogue of commands and skills. The live chat surface is on a ticket:
a real conversation with that ticket's employee.

## The ticket chat

The chat panel on a ticket is a real conversation with that ticket's employee.
Ordinary messages and commands both enter through one request:
`POST /api/chat/{entity_id}/turns`. That request starts a server-owned chat turn,
then one `ChatTurnLifecycle` owns the rest of that human turn. It atomically creates
the visible turn, delivers it to Hermes, records typed activity and output, and lets
the first completion, error, or Pause settle it. There is no separate send, command,
or browser streaming route. The browser
reads one `ChatState` resource: durable visible messages plus the active turn, if
one is running. The same resource survives navigation, remounts, reloads, WebSocket
misses, and simple polling. It is built only from Panels-owned database rows. Reading
it never contacts Hermes, loads Employee session history, or changes a Ticket's
Employee session id.

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

Hermes is still the transport. Before any human Ticket command, image attachment, or
prompt is written, the lifecycle binds the candidate Hermes session to the
still-running turn and to the Ticket's `employee_session_id`. The database writer
compares the candidate with the Ticket's current id, updates the Ticket and turn
together, and returns the effective id. The gateway uses the live Hermes session for
that returned id. This means a concurrent winner is the session that actually receives
the input, not merely the value Panels records later. Day and top-level-agent Chat keep
their own Chat-specific session keys because they have no Ticket employee identity.
The visible transcript and live activity indicator belong to Panels:
`chat_messages` records the product-facing lines, and `chat_turns` records the
current phase, partial output, error, completion, and whether Pause is available.

While a turn is running, the quiet activity row shows the latest safe summary. Its
chevron opens an ordered list of thinking phases, tool use, and commands for that
turn. Panels stores only the category, short label, state, identity, and timing
needed to keep that list current. It never stores reasoning text, tool arguments,
or tool and command output in the activity list. Repeated updates change the same
entry where Hermes supplies an identity, the list keeps at most 100 entries, and
the entries are removed when the turn settles.

That split matters: writing a row to Panels chat state does not append anything to
the employee's Hermes conversation. A normal Ticket Chat send reaches the employee
because it goes through the Ticket's `employee_session_id` and is then mirrored into
Panels Chat. Direct `chat_messages` or `chat_turns` writes are only UI/audit state
unless the same text is also delivered through Hermes.

For a **paired** Stage, this ordinary user-originated Ticket Chat path is how work
continues. It reaches the same durable Employee session and worker context as the rest of
the Ticket. Finishing a Chat turn without a proposal leaves the Ticket in **paired work**.
A real worker proposal moves it to approval and always parks there, even when the Ticket's
scope would auto-accept a worker-owned proposal. Chat itself never settles the gated
field or advances the Stage.

The EmployeeStepRunner uses the same chat-state projection but a separate delivery
path. It claims a Ticket and calls the employee-only `run_ticket_step`; it does not
enter `ChatTurnLifecycle` or the human gateway method. When it starts a worker step,
it writes a worker line and an active turn. Gateway deltas and future tool/activity
events update that turn. When the worker settles, the assistant reply is recorded as
a message and the active turn disappears.

Human admission and the Employee's final claim use the same SQLite write lock. Human
admission rechecks `ticket_status` inside that transaction, while Automatic
Employee-step eligibility rechecks that there is no running Panels Chat turn. If the
ticket is already at `agent_running_step`, the send or command returns
`already_running` instead of creating a competing turn. If a human turn wins first,
the Employee claim does nothing. The chat state remains readable in both cases.

The visible active turn can be paused from the chat panel. While a turn is active,
the composer's send button becomes the pause button; pressing it interrupts that
chat session and settles the `chat_turns` row as interrupted with any partial output
kept. The same Pause action controls a visible human-origin or worker-origin turn.
Pause does not change the Ticket's runtime status or Stage ownership, and Chat settlement
does not wake Automatic Employee-step discovery.

Stop changes the visible Panels turn immediately. It does not guess that Hermes has
finished unwinding the interrupted work. A following send goes straight to Hermes
and follows Hermes's native `streaming`, `queued`, or `steered` result. The gateway
keeps each accepted send tied to its own consequence, so delayed interruption or
completion events cannot finish the wrong visible turn.

For a Ticket, `employee_session_id` names the durable Hermes conversation shared by
human Ticket Chat and Employee steps. A gateway or Panels restart resumes that id and
does not replay prior input. If Hermes rotates it, Panels binds the rotated candidate
before using it and records `employee_session_changed`. Typing exactly `/new` is the
one forced-fresh case: its newly created candidate replaces the prior id before the
command completes. If delivery becomes uncertain, Panels reports the gateway outcome
honestly and does not guess or retry the prompt automatically.

Employee session history is a separate, deliberate inspection. A direct caller asks
for `GET /api/tickets/{ticket_id}/employee-session-history`; there is no generic Chat
history route. The result is Hermes' authoritative record of what that employee
actually received and produced. It may include pending internal context, revision
guidance, system or tool content, or other material intentionally absent from Panels
Chat. It is never copied into an empty Panels transcript. A Ticket without an Employee
session id returns an empty history without starting Hermes.

After a process restart, a running human chat turn is settled as interrupted with its
partial output kept, then Panels creates one visible system recovery turn and resumes
the same stored Hermes session. That recovery message asks Hermes to continue the
interrupted response; it does not resend the original human message. Ticket worker
recovery is separate, so a ticket already owned by `agent_running_step` is not also
continued through ordinary chat recovery.

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

Typing exactly `/new` starts a fresh underlying Hermes conversation for that Ticket.
Panels stores the new durable `employee_session_id`, shows `New session started.` as
a system line, and sends the next ordinary message through the new session's live
handle. The visible Panels transcript stays in place. Commands with arguments, such
as `/new title`, continue through the ordinary command path.

## Handoffs

- **The employee runtime** (`employee-runtime.md`) — the live worker the chat talks
  to, and the role skills it can be handed.
- **Days** (`days.md`) — the day no longer has its own chat surface; loose capture
  is a future day/rollover capability, not current UI.

## Deferred

- **Chat is not queued behind an active worker step.** Sending chat or running a
  command while the worker step is active returns `already_running`; explicit Employee
  session history remains readable.

---

_Last verified: 2026-07-14._
