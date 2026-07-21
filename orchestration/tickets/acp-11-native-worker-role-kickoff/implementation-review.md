# ACP-11 implementation review — round 1

Fixed point: `34bb5c1`

One parallel review round inspected the staged implementation against the ticket contract,
`AGENTS.md`, and `PRINCIPLES.md`.

## Findings

1. **P1 — preserve an earlier pending kickoff when replacement `session/new` fails.** The wrapper
   cleared the old armed session before the delegated creation succeeded. A failed New conversation
   could therefore leave the still-active empty conversation without its required first-prompt role.
2. **P1 — do not turn a first ACP command into an ordinary model prompt.** Prefixing `/compact`,
   `/help`, or another command prevents provider command dispatch. Command-shaped prompts must pass
   unchanged and leave the role armed for the next ordinary prompt.
3. **P1 — normalize the synthetic echo only within its session operation.** A permanent exact-text
   filter both missed Hermes' flattened `directive + newline + prompt` replay and could hide a
   legitimate message equal to the directive. Filtering must be temporary and one-shot, dropping a
   separate synthetic chunk or rewriting a merged chunk to the exact original text.

No other requirement or standards violations were reported.
