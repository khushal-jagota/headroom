# The command line

`panels` is Headroom’s record, reached over HTTP. The browser is the main surface for Khushal. The command is how a Worker acts, how a Manager acts from its own process, and how an operator runs the host. Adding `--json` returns the machine-readable body the route already has.

The command name does not grant authority. The process environment says who is calling (`PLAN_ACTOR`, `PLAN_TICKET_ID`, `PLAN_MANAGER_ID`, and the related keys). The server admits or refuses that principal with the same rule as the browser. See [Who may act](authority.md).

## Two kinds of command

Ordinary help shows the roots a person or a Manager uses:

- `send-message` delivers text into an existing conversation. This is the only way prose reaches an agent or reaches Khushal from an agent.
- `project` reads and edits the catalog.
- `ticket` reads and edits Tickets, including backlog, resume, assignment, locale moves, proposal accept and revise, delete, and restart-worker.
- `manager` reads and edits Managers, including goal, recap, and, for the owner, settlement.

Workers use a separate `worker` group that ordinary help hides: show my Ticket, file a proposal, set a recap, request help. Those commands read the Worker identity from the environment. Proposal bodies come from stdin. A Worker can file. A Worker cannot accept. Acceptance is `ticket proposal` for a principal who stands above the Ticket.

Other groups stay hidden until a skill or an operator needs them: `serve`, `skill`, `worker-type`, `schedule`, `feedback`, `preview`, `environment`, and maintenance. A specialist skill names the one it is allowed to use. On the production host, `~/.local/bin/panels` follows the deployed app, not whatever checkout happens to be on `PATH`.

## Reads and the writes people repeat

`ticket show`, `manager show`, and `project show` return a header and a manifest of parts. Name the parts you want expanded. `ticket show` with no id, inside a Worker, reads that Worker’s Ticket through the self route, including the Worker type and specialist. Neither form returns the conversation. `ticket history` is the conversation record when the record is what you need.

List commands are pages of 30, and each page says what it omitted. Ticket lists can filter on backlog and Worker type. Completed Tickets need `--include-terminal`. Backlog and resume accept a hash of the record that was shown, and a stale hash writes nothing.

`ticket create` sends one JSON object through the ordinary create route. The server applies defaults and validates the whole Ticket before one write. Skill edits and applies are the same writers as Settings. See [Skills](skills.md).

## What the CLI feeds

A Worker’s `propose` feeds the proposal resolver. `send-message` feeds a conversation. Operator commands feed the host: backup, restore, environment prepare, preview register, diagnostic history. None of these are a side channel into a model. If an agent should know something, the text has to be in a prompt the conversation actually sent.

## Where it shows up

Workers see the CLI as the tool inside their skill. Khushal sees it for creation, repair, and host operations the browser does not offer, including delete and the maintenance commands. The browser remains the place Review, the rail, and the conversation pane live.

## Pointers

`src/planner/cli/main.py` is the tree. HTTP handlers under each domain are the writers the commands call.

_Last verified: 2026-10-30._
