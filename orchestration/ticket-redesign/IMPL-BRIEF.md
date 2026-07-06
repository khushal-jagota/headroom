# Implementation brief — wire the ticket redesign into the app

You are the **orchestrator** for this build. Decomposition is done (tickets T1–T5, below and in
`plan.md`). Your job: dispatch Opus implementer sub-agents, coordinate Codex reviews, integrate
serially, keep `./verify` green, and finish with a Codex implementation-review loop (max 3 rounds).
You do **not** write the ticket substance yourself — route it to sub-agents; you plan, review,
integrate, and verify.

## Authoritative specs (read first, in order)
1. `orchestration/ticket-redesign/plan.md` — the reviewed, **DONE** plan: per-ticket specs T1–T5,
   the interaction→endpoint map, Decision B, the approval-component rules, the field-rendering
   rules. **The plan is final — do NOT re-loop plan review.** Only the *implementation* review loop
   is in scope.
2. `orchestration/ticket-redesign/mockup.html` — structural source of truth for the ticket screen
   (its **structure + interaction**, not its literal palette/type).
3. `PROGRESS.md` (top "CURRENT WORK" section), `SPEC.md` (§4 lifecycle/write-model, §9 HTTP+events,
   §10 UI, §14 structural rules), `PRINCIPLES.md`, `CLAUDE.md` — the standing rules.

## Hard constraints
- Work ONLY in this repo. Never read/write `~/.hermes/planning/`, `~/.hermes/hermes-agent/`, or any
  other repo.
- NEVER modify `SPEC.md`, `PRINCIPLES.md`, `CLAUDE.md`, `codex-audit.md`, `GOAL-CONDITION.md`.
- Do NOT commit or push. Leave the finished changes in the working tree for the lead to commit.
- **One canonical writer per transition:** `fields.*.value` is written ONLY by the resolution
  engine. Agents (claim-carrying requests) file proposals; humans decide. No client-side state
  store — the event feed is an invalidation signal; the UI refetches JSON.
- `./verify` is the ONLY completeness gate. Run it fresh and show the full `VERIFY: N/36 PASS`
  line. It must stay **36/36** after every ticket integrates. Never advance over a regression.

## Build order & parallelism
`{T1 ∥ T2} → T3 → T4 → T5` → Codex implementation-review loop.
- **T1** (`assets/tokens.css` only) and **T2** (backend slice) are file-disjoint → dispatch both
  concurrently in the main tree.
- **T3 → T4 → T5** are sequential (T3 defines the primitives T4 consumes; T4 is the screen T5
  tests). Never let two sub-agents write the same file concurrently.
- Use **Opus** sub-agents for implementation (Agent tool, `model: opus`).

## Per-ticket pipeline
Dispatch an Opus implementer with the ticket spec + its exact owned files + the relevant
SPEC/PRINCIPLES sections → it implements and self-validates (ruff/mypy for Python; `node --check`
for JS; the repo's CSS check; runs its own new tests) → run a **Codex diff review** on the
load-bearing tickets (**T2 and T4 mandatory**; T1/T3/T5 light/optional) → address concrete findings
→ you integrate and run full `./verify`. **Spot-check the resolution-engine change (T2) yourself**
by reading the diff — it is the single canonical writer.

## T1 — concrete token mapping (plan.md's T1 is thin; use this)
Re-theme `assets/tokens.css` VALUES to the mockup's warm palette and widen the token set only as far
as the mockup structurally needs. **Keep every existing token name** (rename = break consumers;
check `assets/app.css` for what's consumed). Mockup `:root` reference: `--bg:#1b1917 --sunk:#141210
--line:rgba(230,210,175,.11) --fg1:#f4f1ea --fg2:#dcd8cf --fg3:#b4b0a6 --fg4:#8a867c --fg5:#66625a
--amber:#e7a742 --amber-ink:#1c1406 --done:#7fa564`; raised surface `#24211b`, pill bg `#26221c`,
state-pill bg `#33291a`, user bubble `#2a251b`.
- Surfaces: `--surface-base`←#1b1917 · `--surface-sunken`←#141210 · `--surface-raised`←#24211b ·
  `--surface-overlay`←#26221c.
- Text — **widen to five roles**: `--text-strong`←#f4f1ea · `--text-default`←#dcd8cf ·
  `--text-muted`←#b4b0a6 · `--text-faint`←#8a867c · **ADD** `--text-faintest`←#66625a (5th token
  inside the existing `--text-*` category = a widening, not a new category — allowed & required).
- Accent (the one amber = "needs the human"): `--accent-bright`←#e7a742 · `--accent-surface`←#33291a
  · `--accent-text`←#f4e3bd · **ADD** `--accent-ink`←#1c1406 (dark text ON the amber fill, e.g. the
  Approve button) · **ADD** `--accent-done`←#7fa564 (the green ✓ done marks).
- **ADD** `--border-color`←rgba(230,210,175,.11) (the hairline seam; the file has border *widths* but
  no border *colour*, so consumers would otherwise hardcode it — check app.css for an existing
  border-colour token name first and reuse it if present).
