# T06 dispatch brief (per-ticket orchestrator)

Follow orchestration/orchestrator-playbook.md exactly. Your ticket: orchestration/tickets/T06-sprints/ticket.md.

Model tiers (D8): planner model "opus"; implementer model "opus".

Ticket-specific guidance:
- Item accepts carry NO onward grant (§4.4.7 last sentence) — the accept-status writer takes no grant arguments at all.
- Freeze semantics: rejection is the structured frozen_write error; weekly_addenda append works after kickoff freeze; review freeze is an independent flag.
- Overlap check is inclusive-range intersection against ALL sprints at create time.
- blockers_cleared is computed on read from ticket states of blocked_by ids — never stored.
- Codex reviews must specifically cover: agent-permitted transition set exactness (§3.2), proposal-only statuses, and that item→sprint assignment is a plain event-logged field update (no copy).
