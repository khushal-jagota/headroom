# Conversations

A conversation is one agent, working in one folder, with a permanent record of what was actually delivered to it and what it did. Every Ticket Worker, the Chief, and every Manager has one, and each stores its own conversation id. A person typing, an automatic Worker step, a Manager wake, and a recovery notice after a deploy all enter that same conversation. There is no second input path: an edited Ticket field or a parked proposal reaches an agent only when a later prompt carries it.

## What a conversation is made of

- **The notebook.** The append-only record of finished facts: a delivered prompt, a tool call, a permission ask and its answer, a model change, a turn ending, a message to the owner, a refusal, a discard, and a backend's own reply to a slash command such as `/goal`. A row is written once. Private reasoning is dropped.
- **The live tail.** The text of a running turn, streamed straight to an open pane and not stored as notebook prose.
- **The conversation system.** One core that holds messages while the agent is busy, starts and stops the child process, writes the notebook, and decides each send's fate.
- **Three backend adapters.** Hermes over ACP, Codex through its app server, and Claude through the Claude Agent SDK. The backends are a closed set, and one door turns an untrusted name into a member of it or refuses it.
- **Send Message.** The door a person, the Chief, a Manager, or a Worker uses to address someone. It finds the recipient's current conversation and records who sent the message.
- **The pane.** The same conversation dock on a Ticket, the Chief, and a Manager (a [goal](goals.md) shows its holder's), with a composer for text, images, files, a video, and voice. Voice is transcribed into the draft, so audio never enters the conversation. The dock opens into the full conversation, and on a phone typing always happens there: the open conversation fills the screen above the keyboard. A transcript already read stays loaded at the dock, so opening it again draws it at once. The dock keeps its menus within the visible screen.

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

Claude and Codex run from owner-maintained CLI installations outside the app. Settings → Backends shows their installed and latest versions on the VPS. Only the owner can update them there. Updates change the selected user-local installation, and new agent processes use that version. A failed version lookup does not stop ordinary conversations. Mac CLI updates stay local to the Mac.

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

## Charts in messages

Chief, Manager, and Worker messages use the same Markdown surface. Ordinary Markdown tables already work. A closed `panels-chart` fence draws an inline bar or line chart with the shared goal chart plots. Chat charts omit range controls and the headline number. Charts with multiple series show a compact legend in the plot colors. The message supplies snapshot numbers, without a saved goal or a live measurement binding.

For example, this message compares two repositories:

````markdown
```panels-chart
{"kind":"bar","title":"Merged PRs per week","series":[{"name":"Panels","points":[{"label":"Week 1","value":4},{"label":"Week 2","value":7}]},{"name":"Website","points":[{"label":"Week 1","value":2},{"label":"Week 2","value":3}]}]}
```
````

Use `"kind":"line"` for a line chart. The send-message help describes the supported options. Invalid, unsupported, or incomplete chart specs remain code, so a bad chart does not break its message. The Markdown sanitizer stays in place, and chart labels stay text. Editable Markdown retains the source fence.

Values accept fractions between -9007199254740991 and 9007199254740991, inclusive. Null marks missing data. The `format.decimals` option accepts integers from 0 to 20. Point labels must be unique within each series.

## Lifecycle

A conversation has at most one child process. Nothing runs after a server restart until the next send, except a Mac agent: it keeps working, and the restarted server takes over its turn, open asks included. A child that sits idle is stopped, and the next send resumes the stored session. If the backend cannot restore that session, the send is refused.

A Claude agent can start a background task, such as a long test run, that keeps going after its turn ends. Claude reports its whole set of live tasks after every change, and Headroom keeps the conversation's record equal to that set, so a task whose end Claude never announced still leaves it. A ScheduleWakeup timer is not a task. While a task is live, the child is not idle, the sidebar shows the conversation as working, and the end of the conversation names each task. Stop and the composer still follow the turn alone. The idle clock starts again when a task ends. If the child goes away first, the task dies with it. Claude stops its tasks as it is terminated, so a task stopped just before the stream ends counts as lost too. The child may be stopped, its process may die, or the server may restart. Headroom then sends one prompt per lost task, from Panels, saying the task did not finish and asking the agent to read its output and restart it or report. The prompt waits behind a running turn and resumes the session like any send. A killed conversation drops its tasks without a prompt, because nothing runs in it until someone sends again. Headroom never puts a fresh agent behind an old transcript. A conversation that has gone quiet is compacted automatically while the backend's prompt cache is still warm.

Held messages live in memory, so a restart loses anything not yet delivered.

New conversation ends the running turn, discards held work, and links a fresh conversation. The old notebook stays as history, and only the active conversation accepts sends. After a deploy interrupts sessions, eligible conversations get one recovery prompt through the ordinary send path, and nothing is replayed. See [Deployment](deployment.md). A restart of the Mac's executor process interrupts its turns the same way: once the Mac has sent everything it held for a conversation, that turn ends interrupted and gets the same one recovery prompt, which resumes the session in a fresh agent. A message the old process never fetched goes to the fresh agent instead, and nothing is interrupted. A steer the Mac does not take in time, or that later proves it never reached its turn, waits in the queue as the next message.

## Worker resources on Linux

Each Claude or Codex Ticket agent and its tools run in a separate systemd user service. It keeps a Worker's processes apart from the Panels server and stops them all when the Worker ends. Workers have no memory cap. When the machine runs out of memory, Linux kills one process and everything else carries on. Every agent's tool shells mark themselves to be killed first, so a test or browser goes before an agent, and the live service asks to go last.

Each Worker receives a disk directory for temporary files. The directory survives across turns until the agent process stops. Unit shutdown stops its tools before cleanup. Account and login probes keep their separate lifetime. Non-Linux platforms retain their existing launch path.

The host must supply the Worker slice and its lower CPU weight before Linux Workers can start. A missing slice stops the launch with an error. The [user-service inputs](../ops/panels-environments/README.md#worker-resource-units) describe installation and the operator-owned restart.

## Models and asks

A model or reasoning change rides the next message that is actually delivered. Codex and Hermes switch in place. Claude starts a new child on the new model and carries the session across. The backend itself is fixed once the conversation exists, so a message to the Chief or a Manager that names only a backend, as the iOS app does with every message, is an ordinary message and is not checked against launch defaults. Only a message that starts a conversation resolves its launch settings, and a start on another backend must name that backend's model. The picker reads the model list from the machine that runs the conversation and shows only the newest version of each Claude and Codex model. A model disabled in the [Usage](usage.md) catalog is refused when a Ticket is created or saved and when a message is dispatched, and a refused dispatch stays in the notebook with its reason. A Ticket's saved starting choice belongs to [Tickets](tickets.md).

Permission asks are always shown and always wait for an answer. Headroom does not auto-approve and does not remember a grant. Claude and Codex can also ask structured questions. Hermes has only the permission flow. Production agents run with full tool access, so an ask appears because the agent chose to ask.

## The workspace for an agent

Each conversation starts in one folder and keeps it through resume and restart. A Worker starts in the Ticket's managed workspace. Panels creates its worktree from the project repository on the execution machine. A project without a repository gets an ordinary Ticket workspace. Workers prepare dependencies and local configuration themselves. Existing native sessions keep their actual folder on resume. New managed sessions reuse the attached local checkout, including unpublished changes.

The worktree and its sibling artifacts folder follow the lifetime in [Environments](environments.md#ticket-scratch). The Chief and each Manager instead start in a home folder of their own, made on their first start: `data/files/chief/` for the Chief, and the Manager's files folder for a Manager, beside the `artifacts/` the owner sees. They keep their own notes, scratch files and rules there, and their role text names the server's default workspace, where the repositories are. A new configuration applies to new conversations. [Environments](environments.md) covers the server default.

## What comes back out

Automation sends a Ticket its [Worker steps](worker-steps.md), a Manager its goal and wakes ([Managers and Chief](managers.md)), the Chief a recap request, and any interrupted conversation its recovery notice, all through the ordinary send path.

The notebook feeds attention: messages to the owner, open asks, and failures become the marks the rail, [Review](review.md), and [notifications](notifications.md) read. Unread dots and working marks are per conversation, so a child Ticket's dot is not its Manager's. A working mark means a running turn or a running background task. A failed Ticket turn wakes the agent who holds the Ticket's ceiling, or reaches Khushal through attention when he holds it. Gated Ticket text changes only through [proposals](tickets.md#how-a-stage-advances). Files an agent writes are artifacts on disk, not notebook rows. Usage rows feed the cost lines on [Usage](usage.md). An agent asking for an API key is covered by [Credentials](credentials.md).

After each application save, Headroom updates changed conversation statuses and sends them to the browser. Server startup recalculates every status from conversation records and current runtime state. Plain SQLite edits remain possible.

The pane reads a stored view that is built from the notebook. When the app connection returns, an open, visible pane catches up from that record without another send. A hidden or collapsed pane still reconciles sends with unknown outcomes. When a change alters the format of that view, nothing more is needed: after the server starts, it rebuilds each old view from its notebook in the background, one conversation at a time. Until its view is rebuilt, a pane is told that the view is upgrading and tries again.

## Decisions that shape it

- One input path, so whatever an agent knows from Headroom can be read in its notebook.
- The fate reports delivery honestly, and nothing that may have reached the agent is retried automatically.
- Each real agent identity has one home and one login. Headroom launches children against those homes and keeps no copy of a credential.

## Code

- `src/planner/conversation/`: the contract (`contracts.py`), the core (`system.py`), the notebook store, the stored views and their rebuild (`views/rebuild.py`), the live tail, attachments and video uploads, deploy recovery, and one adapter per backend under `backends/`.
- `src/planner/message_delivery/`: Send Message.
- `src/planner/runtime/conversation_start.py` and `runtime/workspaces.py`: starting a conversation and choosing its folder.
- `web/src/components/conversation/` and `web/src/lib/conversation/`: the pane and composer.

_Last verified: 2026-11-12._
