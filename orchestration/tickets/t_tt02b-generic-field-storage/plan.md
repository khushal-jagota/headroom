# t_tt02b — Implementation plan: Generic per-type field storage + Tier-2 scope

Contract-**shape** change. Three coupled moves:
1. **Field storage** — `TicketFields` goes from a fixed 6-attribute dataclass to a generic
   per-field-id slot map; the codec/accessors become generic over the type's declared `field_ids`;
   the coding-bound boundary (`_CODING_FIELD_IDS`, `machine.require_coding_field`) is lifted.
2. **Domain-contract widening (the P0 the first draft wrongly deferred)** — `Ticket.state`,
   `Ticket.ceiling`, `Decision.new_state`, `Decision.new_ceiling`, `ScopePair.next_ceiling`, and the
   generic resolution/admission state/ceiling/field params go **`TicketState`/`FieldName` → `str`**;
   `_row_to_ticket` stores the RAW string (no `TicketState(row[...])` re-wrap); every `.value` on a
   ticket's state/ceiling/field becomes `str(...)`. Without this, `create_ticket(ticket_type="probe")`
   writes ceiling `"needs_alpha"`, and the very next reload runs `_row_to_ticket` →
   `TicketState("needs_alpha")` → `ValueError` **before any `.value`** — probe fails at creation.
3. **Definition threading** — the full propose/accept/drop/scope drive path threads the per-row
   `WorkflowDefinition` (`resolution.decide_*`, `_accept_gating_proposal`, `plan_handoff_status` via
   `views.transition_effect`, `resolve_scope`/`validate_ceiling`, direct-scope
   `decide_scope_change`/`change_scope`, and admission), so propose/accept/scope operate per type.
   **External-work stays coding-only in t_tt02b** with an explicit non-coding rejection; genericizing
   it (the prefix-derivation, ingress, `supports_prefix_reconciliation`) is deferred to t_tt03.

**Coding behavior is byte-identical**: `TicketState`/`FieldName` are `StrEnum ⊆ str`, so a coding
`str`-typed state/ceiling/field carries the identical string, and `str(x)` equals the old `x.value`.
Existing tests that construct/attribute-access `TicketFields`/`ScopePair` get mechanical,
behavior-preserving edits (no outcome assertion changes).

The domain widening also **removes a strict-mypy lie**: today the `@overload`s claim
`advance_target(ticket.state)` returns `TicketState` because `ticket.state` is typed `TicketState`;
once it is `str`, the honest `str` return flows through and a foreign id no longer masquerades as a
`TicketState`.

Anchors are `file:line` at the tree read for this plan; the implementer re-confirms before editing.
D103's silent-pass invariant is updated: **no second PRODUCTION type may register until t_tt03**
threads external-work + finishes ingress (t_tt02b threads the in-app drive path and lifts field
storage/scope, but leaves external-work coding-bound).

---

## 0. Design question resolved — the new `TicketFields` shape

### Decision: `TicketFields` wraps an ordered `dict[str, FieldSlot]` keyed by field id. No coding names on the struct.

Rejected: keeping the six names as attributes with a compat shim. That re-privileges coding
inside the "generic" type (PRINCIPLES: name for what it is; do not fit-around) and leaves
`with_slot`/`fields_to_json` hardcoding six keys — exactly what this ticket removes.

New shape in `src/planner/tickets/contracts.py` (replacing the frozen 6-field dataclass at
`contracts.py:108-115`):

```python
@dataclass(frozen=True)
class TicketFields:                # tickets.fields JSON column, generic over the type's fields
    """An ordered map field_id -> FieldSlot. The key order is the definition's declared
    field order; the codec relies on it for a stable, byte-identical JSON key order."""
    slots: dict[str, FieldSlot] = field(default_factory=dict)

    @classmethod
    def empty(cls, field_ids: tuple[str, ...]) -> TicketFields:
        """A fresh set of empty slots, one per declared field id, in declared order."""
        return cls({fid: FieldSlot() for fid in field_ids})
```

Notes on the shape:
- `frozen=True` gives value-object immutability at the top level (matching the old struct,
  which was already immutable-by-convention through copy-on-write). The inner `FieldSlot`
  stays a mutable dataclass exactly as today (`contracts.py:101-105`) — unchanged; the
  copy-on-write discipline lives in `with_slot`, not in the slot.
- `slots` is a plain `dict[str, FieldSlot]` (insertion-ordered in 3.12). It is NOT deep-copied
  on read; `get_slot` returns the stored slot; `with_slot` builds a fresh dict (copy-on-write),
  so decision functions never mutate their input — the property `fields_codec`'s docstring
  already promises.
- The default `dict()` is only for a bare `TicketFields()` (used by no production path after
  this change); every real construction goes through `TicketFields.empty(field_ids)` or the
  codec. A frozen dataclass with a mutable default is safe here because `field(default_factory=dict)`
  makes a fresh dict per instance.

### The generic accessors (`fields_codec.py`)

Replace the `FieldName`-identity dispatch (`fields_codec.py:114-136`) with dict operations. The
accessors take a **`str` field id** (not `FieldName`), because a foreign type's field id is a
bare str:

```python
def get_slot(fields: TicketFields, field_id: str) -> FieldSlot:
    slot = fields.slots.get(field_id)
    if slot is None:
        raise PlannerError(
            ErrorCode.validation, "unknown ticket field", {"field": field_id}
        )
    return slot

def with_slot(fields: TicketFields, field_id: str, slot: FieldSlot) -> TicketFields:
    if field_id not in fields.slots:
        raise PlannerError(
            ErrorCode.validation, "unknown ticket field", {"field": field_id}
        )
    new = dict(fields.slots)          # copy-on-write; declared order preserved
    new[field_id] = slot
    return TicketFields(new)
```

- `get_slot`/`with_slot` accept `str`. Every caller passes either a `FieldName` (which *is* a
  `str` — `StrEnum`, so `str(FieldName.success) == "success"`; a `FieldName` value compares and
  hashes as its string) or a bare str. **Key form must be the plain string**: `FieldName.success`
  used as a dict key hashes equal to `"success"` (StrEnum), so `slots["success"]` and
  `slots[FieldName.success]` resolve the same entry. To keep keys canonical and avoid an enum
  leaking into the JSON later, `TicketFields.empty` and the codec store **plain `str` keys**;
  callers may pass a `FieldName` and it will match by string equality.
- Raising on an unknown field id is the new storage boundary (replaces `require_coding_field`):
  a field id the definition never declared cannot be stored, but it fails against *the
  definition's own field set*, not against the coding six.

### `FieldName` stays; `TicketFields` no longer references it

`FieldName` (the coding field enum, `contracts.py:43-49`) remains — it is coding's declared
field vocabulary, still used by coding call sites, the API field-param parse, and the coding
definition. It is simply no longer baked into the storage struct. The seam: coding call sites
keep passing `FieldName.success`; that value is a `str` and lands in the generic slot map.

---

## 1. Codec — generic over `field_ids` (`fields_codec.py`)

`_slot_to_dict` / `_slot_from_obj` / `_proposal_from_obj` / `_require` are unchanged
(`fields_codec.py:20-71`). The changes:

