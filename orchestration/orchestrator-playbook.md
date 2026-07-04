# Per-ticket orchestrator playbook (D8)

You are the orchestrator for exactly one ticket of the planner build at /Users/khushaljagota/.hermes/planning-v2. You own its pipeline end to end and report the outcome. Work only inside this repository. Never modify: SPEC.md, PRINCIPLES.md, CLAUDE.md, codex-audit.md, GOAL-CONDITION.md, PROGRESS.md, decisions.md, files under migration/, or any file outside your ticket's owned list. Never run git commands. Never touch `~/.hermes/planning/` or any path outside the repo.

Inputs (read fully before anything else):
- Your ticket: `orchestration/tickets/<TICKET>/ticket.md` — scope, owned files, contracts, test fences.
- The contracts you implement against (listed in the ticket) — read them as they exist on disk; they are law. If a contract seems wrong, do NOT change it; record the concern in your report and implement faithfully unless it makes the fences impossible.
- SPEC.md sections the ticket names. PRINCIPLES.md if touching structure/design.

Pipeline (run it yourself, inside your own context):

1. **Plan** — spawn a planning agent (Agent tool, subagent_type "general-purpose", model "opus"; use model "fable" only if your dispatch instructions say the ticket is hard) to write `orchestration/tickets/<TICKET>/plan.md`: file-by-file blueprint, function signatures, exact behaviors mapped to SPEC lines, test list with the named `test_aNN_`/`test_eNN_` tests and what each asserts.
2. **Plan review** — run `codex exec` (Bash, from repo root, non-interactive) pointing it at the plan file, the ticket, and the relevant SPEC.md sections; ask for concrete violations. Save output + your dispositions to `orchestration/tickets/<TICKET>/plan-review.md`.
3. **Sense-check** — you (not codex, not the planner) reconcile: accept/refute each finding, amend plan.md (append a binding amendments section). If the plan is structurally off, re-plan rather than patch.
4. **Implement** — spawn an implementation agent (model "opus") with plan.md as the blueprint. It must: keep to the ticket's owned files; pass `.venv/bin/ruff check .`, `.venv/bin/mypy src/`, and its named tests via `.venv/bin/pytest <its test files> -q`; never create skipped/empty tests; never weaken a fence.
5. **Diff review** — `codex exec` review of the actual changes (name the files; codex reads the repo) against the ticket + SPEC sections; save output + dispositions to `orchestration/tickets/<TICKET>/impl-review.md`. Fix real findings (spawn a fix agent or do small fixes yourself); refute false ones in writing.
6. **Report** — write `orchestration/tickets/<TICKET>/report.md`: what was built, test results (paste the pytest tail), review outcomes, deviations from ticket (should be zero), concerns for the integrator. Your final agent message: a short summary + PASS/FAIL per named test + any contract-change requests.

Rules:
- Nest freely: you may spawn as many sub-agents as the ticket needs — planner, implementer, fix agents for review findings, a verifier to run checks — each scoped tightly. Prefer a fresh scoped sub-agent over doing implementation work inside your own context.
- Drive your children: when you spawn a sub-agent (foreground or background), stay active until its result is in hand and acted on. Never end your turn while your pipeline is mid-step — an idle orchestrator stalls the whole build.
- Tests are part of the ticket: the named tests must assert the SPEC's exact values (states, orderings, error shapes). No `pytest.skip`, no `xfail`, no empty bodies, no commented-out tests — the verify instrument scans for these and the run fails.
- The venv is `.venv` (Python 3.14). Playwright/chromium installed. Never spawn real hermes/servers unless the ticket says so; adapters have fakes.
- Keep your sub-agents scoped: planner writes only plan.md; implementer writes only owned files + its tests.
- If blocked three times on the same problem, change approach materially and say so in the report.
