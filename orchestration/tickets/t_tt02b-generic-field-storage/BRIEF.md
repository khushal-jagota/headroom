# Ticket t_tt02b — Generic per-type field storage + Tier-2 scope

The follow-on to t_tt02. Lifts the coding-bound Tier-2 boundary that t_tt01 deliberately erected: field
storage and scope become **generic over the type's declared fields / ceiling ids**, so a non-coding type
(e.g. `probe` with `kickoff/alpha/beta`) can store its field values and use its own ceiling range. This
is a **contract-shape change** to `TicketFields`/`ScopePair` — the second-hardest ticket. **Coding
behavior stays identical**; because the fixed struct becomes generic, existing tests that *construct or
attribute-access* `TicketFields`/`ScopePair` will need **mechanical, behavior-preserving edits** (that is
expected and legitimate — no behavior assertion changes).

## Goal (approved success)

- `TicketFields` stores a `FieldSlot` per **declared field id** of the type, not six hardcoded names.
- The codec (`fields_to_json`/`fields_from_json`) and slot accessors (`get_slot`/`with_slot`) are generic
  over the definition's `field_ids`; the coding-bound guard (`_CODING_FIELD_IDS`) and
  `machine.require_coding_field` are **lifted** (a declared foreign field now stores; validation is
  against the definition, not the six).
- `ScopePair`/`resolve_scope`/`validate_ceiling`/`has_pending_gating_proposal` become **Tier-2 generic**:
  they operate on the type's `ceiling_range`/fields (already exposed by the registry views), not
  `TicketState`/`FieldName` membership.
- `resolution.decide_*`, `machine.plan_handoff_status`, and the **external-work** reconcile/create paths
  thread the per-row `WorkflowDefinition` (from t_tt02's resolution) into the engine and codec, so
  propose/accept/scope/reconcile operate **per type**. (This completes t_tt02's deferred R1 and lifts the
  invariant that gates a second production type — see t_tt02 F6: no second production definition may be
  registered until these coding-default paths are threaded.)

## The load-bearing design question (planner must resolve explicitly)

**How to genericize `TicketFields` with minimal, honest churn.** Today it is a frozen 6-field dataclass
(`contracts.py`), the codec hardcodes the six keys, and `get_slot`/`with_slot` dispatch on `FieldName`
identity (`fields_codec.py:114-136`); many call sites across the codebase read `fields.success` etc. by
attribute. Resolve:
- The new shape (e.g. `TicketFields` wrapping an **ordered `dict[str, FieldSlot]`** keyed by field id),
  its constructor, and how `get_slot(fields, field_id: str)` / `with_slot(fields, field_id, slot)` work
  generically.
- Every attribute-access site (`fields.kickoff`/`.success`/…) across `data.py`, `resolution.py`,
  `views.py`, `external_work.py`, `machine.py`, and tests → convert to `get_slot(fields, "<id>")`. Map the
  full blast radius (grep `\.kickoff`/`\.success`/`\.approach`/`\.plan`/`\.implementation`/`\.closeout`
  on `TicketFields` values).
- Whether a thin coding-compat accessor reduces churn WITHOUT re-privileging coding's names in the generic
  type (prefer generic; do not bake the six names into the "generic" struct).
- How much existing-**test** edit is unavoidable and confirm each such edit is mechanical field-access
  only — **no behavior assertion changes**. (Unlike t_tt00/t_tt01, byte-clean "no test edits" is NOT
  achievable here; be explicit about which tests change and why.)

## Scope

**In:** `TicketFields` shape (`contracts.py`); `fields_codec.py` (codec + slot accessors, generic; drop
the coding-bound guard); `machine.require_coding_field`/`has_pending_gating_proposal` (lift the boundary);
`ScopePair.next_ceiling` type widen + `resolve_scope`/`validate_ceiling` generic; `resolution.decide_*`
thread the definition; every `TicketFields` attribute-access site; `_apply_decision`'s fields handling
stays the single writer.

**Out:** No DB schema change (the `fields` column is already TEXT — generic storage is a codec change, not
a migration). No new type (`probe` is t_tt02x). No CLI/API (t_tt03). No worker (t_tt05). Do not touch the
registry (`ticket_types/`).

## Acceptance (concrete)

1. **Coding parity (behavior):** a coding ticket's fields store/load/access identically byte-for-byte
   (`fields_to_json` output for a coding ticket unchanged); the full `./verify` passes. Existing test
   edits are limited to mechanical `TicketFields`/`ScopePair` construction/access rewrites — assert (in
   the report) that no behavior/outcome assertion changed.
2. **Generic storage:** a synthetic definition with fields `(kickoff, alpha, beta)` round-trips through
   `fields_to_json`/`fields_from_json`; a value written under `"alpha"` reads back via `get_slot(_,
   "alpha")`; `with_slot` on `"beta"` is copy-on-write and leaves others intact.
3. **Boundary lifted:** `fields_from_json`/`require_coding_field` no longer raise for a registered
   non-coding field set; instead they validate the JSON's keys against the definition's `field_ids`
   (declared present + decode; extras still lenient).
4. **Tier-2 generic:** `resolve_scope`/`validate_ceiling` accept a non-coding definition and its ceiling
   ids; `ScopePair` carries a non-coding ceiling id; `has_pending_gating_proposal` reads the type's gate.
5. **decide_\* threaded:** a propose + accept driven on a ticket whose definition is a synthetic type
   routes the value into that type's field and advances per that type's order/ceiling (unit-level; full
   `probe` end-to-end is t_tt02x).
6. **Single-writer intact:** `_apply_decision` remains the sole canonical fields/state/ceiling writer.

## References
- Current field storage: `src/planner/tickets/logic/fields_codec.py` (the six-key codec + identity
  dispatch + `_CODING_FIELD_IDS` guard), `src/planner/tickets/contracts.py` (`TicketFields`, `FieldSlot`,
  `ScopePair`, `NextCeiling`).
- Boundary from t_tt01: `machine.require_coding_field`, the Tier-2 functions.
- Per-row definition from t_tt02: `_row_to_ticket` resolving `require(ticket_type)` + `resolve_and_validate`.
- Registry views: `field_ids`, `ceiling_range`, `default_ceiling`, `gated_state`, `gating_field`.
- Standing rules: `PRINCIPLES.md` (single writer, one door; name for what it is), `decisions.md` D102.
