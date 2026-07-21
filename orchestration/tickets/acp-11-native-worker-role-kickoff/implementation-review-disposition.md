# ACP-11 implementation review disposition

All three round-1 findings were accepted as bounded corrections within the shared role-kickoff
decorator and its tests:

- retain the prior arm until replacement session creation succeeds;
- pass first-turn commands unchanged without consuming the arm;
- scope echo normalization to each decorated prompt or replay operation and support Hermes' merged
  replay shape while preserving colliding legitimate human text.

The corrections now:

- overwrite the armed session only after delegated session creation succeeds;
- pass text-only slash commands unchanged without consuming the arm;
- arm one session-scoped normalizer only around prompt and replay operations, drop a separate
  synthetic directive once, and rewrite Hermes' merged `directive + "\n" + original` replay to the
  exact original once. Later legitimate matching text passes unchanged.

The corrected focused gate passed Ruff, strict Mypy, 37 unit tests, and all 18 ACP conversation e2e
tests. Root inspected the correction diff and the installed Hermes ACP source that flattens pure-text
prompt blocks with a single newline and dispatches only text-only leading-slash prompts as commands.
Root then narrowed the command predicate itself to text-only prompts; the final canonical `./verify`
owns that last integration proof.

Live Safari dogfood also passed. In a fresh Chief conversation, `/help` remained a command, the next
ordinary message produced a completed `skill view (panels-chief-of-staff)` row, and the requested
answer appeared without the directive. A fresh Ticket conversation produced a completed
`skill view (panels-worker)` row and its requested answer. A real Ticket page reload replayed the
human prompt, skill activity, and answer without exposing the directive. A second broad review round
would add no new boundary or unresolved finding.

The single final `./verify` then passed: Ruff, strict Mypy across 130 source files, 1,027 unit tests,
compile/CSS/JS checks, zero Svelte diagnostics, the production build and every frontend suite, and
104 Playwright e2e tests. Final result: `VERIFY: PASS`.
