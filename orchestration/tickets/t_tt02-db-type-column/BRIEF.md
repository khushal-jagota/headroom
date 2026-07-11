# Ticket t_tt02 — DB `ticket_type` column, migration, registry validation on the persistence layer

Phase 2 of `orchestration/ticket-types-redesign/PLAN.md`. Persist the ticket's type and move lifecycle
integrity from enumerating DB `CHECK`s to the registry. **Coding-only in production; `TicketFields` stays
the fixed 6-slot struct** (genericizing field storage so non-coding types can store their fields is the
follow-on t_tt02b). Behavior byte-identical for coding.

## Goal (approved success)

- The `tickets` table has a `ticket_type` column; every row resolves its `WorkflowDefinition` from the
  registry by that type. Existing rows are migrated to `coding`.
- The two enumerating `CHECK`s (on `state` and `ceiling`) are **dropped**; lifecycle integrity is enforced
  by **registry validation on row load and before persist**, plus a **startup integrity audit**.
- The engine resolves each ticket's definition from its stored `ticket_type` (replacing t_tt01's
  coding-default bridge with per-row resolution). Coding rows resolve `coding` → identical behavior.
- **Default ceiling is per-type** (`registry.default_ceiling(type)`), replacing the hard-coded
  `needs_success` at the write layer.

## Scope — what changes

**In:**
- **DDL:** add `ticket_type TEXT NOT NULL` to the `tickets` table. Drop the enumerating `CHECK` on `state`
  (db.py:62-64) and on `ceiling` (db.py:71-73). Keep the type-independent checks (title length, priority,
  `at_cap`, `ticket_status`, `implementer`). Do **not** add an enumerating CHECK on `ticket_type` (types
  are an open code registry — validated by the registry, not the DB). Update **every** DDL copy — the
  canonical `CREATE TABLE tickets` (db.py:59), the migration's `tickets_new` template, and any third copy
  (the plan flagged the DDL is duplicated ~3×; the planner must find and update all).
- **Migration:** a new migration that **mirrors `_migrate_ticket_kickoff_columns` (db.py:272-388)** — the
  atomic-swap / `PRAGMA foreign_keys=OFF` / SAVEPOINT-or-BEGIN-IMMEDIATE / `foreign_key_check` / rollback
  discipline. It creates `tickets_new` with `ticket_type` and without the two enumerating CHECKs, copies
  every row backfilling `ticket_type='coding'`, swaps, and verifies FK integrity. Idempotent (guarded by a
  schema-shape probe like the kickoff migration's).
- **Registry validation doors** (name each, from the plan's "enforcement doors"):
  - `_row_to_ticket` (data.py) — resolve `registry.require(ticket_type)`, decode fields against it,
    validate the row's `state`/`ceiling` are valid for that definition. **This is where the per-row
    definition is resolved and threaded** to the engine calls (replacing t_tt01's
    `coding_bridge.coding_definition()` default — callers now pass `definition=registry.require(...)`).
  - `_apply_decision` (data.py) — validate the complete prospective tuple (type, state, ceiling, fields)
    before issuing SQL. It stays the sole canonical writer.
  - Creation/import paths — `create_ticket`, external-work create, and the **seed importer**
    (`seed/importer.py`) — build and validate a complete registered ticket before insert.
  - Note writes (the fields-JSON rewrite outside `_apply_decision`) — validate field keys against the
    definition before serialize.
  - **Startup integrity audit** — one linear scan over `tickets` at boot, reusing the same validator,
    failing with the offending ticket id + reason (unknown type, state/ceiling invalid for the type,
    missing/extra declared field, malformed slot).
- **Per-type default ceiling:** replace the hard-coded `needs_success` at the write layer
  (data.py:303/391/397 and the DDL `ceiling` default) with `registry.default_ceiling(ticket_type)`. Coding
  = `needs_success` (unchanged).
- **`create_ticket` write-layer bridge:** add `ticket_type: str = "coding"` (data-layer default so the
  existing fixed-arity create call sites/tests are unedited). The **user-facing mandatory `--type`** is
  t_tt03; this default is the same kind of temporary bridge as t_tt01's, removed there.
- **Test-overridable registry:** the registry used for per-row resolution must be swappable/extendable in
  tests so t_tt02x can register `probe` and drive a non-coding row. (e.g. `coding_bridge` exposes a
  test hook, or resolution goes through a swappable accessor.) Name the mechanism; keep production
  coding-only.
- **Index strategy:** decide explicitly (likely `(ticket_type, state)`); state the reasoning.

**Out (do not do here):**
- **Field-storage / Tier-2 genericization** (TicketFields fixed struct → per-type; generic scope) — that
  is **t_tt02b** (next). Coding keeps the fixed 6-slot struct here; the `require_coding_field` loud
  boundary stays. Do not touch `TicketFields`/`FieldSlot` shapes or `get_slot`/`with_slot`.
- No CLI/API `--type` mandatory (t_tt03). No `probe` (t_tt02x). No worker (t_tt05).

## Acceptance (concrete, from the plan review — assert values, not "works")

1. **Migration fidelity:** all pre-migration row ids + count survive; every migrated row
   `ticket_type == "coding"`; `state`/`ceiling`/`fields`/`ticket_status`/`chat_session_key`/relationships/
   `created_at`/`updated_at` unchanged per row; `PRAGMA foreign_key_check` returns `[]`.
2. **Schema shape:** final `sqlite_master.sql` for `tickets` contains `ticket_type TEXT NOT NULL`, no
   enumerating `state` CHECK, no enumerating `ceiling` CHECK; the type-independent checks remain.
3. **Idempotence + safety:** re-running `create_schema`/the migration is a no-op; a simulated failure
   during copy/swap leaves the original `tickets` intact (assert row survival after a raised error).
4. **Startup audit:** each corruption case fails startup with the ticket id + specific reason — unknown
   `ticket_type`; a `state` not in the type's stages; a `ceiling` outside the type's ceiling range; a
   missing/extra declared field; a malformed slot.
5. **Validation doors:** load (`_row_to_ticket`), persist (`_apply_decision`), create, external-work
   create, seed import, and note write each reject an invalid registered ticket with the specific error.
6. **Per-type default ceiling:** a created coding ticket gets `ceiling == "needs_success"` via
   `registry.default_ceiling`, not a literal.
7. **No later-migration regression:** any subsequent rebuild template cannot recreate the retired
   state/ceiling CHECKs or a fixed-field default (a test asserts the current canonical DDL).
8. **Coding parity:** the full existing `./verify` suite passes; existing create call sites work via the
   `ticket_type="coding"` default (no existing assertion edits).

## References
- Plan: `orchestration/ticket-types-redesign/PLAN.md` — Phase 2, "Registry validation — the enforcement
  doors", carried risks (migration ordering, relaxing DB CHECKs).
- Migration pattern to mirror: `src/planner/core/db.py` `_migrate_ticket_kickoff_columns` (:272), the
  tickets DDL (:59), `_table_columns`, `create_schema`.
- Registry API: `src/planner/ticket_types/` (`require`, `default_ceiling`, `ceiling_range`, `field_ids`,
  views) + `src/planner/tickets/logic/coding_bridge.py` (the seam from t_tt01).
- Engine threading from t_tt01: the `definition=` param on `machine`/`resolution`/`admission` ops.
- Standing rules: `PRINCIPLES.md` (single writer, one door), `CLAUDE.md`, `decisions.md` D102.
