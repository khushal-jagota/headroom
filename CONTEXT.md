# Planner

Planner coordinates human decisions and AI employee work across tickets, days, and sprints.

## Language

**Worker type**:
The kind of AI employee assigned to work a Ticket, chosen when the Ticket is created and unchanged
afterward. It determines the Ticket's staged workflow and specialist behavior.
_Avoid_: Ticket type, workflow type

**Stage**:
A Ticket's current point in the workflow for its Worker type. The Ticket stores its current Stage
directly; reading it does not require resolving the Worker type.
_Avoid_: State, lifecycle status

**Review**:
The human decision surface for parked Ticket proposals, alongside the current running-worker count.
It does not contain Sprint items or an overdue digest.
_Avoid_: Queues, approval queue

**Conversation**:
One agent process working in a folder, plus the permanent notebook of everything that happened in it.
A Ticket has one; so does the Chief. Everything that talks to that agent — the automatic step and the
person typing — goes into the same one.

**Conversation id**:
What a conversation is called. Its caller owns it: a Ticket keeps its own in `tickets.conversation_id`
and the Chief keeps its own in the `agents` table. It is the only name a conversation has outside the
conversation system. The backend process's own session id is internal to that system, is rebound
without anything outside noticing, and is never the identity.
_Avoid_: employee session id, ACP session id, durable session

**Notebook**:
A conversation's append-only run of numbered rows. A row is a finished thing — a delivered prompt, a
completed agent message, a tool call, a permission ask and its answer, a model change, a turn ending.
A row in it IS what the agent was told, not a record beside it: nothing reaches the agent without
being one.
_Avoid_: chat transcript, Panels Chat, employee session history

**Automatic Employee-step eligibility**:
Whether Planner may automatically start a Ticket's next Employee step now. It includes today's board
membership, the derived Ticket status, the Stage and its ownership, the user-owned-Stage
opener fact, a parked proposal, and the Consequences lane.
_Avoid_: Ticket readiness, runnable Ticket, ready Ticket, System A

**Employee step**:
One employee turn on a ticket, either advancing its next gated field or revising a rejected
proposal.
_Avoid_: System B run

**Ticket edit**:
One request to change one or more mutable attributes of a Ticket. All requested changes succeed or
fail together.
_Avoid_: Series of field updates
