# T04 dispatch brief (per-ticket orchestrator)

Follow orchestration/orchestrator-playbook.md exactly. Your ticket: orchestration/tickets/T04-tickets-engine/ticket.md.

Model tiers for your sub-agents (D8): planner model "fable" — this is the load-bearing resolution engine, the single door to canonical values; implementer model "opus".

Ticket-specific guidance:
- SPEC §4.4's seven numbered semantics are the contract; your plan must map each to a function and each acceptance item (2,3,4,5,6,7,8,13,36) to its test's exact assertions before implementation starts.
- One canonical writer per transition edge is an audit target: the plan must name the single function for every edge (auto-advance, human accept, needs_review approve, human jump, drop) and show no second path.
- The engine's decision logic must be pure (stdlib+contracts); data.py applies decisions transactionally and writes events. Item tests drive through data.py against a temp DB (shared conftest fixture `tmp_db`; fixture `fake_clock` for time).
- Ceiling values are STATE_ORDER members only (plan.md amendment 1 of T01).
- Events: exact kinds/payloads from core/contracts.py EventKind comments (state_changed {from,to,cause}; proposal_accepted {field, body, resolved_by, edited}; proposal_superseded {field, replaced_body}).
- Codex reviews must specifically cover: §4.4.7 grant-pair semantics, the in_progress result routing special case, supersede-then-accept ordering, and recap gating.
