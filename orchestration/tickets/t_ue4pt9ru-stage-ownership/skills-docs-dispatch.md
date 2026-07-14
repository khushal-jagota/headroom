# Skills and docs implementation dispatch

Implement the skills/docs slice of `contract.md` in this worktree.

Read `AGENTS.md`, `docs/CLAUDE.md`, `contract.md`, `ownership.md`, current skills, and live docs. Own only `skills/**` and `docs/**`. Do not edit source, web, tests, PROGRESS/decisions, or other orchestration files. Do not commit and do not run `./verify`.

Update `panels-worker` with the shared Worker/User/Paired lifecycle, paired Ticket Chat, Chief reconciliation, override behavior, scope separation, Continue UI wording, and execution-route meaning. Update `panels-worker-new-worker` so every nonterminal Stage must choose a default ownership mode and explain how to choose it without redefining mechanics. Keep coding guidance stage-specific and remove stale implementer vocabulary where needed.

Update the canonical live docs for Worker types, Ticket control/scope, runtime eligibility, Chat, CLI, and execution routes. Delete transition-hook/Khushal-implementer claims; do not add compatibility history or duplicate the same explanation across docs. Use plain language and date 2026-07-14.

Run focused content/provisioning tests or scans that do not depend on unfinished backend. Finish with files changed, commands/results, and assumptions.