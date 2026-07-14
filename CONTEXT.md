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

**Panels Chat**:
The product-visible conversation with an employee. It stores the messages and live turn state intended
for the human to see; it is not the employee's conversation context.
_Avoid_: Employee session history, worker context

**Employee session history**:
The authoritative record of what was delivered to and produced by an employee's durable conversation.
It is inspected explicitly and is never silently merged into Panels Chat.
_Avoid_: Panels Chat, chat transcript

**Automatic Employee-step eligibility**:
Whether Planner may automatically start a Ticket's next Employee step now. It includes today's board
membership, control status, Stage, parked proposals, scope, and blockers.
_Avoid_: Ticket readiness, runnable Ticket, ready Ticket, System A

**Employee step**:
One employee turn on a ticket, either advancing its next gated field or revising a rejected
proposal.
_Avoid_: System B run

**Ticket edit**:
One request to change one or more mutable attributes of a Ticket. All requested changes succeed or
fail together.
_Avoid_: Series of field updates
