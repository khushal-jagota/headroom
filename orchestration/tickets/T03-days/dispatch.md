# T03 dispatch brief (per-ticket orchestrator)

Follow orchestration/orchestrator-playbook.md exactly. Your ticket: orchestration/tickets/T03-days/ticket.md.

Model tiers (D8): planner model "opus"; implementer model "opus".

Ticket-specific guidance:
- Planning-date math (§6.1) is spot-checked by the top-level orchestrator afterwards: keep it one tiny pure function; item 1 asserts the exact 04:59/05:00 boundary values from config.
- The boundary job's once-per-date guard is the boundary_runs table; item 18's "second tick same date does nothing" must assert no new day events and no second judgment call (fake adapter call count).
- "Explicit prior planning skips the judgment pass" = any day-ticket exists OR an accepted plan node exists before the pass.
- Plan-tree ops are pure transforms returning (new_tree, effects) — effects like "add ticket t_x to day list" applied by data.py; item 17 asserts accept-all adds child tickets exactly once (idempotent against already-present tickets).
- Codex reviews must specifically cover: the planning-date boundary arithmetic (off-by-one at exactly 05:00), position re-packing after removal, and replan request semantics (root vs child).
