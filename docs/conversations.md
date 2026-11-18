# Conversations

A conversation is one agent, working in one folder, with a permanent record of what was actually delivered to it and what it did. Every Ticket Worker, the Chief, and every Manager has one, and each stores its own conversation id. A person typing, an automatic Worker step, a Manager wake, and a recovery notice after a deploy all enter that same conversation. There is no second input path: an edited Ticket field or a parked proposal reaches an agent only when a later prompt carries it.

## What a conversation is made of

- **The notebook.** The append-only record of finished facts: a delivered prompt, an agent's finished reply, a tool call, a permission ask and its answer, a model change, a turn ending, a message to the owner, a refusal, a discard, and a backend's own reply to a slash command such as `/goal`. A row is written once. Private reasoning is dropped. A finished reply shows in the full conversation only. It is not addressed to anyone, so it raises no unread mark or notification.
- **The live tail.** The text of a running turn, streamed straight to an open pane. When that text finishes, the notebook records it as a reply and the tail clears.
- **The conversation system.** One core that holds messages while the agent is busy, starts and stops the child process, writes the notebook, and decides each send's fate.
- **Three backend adapters.** Codex through its app server, Claude through the Claude Agent SDK, and Antigravity, Google's `agy` CLI, over the Agent Client Protocol (ACP) through a shared ACP layer. The backends are a closed set, and one door turns an untrusted name into a member of it or refuses it.
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
   adapter (Codex · Claude · Antigravity)
               │
               ▼
   child process on the VPS, or on the Mac through the executor