### `fields_to_json` (currently `fields_codec.py:31-40`) — iterate the slot map in order

```python
def fields_to_json(fields: TicketFields) -> str:
    payload = {fid: _slot_to_dict(slot) for fid, slot in fields.slots.items()}
    return json.dumps(payload)
```
Because a coding `TicketFields` is built with keys in declared order
(`kickoff, success, approach, plan, implementation, closeout`), the JSON key order is
byte-identical to today's hardcoded literal (Acceptance 1 — proven by a golden byte test).

### `fields_from_json` (currently `fields_codec.py:81-111`) — validate against the definition; drop the coding guard

```python
def fields_from_json(raw: str, definition: WorkflowDefinition | None = None) -> TicketFields:
    if definition is None:
        definition = coding_bridge.coding_definition()
    data: Any = json.loads(raw)
    _require(isinstance(data, dict))
    slots: dict[str, FieldSlot] = {}
    for field_id in coding_bridge.field_ids(definition):   # STRICT: each declared field present + decodes
        _require(field_id in data)
        slots[field_id] = _slot_from_obj(data[field_id])
    return TicketFields(slots)                              # LENIENT: extra top-level keys (e.g. "result") ignored
```

- **Delete `_CODING_FIELD_IDS`** (`fields_codec.py:74-78`) and the guard block
  (`fields_codec.py:84-94` — the `field_ids(definition) != _CODING_FIELD_IDS` raise). A
  registered non-coding field set now decodes into its own slots.
- STRICT-on-missing-declared is preserved (`_require(field_id in data)`); LENIENT-on-extra is
  preserved (unlisted top-level keys, e.g. the live legacy `result` key, are never read — D103).
- The construction at `fields_codec.py:104-111` (the six-kwarg `TicketFields(kickoff=…, …)`)
  collapses into `TicketFields(slots)`.

---

## 2. Boundary lift — `machine.require_coding_field` + `has_pending_gating_proposal` + admission

### Remove `machine.require_coding_field` (`machine.py:227-238`)

It exists only to reject a foreign field id from the fixed struct. The generic `get_slot`/
`with_slot` now enforce the same thing against the definition's own field set, so the function
is deleted, and its two call sites change:

**`machine.has_pending_gating_proposal` (`machine.py:241-247`)** — read the type's gate generically
(`state` widens to `str`, since `has_pending_parked_proposal(ticket)` at `machine.py:250-253` now
passes a `str` `ticket.state`, and the two board/sprint callers pass a `str` state):
```python
def has_pending_gating_proposal(
    state: str, fields: TicketFields, *, definition: WorkflowDefinition | None = None
) -> bool:
    field = gating_field(state, definition=definition)   # already definition-aware (machine.py:94)
    if field is None:
        return False
    return fields_codec.get_slot(fields, str(field)).proposal is not None
```
`gating_field` already returns `FieldName | str | None` from the definition; `str(field)` is the
slot key. `require_coding_field` is gone from the return path. The two read-model callers
(`views.py:268`, `sprints/views.py:123`) currently pass `TicketState(state)` — they may keep
passing the coding default (no definition) since board/sprint are made type-aware in Phase 4a; a
`TicketState` arg still satisfies the `str` param.

**`admission.check_agent_proposal` (`admission.py:59`, and `.value` at `admission.py:67, 79`)** —
this is a **necessary co-edit the BRIEF's "lift the boundary" implies** (the BRIEF names
`machine`, but `require_coding_field` has a second caller in admission that also uses `gating.value`).
`gating` here is `machine.gating_field(state, definition=defn)` → `FieldName | str | None`. After
line 51's `None` guard it is `FieldName | str`. Replace:
```python
    gating = machine.require_coding_field(gating)     # DELETE this line (admission.py:59)
```
and replace the two `gating.value` reads (`admission.py:67, 79`) with `str(gating)` (a `FieldName`
stringifies to its value; a foreign str is already the value). The `field != gating` comparison
(`admission.py:73`) stays correct: `field` is a `FieldName`, `gating` may be `FieldName | str`,
and StrEnum equality means `FieldName.success == "success"` is `True` — but to be identity-safe
for a foreign type, keep the comparison as `str(field) != str(gating)`.

