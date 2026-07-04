# T07 dispatch brief (per-ticket orchestrator)

Follow orchestration/orchestrator-playbook.md exactly. Your ticket: orchestration/tickets/T07-seed/ticket.md.

Model tiers (D8): planner model "fable" (parser semantics must hold for both synthetic fixtures and the real snapshot — item 34 later runs the same importer against migration/source-snapshot/ with pinned counts); implementer model "opus".

Ticket-specific guidance:
- You may READ migration/source-snapshot/ to confirm shapes (never modify it). The parser must, when run against it, yield: 1 sprint 2026-07-01→2026-07-12; 12 items (6 todo/5 active/1 done, done = "Ship waitlist mechanics."); 4 tickets from daily/2026-07-03/workspace.md (two needs_plan, two in_progress, Chat ID 20260702_114500_0ec57a on landing-gate-1); 9 deferred (4 Vylo/2 Tribe/2 Learning/1 Other); 20 ideas. Do NOT wire tests to the snapshot (that is e2e item 34, stage 6) — but sanity-run the importer against it yourself during implementation and report the counts.
- Items = bullets under recognized section headings only; preamble/rules text is not an item; every skipped file/section is enumerated in the report (Necessary Calls, tracker.md, overview.md, historical dailies, preambles).
- Idempotency: alias (tickets) and title (others) keyed; re-run → zero duplicates, duplicates_skipped counted.
- Body reconstruction: sub-bullets that aren't recognized fields join into body markdown, preserving order and nesting.
- Codex reviews must specifically cover: the five status mappings, four readiness mappings, title-match linking rule (exact match, unambiguous only), R6 latest-day-only, report completeness (silent drops), and demo dataset determinism + empty-DB guard.
