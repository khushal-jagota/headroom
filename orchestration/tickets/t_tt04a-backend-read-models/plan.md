# plan.md — t_tt04a: Backend read-models, type-driven and non-throwing

## 0. Design invariant (the correctness bar)

Every seam below already has a proven per-type resolution path in the write layer: `_row_to_ticket`
(`src/planner/tickets/data.py`) resolves a row's `ticket_type` through
`ticket_type_guard.resolve_and_validate(...)` → a `WorkflowDefinition`, then decodes `fields` with
`fields_codec.fields_from_json(row["fields"], defn)`. The read-models currently short-circuit this by
hardcoding `coding_bridge.coding_definition()` and coercing `TicketState(state)`. **The fix at every seam
is to mirror the write layer: resolve the row's own definition via `coding_bridge`, decode/inspect against
it, and never coerce a bare state id into the `TicketState` enum.**

Resolution primitive used everywhere below (only `coding_bridge` is imported — F6-safe):

```
defn = coding_bridge.require(ticket_type)          # unknown type -> not_found (never silently coding)
fields = fields_codec.fields_from_json(raw_fields_json, defn)
```

Derived views used: `coding_bridge.views.field_ids(defn)`, `.gating_field(defn, state)`,
`.require_stage(defn, state)` (for `.label`), `.is_terminal(defn, state)`, `.stage_ids(defn)`,
`.state_index(defn, state)`, `.default_ceiling(defn)`, and `defn.fields` (for field labels).
`machine.has_pending_gating_proposal(state, fields, definition=defn)` already accepts a `definition` kwarg
and is string-id-native. **Implementer: confirm each of these view helper names exists in
`src/planner/ticket_types/logic/views.py`; if a name differs, use the actual one — do not add new views
unless a needed one is genuinely absent.**

**"Byte-identical for coding" means, precisely:** the emitted payload is a *superset* of today's — every
key present today keeps its exact value and the container ordering (column order, card order, JSON key
order of pre-existing keys) is unchanged. New keys are additive. No test in the repo asserts a whole
board/queue dict by equality (`test_board_view.py` iterates `columns[].cards[]`; e2e reads DOM attributes;
`test_links.py` asserts `copy_text` substrings). So a superset payload keeps every existing assertion
green. Where a payload is a plain string (`copy_text`) or a fixed-shape digest (`_approval_digest`),
byte-identity is literal and pinned by a golden.

Nothing in contracts, the engine (`machine.py`, `resolution.py`, `external_work.py`), storage (`data.py`
writers, `db.py`), or `web/` is modified. Production registry stays coding-only.

---

## 1. Seam 1 — `tickets/views.py::copy_text`

Replace the six hardcoded field blocks (`kickoff` … `closeout` with their `_user_note`) with a loop over
the type's ordered fields.

- `ticket = tickets_data.read_ticket(...)` already carries `ticket.ticket_type` and `ticket.fields`
  (decoded against its own definition by `_row_to_ticket`). Resolve `defn = coding_bridge.require(ticket.ticket_type)`.
- Iterate `coding_bridge.views.field_ids(defn)` in order; per `field_id` emit exactly:
  ```
  <field_id>:\n{show(slot(field_id).value)}\n
  <field_id>_user_note:\n{show(slot(field_id).user_note)}\n
  \n
  ```
  Two labelled lines per field, then one blank line — the exact 3-line-group shape the current code emits.
  `slot()`/`show()` helpers stay as-is.
- Keep the surrounding fixed lines byte-for-byte: the leading `title / state / priority / implementer`
  block (incl. the trailing blank line before the first field) and the trailing `recap / blocked_by /
  blocks` block. `blocked_by_block` / `blocks_block` construction unchanged.

