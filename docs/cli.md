# The command line

`panels` is Headroom's record, reached over HTTP. The browser is Khushal's main surface. The command is how every agent reads and changes Panels, and how an operator runs the host. Every command calls the same routes the browser uses, and `--json` returns the route's own body.

Agents use it for three jobs. They talk to someone. They work on their own record: a Worker reads its Ticket, files its proposal, and writes its recap. And the Chief and Managers supervise what is below them: they see what needs whom, decide proposals, and create and steer Tickets. The rest of the tree serves pages and data, owner configuration, and the host.

## Who is calling

The command name grants nothing. Headroom gives each agent conversation a private launch token in its environment. The CLI sends it automatically, and the server resolves the conversation and its current principal. The server admits or refuses that caller with the same rule as the browser. On the live host, a terminal with no identity is unidentified: it can read and send a message to Chief, and nothing else. Owner writes go through the web app or the iOS app. See [Who may act](authority.md).

Role and workspace variables, such as `PLAN_ACTOR`, `PLAN_TICKET_ID`, and `PLAN_MANAGER_ID`, are context, not credentials. The CLI uses them to choose the caller's own record and the caller's help.

If an operation has no dedicated command, `panels api METHOD PATH --input-json FILE --json` calls the route with the same credential. Agents do not copy tokens or construct identity headers. Agent calls pass through machine code and its relay to the same server routes. The relay converts document numbers to JSON numbers before validation and refuses integers that it cannot carry exactly. The executor transport still decides which operations are supported.

## What it is made of

A command is `panels <thing> <action>`. The things are the things in the product: Ticket, Manager, Review, page, goal. No group is named for a role. Each job has one command.

- **For everyone.** `send-message` addresses Khushal, the Chief, a Ticket's Worker, or a Manager through the [conversation](conversations.md) door. It needs exactly one recipient. `recap` writes the recap of whoever calls it: a Worker's Ticket, a Manager, or the Chief.
- **For a Worker, on its own Ticket.** `ticket show` reads the Ticket and its workflow guide. `ticket propose` files the proposal for the current Stage. `preview` publishes a running app; see [previews](previews.md).
- **For the Chief and Managers, on what is below them.** `review` reads the queue that waits on Khushal. `ticket list`, `ticket show`, and `ticket history` read Tickets and what was said. `ticket approve` and `ticket send-back` decide a parked proposal. `ticket create`, `edit`, and `schema` make and correct Tickets. `ticket backlog`, `resume`, `delete`, `block`, `unblock`, `assign`, and `complete` steer them. `ticket restart-worker` and `recheck-worker-cleanup` repair a stalled Worker. `ticket request-recap` and `manager request-recap` ask for a fresh recap. `manager list`, `show`, `create`, `set`, `delete`, `settle`, and `artifact` keep the Managers.
- **Pages and data.** `page` reads and edits Manager and Chief documents. `goal` edits [goal](goals.md) pages. `measure`, `dataset`, and `measure-source` maintain [project measures](measures.md). `credential` lists and requests [credentials](credentials.md). `attention` reads [historical attention](attention.md). `project` and `machine` read and edit the catalog and the named destinations.
- **Owner configuration.** `skill` and `worker-type` reach the owner-only [skill](skills.md) and [Worker type](worker-types.md) writers.
- **The host.** `serve`, `restart`, `environment`, `executor`, and `stalls` run the server, the [Mac executor](executor.md), and [environments](environments.md). Services and installers call them by name. `restart` asks the supervisor, over its local control socket, to replace the application.

## How reads and writes work

**The caller's own record is the default.** A Worker that runs `ticket show` reads its own Ticket, and `ticket propose` files on it. A Manager that runs `manager show` reads itself. A command that changes another record, such as `ticket backlog`, `resume`, or `delete`, always names it.

**Every Ticket carries one need word.** It says what the Ticket waits on, and from whom: Khushal to approve, answer, take his stage, or reply; an agent holder to approve; or working, ready, blocked, errored, backlog, or done. It comes from the grouping the browser shows the Ticket under, so an agent and Khushal read the same state. Backlog and done win over every other fact, so a deferred or finished Ticket never reads as waiting on anyone. Human output shows the waits as `Khushal: approve`, `Chief: approve`, or `Manager: approve`. `ticket list --needs` filters on the word. A Manager's counts use the same words and keep backlog and done in their own buckets.

