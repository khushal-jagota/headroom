# Conversations

A conversation is one agent, working in one folder, with a permanent record of what was actually delivered to it and what it did. Every Ticket Worker, the Chief, every Manager, and every [goal](goals.md) has one, and each stores its own conversation id. A person typing, an automatic Worker step, a Manager wake, and a recovery notice after a deploy all enter that same conversation. There is no second input path: an edited Ticket field or a parked proposal reaches an agent only when a later prompt carries it.

## What a conversation is made of

- **The notebook.** The append-only record of finished facts: a delivered prompt, a completed agent message, a tool call, a permission ask and its answer, a model change, a turn ending, a message to the owner, a refusal, a discard. A row is written once. Private reasoning is dropped.
- **The live tail.** The text of a running turn, streamed straight to an open pane and not stored as notebook prose.
- **The conversation system.** One core that holds messages while the agent is busy, starts and stops the child process, writes the notebook, and decides each send's fate.
- **Three backend adapters.** Hermes over ACP, Codex through its app server, and Claude through the Claude Agent SDK. The backends are a closed set, and one door turns an untrusted name into a member of it or refuses it.
- **Send Message.** The door a person, the Chief, a Manager, or a Worker uses to address someone. It finds the recipient's current conversation and records who sent the message.
- **The pane.** The same conversation dock on a Ticket, the Chief, a Manager, and a goal, with a composer for text, images, files, a video, and voice. Voice is transcribed into the draft, so audio never enters the conversation.

```
 person or agent             Worker step · Manager wake · recovery
 (Send Message)                          │
        └──────────────┬─────────────────┘
                       ▼
             conversation system ── notebook ──► attention, Review, notifications
               │            │
               │            └─ live tail ──────► open pane
               ▼
   adapter (Hermes · Codex · Claude)
               │
               ▼
   child process on the VPS, or on the Mac through the executor
```
The backend's own session id is internal and can be rebound without anything outside the system noticing.

## How a send works

A busy agent is not a refusal. The sender picks how a busy agent meets the message:

- **Steer**, the default when a person sends, injects text into the running turn when the backend allows it.
- **Queue** holds the message until the agent is free. Worker steps, wakes, and other automation use Queue so they never steer into an unrelated turn.
- **Send now** ends the running turn and runs this message ahead of anything held.

Messages that cannot join a running turn, such as attachments or a model change, wait in the queue with a visible reason. **Stop** ends the running turn.

Every send returns a fate. The fate is about delivery, not about whether the model agreed or obeyed.

| Fate | Meaning |
| --- | --- |
| started | The prompt reached a live backend. |
| queued | Headroom is holding it until the agent is free. |
| injected | A steer was admitted into the running turn. |
| refused | Delivery did not happen, and the reason says why. |
| uncertain | The prompt may have crossed the wire. Headroom does not retry it. |
| suppressed | Dropped on purpose before the wire, usually because a notice went stale. |
| recorded | A message to the owner was written. No backend was called. |

What happens to a Worker step after each fate belongs to [Worker steps](worker-steps.md).

## Lifecycle

A conversation has at most one child process. Nothing runs after a server restart until the next send. A child that sits idle is stopped, and the next send resumes the stored session. If the backend cannot restore that session, the send is refused. Headroom never puts a fresh agent behind an old transcript. A conversation that has gone quiet is compacted automatically while the backend's prompt cache is still warm.

Held messages live in memory, so a restart loses anything not yet delivered.

New conversation ends the running turn, discards held work, and links a fresh conversation. The old notebook stays as history, and only the active conversation accepts sends. After a deploy interrupts sessions, eligible conversations get one recovery prompt through the ordinary send path, and nothing is replayed. See [Deployment](deployment.md).

## Worker resources on Linux

Each Claude or Codex Ticket agent and its tools run in a separate systemd user service. Memory exhaustion stops that Worker unit and reports the cause in its conversation. Other Workers and Panels stay available.

Each Worker receives a disk directory for temporary files. The directory survives across turns until the agent process stops. Unit shutdown stops its tools before cleanup. Account and login probes keep their separate lifetime. Non-Linux platforms retain their existing launch path.

The host must supply the Worker slice and its CPU and memory limits before Linux Workers can start. A missing budget stops the launch with an error. The [user-service inputs](../ops/panels-environments/README.md#worker-resource-units) describe installation and the operator-owned restart.

## Models and asks

A model or reasoning change rides the next message that is actually delivered. Codex and Hermes switch in place. Claude starts a new child on the new model and carries the session across. The backend itself is fixed once the conversation exists. The picker reads the model list from the machine that runs the conversation and shows only the newest version of each Claude and Codex model. A model disabled in the [Usage](usage.md) catalog is refused when a Ticket is created or saved and when a message is dispatched, and a refused dispatch stays in the notebook with its reason. A Ticket's saved starting choice belongs to [Tickets](tickets.md).

Permission asks are always shown and always wait for an answer. Headroom does not auto-approve and does not remember a grant. Claude and Codex can also ask structured questions. Hermes has only the permission flow. Production agents run with full tool access, so an ask appears because the agent chose to ask.

## The workspace for an agent

Each conversation starts in one folder and keeps it through resume and restart. The folder is an explicit workspace when one is given, otherwise the Ticket's Project folder, otherwise the server's default workspace. A Mac Worker without either uses the executor's own Panels workspace on the Mac. A new configuration applies to new conversations. [Environments](environments.md) covers the server default.

## What comes back out

Automation sends a Ticket its [Worker steps](worker-steps.md), a Manager its goal and wakes ([Managers and Chief](managers.md)), the Chief a recap request, and any interrupted conversation its recovery notice, all through the ordinary send path.

The notebook feeds attention: messages to the owner, open asks, and failures become the marks the rail, [Review](review.md), and [notifications](notifications.md) read. Unread dots and working marks are per conversation, so a child Ticket's dot is not its Manager's. A failed Ticket turn wakes the agent who holds the Ticket's ceiling, or reaches Khushal through attention when he holds it. Gated Ticket text changes only through [proposals](tickets.md#how-a-stage-advances). Files an agent writes are artifacts on disk, not notebook rows. Usage rows feed the cost lines on [Usage](usage.md). An agent asking for an API key is covered by [Credentials](credentials.md).

## Decisions that shape it

- One input path, so whatever an agent knows from Headroom can be read in its notebook.
- The fate reports delivery honestly, and nothing that may have reached the agent is retried automatically.
- Each real agent identity has one home and one login. Headroom launches children against those homes and keeps no copy of a credential.
- Claude sessions start with the user's ordinary hooks turned off, so local hooks do not inject context or side effects into Headroom's agents.

## Code

- `src/planner/conversation/`: the contract (`contracts.py`), the core (`system.py`), the notebook store, the live tail, attachments and video uploads, deploy recovery, and one adapter per backend under `backends/`.
- `src/planner/message_delivery/`: Send Message.
- `src/planner/runtime/conversation_start.py` and `runtime/workspaces.py`: starting a conversation and choosing its folder.
- `web/src/components/conversation/` and `web/src/lib/conversation/`: the pane and composer.

_Last verified: 2026-11-04._