**Coding byte-identity:** coding's `field_ids(defn)` = `("kickoff","success","approach","plan",
"implementation","closeout")`. Emitting the two labelled lines per field, blank-line separated, reproduces
the current lines character-for-character, including the terminal blank line before `recap:`.

**Probe:** `field_ids` = `("kickoff","alpha","beta")` → only those blocks; no coding field appears.
`slot()` never raises (reads only declared fields).

**Note:** `str(ticket.state)` stays — `ticket.state` is already a raw stage id string; do NOT wrap in
`TicketState(...)`.

---

## 2. Seam 2 — `tickets/views.py::board_view` (the crux)

Three coding assumptions to remove; one payload shape to preserve exactly.

### 2a. SQL — add `ticket_type` to the SELECT (REQUIRED)
The board SQL selects `tickets.fields` but not `tickets.ticket_type`. Add `tickets.ticket_type`.

### 2b. Per-row decode with the row's own definition
```
ticket_type = str(row["ticket_type"])
defn = coding_bridge.require(ticket_type)
fields = fields_codec.fields_from_json(str(row["fields"]), defn)
```

### 2c. Pending-proposal signal via that definition
```
"has_pending_proposal": machine.has_pending_gating_proposal(state, fields, definition=defn),
```
(string-id-native; never constructs `TicketState(state)`.)

### 2d. Stop grouping by the global `STATE_ORDER` (the throw)
Today `by_state = {s.value for s in STATE_ORDER}` + `by_state[state].append` KeyErrors the moment a probe
row carries `needs_alpha`. Replace with a grouping driven by the definitions of the rows present:

- Build the column skeleton from coding's OWN ordered stage ids
  (`coding_bridge.views.stage_ids(coding_bridge.coding_definition())`) — reproduces today's 7 columns in
  order and guarantees coding-only byte-identity (same 7 columns, same order, including empties).
- For a heterogeneous board, append — in first-seen order, de-duplicated — any present-row stage id not
  already a column (probe's `needs_alpha`/`needs_beta`). Extra columns appended AFTER the coding columns.
  The heterogeneous column *taxonomy* is explicitly 4b's problem; 4a owes only "does not throw" + "coding
  byte-identical".
- Card sort within a column is unchanged (existing `sort_key` tuple).
- Emit `columns` as `[{"state": <id>, "cards": [...]}, ...]` in the computed order.

```
coding_order = coding_bridge.views.stage_ids(coding_bridge.coding_definition())
column_order = list(coding_order)
by_state = {sid: [] for sid in column_order}
# in the row loop, after computing `state`:
if state not in by_state:
    by_state[state] = []
    column_order.append(state)
by_state[state].append((sort_key, card))
# after the loop:
columns = [
    {"state": sid, "cards": [card for _, card in sorted(by_state[sid], key=lambda i: i[0])]}
    for sid in column_order
]
```

### 2e. Card enrichment (additive keys, appended after existing keys)
```
"ticket_type": ticket_type,
"state": state,
"state_label": coding_bridge.views.require_stage(defn, state).label,
"gating_field": gating_field_id,                 # views.gating_field(defn, state); None on terminal
"gating_field_label": <label of that field, or None>,
"is_done": state == <coding_bridge.views terminal-stage id for defn>,
"is_dropped": state == defn.dropped_stage.id,
```
- `gating_field_id = coding_bridge.views.gating_field(defn, state)` (None for terminal).
- `gating_field_label`: `next((f.label for f in defn.fields if f.id == gating_field_id), None)`.
- `is_dropped`: always False on the board in practice (`WHERE state != 'dropped'`), emitted for card-shape
  uniformity; derived from `defn.dropped_stage.id` to stay type-correct.

Existing keys (`id, title, priority, deadline, project_id, project, group_project_id, group_project,
activity_at, has_pending_proposal, ticket_status`) keep exact values and positions.

**Coding byte-identity:** coding-only board → `column_order` = coding's 7 ids (no extra column), each
`{"state","cards"}` built by the same sort. Pre-existing card keys/values unchanged; only additive keys
added. `BoardRoute.svelte` reads `column.state`/`column.cards`/existing card keys and derives its own rail
from `card.state`; it ignores the new keys. No unit/e2e board assertion inspects them.

**Mixed board:** coding rows in the 7 coding columns; probe rows in shared `needs_kickoff`/`done` + appended
`needs_alpha`/`needs_beta`. No throw. Each probe card decoded against `PROBE_DEFINITION`.

---

## 3. Seam 3 — `tickets/views.py::queues_view` / `_approval_digest`

### 3a. `_approvals` SQL — add `ticket_type` (REQUIRED)
`_approvals` selects `id, title, state, ticket_status, fields, updated_at` — no `ticket_type`. Add it and
carry `"ticket_type": str(r["ticket_type"])` into the digest dict.

### 3b. `_approval_digest` — resolve the gating field per row
`_approval_digest` receives `fields` as a raw dict (`json.loads`), not a `TicketFields`. Keep the raw-dict
access but resolve the gating field from the row's definition:
```
defn = coding_bridge.require(str(row.get("ticket_type")))
gating_field_id = coding_bridge.views.gating_field(defn, state)   # None on terminal -> continue
if gating_field_id is None:
    continue