- Radius/motion/spacing/border-widths/fonts: keep. **Type scale: EXACTLY FIVE SIZES** (PRINCIPLES,
  non-negotiable) — you may nudge values to land the roles (`--type-lg:20px` for the 20px title) but
  never add a sixth. The mockup's 12/14/16 compress onto the five roles in the CSS consumers.
- DO NOT add new token *categories*, a sixth type size, rename tokens, or touch any other file.

## T2 — reaffirm the code shapes (plan.md "Decision B" + T2 are authoritative; pin these)
- `machine.field_is_passed(field, state) -> bool`: a field is *passed* iff the state it gates
  strictly precedes the current state in `STATE_ORDER`. Field→gated-state = invert `GATING_FIELD`
  (success←needs_success, approach←needs_approach, plan←needs_plan, result←in_progress). Implement as
  `state_index(state) > state_index(FIELD_GATES[field])`. `state_index(dropped)` already raises
  `validation` (dropped ∉ STATE_ORDER) — so `dropped` is rejected for free.
- `resolution.decide_edit_value(ticket, field, new_body, actor) -> Decision`, check order:
  (1) `admission.require_human`; (2) `admission.validate_body`; (3) reject if
  `ticket.state is dropped` (explicit, clear message); (4) reject if `slot.value is None`;
  (5) reject if `slot.proposal is not None`; (6) reject if not `field_is_passed(field, state)`.
  On success: write `value=new_body`, keep `notes`, leave state/ceiling/at_cap untouched, emit one
  `EventSpec(EventKind.field_value_edited, {"field": field.value, "body": new_body})`.
- `core/contracts.py`: add `field_value_edited = "field_value_edited"` to `EventKind` (supplemental
  fields group), comment `{field, body}`. Do NOT reuse `proposal_accepted`.
- `tickets/contracts.py`: add `ValueEditBody(TypedDict, total=False)` with `body: str`.
- `tickets/data.py`: `edit_field_value(conn, ticket_id, *, field, new_body, actor, now)` — one
  `_txn`, load → `resolution.decide_edit_value` → `_apply_decision` (the sole appender).
- `tickets/api.py`: `PUT /tickets/{ticket_id}/value/{field}` — `reject_agents(ctx)` (human-only),
  marshal `ValueEditBody` via `body_str`, `parse_enum(FieldName,...)`, call `edit_field_value`,
  return `ticket_json`. Import `ValueEditBody`.
- **Harden `decide_accept`:** reject when `ticket.state is dropped` **first**, before the
  `slot.proposal is None` / gating-field branches (today a dropped ticket carrying a pending
  non-gating proposal can still be accepted — that writes `value` with no state change; close it).
- Tests (new files in `tests/unit/`, NOT the item 1–36 fence tests): (a) pure-logic
  `decide_edit_value` — passed value edits OK (value updated, `field_value_edited` emitted,
  state/ceiling unchanged); rejects unset value, a live-proposal field, the current gating field, a
  **future** field that already holds a value + no proposal (backward-jump hazard), a **dropped**
  ticket, and an **agent** actor; plus a **drop-with-pending-proposal → accept rejected** case for
  the `decide_accept` guard. (b) API-level `PUT /value/{field}` via TestClient — `ValueEditBody`
  marshalling, `reject_agents`→400 `agent_forbidden`, bad `field`→validation, response shape, and
  `GET /tickets/{id}/events` then contains a `field_value_edited {field, body}` row.
- Record the new human write-model capability (`PUT /value/{field}` + `field_value_edited`) in
  `decisions.md` as a documented extension of the §4/§9 human write surface (value is still written
  solely by the resolution engine).

## Codex review protocol
- Non-interactive: write the review prompt to a file, then
  `codex exec --model gpt-5.5 -c model_reasoning_effort=high "$(cat PROMPT.txt)" < /dev/null`
  (the `< /dev/null` stops it blocking on stdin). If that model string errors, fall back to plain
  `codex exec "$(cat PROMPT.txt)" < /dev/null`.
- Codex output is **buffered**: it can sit silent ~9 minutes then print everything at once. Silence
  ≠ hang. Do NOT kill it early — check the output file's size/mtime before assuming it's stuck.
- Point it at the specific changed files + the relevant `plan.md`/`SPEC.md`/`PRINCIPLES.md`
  sections; ask for concrete violations only. Surface its full output; address or refute each point.
- **Final implementation review loop — MAX 3 rounds:** review the full diff vs plan + mockup +
  SPEC/PRINCIPLES → if it names concrete violations, fix them (a scoped fix sub-agent, or inline for
  trivia) and re-review → stop at "no violations" OR after 3 rounds, reporting any residual findings
  honestly rather than looping further.

## Finish
- Fresh `./verify` → **36/36** (show the line).
- Update `PROGRESS.md` (what landed, current state) and `decisions.md` (the write-model extension +
  any delegated judgment calls).
- Return a report: what each ticket (T1–T5) changed, the final `./verify` line, the Codex review
  outcome (clean, or residual findings with your disposition), and anything the lead should
  double-check before committing.
