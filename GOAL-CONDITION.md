# GOAL-CONDITION.md

The text below is the condition passed to `/goal`. It defines done and the fences; the substance lives in the documents it references. Fire with:

```
claude -p "/goal <condition text>"
```

---

## Condition

Build the application defined in SPEC.md, working under the process rules in CLAUDE.md. The goal is met only when ALL of the following are demonstrated in the final turn, freshly, with complete command output shown in that same turn — no claims from memory, no quoting earlier runs:

1. `./verify` exits 0 and prints `VERIFY: 34/34 PASS` — the per-item scoreboard defined in SPEC.md Section 18.2, covering every acceptance test in Section 18.3, including the real-data snapshot migration (item 34).

2. The Codex audit gate passes: `codex exec` run from the repo root with the contents of codex-audit.md, full output shown, final line exactly `AUDIT: PASS`. The audit must be run after the last code or documentation change — any change after an audit invalidates it.

3. PROGRESS.md shows all six build stages from SPEC.md Section 18 completed in order; decisions.md lists every delegated choice made; DOCS.md is complete per SPEC.md Section 18.1.

Constraints:
- Operate as a planner and orchestrator per CLAUDE.md: implementation substance is dispatched to contract-scoped sub-agents; you plan, dispatch, review, integrate serially, and verify. Direct edits are for glue and integration repairs only, noted in PROGRESS.md.
- Follow the build order in SPEC.md Section 18 exactly. The verify instrument is built at stage 2, before implementation, and initially reports all items FAIL. No stage begins while a prior stage's tests fail.
- Tests obey the fences in SPEC.md Section 18.3: one named test per checklist item, assertions using the spec's stated values, no skipped or disabled tests, post-first-write changes justified in decisions.md.
- Never modify SPEC.md, PRINCIPLES.md, CLAUDE.md, codex-audit.md, or this condition's source document. Work only inside this repository; never touch `~/.hermes/planning/` or `~/.hermes/hermes-agent/`.
- Update PROGRESS.md every work cycle. If blocked on the same problem for three consecutive attempts, log it and change approach materially.
- Do not ask questions. Delegated choices are yours; make them and record them in decisions.md.
- If the Codex audit returns AUDIT: FAIL, address every listed violation, re-run `./verify`, then re-run the audit. Repeat until it passes. Concerns from the audit must be addressed or explicitly refuted in decisions.md but do not block completion.