slot = fields.get(gating_field_id)
...
"kind": gating_field_id,
```
Drops `GATING_FIELD` and `TicketState(state)`. Digest shape (`entity_id`, `kind`, `waiting_since`)
unchanged. The queue SQL already filters `state NOT IN ('done','dropped')`, so the `continue` guard
matches today's `if gating is None: continue`.

Import re-audit once Seams 2 and 3 land: `GATING_FIELD` and `STATE_ORDER` become unused — drop them.
**KEEP `TicketState`** (Codex F2): `_TICKET_CLOSED` at `tickets/views.py:29` still reads
`TicketState.done.value` / `.dropped.value` for the overdue queue. Removing the import raises
`NameError` at module import and disables every route importing `planner.tickets.views`. (Do NOT touch
`_TICKET_CLOSED`; leave it on the universal `done`/`dropped` bookends via `TicketState`.) Keep
`TicketStatus`, `FieldSlot`.

**Coding byte-identity:** `views.gating_field(defn, state)` returns exactly `GATING_FIELD[TicketState(state)].value`
for coding (coding's gates are sourced from `GATING_FIELD`). `kind`, ordering, shape unchanged.
`_overdue`/`_overdue_digest` untouched.

**Probe:** a probe ticket parked at `needs_alpha` with an `alpha` proposal surfaces with `kind == "alpha"`.

---

## 4. Seam 4 — `sprints/views.py::item_tickets`

### 4a. SQL — add `ticket_type` (REQUIRED)
`item_tickets` selects `id, title, state, priority, ticket_status, fields` — add `ticket_type`.

### 4b. Decode + signal per row
```
defn = coding_bridge.require(str(r["ticket_type"]))
fields = fields_codec.fields_from_json(str(r["fields"]), defn)
...
"has_pending_proposal": machine.has_pending_gating_proposal(state, fields, definition=defn),
```
Projection shape (`id, title, state, priority, has_pending_proposal, ticket_status`) unchanged; `state`
stays the raw id string.

**Import audit:** `item_rollup` uses `{s.value for s in TicketState}` to seed its rollup dict, so
`TicketState` STAYS imported in `sprints/views.py`. The `item_rollup` seed is coding-shaped but is NOT a
named 4a seam and no probe rollup assertion is required — leave it (note in decisions.md).

**Coding byte-identity:** coding rows decode identically. Probe rows decode against probe, never throw on
`needs_alpha`.

---

## 5. Seam 5 — `sprints/logic/status.py::derive_sprint_item_status`

The genuine per-type semantic. `_IN_PROGRESS_STATES = {needs_approach, needs_plan, needs_implementation,
needs_closeout}` = coding's mid-states, i.e. "advanced past the opening stages," excluding
`needs_kickoff` and `needs_success`.

### Chosen rule
> A child ticket is **"in progress" by state** iff its state is a **non-terminal linear stage whose index
> in the type's stage order is strictly greater than the index of the type's `default_ceiling`** — strictly
> past the type's opening stage(s), not terminal.

Justification (coding): indices `needs_kickoff`=0, `needs_success`=1, `needs_approach`=2, `needs_plan`=3,
`needs_implementation`=4, `needs_closeout`=5, `done`=6; `default_ceiling`=`needs_success` (1). Rule ⇒
indices > 1 and non-terminal = `{needs_approach, needs_plan, needs_implementation, needs_closeout}` —
**reproduces `_IN_PROGRESS_STATES` EXACTLY**. `default_ceiling` is precisely "the first worker stage"
(`ceiling_range(defn)[0]`); "strictly past it" is the principled generalization of "past the opening
stages" — both the universal `needs_kickoff` bookend and the type's first worker gate are excluded. Do NOT
narrow to "non-terminal".

Probe: indices `needs_kickoff`=0, `needs_alpha`=1, `needs_beta`=2, `done`=3; `default_ceiling`=`needs_alpha`
(1). Rule ⇒ `{needs_beta}` is in-progress-by-state; `needs_kickoff`/`needs_alpha` are not (unless an
in-progress ticket_status applies). Correct analogue.

### Purity — Option A (chosen)
`derive_sprint_item_status` is dependency-free (`PRINCIPLES.md` pure-logic rule) and must stay so — it
cannot import `coding_bridge`. Pre-compute the per-child "in-progress-by-state" boolean at the caller
`sprints/data.py::read_item` (which already knows each child's `ticket_type` and `state`; add
`tickets.ticket_type` to the child SQL). Extend `SprintItemChildStatus` with `state_in_progress: bool`
computed in `read_item`:
```
defn = coding_bridge.require(ticket_type)
terminal = coding_bridge.views.is_terminal(defn, state)
default_ceiling_idx = coding_bridge.views.state_index(defn, coding_bridge.views.default_ceiling(defn))
# CRITICAL (Codex F1): `dropped` is OUTSIDE the linear stages tuple, so state_index("dropped")
# RAISES. Check terminality FIRST and let `and` short-circuit so state_index is never called on a
# terminal (done/dropped) state. Do NOT compute the index unconditionally.
state_in_progress = (not terminal) and coding_bridge.views.state_index(defn, state) > default_ceiling_idx
```
A dropped or done child therefore yields `state_in_progress = False` without raising. The existing
all-dropped-children → `ItemStatus.todo` case (`tests/unit/test_sprints.py`) MUST stay green — keep/retain
that regression.
`derive_sprint_item_status` replaces `child.state in _IN_PROGRESS_STATES` with `child.state_in_progress`
and drops `_IN_PROGRESS_STATES`. Keep `_DONE_STATE`/`_DROPPED_STATE` literal checks (universal bookends
`done`/`dropped`, shared by every type by validation — note in decisions.md).

**Reject Option B** (threading a `WorkflowDefinition` or callback into the pure function): couples it to
registry types / adds a speculative parameter. Option A keeps it on plain precomputed primitives,
consistent with the existing `SprintItemChildStatus` NamedTuple.

**F6 check:** `sprints/data.py` importing `planner.tickets.logic.coding_bridge` imports the *seam*, not
`ticket_types` — consistent with `sprints/views.py`'s existing import. Confirm the F6 guard test's
importer set (`{coding_bridge}` for `ticket_types`) still passes.

**Coding byte-identity:** `state_in_progress` is True exactly for coding's four mid-states, so
`derive_sprint_item_status` returns the identical `ItemStatus` for every coding child set.

---

## 6. Files touched (ordered)
1. `src/planner/ticket_types/logic/views.py` — expected no change; confirm needed view helpers exist.
   Optionally add a pure `field_label(defn, field_id)` one-liner instead of the inline `next(...)`.
2. `src/planner/tickets/views.py` — Seams 1, 2, 3 (+ SQL `ticket_type` in board & `_approvals`; drop dead
   imports after re-audit).
3. `src/planner/sprints/logic/status.py` — Seam 5 (add `state_in_progress`; drop `_IN_PROGRESS_STATES`).
4. `src/planner/sprints/data.py` — Seam 5 caller (child SQL `ticket_type`; import `coding_bridge`; compute
   `state_in_progress`).
5. `src/planner/sprints/views.py` — Seam 4 (`item_tickets` SQL `ticket_type`; decode per row; `definition=defn`).

## 7. Test strategy (per seam: coding byte-identical + probe correct/non-throwing)
Probe tests register via `tests/support/probe.py::install_probe_registry()`/`uninstall_probe_registry()`.
**Each new test module must define its OWN local yield fixture** wrapping install/uninstall (Codex F3):
the `probe_registry` fixture in `test_probe_type.py` is module-local and is NOT shared via `conftest.py`,
so referencing it from another module fails with `fixture 'probe_registry' not found`. Either copy a small
local fixture into each new module, or (cleaner) add one shared `probe_registry` yield fixture to
`tests/unit/conftest.py` and have all modules use that. Probe rows inserted through the real
`create_ticket(..., ticket_type="probe")` door (not hand-built dicts).

- **`test_copy_text_type_driven.py`** — `..._coding_is_byte_identical_golden` (pinned full string, six
  blocks in order = the permanent copy_text golden); `..._probe_renders_own_fields`
  (kickoff/alpha/beta blocks, no coding field).
- **`test_board_view.py`** (extend) — `..._coding_card_keys_superset_and_columns_unchanged` (7 coding
  states in order; existing keys + additive keys; no extra column for coding-only);
  `..._mixed_coding_probe_does_not_throw` (probe card in its own column, probe-correct enrichment).
- **`test_queues_approval_type_driven.py`** — `..._coding_kind_unchanged`;
  `..._probe_surfaces_on_registry_field` (`kind == "alpha"`).
- **`test_sprint_item_status_buckets.py`** (permanent golden) —
  `..._coding_buckets_golden` (derivation == pinned coding set for every coding stage);
  `..._probe_midstage_rolls_up` (`needs_beta` → in_progress, `needs_alpha` → not);
  `..._rollup_probe_child_via_read_item` (integration through `read_item`; coding rollups unchanged).
- **`test_sprint_views_type_driven.py`** — `..._item_tickets_probe_child_decodes` (no throw; probe-correct
  `has_pending_proposal`).

## 8. Risks
- **Board byte-identity (highest):** coding skeleton from coding's own `stage_ids`; pre-existing keys/order
  untouched; new keys appended. Pinned by the board test + the unchanged e2e `test_board_stage_indicators.py`.
- **copy_text byte drift:** the golden string + existing `test_links.py` substrings.
- **Missing `ticket_type` in a read SQL:** called out at four SELECTs — `board_view`, `_approvals`,
  `item_tickets`, and `read_item`'s child SQL.
- **`derive_sprint_item_status` purity:** Option A moves per-type derivation to `read_item`; the pure
  function consumes only `state_in_progress` + universal `done`/`dropped` literals.
- **F6 boundary:** every seam reaches the registry only through `coding_bridge`.
- **Unknown-state strictness preserved:** `state_index`/`require_stage`/`is_terminal` raise on a foreign id;
  the board's extra-column path only triggers for a state valid for the row's resolved definition (rows are
  written through the validated door; startup audit guarantees registry-validity).

## 9. Keeping core free of per-type conditionals
No seam gains an `if ticket_type == "coding"` branch. Per-type behavior comes only from resolving the row's
`WorkflowDefinition` and reading its derived views — the same mechanism the write layer uses. The board's
"coding skeleton + append extras" rule is expressed over coding's own definition's `stage_ids`, and the
append path is type-agnostic. `_IN_PROGRESS_STATES` — the last hardcoded coding table in a read-model — is
deleted. Remaining hardcodings after this ticket: the coding-shaped `item_rollup` seed and the universal
`done`/`dropped` bookend literals — both out of 4a's named scope and noted in decisions.md.
