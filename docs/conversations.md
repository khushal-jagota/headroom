# Conversations

A conversation is one agent, working in one folder, with a permanent record of what was actually delivered to it and what it did. Every Ticket Worker, the Chief, and every Manager has one, and each stores its own conversation id. A person typing, an automatic Worker step, a Manager wake, and a recovery notice after a deploy all enter that same conversation. There is no second input path: an edited Ticket field or a parked proposal reaches an agent only when a later prompt carries it.

## What a conversation is made of

- **The notebook.** The append-only record of finished facts: a delivered prompt, an agent's finished reply, a tool call, a permission ask and its answer, a model change, a turn ending, a message to the owner, a refusal, a discard, and a backend's own reply to a slash command such as `/goal`. A row is written once. Private reasoning is dropped. A finished reply shows in the full conversation only. It is not addressed to anyone, so it raises no unread mark or notification.
- **The live tail.** The text of a running turn, streamed straight to an open pane. When that text finishes, the notebook records it as a reply and the tail clears.
- **The conversation system.** One core that holds messages while the agent is busy, starts and stops the child process, writes the notebook, and decides each send's fate.
- **Three backend adapters.** Hermes over ACP, Codex through its app server, and Claude through the Claude Agent SDK. The backends are a closed set, and one door turns an untrusted name into a member of it or refuses it.
- **Send Message.** The door a person, the Chief, a Manager, or a Worker uses to address someone. It finds the recipient's current conversation and records who sent the message.
- **The pane.** The same conversation dock on a Ticket, the Chief, and a Manager (a [goal](goals.md) shows its holder's), with a composer for text, images, files, a video, and voice. Voice is transcribed into the draft, so audio never enters the conversation. The dock opens into the full conversation, and on a phone typing always happens there: the open conversation fills the screen above the keyboard. A transcript already read stays loaded at the dock, so opening it again draws it at once. The dock stands on the page's bottom edge, and no box between it and the window can scroll, so a long paste grows the composer upward and never lifts it off the floor. The dock keeps its menus within the visible screen. Hover previews show the same model label as the composer and the current activity. While a turn runs, previews and the status above the composer show the same live elapsed time as the open conversation.
- **Files in a message.** HTML pages run on the Panels origin in the web and iPhone apps. They can use the owner’s Panels access. Managed file boundaries still protect private files and worktrees. A named HTML or Markdown file is a link in its sentence that opens the full preview. A picture or video keeps its own shape, several side by side form a grid, and a press opens one at a time in a dark full-screen view that Esc closes. Audio keeps its inline player.

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

Each backend is a driver package under `server/drivers/`, holding the adapter, its fixed facts (executable name, home environment names, install files), and the label and icon the apps will show. At startup Panels builds a registry with one instance per driver, and the conversation core asks the registry rather than a fixed list of three. A driver that fails to start, has invalid configuration, or is unknown becomes an unavailable entry with a reason, and nothing falls back to another backend. Until the lists are opened up, startup still refuses to run unless Claude, Codex, and Hermes are all live. A check in CI keeps backend names out of code outside their own package; each place that still names one is listed with the stage that removes it.

Panels gives a local agent a private launch credential for its current conversation. It enters only the child’s environment, after saved launch settings have been written. The database keeps its hash, never the token. A new child receives a fresh credential from its canonical association, including after a restart. Reset revokes the old conversation’s authority while preserving its notebook. Generic conversation starts cannot choose a privileged identity. Remote children retain the executor’s existing per-child capability; a local token never enters an executor command.

Claude and Codex run from owner-maintained CLI installations outside the app. Settings → Backends shows their installed and latest versions on the VPS. Only the owner can update them there. Updates change the selected user-local installation, and new agent processes use that version. A failed lookup of newer releases does not stop ordinary conversations. Before each backend starts, it checks its selected CLI. The native server remembers each version verdict for the exact installed file, from the backend card's version read or an earlier check, so a start reads the version again only after the CLI changes. It also checks the version the running CLI reports when it connects. If that CLI lacks a required feature, the backend names the required update, and a turn is refused before any launch is recorded. The adapter reads only the protocol fields it needs and ignores unrelated additions. The feature minimums and their reasons live in `src/planner/conversation/backends/cli_compatibility.py` and `server/conversation/backends/cli_compatibility.ts`. Mac CLI updates stay local to the Mac.

## How a send works

A busy agent is not a refusal. The sender picks how a busy agent meets the message:

- **Steer**, the default for messages from people and Panels system prompts, injects text into the running turn when the backend allows it.
- **Queue** holds the message until the agent is free.
- **Send now** ends the running turn and runs this message ahead of anything held.

Messages that cannot join a running turn, such as attachments or a model change, wait in the queue with a visible reason. A refused steer also waits there, and a held message promoted to steer keeps its place when the steer is refused. **Stop** ends the running turn.

Every send returns a fate. The fate is about delivery, not about whether the model agreed or obeyed. A proven failure before a native send is a refusal. A failure after a possible native send retains uncertainty and never authorizes a replay.

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

The phone keeps at most 300 transcript items and 512 KiB of inline and ordinary expanded bodies. One visible deferred expansion can exceed that budget, up to 2 MB. Another oversized expansion replaces it, and it leaves decoded memory when it leaves view. The reader preserves the visible neighborhood. Older ranges remain available through Earlier messages, and Latest returns to the newest window. The reader retains at most 20 server history scopes. A late older page survives reconnect only while its history window remains valid. A replacement snapshot invalidates the pending page and supplies the current Earlier messages cursor.

The collapsed phone dock requests status only. The full conversation acquires transcript demand and a fresh stream snapshot. The reader saves bounded display through the background cache writer. Saved rows start stale and supply neither stream coverage nor read credit. Only visible decoded remote rows from the current reader can mark messages read. Drafts and audio keep their separate owners.

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

A conversation has at most one child process. During a server restart, local native processes stop. Startup checks ownership and automatically resumes eligible interrupted conversations in fresh processes with new credentials. Cleanup that remains unproven keeps the conversation fenced.

An executor agent can keep its process, turn and open asks across a home restart. Its existing capability keeps working. A child that sits idle is stopped, and the next send resumes the stored session. If the backend cannot restore that session, the send is refused. A refusal or failed turn that a backend caused keeps the backend's own error text in the notebook and in one log line, so the reason can be read later.

A Mac Claude start that times out during initialization can recover before the waiting message is delivered. The [executor](executor.md#commands-and-output) keeps the message in order and only replaces a child after proving cleanup. This does not retry a message that may have reached an agent.

A Claude agent can start a background task, such as a long test run, that keeps going after its turn ends. Claude reports its whole set of live tasks after every change, and Headroom keeps the conversation's record equal to that set, so a task whose end Claude never announced still leaves it. A ScheduleWakeup timer is not a task. While a task is live, the child is not idle, and the sidebar, the status line above the composer, and hover cards show the conversation as working. After the turn's last row, the conversation names each live task by its description and removes the line when the task ends. Stop and the composer still follow the turn alone. The idle clock starts again when a task ends. If the child goes away first, the task dies with it. Claude stops its tasks as it is terminated, so a task stopped just before the stream ends counts as lost too. The child may be stopped, its process may die, or the server may restart. Headroom then sends one prompt per lost task, from Panels, saying the task did not finish and asking the agent to read its output and restart it or report. A killed conversation drops its tasks without a prompt, because nothing runs in it until someone sends again. Headroom never puts a fresh agent behind an old transcript. Headroom can automatically compact a large, quiet conversation while its agent is still live and the backend's prompt cache is warm. It skips conversations with no recorded size and completed Tickets. Hermes does not report a usable current size, so its conversations are skipped. Older conversations need a new measured turn before they qualify. A message sent in that time goes straight in. It waits only if that compaction is already under way. Panels' own upkeep, that compaction and a Worker's memory-loss note, is not work, so it never schedules another round. A Worker's conversation gets upkeep only while it is its Ticket's current one and the Ticket is not in the backlog.

Held messages live in memory, so a restart loses anything not yet delivered.

A save that fails for a passing reason, such as contention, retries until it succeeds. A save that cannot succeed gives up after a few tries. Its conversation then stops its agent, ends the turn as failed, and refuses new messages until a restart. A prompt the agent may have is recorded as uncertain, so nothing resends it. A corrupt database stops all new work. Both failures raise an alarm.

New conversation ends the running turn, discards held work, and links a fresh conversation. The old notebook stays as history, and only the active conversation accepts sends.

After a deploy or ordinary crash interrupts a session, an eligible conversation gets one inspection prompt through the ordinary send path. Headroom never replays uncertain actions. Recovery requires proof that the exact previous child stopped. The home records its identity before dispatch and checks its process birth, host boot, and supervision records. Older unit-backed children retain exact-unit checks. If a machine is unavailable, recovery retries with a capped delay. An update can start between attempts, and the next startup resumes any remaining recovery.

Missing evidence, a live child, or a failed inspection keeps the conversation fenced. An unresolved owner never counts as an idle workspace. A cleanup recheck proves ownership without a new start. Restart checks remote cleanup again and keeps delivery fenced until the old conversation is unlinked. Local retirement and remote retirement are separate proofs. An unknown delivery remains unknown. The next ordinary send can resume eligible held messages.

Codex, Hermes, and Claude save the exact process identity as soon as native spawn returns, before protocol initialization. Stop can then end that recorded process after a home crash. If identity inspection or the durable write fails, startup must stop its owned process. Failed cleanup keeps that handle and blocks a second agent.

A crash before the first durable identity write can leave only a pending startup record. Stop preserves the fence unless supervision proves cleanup or the recorded host boot ended. If no identity or supervision evidence exists, reboot the same host, reopen Headroom, select Stop, then send again. An application restart alone does not prove that the old process ended.

A pre-upgrade in-flight turn without a child ownership record keeps the existing restart rule. An Update Now capture still supplies its recovery prompt. The upgrade adds no inspection, fence, or repair requirement for that legacy turn. Every child launched by this version records ownership before it starts, so subsequent interruptions use the exact-source proof above. Stop, New Conversation, and Restart Worker can stop that exact recorded source on Linux. Stop cancels preparation only after proof that its process ended or its supervised launch never started. On macOS, an agent that survives an application crash can require an operator to stop its recorded process. “Recheck Worker cleanup” inspects the exact Ticket source again without a new start. The action also remains available after a Ticket ends.

A restart of the Mac's executor process interrupts its turns the same way: once the Mac has sent everything it held for a conversation, that turn ends interrupted and gets the same one recovery prompt, which resumes the session in a fresh agent. A message the old process never fetched goes to the fresh agent instead, and nothing is interrupted. A steer the Mac does not take in time, or that later proves it never reached its turn, waits in the queue as the next message.

## Worker processes

One subsystem supervises Claude, Codex, and Hermes Workers on the home and on executors. It lowers process priority, records exact ownership, bounds shutdown, and reconciles records after restart. A shared observer checks local Workers together, so more Workers do not each require a full inventory of the host. It saves new ownership evidence. Concurrent stops share fresh checks and use temporary cleanup threads, so other background work can continue. On supported Linux hosts, each Worker enters its own delegated cgroup before backend execution. Stop kills the group and requires proof that it is empty. Session changes and parent exit do not escape this boundary. Each Worker gets private tmux and screen socket directories, so their default servers stay with that Worker. Requests to preexisting external daemons are outside descendant ownership. This is cleanup custody, not a security sandbox. If cgroup support is unavailable, launch continues, but cleanup retains its fence and reports the reason. On Linux, older Ticket Worker records without group custody cannot prove cleanup from root process exit alone; a new host boot can prove those processes ended. Unknown cleanup blocks replacement and folder removal. On macOS, the owned transport stops the ordinary root. Signals to recorded survivors require an immediate PID and microsecond birth check. A small check-to-signal race remains. Unknown cleanup retains its fence.

Workers on Linux share the home's resource domain. They do not inherit its protected OOM score: each Worker raises its own score before the backend starts. Tools inherit the Worker score. Separate Worker units, aggregate CPU weight, and the tool-first OOM hook no longer apply. The [operator inputs](../ops/panels-environments/README.md#worker-processes) describe the inherited service properties.

Linux can adopt a late tool from the root's session after the leader exits. This assumes that the whole session did not end before the root PID served a new session leader that forked and exited. That unlikely reuse sequence can make a stranger appear to belong to the old session. Each signal still uses that process's own pidfd.

`WorkerScratch` supplies temporary disk storage separately. The directory survives across turns and closes after confirmed cleanup. Preparation and removal run on awaited worker threads. Cancellation waits for physical removal, and concurrent Stop and natural exit share that removal. Account and login probes retain their separate lifetime.

## Models and asks

A model or reasoning change rides the next message that is actually delivered. Codex and Hermes switch in place. Claude switches effort in place. A new Claude model, or cleared effort, starts a new child and carries the session across. The backend itself is fixed once the conversation exists, so a message to the Chief or a Manager that names only a backend, as the iOS app does with every message, is an ordinary message and is not checked against launch defaults. Only a message that starts a conversation resolves its launch settings, and a start on another backend must name that backend's model. The picker reads the model list from the machine that runs the conversation and shows only the newest version of each Claude and Codex model. A model is refused when a Ticket is created or saved, when a Worker restarts on it, when launch defaults are set, and when a message is dispatched. It is refused if it is disabled in the [Usage](usage.md) catalog. A Codex or Claude model is also refused if the machine that runs it does not offer it. That machine is a Ticket's own, and the VPS for everything else. The refusal names the valid choices. Claude's short names, such as `opus`, and the `[1m]` form of a listed model count as offered. A refused dispatch stays in the notebook with its reason. A Worker step refused this way leaves the Ticket errored, and a restart on another model is the repair. A Ticket's saved starting choice belongs to [Tickets](tickets.md). If that saved choice fails to update after delivery, the accepted turn continues and the error still reaches the caller.

Permission asks are always shown and always wait for an answer. Headroom does not auto-approve and does not remember a grant. Claude and Codex can also ask structured questions. Hermes has only the permission flow. Production agents run with full tool access, so an ask appears because the agent chose to ask.

## The workspace for an agent

Each conversation starts in one folder and keeps it through resume and restart. A Worker starts in the Ticket's managed workspace. Panels creates its worktree from the project repository on the execution machine. A project without a repository gets an ordinary Ticket workspace. Workers prepare dependencies and local configuration themselves. Existing native sessions keep their actual folder on resume. New managed sessions reuse the attached local checkout, including unpublished changes.

The worktree and its sibling artifacts folder follow the lifetime in [Environments](environments.md#ticket-scratch). The Chief and each Manager instead start in a home folder of their own, made on their first start: `data/files/chief/` for the Chief, and the Manager's files folder for a Manager, beside the `artifacts/` the owner sees. They keep their own notes, scratch files and rules there, and their role text names the server's default workspace, where the repositories are. A new configuration applies to new conversations. [Environments](environments.md) covers the server default.

## What comes back out

The owner can request a fresh recap from any agent. Chief can request one from any Manager or Ticket. A Manager can request one from its own Tickets. A Ticket cannot request one. The request clears the previous recap and sends the standard Panels instruction with the requester identified.

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

_Last verified: 2026-11-16._
