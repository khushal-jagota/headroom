# T17 — Sprint and Backlog & Ideas screens (stage 5)

## Scope

SPEC §10 screens 5 and 6 on the T14 foundation, composing D11 components.

- **Sprint (#/sprint)**: kickoff fields (Markdown blocks; editable via Field editor pre-freeze; frozen state shown locked); items grouped by status with ticket rollup counts and blockers-cleared flags (Entity rows + chips); loose tickets section (sprint_id set, no item); review fields (same freeze pattern); freeze-kickoff/freeze-review actions; weekly addenda list + append form (allowed post-freeze).
- **Backlog & Ideas (#/backlog)**: itemless sprint items (sprint_id NULL) by priority with a create form (D11 #13 variant: item); ideas list with create form (variant: idea).

## Files owned

- `assets/screens-sprint.js`, `assets/screens-backlog.js`, plus components.js addition ONLY for D11 item 13 (Create form variants). Everything else reused.

## Acceptance for integration

Scripted smoke against a seeded server: sprint shows grouped items with rollups; freeze locks kickoff edits but addenda append still works (structured error surfaced via Error line on a frozen write); backlog lists deferred items by priority; creates work for item and idea. node --check green; tokens-only styling.
