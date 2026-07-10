# Implementation review

## Review command

All passes used Codex CLI model `gpt-5.5`, `--sandbox read-only`, and
`model_reasoning_effort=high`, with stdin closed. The prompts used for each pass are
stored beside this record. The raw command output included unrelated review-skill
bootstrap text, so this record preserves the complete actionable Codex output and each
disposition without committing tens of thousands of irrelevant bootstrap lines.

## Initial pass

Codex reported four violations:

1. The external-work `TypedDict` shapes used `total=False`, so required `state`,
   `user_note`, and create `title` were optional in the type contract. The request-body
   comment also claimed every key was optional and unknown keys were ignored.
2. Touched source comments still used human-only/human-edit authority wording in ticket
   contracts, ticket resolution, and the chat command catalog.
3. `docs/systems.md` and `docs/systems.html` still said product commands were headerless
   human requests and omitted the Chief command group.
4. `docs/cli.md`, `docs/tickets-and-gates.md`, and `AGENTS.md` retained stale
   human-only/direct-operation wording or omitted Chief from the command map.

Codex ended with `VIOLATIONS REMAIN`.

### Disposition

All four were fixed. External-work request types now encode required keys and optional
keys explicitly with `NotRequired`. Marshalling still rejects unknown keys and wrong
values. Source/docs now use direct, unattributed, worker, and Chief terminology. The
plain-language and HTML system maps include the Chief group and the production actor
environment wiring.

## Follow-up pass 1

Codex reported two remaining stale phrases:

- `src/planner/tickets/logic/resolution.py` said acceptance was “auto and human alike”.
- `src/planner/sprints/api.py` called direct-only fields “human-editable”.

Codex ended with `VIOLATIONS REMAIN`.

### Disposition

Both phrases were corrected. The same wording in day data was corrected, and the CLI
module command map was updated to include Chief.

## Follow-up pass 2

Codex reported stale authority terminology in:

- `tests/e2e/conftest.py`;
- `tests/unit/test_authctx_routes.py`;
- `tests/unit/test_chat_commands.py`;
- `tests/unit/test_value_edit_api.py`.

Codex ended with `VIOLATIONS REMAIN`.

### Disposition

The helpers, comments, test names, local variables, and sample text now say direct or
unattributed. Every E2E call site now uses `direct_post`/`direct_patch`. Repository scans
found no remaining `human-only`, headerless-as-human, `reject_agents`,
`human-editable`, or `auto and human` wording in source or tests.

## Follow-up pass 3

Codex inspected the final diff and ended with:

```
NO VIOLATIONS
```

## Verification available to the final pass

- Unit tests plus relevant CLI E2E: 239 passed.
- Mypy: success in 91 source files.
- Ruff: all changed Python files passed.
- `git diff --check`: clean.
