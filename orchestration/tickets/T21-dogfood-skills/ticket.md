# T21 — Dogfood Level A script (item 35) + the four skills documents (stage 7)

## Scope

1. `scripts/dogfood_cli.py` — the item-35 scripted walkthrough against a test server seeded with `plan seed --demo`: agent actions through the real CLI (subprocess), human actions through the HTTP API (the CLI has no human verbs). Steps, in order, each asserted via `--json`/response output, exiting non-zero on first mismatch: create a ticket; propose and accept through EVERY gate with every accept carrying its grant pair — including one edit-accept and one superseded proposal; write then overwrite the recap; assign to the day and remove; create a blocking link, confirm dispatch-ineligibility (pickup queue), complete the blocker, confirm eligibility; claim via `POST /api/test/tick-dispatcher` (fake spawn); propose result WITH claim env; approve from needs_review to done. Print a step-by-step PASS trace.
2. `tests/e2e/test_dogfood.py` — `test_e35_dogfood_level_a`: boots a fresh test server (T18 fixtures), runs `plan seed --demo` via CLI, then `scripts/dogfood_cli.py` as a subprocess pointed at it; asserts exit 0 (stdout on failure).
3. `skills/planning-worker.md`, `skills/planning-boundary.md`, `skills/planner-main.md`, `skills/planning-executor.md` — each ≤150 lines, written FOR the agent that loads it, per SPEC §17's stated content (worker: orient with `plan ticket show --json`, propose via stdin, recap discipline, heartbeats, never ask questions; boundary: the judgment-pass contract — inputs it gets, {brief_markdown, plan_tree} it returns; planner-main: operating queues and day plan via CLI + quick capture to P3 tickets/ideas immediately; executor: working an owned in_progress ticket + the needs_review Codex-review discipline against result + review notes). Ground every CLI example in the real verb surface (run them against a test server first).

## Acceptance for integration

`./verify` flips item 35 to PASS; skills read true against the real CLI (spot-check commands actually work); each file ≤150 lines.
