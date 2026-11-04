# The command line

`panels` is Headroom's record, reached over HTTP. The browser is Khushal's main surface. The command is how a Worker acts, how the Chief, a Manager, or a goal agent acts from its own process, and how an operator runs the host. Every command calls the same routes the browser uses, and `--json` returns the route's own body.

## Who is calling

The command name grants nothing. The process environment says who is calling: Headroom sets the actor, the Ticket, Manager, or goal id, and the conversation id when it starts an agent. The server admits or refuses that caller with the same rule as the browser. See [Who may act](authority.md).

## What it is made of

- **Ordinary roots**, shown in help, for people and supervising agents:
  - `send-message` addresses Khushal, the Chief, a goal, a Ticket's Worker, or a Manager through the [conversation](conversations.md) door.
  - `project`, `ticket`, and `manager` read and edit the catalog, Tickets, and Managers, including backlog and resume, assignment, accepting or revising a parked proposal, restarting a stalled Worker, and settling a Manager.
  - `goal` edits [goal](goals.md) pages. `measure`, `dataset`, and `measure-source` maintain [project measures](measures.md). `credential` lists and requests [credentials](credentials.md). `attention` reads [historical attention](attention.md).
  - `executor`, `environment`, and `preview` serve the operator: the [Mac executor](executor.md), [environments](environments.md), and [previews](previews.md).
- **The Worker group**, hidden from ordinary help: read my Ticket, file a proposal, set a recap, request help. A Worker can file a proposal. Accepting one is `ticket proposal`, for a caller who stands above the Ticket.
- **Hidden groups** that a specialist skill or an operator names when needed: `serve`, `skill`, `worker-type`, and `feedback`.

## How reads and writes work

A `show` returns a short header and a list of parts, and the caller names the parts to expand. Lists come in pages and say what they left out. Backlog and resume need no record fingerprint. `ticket delete` needs no confirmation flag, but a running Ticket still needs `--force`. `ticket create --input-json FILE` sends one object through the ordinary create route, which validates the whole Ticket before one write.

## What the CLI feeds

A Worker's proposal feeds the [proposal resolver](tickets.md#how-a-stage-advances). `send-message` feeds a conversation. Operator commands feed the host. None of these are a side channel into a model: an agent learns something only when a prompt its conversation actually sent carries it.

## Decisions that shape it

- One set of writers. The CLI adds no rule of its own, so the browser and the command cannot disagree.
- Identity comes from the environment Headroom set, not from a flag the caller chooses.
- Help shows the roots a person or a supervisor needs. Specialist and operator groups stay hidden until a skill names them.

## Code

- `src/planner/cli/main.py`: the command tree. `src/planner/cli/http.py`: the HTTP client and identity headers.
- Each domain's `cli.py` holds its own group, such as `src/planner/credentials/cli.py` and `src/planner/goals/cli.py`. The routes they call are in each domain's `api.py`.

_Last verified: 2026-11-04._