**Lists show live work by default and say what they left out.** `ticket list` shows Tickets that are neither backlogged nor done, ordered by need and then by recent activity. `--backlog`, `--done`, or `--all` widen it, and its footer counts what it did not show. `manager list` shows active Managers and counts the settled ones it left out. Ticket and Manager lists include each recap and its age.

**Review is one read.** `review` returns the cards that wait on Khushal, oldest first, from the same source as the browser and iPhone [Review](review.md). Each card says why it waits: a proposal, a message, or a question. If the source is not yet trustworthy, the command says Review is unavailable rather than showing an empty queue.

**A show is a header and parts.** The caller names the parts to expand. The Ticket header leads with its need, Stage, ceiling, holder, and active blockers. `ticket history` prints the conversation's messages as text, oldest first, and counts other events. `manager show` gives the purpose, the recap, the held goals, the count of Tickets by need, and which document the browser displays with its read and edit commands.

**Decisions pin what was read.** `ticket approve` needs the new ceiling, and `--holder` names who supervises next: `owner`, `chief`, or a Manager. Without it, the holder is the caller. `ticket send-back` carries the guidance into the Worker's prompt. A separate message does not replace that guidance. Both pin the proposal revision from their own read, or from `--expected-proposal-revision` for a proposal the caller inspected earlier.

**Creation validates the whole Ticket before one write.** `ticket create` sends one object to the same route as the browser. `ticket schema create` and `ticket schema edit` print the keys those objects accept, from the list the server validates with. A new Ticket starts from a preset or custom stages. `machine_id` chooses its destination from `machine list`, and omission selects the home. A machine rename never moves work. The owner can use `machine move-work MACHINE_ID` with `--ticket ID`, `--manager ID` or `--chief` for an explicit move. After a server move, `--everything` moves unfinished Tickets, active Managers and the Chief to the server's new machine. Blockers go in the object or later through `ticket block`. A running Ticket needs `--force` to delete.

## One way to pass input

Text comes from stdin, from `--message TEXT`, or from `--body-file FILE`, where `-` also means stdin. The three `set`-style commands take `--value` instead of `--message`, because the text is a field value. JSON objects come from stdin or from `--input-json FILE`. `--json` only selects JSON output. More than one source, or empty text, is an error. A command that needs input never waits on a terminal: it fails and names the three ways to supply it.

## Help for the caller

Every command declares who it is for: a Worker on its own Ticket, a supervisor, everyone, data, configuration, or the host. `panels --help` in an agent conversation prints a short card for that role, grouped by job, and names the other groups. With no agent role it prints every group. Each description is one line that says what the command does. `api` stays hidden.

## Removed names fail with their replacement

A conversation that started before a change still holds old skill text. So a removed command or option is not kept as an alias. It fails with one line that names its replacement, such as `Use: panels ticket propose < PROPOSAL_FILE`. The replacements come from one stated table of removed names. A wrong name that is not in the table lists the commands of its group, and may ask `Did you mean …?`. A guess by spelling is never printed as the replacement.

`panels skill check` reads the same command tree that the CLI declares, so a skill that names a removed command is reported.

A machine updates after the server, so for a time an older command line calls a newer server. The Ticket list route keeps the parameters that older command lines and the iPhone app send. For the minutes of an install, a newer command line can call an older server. A read that needs the newer server then refuses and says to run it again, rather than print an answer without the need word.

## What the CLI feeds

A Worker's proposal feeds the [proposal resolver](tickets.md#how-a-stage-advances). `send-message` feeds a conversation. Host commands feed the host. None of these are a side channel into a model: an agent learns something only when a prompt its conversation actually sent carries it.

## Decisions that shape it

- One set of writers. The CLI adds no rule of its own, so the browser and the command cannot disagree.
- The command line shows what the browser shows: the same need word and the same Review.
- Identity comes from the environment Headroom set, not from a flag the caller chooses.
- Old names fail loudly with their replacement instead of living on as aliases.

## Code

- `server/cli/main.ts`: the entry. `server/cli/commands.ts`: the command tree, with each command's audience. `server/cli/http.ts`: the HTTP client and launch credential.
- `server/navigation/views/logic.ts`: the need word, beside the browser's grouping.
- `server/lifecycle/control.ts`: the control socket and its messages.
- Each domain's `cli/` folder holds its own group, such as `server/credentials/cli/` and `server/goals/cli/`. The routes they call are in each domain's `routes.ts`.
- `server/skills/staleness.ts`: the skill check, which reads `create_commands`.

_Last verified: 2026-11-24._