```
The backend's own session id is internal and can be rebound without anything outside the system noticing.

Each backend is a driver package under `server/drivers/`, holding the adapter, its fixed facts (executable, home folder, launcher variables, CLI version floor, install files, how its card is probed), its model rules, and the label and icon the apps will show. At startup Panels builds a registry with one instance per driver, and the conversation core, the backend cards, sign-in and usage ask the registry rather than a fixed list. Each instance owns its own card probe, sign-in, usage readers and cleanup. A driver that fails to start, has invalid configuration, or is unknown becomes an unavailable entry with a reason, and nothing falls back to another backend. Startup still refuses to run while any entry is unavailable. One line in `server/drivers/built_in.ts` brings a driver in; the installer reads the same list, because a driver loads its adapter only when it starts. The web and iPhone apps name no backend. They read each driver's label and icon from `GET /api/drivers`, the web also its update flag, and each backend card says whether Panels can sign it in and whether it reports allowance usage. A backend they do not recognise shows its name and its initials. The iPhone keeps its bundled marks for Claude and Codex, and for Hermes so that saved Hermes conversations keep their mark. It shows another driver's icon only if it is a PNG, because it cannot draw SVG. A driver should therefore ship a PNG icon. A check in CI keeps backend names out of code outside their own package; each place that still names one is listed with the stage that removes it.

Hermes is not a backend now. A saved Hermes conversation still opens with its whole record, under its name and initials, and a message to it, or any launch whose saved settings name Hermes, is refused by name and starts nothing; a Ticket saved on Hermes waits and says why until it is restarted on another backend.

Every adapter keeps the same event rules, written in the backend contract. Each event inside a turn names its turn, and each accepted prompt ends in exactly one turn ending. Streamed text and tool progress go to the live tail and are never stored. A turn ending says whether the child can take another prompt. An ask is answered only through the child that raised it. A tool call names one of the standard ACP tool kinds and keeps the tool's own name as its title. A replay suite runs the Codex and Claude adapters against recorded wire traffic and checks these rules.

Panels gives a local agent a private launch credential for its current conversation. It enters only the child’s environment, after saved launch settings have been written. The database keeps its hash, never the token. A new child receives a fresh credential from its canonical association, including after a restart. Reset revokes the old conversation’s authority while preserving its notebook. Generic conversation starts cannot choose a privileged identity. Remote children retain the executor’s existing per-child capability; a local token never enters an executor command.

Claude and Codex run from owner-maintained CLI installations outside the app. Settings → Backends shows their installed and latest versions on the VPS. Only the owner can update them there. Antigravity runs the host's own `agy`, version 1.5.1 or later, with its normal CLI login under `~/.gemini`; Panels never updates it. Updates change the selected user-local installation, and new agent processes use that version. A failed lookup of newer releases does not stop ordinary conversations. Before each backend starts, it checks its selected CLI. Each version verdict is remembered for the exact installed file, so a start reads the version again only after the CLI changes. It also checks the version the running CLI reports when it connects. If that CLI lacks a required feature, the backend names the required update, and a turn is refused before any launch is recorded. The adapter reads only the protocol fields it needs and ignores unrelated additions. The feature minimums and their reasons live in each driver's manifest and in `server/conversation/backends/cli_compatibility.ts`. Mac CLI updates stay local to the Mac.

## How a send works

A busy agent is not a refusal. The sender picks how a busy agent meets the message:

- **Steer**, the default for messages from people and Panels system prompts, injects text into the running turn when the backend's driver says it can steer an active turn. Antigravity cannot, so a message sent during its turn waits for the turn to end.
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

An executor agent can keep its process, turn, open asks and capability across a home restart. An idle child is stopped, and the next send resumes the stored session, or is refused if the backend cannot restore it. A local child whose process or wire ended during a turn says so at the turn's end; the core holds new messages, stops that child, and the next message starts a fresh one. A Codex child that died while idle is replaced by the next send. A refusal or failed turn caused by a backend keeps the backend's own error text, with secrets removed, in the notebook and in one log line.

A Mac Claude start that times out during initialization can recover before the waiting message is delivered. The [executor](executor.md#commands-and-output) keeps the message in order and only replaces a child after proving cleanup. This does not retry a message that may have reached an agent.

A Claude agent can start a background task, such as a long test run, that outlives its turn. Headroom keeps the conversation's record equal to the set of live tasks Claude reports after every change; a ScheduleWakeup timer is not a task. While a task is live the child is not idle, the conversation shows as working, and after the turn's last row it names each live task until that task ends. Stop and the composer still follow the turn alone. If the child goes away first, because it was stopped, died, or the server restarted, its tasks die with it, and Headroom sends one prompt per lost task asking the agent to read its output and restart it or report. A killed conversation drops its tasks without a prompt. Headroom never puts a fresh agent behind an old transcript.

Headroom can automatically compact a large, quiet conversation while its agent is still live and the backend's prompt cache is warm. It skips completed Tickets and conversations with no measured size. A message sent meanwhile goes straight in unless compaction is already under way. That compaction and a Worker's memory-loss note are upkeep, not work, so they never schedule another round. A Worker's conversation gets upkeep only while it is its Ticket's current one and the Ticket is not in the backlog.

Held messages live in memory, so a restart loses anything not yet delivered.

A save that fails for a passing reason, such as contention, retries until it succeeds. A save that cannot succeed gives up after a few tries. Its conversation then stops its agent, ends the turn as failed, and refuses new messages until a restart. A prompt the agent may have is recorded as uncertain, so nothing resends it. A corrupt database stops all new work. Both failures raise an alarm.

New conversation ends the running turn, discards held work, and links a fresh conversation. The old notebook stays as history, and only the active conversation accepts sends.

After a deploy or ordinary crash interrupts a session, an eligible conversation gets one inspection prompt through the ordinary send path. Headroom never replays uncertain actions. Recovery requires proof that the exact previous child stopped: the home records its identity before dispatch and checks its process birth, host boot, and supervision record. If a machine is unavailable, recovery retries with a capped delay, and the next startup resumes whatever remains.

Missing evidence, a live child, or a failed inspection keeps the conversation fenced, and an unresolved owner never counts as an idle workspace. Restart checks remote cleanup again and keeps delivery fenced until the old conversation is unlinked; local and remote retirement are separate proofs. An unknown delivery remains unknown. The next ordinary send can resume eligible held messages.

Codex and Claude save the exact process identity as soon as native spawn returns, before protocol initialization, so Stop can end that process after a home crash. If that write fails, startup stops its own process, and failed cleanup keeps the handle and blocks a second agent.

On the home, each backend saves a supervision record before it spawns an agent, and the core checks for it: a child that started without one is stopped at once and its prompt refused. So a crash before the first identity write leaves a pending startup record that, if it has no supervision record, proves the agent never started, and Stop clears it. Pending records from before this rule have no such proof. Stop keeps their fence unless supervision proves cleanup or the host has rebooted since; with no identity or supervision evidence, reboot the same host, reopen Headroom, select Stop, then send again. An application restart alone does not prove that the old process ended.

A turn left in flight from before child ownership records existed keeps the old restart rule, and an update's capture still supplies its recovery prompt; that legacy turn gets no new inspection, fence, or repair requirement. Every child this version launches records ownership before it starts, so later interruptions use the exact-source proof above. Stop, New Conversation, and Restart Worker stop the exact recorded process on Linux. Stop cancels preparation only after proof that its process ended or its supervised launch never started. On macOS, an agent that survives an application crash can require an operator to stop its recorded process. "Recheck Worker cleanup" inspects the exact Ticket source again without a new start, even after the Ticket ends.

A restart of the Mac's executor process interrupts its turns the same way: once the Mac has sent everything it held for a conversation, that turn ends interrupted and gets the same one recovery prompt, which resumes the session in a fresh agent. A message the old process never fetched goes to the fresh agent instead, and nothing is interrupted. A steer the Mac does not take in time, or that later proves it never reached its turn, waits in the queue as the next message.

## Worker processes

One subsystem supervises every local agent process on the home, whichever role it serves: Workers, the Chief, Managers, and generic and test conversations. Executors supervise their Workers the same way. Supervision records exact ownership before the backend starts, bounds shutdown, and reconciles records after restart. A Worker's record belongs to its Ticket, so a new Worker stops that Ticket's previous one. Every other record belongs to its conversation, so a relaunch stops only that conversation's previous process, and a Chief or Manager can neither stop nor be stopped by a Worker's record. When a new launch scans the records, the home deletes finished ones that no conversation still names; executors delete none. One shared observer checks local processes together. It saves new ownership evidence and drops processes that the cleanup check already counts as ended, so a long-running agent's record stays small. Concurrent stops share fresh checks without holding up other background work.

On Linux, each supervised process gets its own session and, on hosts with cgroup delegation, its own cgroup before the backend runs. Stop kills the group and requires proof that it is empty, and neither session changes nor parent exit escape it. Requests to preexisting external daemons are outside this boundary: this is cleanup custody, not a security sandbox. Without cgroup support, launch continues, but stopping any supervised conversation keeps its fence when cleanup cannot be proven. Older Worker records without group custody can prove cleanup only through a new host boot. Unknown cleanup blocks replacement and folder removal. On macOS, the owned transport stops the ordinary root, and a signal to a recorded survivor first rechecks its PID and birth time; a small check-to-signal race remains. A late tool adopted from a Linux root's session carries a similar, unlikely PID-reuse risk, though each signal still goes through that process's own pidfd.

Only Workers give way. Each runs at nice 10 and, on Linux, raises its OOM score to at least 0 before the backend starts, so the protected home outlasts it under memory pressure; its tools inherit that score. The Chief, Managers, and other conversations keep the home's settings. Everything shares the home's resource domain, with no separate Worker units. The [operator inputs](../ops/panels-environments/README.md#worker-processes) describe the inherited service properties.

Workers alone also get `WorkerScratch`: private tmux and screen socket directories, so their default servers stay with that Worker, and scratch disk storage. Scratch survives across turns and closes after confirmed cleanup. Account and login probes keep their own lifetime.

## Models and asks

A model or reasoning change rides the next message that is actually delivered. Each driver states whether its running child applies the change on the next turn, needs a fresh child, or refuses it. Codex switches in place. Claude switches effort in place. A new Claude model, or cleared effort, starts a new child and carries the session across. Antigravity switches model and thinking level in place, choosing from agy's own lists. The backend itself is fixed once the conversation exists, so a message to the Chief or a Manager that names only a backend, as the iOS app does with every message, is an ordinary message and is not checked against launch defaults. Only a message that starts a conversation resolves its launch settings, and a start on another backend must name that backend's model. The picker reads the model list from the machine that runs the conversation and shows only the newest version of each Claude and Codex model. A model is refused when a Ticket is created or saved, when a Worker restarts on it, when launch defaults are set, and when a message is dispatched. It is refused if it is disabled in the [Usage](usage.md) catalog. A Codex or Claude model is also refused if the machine that runs it does not offer it. That machine is a Ticket's own, and the VPS for everything else. The refusal names the valid choices. A refused dispatch stays in the notebook with its reason. A Worker step refused this way leaves the Ticket errored, and a restart on another model is the repair. A Ticket's saved starting choice belongs to [Tickets](tickets.md).

Permission asks are always shown and always wait for an answer. Headroom does not auto-approve and does not remember a grant. Claude and Codex can also ask structured questions. Production agents run with full tool access, so an ask appears because the agent chose to ask.

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

- `server/conversation/`: the contract (`contracts.ts`), the core (`system.ts`), the notebook store (`record/`), the stored views and their rebuild (`views/data/rebuild.ts`), the live tail, attachments and video uploads, and deploy recovery. Each backend's adapter is a driver under `server/drivers/`; `server/drivers/acp/` is the shared ACP layer Antigravity runs on.
- `server/message_delivery/`: Send Message.
- `server/runtime/conversation_start.ts`: starting a conversation and choosing its folder.
- `web/src/components/conversation/` and `web/src/lib/conversation/`: the pane and composer.

_Last verified: 2026-11-18._
