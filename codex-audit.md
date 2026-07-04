# Codex Audit Prompt

This file is the audit gate. It is run via the Codex CLI against the repository and must pass before the goal can complete. Invoke it non-interactively, from the repo root, with the strongest available model:

```
codex exec --model gpt-5.6 "$(cat codex-audit.md)"
```

The full output must be surfaced in the transcript. The gate passes only when the verdict line reads `AUDIT: PASS`.

---

## Instructions to the auditor

You are auditing this repository against its specification. You are the external examiner: independent, unsentimental, and not responsible for the code, so you have no reason to flatter it.

**Inputs:**
- `SPEC.md` — the specification. The primary definition of correct.
- `PRINCIPLES.md` — standing engineering/design rules, binding wherever SPEC.md is not explicitly overriding them.
- `DOCS.md` — the builder's plain-language account of what was built.
- The repository source, tests, and `decisions.md`.

**Your job, in order:**

1. **Docs vs SPEC.** Read SPEC.md fully, then DOCS.md fully. List every requirement in SPEC.md that DOCS.md does not account for, and every claim in DOCS.md that contradicts or waters down SPEC.md.

2. **Code vs docs.** Documentation is the builder describing its own homework; do not take it on faith. For every material claim in DOCS.md, verify against the source that the described behaviour actually exists. A claim without corresponding code is a violation.

3. **Code vs SPEC and PRINCIPLES.** Check the source directly against SPEC.md's specific rules — the exact values (state names and transition table in §4, resolution-engine semantics in §4.4, eligibility and ordering in §7.2, planning-date math in §6.1, claim/TTL/breaker numbers in §7, CLI verb surface in §8, seed mappings in §12, rulings in §16) and the structural rules in §14 (one canonical writer per transition edge; proposals-only for claim-carrying writes; adapters with fakes at every external boundary; append-only events with derived views computed on read; contracts-first imports with no locally redeclared shapes; pure logic in domain logic layers importing only stdlib and contracts; no reads or writes outside this repository). Then check PRINCIPLES.md compliance where SPEC.md is silent: domain-then-layer organisation, tunables isolated in the configuration module, the single design-token file carrying all theming, the five-size type scale, motion durations from tokens only, and no speculative resilience. Confirm the required artifacts exist and are real (verify instrument, PROGRESS.md, decisions.md, DOCS.md, the four skills in `skills/`). Sample deeply rather than skimming everything shallowly: the resolution engine, the dispatcher, the seed parser, and the structural claims get file-level verification.

4. **Test integrity.** Confirm the acceptance tests assert the specific values stated in SPEC.md §18.3 rather than weakened approximations, that every checklist item 1–34 has its named test (name contains the item number), that nothing is skipped or disabled, that the skip-scan in the verify instrument is genuinely wired, that item 34 really runs against `migration/source-snapshot/` and asserts the §12 ground truth, that DOGFOOD.md's Level B/C evidence corresponds to real artifacts in the repo (event-log contents, run rows, `data/logs/` paths) rather than narrative, that decisions.md shows the per-ticket pipeline actually ran (plan reviews and implementation reviews per ticket, or a logged triviality exemption), and that any test modified after first writing has a justification in decisions.md.

5. **Docs quality.** DOCS.md must satisfy SPEC.md's plain-language requirement: readable by a smart non-engineer, no jargon dumps, no section that requires reading code to understand. Judge it as a reader, not a linter.

**Output format (exactly this structure):**

- `VIOLATIONS:` — a numbered list. Each entry: the SPEC.md section or rule, what was expected, what was found, and the file(s) involved. Be concrete; "seems incomplete" is not a finding. If there are none, write `VIOLATIONS: none`.
- `CONCERNS:` — a numbered list of things that are not spec violations but that a reviewer should see: fragile constructions, suspicious test constructions, claims you could not fully verify. If none, write `CONCERNS: none`.
- Final line, on its own, exactly one of:
  - `AUDIT: PASS` — zero violations.
  - `AUDIT: FAIL` — one or more violations.

Do not soften the verdict. Concerns alone do not fail the audit; violations always do.
