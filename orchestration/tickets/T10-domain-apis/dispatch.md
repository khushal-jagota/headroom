# T10 dispatch brief (per-ticket orchestrator)

Follow orchestration/orchestrator-playbook.md. Ticket: orchestration/tickets/T10-domain-apis/ticket.md.

Model tiers: planner "fable" (route-to-writer mapping across four domains; §7.6 actor plumbing; derived-view ordering rules), implementer "opus".

Guidance:
- The stage-3 data layers and views are law — your planner must read tickets/data.py, sprints/data.py, days/data.py, dispatch/data.py, core/links.py signatures BEFORE writing the plan; routes adapt HTTP to those calls, never re-implement rules.
- authctx API is pinned in T09's ticket seam section; consume exactly that.
- Queues ordering: approvals oldest-pending-first (§4.5); pickup uses dispatch ordering (§7.2); overdue = deadline < current planning date and not done/dropped.
- Codex reviews must specifically cover: (H)-route agent rejection completeness, grant-pair passthrough (no route-level defaults!), day `today` resolution, board card fields per §10.3, one-writer-per-edge preservation (no state mutation outside data layers).
- Self-smoke via a booted test-mode server on a temp DB (T09 shell is integrated by the time you implement).
