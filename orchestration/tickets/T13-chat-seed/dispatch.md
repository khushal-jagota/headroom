# T13 dispatch brief (per-ticket orchestrator)

Follow orchestration/orchestrator-playbook.md. Ticket: orchestration/tickets/T13-chat-seed/ticket.md.

Model tiers: planner "opus", implementer "opus" (small, well-bounded ticket).

Guidance:
- Gateway adapter protocol + fakes are in core/adapters/; select via registry. Session key persistence is per-entity (tickets and days both carry chat_session_key).
- The seed endpoint calls seed.importer/demo directly against the server's DB connection factory; the CLI (T12) will drive it. Report shape must serialize exactly the MigrationReport contract (dataclasses → JSON).
- Codex review focus: gateway-offline error path (503 + panel-consumable shape), db_not_empty guard, no path escapes outside the given source_dir semantics.
