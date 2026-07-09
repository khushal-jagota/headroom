# Planner

Planner coordinates human decisions and AI employee work across tickets, days, and sprints.

## Language

**Ticket readiness**:
Whether a ticket on today's board is eligible to begin its next employee step. It comes from the
ticket's state, scope, and blockers; the employee does not decide it.
_Avoid_: System A

**Employee step**:
One employee turn on a ticket, either advancing its next gated field or revising a rejected
proposal.
_Avoid_: System B run

**Ticket edit**:
One request to change one or more mutable attributes of a Ticket. All requested changes succeed or
fail together.
_Avoid_: Series of field updates
