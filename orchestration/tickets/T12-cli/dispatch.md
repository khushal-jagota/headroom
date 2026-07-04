# T12 dispatch brief (per-ticket orchestrator)

Follow orchestration/orchestrator-playbook.md. Ticket: orchestration/tickets/T12-cli/ticket.md.

Model tiers: planner "opus", implementer "opus" (the verb tree and exit-code contract are already fixed; this is faithful wiring).

Guidance:
- The routes are live by the time you run (T10/T13 integrated); read the api.py files for exact paths/bodies rather than guessing.
- Exit-code contract is absolute: 0 success / 1 structured error / 2 connection failure — test all three in your smoke.
- e2e items 22/23/27/30/32/33/34 will drive this CLI via subprocess with env pinned; keep startup light (no server imports — contracts + click + httpx only, per decisions.md D4).
- Codex review focus: §8 conformance (no resolution verbs anywhere, body-input rules, env defaults incl. PLAN_TICKET_ID fallback), error passthrough fidelity (never swallowing the structured error), `plan seed` report rendering.
