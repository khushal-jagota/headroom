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
The durable product-visible messages and live turn state intended for the human to see. Reading it never
loads or merges Employee session history, and a Panels row alone is not delivery to the employee.
_Avoid_: Employee session history, worker context

**Employee session history**:
The authoritative Hermes record of what was delivered to and produced by a Ticket employee's durable
`employee_session_id`. It is inspected explicitly and may contain internal context absent from Panels Chat.
_Avoid_: Panels Chat, chat transcript

**Employee session id**:
The durable Hermes conversation identity stored by a Ticket. Human Ticket Chat and Employee steps both
deliver through it, and a restart resumes it. Day and top-level-agent Chat keys are separate Chat identity.
_Avoid_: Ticket chat session key

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