**Widen `check_agent_proposal`'s `state`/`ceiling` params to `str` (domain widening, §3).** The
signature is `state: TicketState, ceiling: TicketState, at_cap: AtCap, field: FieldName`
(`admission.py:38-41`); `decide_file_proposal` passes `ticket.state`/`ticket.ceiling` — now `str`.
So `state: str`, `ceiling: str` (leave `at_cap: AtCap`, `field: FieldName` — `field` is a coding
caller's `FieldName`, used as `str(field)`). Its `.value` reads on state/ceiling/field
(`admission.py:48, 55, 68, 69, 78, 80`) → `str(...)`.

> Risk note: `check_agent_proposal`'s signature already threads `definition` (`admission.py:43`);
> this edit stays within the boundary-lift, adds no new parameter, and is coding byte-identical
> (for coding, `str(FieldName.success) == "success"` exactly as `.value` gave).

---

## 3. Tier-2 scope widen (`contracts.py` + `machine.py` + `resolution.py`)

### `ScopePair.next_ceiling` type (`contracts.py:119-127`)

Today `NextCeiling = TicketState | Literal["none"]` (`contracts.py:120`) and
`ScopePair.next_ceiling: NextCeiling` (`contracts.py:125`). Widen to accept a non-coding ceiling id:

```python
NO_FURTHER: Final = "none"
# A ceiling id is any member of the type's ceiling_range (a str); "none" is the wire sentinel
# meaning "the newly entered state". For coding these ids are TicketState values.
NextCeiling = str | Literal["none"]     # was: TicketState | Literal["none"]

@dataclass(frozen=True)
class ScopePair:
    next_ceiling: str          # a resolved ceiling id (never "none" — resolve_scope concretizes it)
    at_cap: AtCap
```
`ScopePair.next_ceiling` becomes a plain `str` (a resolved ceiling id). `TicketState` is a
`StrEnum`, so a coding `ScopePair(next_ceiling=TicketState.needs_success, ...)` still type-checks
as `str` and stringifies identically. The `NextCeiling` alias (the *input* to `resolve_scope`)
widens to `str | "none"` since the API/CLI parse of `next_ceiling` is a raw wire string.

### `machine.validate_ceiling` (`machine.py:182-189`)

Currently typed `ceiling: TicketState`. Widen to `str`:
```python
def validate_ceiling(ceiling: str, *, definition: WorkflowDefinition | None = None) -> None:
    defn = definition or coding_bridge.coding_definition()
    if str(ceiling) not in coding_bridge.views.ceiling_range(defn):
        raise PlannerError(
            ErrorCode.scope_invalid, "ceiling must be a linear state", {"ceiling": str(ceiling)}
        )
```
`.value` at line 188 → `str(ceiling)` (works for both `TicketState` and a foreign str).

### `machine.resolve_scope` (`machine.py:192-224`)

Widen `new_state` and the branch logic to strings and the definition's `ceiling_range`:
```python
def resolve_scope(
    new_state: str,
    next_ceiling: NextCeiling | None,
    at_cap: AtCap | None,
    *,
    definition: WorkflowDefinition | None = None,
) -> ScopePair:
    defn = definition or coding_bridge.coding_definition()
    if next_ceiling is None or at_cap is None:
        ... # unchanged scope_missing (machine.py:200-208)
    if next_ceiling == NO_FURTHER:
        return ScopePair(next_ceiling=str(new_state), at_cap=at_cap)     # newly entered state
    ceiling_id = str(next_ceiling)
    if ceiling_id not in coding_bridge.views.ceiling_range(defn) or (
        state_index(ceiling_id, definition=defn) < state_index(str(new_state), definition=defn)
    ):
        raise PlannerError(
            ErrorCode.scope_invalid,
            "next_ceiling must be at or beyond the new state",
            {"next_ceiling": ceiling_id, "new_state": str(new_state)},
        )
    return ScopePair(next_ceiling=ceiling_id, at_cap=at_cap)
```
- The `not isinstance(next_ceiling, TicketState)` "unknown next_ceiling" branch
  (`machine.py:212-215`) is **removed**: an unknown ceiling is now caught by the
  `not in ceiling_range` check (which raises `scope_invalid` "next_ceiling must be at or beyond
  the new state" with the same `ErrorCode`). For coding this is behavior-identical — the
  membership test rejects the same set. (If a byte-exact message on the *unknown* case matters to
  a test, keep an explicit membership pre-check raising the old "unknown next_ceiling" message;
  see §6 test notes — the current suite asserts the `ErrorCode`, not the message.)
- `.value` reads at `machine.py:222` → `str(...)`.

### `resolution._accept_gating_proposal` ceiling handling (`resolution.py:74-97`)

Currently `new_ceiling: TicketState | None` and `assert isinstance(ceiling, TicketState)`
(`resolution.py:74, 78`). Widen:
```python
new_ceiling: str | None = None
...
if scope is not None:
    new_ceiling = scope.next_ceiling      # already a resolved ceiling id (str); drop the isinstance assert
    new_at_cap = scope.at_cap
    events.append(EventSpec(EventKind.scope_changed, {
        "ceiling": scope.next_ceiling, "at_cap": scope.at_cap.value, "cause": CAUSE_ONWARD_SCOPE,
    }))
```
The `EventSpec` payload used `ceiling.value` (`resolution.py:85`) → `scope.next_ceiling`
(already the string). For coding the emitted `"ceiling"` string is identical.

### `Decision.new_ceiling`/`new_state` (`decisions.py:23-24`) and `_apply_decision` (`data.py:124-131, 151-152`)

`Decision.new_state: TicketState | None` → `str | None`; `Decision.new_ceiling: TicketState | None`
→ `str | None` (`decisions.py:23-24`). In `_apply_decision`:
- `new_state`/`new_ceiling` merge with `ticket.state`/`ticket.ceiling` (`data.py:125-126`) — both
  now `str` (see the domain-widening block below), so the merged value is a plain `str`.
- The pre-persist door `ticket_type_guard.resolve_and_validate(..., state=new_state.value,
  ceiling=new_ceiling.value)` (`data.py:131`) → `state=str(new_state), ceiling=str(new_ceiling)`.
- The SQL binds `new_state.value`/`new_ceiling.value` (`data.py:151-152`) → `str(new_state)` /
  `str(new_ceiling)`. `str()` on a coding `TicketState` yields the identical stored string and
  also works for a foreign id — no AttributeError.

### THE DOMAIN-CONTRACT WIDENING (P0) — `Ticket`/`Decision` state/ceiling/field → `str`

The first draft wrongly deferred this. It is **mandatory**: `create_ticket(ticket_type="probe")`
writes ceiling `"needs_alpha"`; the immediate `_load_ticket` → `_row_to_ticket` re-wraps
`TicketState(row["ceiling"])` (`data.py:84`) → `ValueError` **at creation**, before any codec or
`.value`. So probe cannot even be created without this. The widening:

**Type flips (all `TicketState`/`FieldName` → `str` for a *ticket's* state/ceiling/field):**
- `Ticket.state: str`, `Ticket.ceiling: str` (`contracts.py:227, 235`). `at_cap`/`ticket_status`/
  `priority`/`implementer` stay their enums (not per-type). `ticket_type: str` already.
- `Decision.new_state: str | None`, `Decision.new_ceiling: str | None` (`decisions.py:23-24`).
- `ScopePair.next_ceiling: str`; `NextCeiling = str | Literal["none"]` (§3 above).
- `_state_change(old: str, new: str, cause: str)` (`resolution.py:36`).
- The generic resolution/admission/machine state/ceiling/field params → `str` (they are already
  string-native at Tier-1; this makes the *ticket-carried* args honest). The data-layer
  proposal/accept/note field params (`field: FieldName`) may stay `FieldName` at the public
  boundary for coding callers, but internally are used as `str(field)` — see §5.

**`_row_to_ticket` stores the RAW string (`data.py:76, 84`):**
```python
    state=str(row["state"]),      # was TicketState(row["state"])
    ceiling=str(row["ceiling"]),  # was TicketState(row["ceiling"])
```
`resolve_and_validate` (`data.py:70`) already validated the string against the type before this,
so no re-wrap is needed; dropping the enum construction is exactly what unblocks a foreign id.
`at_cap=AtCap(row["at_cap"])` (`data.py:85`) stays — `AtCap` is type-independent.

**Convert EVERY `.value` on a ticket's state/ceiling/field to `str(...)`.** Grepped write path
(these are the sites, ~30, not "two SQL binds"):
- `resolution.py`: `_state_change` `old.value`/`new.value` (`:37`); event `field.value`
  (`:59, 66, 129, 135, 162, 183, 209, 213, 219, 225, 263`); `ticket.state.value`
  (`:219, 243, 256`); direct-scope `ceiling.value` (`:308`) — all → `str(...)`.
- `admission.py`: `state.value` (`:48, 55, 68, 80`), `ceiling.value` (`:69`), `field.value` (`:78`),
  and the already-covered `gating.value` (`:67, 79` → `str(gating)`, §2).
- `machine.py`: `ceiling.value` (`:188`), `next_ceiling.value`/`new_state.value` (`:222`) — §3.
- `external_work.py`: coding-only in t_tt02b (Ruling 2 / §4), so its `.value` sites stay; but the
  `definition=` thread + the non-coding rejection are added. (The state/field `.value` there remain
  valid because external-work only ever runs on coding `TicketState`/`FieldName` values.)
- `data.py`: `new_state.value`/`new_ceiling.value` (`:131, 151, 152`, above); `ticket.state.value`
  in the recap-field guard (`:686`); the note event `field.value` (`:976`) → `str(field)`; the
  `create_ticket` event `FieldName.kickoff.value` (`:340`) is a *literal enum*, leave as-is
  (it is not a ticket-carried value and is coding-fixed intake).

**READ path — mechanical `str()` wrap (NOT genericizing structure):**
- `views.py:64` `"state": ticket.state.value` → `str(ticket.state)`; `:72`
  `"ceiling": ticket.ceiling.value` → `str(ticket.ceiling)`; `:111` `params.append(state.value)`
  → `params.append(str(state))` (the `?state=` filter param); `:191` `copy_text` `ticket.state.value`
  → `str(ticket.state)`. **`copy_text`'s six-field body stays coding-fixed** (§5) — only the
  state string wraps.
- `employee_step_runner.py:45` `ticket.state.value` → `str(ticket.state)`.
- `sprints/views.py` any `state.value` on a ticket → `str(...)` (re-grep at ticket time).

**Completeness step (required):** after the edits, run
`grep -rnE '\.(state|ceiling)\.value|\bfield\.value|\bgating\.value' src/planner/tickets src/planner/runtime src/planner/sprints`
and confirm **zero remain on any ticket-carried state/ceiling/field in a reachable path** (literal
`FieldName.kickoff.value`/`TicketState.dropped.value` constants and `AtCap`/`TicketStatus`/`Priority`
`.value` are fine — they are type-independent enums). This grep is the acceptance gate for the
widening.

**Why coding stays byte-identical + why this is honest.** `TicketState`/`FieldName` are `StrEnum`,
so a coding `str`-typed `ticket.state` still *holds* `"needs_success"` and `str(x) == x.value`
exactly. The literal `==` comparisons in `decide_drop`/`decide_state_jump`
(`resolution.py:153, 202, 239, 271, 273, 275, 280, 288, 292, 296-297`) keep comparing against
`TicketState.dropped`/`.done`/`.needs_kickoff` members; `str == StrEnum` is `True` when equal, so
`ticket.state == TicketState.dropped` still holds with `ticket.state` a `str` — no logic change,
and the reserved bookends stay named literally (invariant 1). And it removes the strict-mypy lie:
with `ticket.state: str`, `advance_target(ticket.state)` resolves the *string* overload returning
`str`, so a foreign id is no longer mis-inferred as a `TicketState`.

**Direct-scope params also widen** (Ruling 3): `decide_scope_change(ticket, ceiling: str, …)`
(`resolution.py:300`) and `change_scope(…, ceiling: str, …)` (`data.py:940`) → `str`, and
`decide_scope_change` threads `definition` into `machine.validate_ceiling(ceiling,
definition=defn)` (`resolution.py:304`) so a probe ceiling change validates against probe's range,
not coding's. See §4.

---

## 4. Thread the definition through the in-app drive path (D103 invariant)

**Updated D103 invariant.** t_tt02b threads the **in-app drive path** (propose / accept / drop /
edit-value / return-for-revision / direct-scope) + `plan_handoff_status`, and lifts field storage
and scope. **External-work stays coding-only** (Ruling 2) — genericizing it is deferred to t_tt03.
So the silent-pass guard becomes: **no second PRODUCTION type may register until t_tt03** threads
external-work reconcile/create + finishes ingress. t_tt02x (fixture `probe`, test-only) drives the
in-app path this ticket threads, and does **not** exercise external-work.

`data.py` already resolves the per-row `WorkflowDefinition` (`ticket_type_guard.resolve_and_validate`
returns it in `_row_to_ticket` `data.py:70`; and each writer can call
`coding_bridge.require(ticket.ticket_type)` as `file_current_proposal_with_recap` already does at
`data.py:680`).

### `resolution.decide_file_proposal` (`resolution.py:100-138`)
Add `*, definition: WorkflowDefinition | None = None`; pass it into the three engine calls that
default to coding today:
- `admission.check_agent_proposal(ticket.state, ticket.ceiling, ticket.at_cap, field,
  definition=definition)` (`resolution.py:104`)
- `machine.auto_accept_target(ticket.state, ticket.ceiling, field, definition=definition)`
  (`resolution.py:107`)
- the `_accept_gating_proposal(...)` call (`resolution.py:108`) → forward `definition`
  (its `machine.advance_target(ticket.state)` at `resolution.py:53` must become
  `advance_target(ticket.state, definition=definition)`).

### `resolution._accept_gating_proposal` (`resolution.py:40-97`)
Add `*, definition: WorkflowDefinition | None = None`; use it in `advance_target` (`resolution.py:53`).

### `resolution.decide_accept` (`resolution.py:141-190`)
Add `*, definition`; thread into:
- `machine.gating_field(ticket.state, definition=definition)` (`resolution.py:165`)
- `machine.advance_target(ticket.state, definition=definition)` (`resolution.py:166`)
- `machine.resolve_scope(new_state, next_ceiling, at_cap, definition=definition)`
  (`resolution.py:167`)
- the `_accept_gating_proposal(..., definition=definition)` call (`resolution.py:168`).

### `resolution.decide_edit_value` (`resolution.py:193-228`)
Add `*, definition`; thread into `machine.field_is_passed(field, ticket.state, definition=definition)`
(`resolution.py:215`).

### `resolution.decide_return_for_revision` (`resolution.py:231-266`)
Add `*, definition`; thread into `machine.gating_field(ticket.state, definition=definition)`
(`resolution.py:251`).

### `resolution.decide_drop` (`resolution.py:286-297`) and `decide_state_jump` (`resolution.py:269-283`)
No new engine lookups here, but the domain widening (§3) touches them: `_state_change(ticket.state,
TicketState.dropped, …)` (`resolution.py:296`) now passes a `str` `ticket.state` and a reserved
`TicketState.dropped` literal (StrEnum → `str`), and `Decision(new_state=TicketState.dropped)`
(`resolution.py:297`) assigns a `str`-compatible value — no signature change, the literal reserved
bookends stay named. The `== TicketState.dropped/.done/.needs_kickoff` comparisons
(`resolution.py:271, 273, 275, 288, 292`) stay literal (`str == StrEnum` holds). `decide_drop` does
not need `definition` (drop is universal); leave it unthreaded.

### `resolution.decide_scope_change` (`resolution.py:300-311`) — thread direct scope (Ruling 3)
Add `*, definition: WorkflowDefinition | None = None` and `ceiling: str`; thread into
`machine.validate_ceiling(ceiling, definition=definition)` (`resolution.py:304`). Today it calls
`validate_ceiling(ceiling)` with no definition, so a probe ceiling change would validate against
**coding's** range — the direct-scope bug Ruling 3 names. The `.value` in the `scope_changed` event
(`resolution.py:308`) → `str(ceiling)`. Its `data.py` caller `change_scope` (`data.py:947`) resolves
`defn = coding_bridge.require(ticket.ticket_type)` and passes `definition=defn` (see §4 data.py).

### `machine.plan_handoff_status` (`machine.py:256-265`)
Currently a hardcoded `old_state == needs_plan and new_state == needs_implementation` check
(coding-specific). The registry already models this as `views.transition_effect` (`views.py:160`).
Rewrite `plan_handoff_status` to read the definition's transition hook:
```python
def plan_handoff_status(
    implementer: Implementer | None,
    old_state: str,               # was TicketState — ticket.state is str now
    new_state: str | None,        # was TicketState | None — Decision.new_state is str | None
    *,
    definition: WorkflowDefinition | None = None,
) -> TicketStatus | None:
    if new_state is None or implementer is None:
        return None
    defn = definition or coding_bridge.coding_definition()
    effect = coding_bridge.views.transition_effect(
        defn, str(implementer), str(old_state), str(new_state)
    )
    return TicketStatus(effect) if effect is not None else None
```
- Coding parity: **confirmed** — the coding definition declares exactly this hook at
  `ticket_types/coding.py:86-93` (`old_state=needs_plan`, `new_state=needs_implementation`,
  `implementer=khushal`, `effect=user_takeover`), wired into `CODING_DEFINITION.transition_hooks`
  (`coding.py:102`). So `views.transition_effect` reproduces `plan_handoff_status` exactly. For
  every other implementer/transition, `transition_effect` returns `None` → identical to today.
- `TicketStatus(effect)` maps the hook's `effect` string (`"user_takeover"`) back to the enum.

### `external_work` — stays coding-only in t_tt02b, with an EXPLICIT non-coding rejection (Ruling 2)

t_tt02b does **NOT** genericize external-work. The first draft's `_PREFIX_COUNT` derivation had a
real bug: it iterated `field_ids(defn)` as the *reconciliation prefix order*, but the registry does
**not** enforce field-declaration-order == stage-gate-order (a `(kickoff, beta, alpha)` field order
with a `needs_alpha → needs_beta` stage order would mis-validate the prefix). The correct derivation
keys off the non-terminal stages' `gating_field` values (the enforced one-to-one gate model), not
`field_ids` — and that, plus ingress widening and `supports_prefix_reconciliation`, is a coherent
t_tt03 unit. So:

- **`external_work.decide_external_work` (`external_work.py:37-146`) keeps `_FIELD_ORDER`
  (`:19-26`) and `_PREFIX_COUNT` (`:27-34`) exactly as-is, coding-bound.** Its `.value` reads on
  states/fields stay (external-work only runs on coding `TicketState`/`FieldName` values). No
  genericization.
- **Add `*, definition: WorkflowDefinition | None = None` to the signature for uniformity**, and at
  the top add an **explicit non-coding rejection** — fail loud, never silently run coding logic on a
  foreign type:
  ```python
  defn = definition or coding_bridge.coding_definition()
  if defn.type_id != "coding":
      raise PlannerError(
          ErrorCode.validation,
          "external-work reconciliation is coding-only until t_tt03",
          {"type_id": defn.type_id},
      )
  ```
  (Keep the existing inline `coding_definition()` usage at `external_work.py:47` for the
  `ceiling_range` guard, or reuse `defn`.)
- `create_ticket_from_external_work`'s hardcoded initial state `needs_success`
  (`data.py:418`) stays as-is — coding-only.
- `provided_values` **keeps its `Mapping[FieldName, str]` type** (`external_work.py:40`,
  `data.py:359, 455`). No key-type change — that (and the wire ingress) is t_tt03.

**Deferred to t_tt03 (named, not dropped):** replace `_PREFIX_COUNT`/`_FIELD_ORDER` (deriving the
reconciliation prefix from non-terminal stages' `gating_field` values, respecting the enforced gate
model — NOT `field_ids`); honor `supports_prefix_reconciliation` (`contracts.py:57`, coding=True at
`coding.py:103`); widen external-work ingress (wire contracts + API marshal); add a permanent golden
test pinning coding's exact prefix map; test probe external-work end-to-end. The D103 invariant
update (above) reflects this: external-work threading is a t_tt03 precondition for a 2nd production
type.

**data.py external-work callers.** `create_ticket_from_external_work` (`data.py:435`) and
`reconcile_ticket_from_external_work` (`data.py:485`) pass `definition=coding_bridge.require(
ticket.ticket_type)` for uniformity; for a coding ticket this is the coding definition (passes the
`type_id == "coding"` guard), and a (test-only, not yet reachable) non-coding ticket would hit the
explicit rejection rather than silently reconciling with coding's prefix.

### `data.py` — pass the definition each writer already can resolve
Each writer resolves `defn = coding_bridge.require(ticket.ticket_type)` after `_load_ticket`
(the pattern at `data.py:680`), then forwards `definition=defn`:
- `file_proposal` (`data.py:646`): `resolution.decide_file_proposal(ticket, field, body, actor,
  now, definition=defn)`; `machine.plan_handoff_status(..., definition=defn)` (`data.py:652`).
- `file_current_proposal_with_recap` (`data.py:688`): already resolves `defn` at `data.py:680` —
  pass it to `decide_file_proposal` (`data.py:688`) and `plan_handoff_status` (`data.py:698`).
- `accept_proposal` (`data.py:719`): `resolution.decide_accept(..., definition=defn)`;
  `plan_handoff_status(..., definition=defn)` (`data.py:723`).
- `create_ticket_from_external_work` (`data.py:435`) and
  `reconcile_ticket_from_external_work` (`data.py:485`):
  `external_work.decide_external_work(ticket, target_state, provided_values, definition=defn)`
  where `defn = coding_bridge.require(ticket.ticket_type)`.
- `decide_edit_value` caller (`edit_field_value` writer) and `decide_return_for_revision` caller
  (`return_for_revision` writer): resolve `defn = coding_bridge.require(ticket.ticket_type)` and
  pass `definition=defn`.
- `change_scope` (`data.py:947`) — Ruling 3: resolve `defn = coding_bridge.require(
  ticket.ticket_type)` and call `resolution.decide_scope_change(ticket, ceiling, at_cap, actor,
  definition=defn)` (`data.py:947`), so a probe ceiling change validates against probe's range, not
  coding's. `ceiling` param on `change_scope`/`decide_scope_change` widens to `str` (§3).

The ONLY drive paths left coding-only in t_tt02b are the two external-work writers (Ruling 2). For
coding, every `defn` is the coding definition, so behavior is byte-identical; the threading is what
lets a synthetic-type ticket route correctly through propose/accept/drop/scope (Acceptance 5).

---

## 5. The attribute-access rewrite table

**`src/` — every `TicketFields` attribute-access site → `get_slot(fields, "<id>")`:**

| File:line | Current | Rewrite |
|---|---|---|
| `fields_codec.py:33-38` | `_slot_to_dict(fields.kickoff)` … `.closeout` | generic dict comprehension (§1) |
| `fields_codec.py:104-111` | `TicketFields(kickoff=decoded["kickoff"], …)` | `TicketFields(slots)` (§1) |
| `fields_codec.py:114-136` | `get_slot`/`with_slot` `FieldName`-identity dispatch | generic dict ops (§0) |
| `views.py:195-211` (`copy_text`) | `fields.kickoff.value`, `fields.kickoff.user_note`, … ×6 | `get_slot(fields, "kickoff").value`, `.user_note`, … — see note |
| `data.py:279-285` (`create_ticket`) | `TicketFields(kickoff=FieldSlot(…))` | `TicketFields.empty(field_ids)` then `with_slot("kickoff", FieldSlot(…))`, or `TicketFields({**empty, "kickoff": …})` — see note |
| `data.py:382` (external create) | `TicketFields(kickoff=FieldSlot(value=kickoff_note))` | same empty-then-set pattern |
| `data.py:969-971` (`set_field_user_note`) | `get_slot(ticket.fields, field)` / `with_slot(..., field, …)` | pass `str(field)`; already generic-shaped |

**`src/` — every `.value` on a ticket's state/ceiling/field → `str(...)` (the domain-widening, §3).**
Write path: `resolution.py:37, 59, 66, 85, 129, 135, 162, 183, 209, 213, 219, 225, 243, 256, 263,
308`; `admission.py:48, 55, 68, 69, 78, 80`; `machine.py:188, 222`; `data.py:131, 151, 152, 686,
976`. Read path (mechanical `str()` wrap, structure unchanged): `views.py:64, 72, 111, 191`
(`copy_text` state line); `employee_step_runner.py:45`; `sprints/views.py` (re-grep). LEAVE literal
enum constants (`FieldName.kickoff.value` `data.py:340`, any `TicketState.X.value` / `AtCap` /
`TicketStatus` / `Priority` `.value`). Acceptance gate: the completeness grep in §3 returns zero
ticket-carried `.state/.ceiling/field .value` in a reachable path.

**`copy_text` note (important — do NOT genericize the output here).** `copy_text`
(`views.py:161-211`) renders a fixed labelled block of the six coding fields. Making it a generic
loop over `field_ids` is a **Phase 4a (t_tt04a) read-model change**, explicitly out of scope here
(master PLAN Phase 4a). For t_tt02b the rewrite is **mechanical and byte-identical**: replace each
`fields.kickoff.value` with `get_slot(fields, "kickoff").value` (and `.user_note`), leaving the
literal labels and order exactly as-is. Coding output is unchanged (Acceptance 1). Alternatively,
since `copy_text` reads coding fields on a coding-only production, a thin local
`slot = lambda fid: get_slot(fields, fid)` keeps the diff tiny.

**`create_ticket` construction note.** The old `TicketFields(kickoff=FieldSlot(proposal=…))`
built all six slots with `kickoff` populated. Replace with:
```python
defn = coding_bridge.require(ticket_type)          # already required at data.py:276
initial_fields = TicketFields.empty(coding_bridge.field_ids(defn))
initial_fields = fields_codec.with_slot(
    initial_fields, "kickoff",
    FieldSlot(value=None, proposal=Proposal(body=kickoff_note, proposed_by=actor, created_at=now)),
)
```
This is generic (a synthetic type with a different first field would still work) and byte-identical
for coding (same six keys, `kickoff` populated). Same pattern for `data.py:382`.

**`seed/importer.py:229-241`** already writes a **raw dict** (`{"kickoff": {…}, "success": {…}, …}`)
straight to JSON — it does NOT construct a `TicketFields`, so it needs **no change** for the shape.
(It is coding-hardcoded; making it type-driven is Phase 3 / t_tt03, out of scope. Its
`ticket.success`/`ticket.approach` at `importer.py:234-235` are the **seed dataclass**'s
attributes, not `TicketFields` — unrelated.) Confirmed: not in the blast radius.

**`tests/` — the mechanical blast radius (85 attribute-access hits + 5 `TicketFields(...)`
constructions):**

| Test file | Hits | Kind |
|---|---|---|
| `tests/unit/test_tickets_engine.py` | ~46 | `t.fields.<name>.value/.proposal/.user_note`, `parsed.<name>.*` |
| `tests/unit/test_engine_parameterization.py` | ~13 | same + `TicketFields` round-trip |
| `tests/unit/test_value_edit_logic.py` | 9 | `.fields.<name>.value/.proposal` |
| `tests/unit/test_ticket_lifecycle.py` | 6 | `.fields.<name>.value/.proposal` |
| `tests/unit/test_employee_step_runner.py` | 6 | `.fields.<name>.value/.proposal` |
| `tests/unit/test_ticket_readiness_loop.py` | 4 | `.fields.<name>.proposal/.value` |
| `tests/unit/test_readiness_actions.py` | 2 | `.fields.success.proposal` |
| `tests/unit/test_return_for_revision.py` | 1 | `.fields.plan.proposal` |
| `tests/typing/tt01_overload_cases.py` | — | `has_pending_gating_proposal` type assertion (may need retype) |

Every one is `X.fields.<field>.<attr>` → `fields_codec.get_slot(X.fields, "<field>").<attr>`
(or a test helper `slot(X, "<field>")`). **All are field-access reads inside assertions; none
asserts an outcome that changes.** Example: `assert t.fields.success.value == "success proposal"`
→ `assert get_slot(t.fields, "success").value == "success proposal"` — same asserted value.

The 5 test `TicketFields(...)` constructions rebuild to `TicketFields.empty(...)` +/or the slot-map
constructor with the same slots — the constructed *content* is unchanged. Recommendation: add a
tiny test helper (e.g. in a shared conftest or the codec's public surface) so the churn is a
find-replace to `slot(fields, "success")`, keeping each edit a one-liner and reviewable as
mechanical.

**Churn estimate (revised for the domain widening).** Three mechanical passes:
1. **`TicketFields` attribute-access + construction** — ~7 `src/` sites (`fields_codec`, `data`,
   `views` copy_text body, `set_field_user_note`) + **~85 test assertion rewrites across 8 files** +
   5 test `TicketFields(...)` constructions. All mechanical `X.fields.<name>.<attr>` →
   `get_slot(X.fields, "<name>").<attr>`; no outcome assertion changes.
2. **`.value` → `str()` domain widening** — **~30 `src/` sites codebase-wide** (write path in
   `resolution`/`admission`/`machine`/`data`; read path in `views`/`employee_step_runner`/`sprints`),
   all mechanical, gated by the completeness grep (§3). Tests that assert `event["from"]`/`"to"`/
   `"ceiling"`/`"field"` payloads are **unaffected** — those payloads were already strings
   (`.value`), and `str()` produces the identical string. A handful of tests that construct a
   `Ticket`/`Decision` with `TicketState.X`/`FieldName.X` still pass (StrEnum ⊆ str); a few may
   read `ticket.state`/`.ceiling` expecting a `TicketState` and comparing `== TicketState.X` — still
   `True` (StrEnum equality). Expect **near-zero test-value changes** from this pass, only possible
   type-annotation touch-ups in `tests/typing/`.
3. **Definition threading** — signature-only edits on ~8 resolution/machine functions + their
   `data.py` callers; no test-value changes (coding resolves the same definition).

Net: the widening makes the `src/` churn **codebase-wide but purely mechanical** (`.value → str()`,
`TicketState(row) → str(row)`); the test churn is dominated by pass 1 (~85 field-access rewrites),
all behavior-preserving. Assert in the report: no behavior/outcome assertion changed (Acceptance 1).

---

## 6. Tests (new + changed), with asserted values

Add to `tests/unit/` (co-located with the existing engine/codec suites). Use
`coding_bridge.set_registry_for_test(reg)` to install a synthetic `(kickoff, alpha, beta)`
definition; clear it in teardown.

### T1 — Coding parity, byte-identical `fields_to_json` (Acceptance 1) — REQUIRED golden byte test
- Build a coding `TicketFields` via `TicketFields.empty(coding field_ids)` with `success.value="s"`.
- `assert fields_to_json(fields) == '{"kickoff": {"value": null, "proposal": null, "user_note":
  null}, "success": {"value": "s", "proposal": null, "user_note": null}, "approach": {...}, "plan":
  {...}, "implementation": {...}, "closeout": {...}}'` — the **exact byte string** and key order
  today's hardcoded codec emits. Pin the literal (Ruling 1's "add a golden test that coding
  `fields_to_json` bytes are unchanged"); a key-order or whitespace drift fails it.
- `fields_from_json(that_json)` round-trips to an equal `TicketFields`.
- A coding ticket driven through create → propose → accept produces the same stored `fields` JSON
  bytes as on `main` for the same inputs (snapshot-compare against a captured baseline).

### T2 — Generic storage round-trip for a synthetic `(kickoff, alpha, beta)` definition (Acceptance 2)
- Register `probe`-like defn (stages `needs_kickoff → needs_alpha → needs_beta → done`, fields
  `kickoff, alpha, beta`) via `set_registry_for_test`.
- `f = TicketFields.empty(("kickoff","alpha","beta"))`.
- `f2 = with_slot(f, "alpha", FieldSlot(value="A"))`.
- `assert get_slot(f2, "alpha").value == "A"` and `assert get_slot(f2, "beta").value is None` and
  `assert get_slot(f2, "kickoff").value is None` (copy-on-write leaves others intact; `f`
  unchanged: `assert get_slot(f, "alpha").value is None`).
- `raw = fields_to_json(f2)`; `assert fields_from_json(raw, probe_defn)` reproduces the slots;
  `assert json.loads(raw) == {"kickoff": {...}, "alpha": {"value":"A",...}, "beta": {...}}`
  (declared key order).
- Boundary: `assert get_slot(f2, "success")` raises `PlannerError(validation, "unknown ticket
  field", {"field": "success"})` — the storage boundary now rejects against the definition's own
  set, not the coding six.

### T3 — Boundary lifted (Acceptance 3)
- `fields_from_json(json_with_kickoff_alpha_beta, probe_defn)` **succeeds** (no
  "field storage is coding-bound" raise). Assert the decoded `alpha` slot value.
- A JSON missing a declared field → `PlannerError(validation, "corrupt ticket fields JSON")`
  (STRICT-on-missing preserved). A JSON with an extra `"result"` key → succeeds, extra ignored
  (LENIENT-on-extra preserved).
- `machine.require_coding_field` no longer exists — assert via `not hasattr(machine,
  "require_coding_field")` (or simply that its former callers work for a foreign gate).

### T4 — Tier-2 generic (Acceptance 4)
- `resolve_scope("needs_alpha", "needs_beta", AtCap.stop, definition=probe_defn)` →
  `ScopePair(next_ceiling="needs_beta", at_cap=AtCap.stop)` (a **non-coding ceiling id** carried
  in `ScopePair`).
- `resolve_scope("needs_alpha", NO_FURTHER, AtCap.propose, definition=probe_defn)` →
  `ScopePair(next_ceiling="needs_alpha", at_cap=AtCap.propose)`.
- `resolve_scope("needs_beta", "needs_alpha", …, definition=probe_defn)` raises
  `scope_invalid` (ceiling before new state).
- `validate_ceiling("needs_beta", definition=probe_defn)` returns None;
  `validate_ceiling("needs_kickoff", definition=probe_defn)` raises `scope_invalid` (kickoff not
  in the ceiling range).
- `has_pending_gating_proposal(state="needs_alpha", fields_with_alpha_proposal,
  definition=probe_defn)` is `True`; reads the *type's* gate (`alpha`), not coding's.
- Coding parity: `resolve_scope(TicketState.needs_approach, TicketState.needs_plan, AtCap.stop)`
  (no definition) → `ScopePair(next_ceiling="needs_plan", at_cap=stop)` — unchanged.

### T5 — decide_* threaded (Acceptance 5, unit-level)
Register the synthetic type via `coding_bridge.set_registry_for_test(reg)` (clear in teardown), so
the engine + any `_row_to_ticket` resolve it. Build a `Ticket` with **bare strings** — the domain
widening (§3) makes `Ticket.state`/`.ceiling` plain `str`, and the probe field id is a bare `str`,
so **do NOT** use `FieldName("alpha")` (impossible — `FieldName` has no `alpha` member; that was the
first draft's bug):
- `ticket = Ticket(..., ticket_type="probe", state="needs_alpha", ceiling="needs_beta",
  fields=TicketFields.empty(("kickoff","alpha","beta")), ...)` — plain string state/ceiling.
- **Propose (parks):** with `ceiling="needs_alpha"` (== state) and `at_cap=propose`,
  `decide_file_proposal(ticket, field="alpha", body="B", actor="agent", now=…,
  definition=probe_defn)` → parks: `get_slot(new_fields, "alpha").proposal.body == "B"`,
  `new_state is None`.
- **Propose (auto-accepts):** with `ceiling="needs_beta"` (> state), same call → auto-accepts:
  `get_slot(new_fields, "alpha").value == "B"`, `new_state == "needs_beta"` (probe's
  `advance_target("needs_alpha")`), `proposal_accepted` event `"field": "alpha"`.
- **Direct accept:** on a parked ticket, `decide_accept(ticket, "alpha", actor, edited_body=None,
  next_ceiling="needs_beta", at_cap=AtCap.stop, definition=probe_defn)` → `new_state == "needs_beta"`,
  `proposal_accepted` `"field": "alpha"`, `scope_changed` `"ceiling": "needs_beta"`.
- **Direct scope (Ruling 3):** `decide_scope_change(ticket, "needs_beta", AtCap.stop, actor,
  definition=probe_defn)` succeeds; `decide_scope_change(ticket, "needs_success", …,
  definition=probe_defn)` raises `scope_invalid` (proves it validates against probe's range, not
  coding's — the bug Ruling 3 names).
- This proves propose/accept/scope on a synthetic-type ticket routes to **that type's field / order /
  ceiling**. (Full `probe` end-to-end through the DB is t_tt02x; external-work is t_tt03.)

### T7 — Domain widening: probe survives create+reload (the P0 regression guard)
Register probe via `set_registry_for_test`; `create_ticket(conn, ticket_type="probe", ...)` then
`read_ticket(conn, id)` — asserts the reload does **not** raise (the `_row_to_ticket` `str(row)` fix;
this is the exact path that `ValueError`d in the first draft). Assert `ticket.state == "needs_kickoff"`,
`ticket.ceiling == "needs_alpha"` (probe's default ceiling), and that `str(ticket.state)` /
`str(ticket.ceiling)` are plain strings. (t_tt02x owns the full end-to-end gate drive; T7 is the
narrow reload guard that belongs with the widening.)

### T6 — Single-writer intact (Acceptance 6)
- Keep/confirm the existing structural regression test that state/ceiling/fields updates flow only
  through `_apply_decision` (master PLAN "enforcement doors": `_apply_decision` is the single
  canonical writer). Assert the note path (`set_field_user_note`) and recap path still write
  through their existing narrow `UPDATE` (unchanged) and that no new fields-writing SQL path was
  added. If a "sole writer" grep/AST test exists, it stays green; if not, the plan does not add
  one (out of scope — it is a t_tt02 concern already covered).

### Changed existing tests
- `test_engine_parameterization.py:158-183, 431-436` — the codec round-trip and
  `updated.fields.approach.*` reads → `get_slot(...)`; the `TicketFields` constructions rebuilt to
  the slot-map form. Asserted values unchanged.
- `tests/typing/tt01_overload_cases.py:36-38` — `has_pending_gating_proposal` still returns
  `bool`; the `require_coding_field` overload note (if any) is removed. Re-run mypy over
  `tests/typing`.
- All 85 attribute-access assertions across the 8 files (table §5) → `get_slot`. Mechanical.

---

## 7. Order of edits (single worktree, serial — this ticket owns contracts/codec/machine/data)

1. `contracts.py`: new `TicketFields` (+ `empty`); `Ticket.state: str`, `Ticket.ceiling: str`;
   `NextCeiling = str | "none"`, `ScopePair.next_ceiling: str`. (breaks everything → fix forward)
2. `decisions.py`: `Decision.new_state: str | None`, `Decision.new_ceiling: str | None`.
3. `fields_codec.py`: generic `get_slot`/`with_slot`/`fields_to_json`/`fields_from_json`; drop
   `_CODING_FIELD_IDS`.
4. `machine.py`: delete `require_coding_field`; `validate_ceiling`/`resolve_scope` widen to `str`;
   `has_pending_gating_proposal` generic; `plan_handoff_status(old_state: str, new_state: str|None)`
   via `views.transition_effect`; `.value → str()` at `:188, 222`.
5. `admission.py`: drop `require_coding_field` (`:59`); `str(gating)` (`:67, 79`); `.value → str()`
   on state/ceiling/field (`:48, 55, 68, 69, 78, 80`).
6. `resolution.py`: `_state_change(old: str, new: str)`; `_accept_gating_proposal` ceiling as str;
   `decide_scope_change(ceiling: str, *, definition=…)` threading `validate_ceiling`; thread
   `definition` through `decide_file_proposal`/`decide_accept`/`decide_edit_value`/
   `decide_return_for_revision`; `.value → str()` on every event/error state/ceiling/field.
7. `external_work.py` (Ruling 2 — coding-only): add `*, definition=None` param + explicit
   `defn.type_id != "coding"` rejection; **keep** `_FIELD_ORDER`/`_PREFIX_COUNT`; no genericization.
8. `data.py`: `_row_to_ticket` `str(row["state"])`/`str(row["ceiling"])` (the P0 fix);
   `create_ticket`/external-create fields via `TicketFields.empty` + `with_slot`; `_apply_decision`
   `str()` at door + binds (`:131, 151, 152`); resolve `defn` and thread into every in-app writer's
   `decide_*`/`plan_handoff_status` (`file_proposal`, `file_current_proposal_with_recap`,
   `accept_proposal`, edit-value + return-for-revision writers, `change_scope`); external-work
   callers pass `definition=defn`; `str(field)`/`.value → str()` at `:686, 976`.
9. `views.py`: `.value → str()` at `:64, 72, 111, 191`; `copy_text` field body → `get_slot(...)`
   (byte-identical, six-field). `sprints/views.py`: `.value → str()`, `has_pending_gating_proposal`
   keeps the coding default. `employee_step_runner.py:45`: `str(ticket.state)`.
10. **Completeness grep (§3 gate):** `grep -rnE '\.(state|ceiling)\.value|\bfield\.value|\bgating\.value'
    src/planner/tickets src/planner/runtime src/planner/sprints` → confirm zero ticket-carried hits
    remain (only type-independent-enum `.value` constants allowed).
11. Tests: mechanical `get_slot` rewrites (~85) + `.value→str` follow-ups + new T1–T7. Run `./verify`
    (mypy strict + the full suite is the arbiter).

---

## 8. Open risks

1. **Codebase-wide `.value → str()` + `Ticket.state/.ceiling: str` domain widening is the largest
   surface — mechanical, but a missed site fails silently or at mypy, not with a clear error.**
   The widening touches ~30 `src/` sites across write and read paths (the two tables in §5). Two
   failure modes: (a) a *missed write-path* `.value` on a foreign-type state/ceiling/field
   `AttributeError`s only once a foreign type flows through — invisible on coding-only `./verify`;
   (b) a *missed read-path* `.value` fails strict mypy immediately once `ticket.state` is `str`.
   Mitigation: the §3 completeness grep is a **required acceptance gate** (zero ticket-carried
   `.state/.ceiling/field .value` in reachable paths), and T7 exercises the create+reload path that
   was the actual `ValueError`. The honest upside: this removes the strict-mypy lie where
   `advance_target(ticket.state)` mis-infers a `TicketState` return. The `tests/typing/` overload
   suite must be re-run; the machine.py `@overload`s (`gating_field`/`advance_target`/
   `auto_accept_target`) are unchanged (Tier-1, string-native), but a caller that passed
   `ticket.state` (was `TicketState`, now `str`) now resolves the *string* overload — confirm no
   test asserted the enum-narrowing overload on a `ticket.state` arg.

2. **The `==` reserved-bookend comparisons must survive the `str` flip — verify, don't assume.**
   `decide_drop`/`decide_state_jump`/`decide_return_for_revision` compare `ticket.state ==
   TicketState.dropped/.done/.needs_kickoff` (`resolution.py:153, 202, 239, 271-297`). `str ==
   StrEnum` is `True` when the strings match, so these hold with `ticket.state: str` — **but only
   because the reserved bookends are shared literally by every type** (invariant 1). A test must
   assert drop/jump still behave identically for a coding ticket after the flip (they are the one
   place a `str`/enum equality subtlety could bite). No logic change; a coding-parity regression
   test is the guard.

3. **Attribute-access churn size (~85 test edits) can hide a real change.** Large enough that a
   reviewer skims. Mitigation: one `slot(fields, "<id>")` test helper so every edit is an identical
   mechanical substitution — the Codex diff-review becomes a pattern-check, not 85 judgments; state
   in the report that no asserted *value* changed (only the accessor). Secondary: `copy_text`
   (`views.py`) — the temptation to "clean it up" into a generic loop would change coding output and
   stray into Phase 4a; the plan holds it byte-identical (only the state-string wraps).

Resolved by the revision (no longer risks): **external-work prefix derivation** — the first draft's
`field_ids`-based derivation had a real bug (field-declaration-order ≠ stage-gate-order is not
registry-enforced); per Ruling 2 external-work stays coding-only with an explicit non-coding
rejection, and the correct gate-based derivation is deferred to t_tt03. **coding `transition_hooks`**
— verified present (`coding.py:86-93, 102`), so `plan_handoff_status` via `views.transition_effect`
is coding-parity-safe; the residual is a test (cover auto-accept, direct accept, and a
non-khushal/other-transition case), not a code gap. **D103 silent-pass invariant** — updated
everywhere: no second PRODUCTION type until **t_tt03** threads external-work + finishes ingress
(t_tt02b threads only the in-app drive path).
