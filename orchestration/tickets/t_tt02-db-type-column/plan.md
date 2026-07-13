# t_tt02 — implementation plan: DB `ticket_type` column, migration, registry validation doors

Persist each ticket's type, retire the two enumerating DB `CHECK`s in favour of registry validation
(row-load, pre-persist, startup audit), thread the per-row `WorkflowDefinition` into the engine, and make
the default ceiling per-type. **Coding-only in production; `TicketFields`/`FieldSlot` shapes and
`get_slot`/`with_slot` are NOT touched** (field-storage genericization is t_tt02b).

All file:line anchors are against the tree as read for this plan; re-confirm at implement time.

---

## 0. Ground truth established by reading the code (constraints the plan obeys)

> **Note on the "F6" label:** the codebase's own **F6 guard** is the import-boundary test
> (`coding_bridge` is the sole `ticket_types` importer). The plan-review's **finding F6** is the separate
> silent-pass/registers-coding-alone invariant. Below, "F6 import boundary / F6-safe / F6 guard" = the test;
> "review-F6" = the review finding. They are unrelated despite the shared number.

- **F6 import boundary (hard).** `tests/unit/test_ticket_type_registry.py:761` asserts the ONLY production
  module importing `planner.ticket_types` is `src/planner/tickets/logic/coding_bridge.py`
  (`_ALLOWED_TICKET_TYPES_IMPORTER`, :758). Therefore **every** registry access this ticket adds — per-row
  resolution in `data.py`, the startup audit, seed validation — must reach the registry **through
  `coding_bridge`**, never by importing `ticket_types`. `data.py` already imports `coding_bridge` (:36-43);
  `db.py` and `server.py` must too (both already touch `coding_bridge` indirectly — `server.py:39,114`
  already imports it, `db.py` does not yet).
- **`coding_bridge` exposes:** `coding_registry() -> Registry`, `coding_definition() -> WorkflowDefinition`,
  `views`, `field_ids`, `has_field` (coding_bridge.py:52-81). `Registry` exposes `require(type_id)`,
  `default_ceiling(type_id)`, `ceiling_range(type_id)`, `stage_ids`, `is_terminal`, `field_ids`, etc.
  (registry.py:57-123). **New seam surface this ticket adds to `coding_bridge`** (see §7): a
  swappable/injectable registry accessor plus a `require(type_id)` / `default_ceiling(type_id)` passthrough,
  so callers never name `ticket_types`.
- **The engine's `resolution.decide_*` functions do NOT take `definition=` today** (resolution.py:100,141,
  193,231,269,286,300). They internally call `machine.*`/`admission.*` **without** `definition`, i.e. they
  default to `coding_bridge.coding_definition()`. t_tt01 threaded `definition=` only through the Tier-1
  `machine.*` lookups and `admission.check_agent_proposal`. **This is the load-bearing tension of t_tt02**
  — see §4 "definition threading" and Risk R1. For coding, default == per-row-resolved, so behavior is
  byte-identical; the question is how far to plumb `definition=` to make per-row resolution *real* rather
  than nominal, without over-building for a `probe` row the codec can't decode yet.
- **The fixed codec rejects a non-coding FIELD SET — not a non-coding TYPE ID (review-F6, load-bearing).**
  `fields_codec.fields_from_json` raises loudly (fields_codec.py:89-94) unless `field_ids(definition) ==
  ("kickoff","success","approach","plan","implementation","closeout")`, and `machine.require_coding_field`
  (machine.py:227-238) rejects a foreign gate. So a **structurally-different** `probe`
  (`test_engine_parameterization.py:62`, fields `kickoff/alpha/beta`) cannot pass `_row_to_ticket` or the
  Tier-2 engine. **But a coding-SHAPED second type (same six fields, different `type_id`) WOULD pass the
  codec** and reach the coding-default `decide_*`/`admission`/`resolve_scope`/external-work paths and
  silently get coding semantics. Therefore the deferral of resolution-engine threading (§3c) is **NOT** safe
  because "a non-coding row is unreachable" — it is false. It is safe for one reason only: **production
  registers `coding` alone** (`coding_registry()`, coding_bridge.py:59-63), so no non-coding definition
  exists to resolve. The binding invariant left for t_tt02b: **no second PRODUCTION definition until the
  definition is threaded through `resolution.decide_*`, `machine.plan_handoff_status`, and external-work**
  (§3c, §7, R1). What the injectable second-type test proves here (§7): type-id resolution, the doors, and a
  demonstration of that coding-default boundary — NOT full non-coding field flow (t_tt02b/t_tt02x territory).
- **DDL is duplicated across live-data migration templates** (see §1). The canonical `CREATE TABLE tickets`
  is db.py:59; the current-shape migration template is `_migrate_ticket_kickoff_columns`'s `tickets_new`
  (db.py:289-316). Two *older* templates (`_migrate_ticket_lifecycle` db.py:558-584,
  `_rebuild_tickets_with_project_id` db.py:846-871) build **historical** shapes and MUST NOT be changed.
- **`create_schema` runs on an autocommit connection** at both entry points (cli/main.py:279-280 uses
  `with connect(...) as bootstrap: create_schema(bootstrap)`; seed/__main__.py:55). The kickoff migration's
  `foreign_keys` autocommit assertion (db.py:353-355) therefore holds; the new migration mirrors it.
- **Naming drift to ignore:** the PLAN/BRIEF cite `SETTLED_PREFIX_INDEX (external_work.py:29)` and
  `data.py:303/391/397`. The live code has `_PREFIX_COUNT` (external_work.py:27, a Phase-3 concern, OUT of
  scope here) and the hard-coded `needs_success` defaults are at data.py:310 (create_ticket),
  data.py:398/404 (external-work create), and the seed insert at seed/importer.py:247. The plan uses the
  live anchors.

---

## 1. DDL diff

### 1a. Canonical `CREATE TABLE tickets` (db.py:59-85) — the change

Add `ticket_type` as a `NOT NULL` column with **no `DEFAULT`** (write layer supplies it; production default
lives at the write bridge, not the DB — §5). Remove the enumerating `state` CHECK (db.py:62-64) and the
enumerating `ceiling` CHECK (db.py:71-73). **Remove the `ceiling` DEFAULT `'needs_success'`** (keep `ceiling`
NOT NULL) — see §1b (F3). **Keep** every type-independent check: `length(title) <= 200` (:61),
`priority IN (...)` (:65), `at_cap IN ('stop','propose')` (:74), `ticket_status IN (...)` (:75-77),
`implementer IN (...)` (:78-79). **Keep the DDL `state` default `'needs_kickoff'`** — that bookend is
genuinely universal across all types (PLAN invariant 1). Keep the `fields` default JSON literal unchanged
(coding six-slot).

New column, placed adjacent to `state` (co-located as the `(ticket_type, state)` composite the read model
keys on):

```
  id                   TEXT PRIMARY KEY,
  title                TEXT NOT NULL CHECK (length(title) <= 200),
  ticket_type          TEXT NOT NULL,                 -- registry-validated; NO enumerating CHECK (open registry)
  state                TEXT NOT NULL DEFAULT 'needs_kickoff',   -- CHECK removed; registry validates (type,state); universal bookend default KEPT
  priority             TEXT NOT NULL DEFAULT 'P3' CHECK (priority IN ('P0','P1','P2','P3')),
  ...
  ceiling              TEXT NOT NULL,    -- CHECK removed AND DEFAULT removed (F3); every writer sets the per-type default
  ...
```

- **No enumerating `CHECK` on `ticket_type`** — types are an open code registry (BRIEF/PLAN invariant 4);
  the registry validator + startup audit are the enforcement, not the DB.
- **Column ordering caveat (SQLite):** placing `ticket_type` *before* `state` reorders columns vs the old
  table. The migration copies **by explicit column name** (§2), not positionally, so ordering is free to
  choose; picking adjacency to `state` documents the composite key intent. (If the implementer prefers to
  minimize diff churn, appending `ticket_type` after `implementer`/before `alias` is equally correct — the
  only hard requirement is name-based copy. Flag the choice in the diff; default to adjacent-to-`state`.)

### 1b. `ceiling` DDL default — DECISION: **DROP `DEFAULT 'needs_success'`, keep `NOT NULL`** (F3, owner ruling)

`needs_success` is **coding's** default ceiling, not a type-independent value. With the enumerating CHECK
gone, a raw `INSERT` that omits `ceiling` would silently store **coding's** value for a row of any type —
inventing coding semantics for a non-coding ticket. So the DDL default is removed: an omitted `ceiling` must
fail loudly (NOT NULL violation), not default-invent.
- Every sanctioned writer already sets `ceiling` explicitly to the per-type default: `create_ticket`
  (data.py:310), `create_ticket_from_external_work` (data.py:404), the seed importer (:247), and the
  migration copy (§2b) — none rely on the DDL default, so dropping it is behavior-preserving for all real
  inserts.
- Contrast with `state DEFAULT 'needs_kickoff'`, which is **kept**: `needs_kickoff` is the universal leading
  bookend shared identically by every type (PLAN invariant 1), so it is a genuinely type-independent default.
- This resolves the prior R4 ambiguity — the owner reversed the earlier "keep" and ruled "drop the `ceiling`
  default." No open question remains here.

### 1c. Index — DECISION: ADD `idx_tickets_type_state`, RETAIN `idx_tickets_state` (F7, owner ruling)

PLAN invariant 3 says `(ticket_type, state)` is the real key, so **add** a composite
`idx_tickets_type_state ON tickets(ticket_type, state)`. But current readers still filter/group by `state`
**alone**, without a `ticket_type` — verified:
- `sprints/views.py:94` — `SELECT state, COUNT(*) ... GROUP BY state`.
- `tickets/views.py:110` — `WHERE state = ?` (the `GET /tickets?state=` filter), :237/:277 board bucketing
  by `state`, :355 `WHERE state NOT IN ('done','dropped')`.

A composite `(ticket_type, state)` index **cannot** serve a `state`-only predicate (leading column absent),
so dropping `idx_tickets_state` would force table scans on the board/queue/sprint reads — a live perf
regression. Until t_tt04 makes these readers type-scoped, **retain `idx_tickets_state(state)`**.
- **Change:** in the canonical DDL (db.py:86-87) and in `_create_indexes` (db.py:730-740), **keep**
  `idx_tickets_state`, **add** `CREATE INDEX IF NOT EXISTS idx_tickets_type_state ON tickets(ticket_type,
  state)`. Keep `idx_tickets_alias` and `idx_tickets_project_id` unchanged.
- Cheap insurance: two indexes on a low-write table cost negligible write amplification vs. a board scan.
  t_tt04 can drop `idx_tickets_state` once every reader carries a `ticket_type` (or prove it unneeded with
  `EXPLAIN QUERY PLAN`). No existing test pins `idx_tickets_state` (grepped `tests/` — the name appears only
  in `db.py`), so retaining it is invisible to the suite.

### 1d. DDL copies to update — the exhaustive list

| # | Location | Action |
|---|---|---|
| 1 | Canonical `CREATE TABLE tickets` — **db.py:59-85** | Add `ticket_type TEXT NOT NULL`; drop `state` CHECK (62-64) + `ceiling` CHECK (71-73); drop `ceiling` DEFAULT (F3); keep `state` DEFAULT + all type-independent checks. |
| 2 | Canonical index block — **db.py:86-87** | KEEP `idx_tickets_state`; ADD `idx_tickets_type_state` (F7). |
| 3 | `_create_indexes` — **db.py:730-740** | ADD `idx_tickets_type_state`; leave `idx_tickets_state` (this is the idempotent re-create path run every boot). |
| 4 | **NEW** `_migrate_ticket_type_column`'s `tickets_new` template (§2) | Build with `ticket_type`, no enumerating CHECKs — the migration's copy of the new canonical shape. |
| — | `_migrate_ticket_kickoff_columns` `tickets_new` — db.py:289-316 | **DO NOT edit** — it produces the *pre-type* shape that the new migration's schema-shape probe detects and upgrades from. Editing it would erase the migration boundary. |
| — | `_migrate_ticket_lifecycle` `tickets_new` — db.py:558-584 | **DO NOT edit** — historical shape. |
| — | `_rebuild_tickets_with_project_id` `tickets_new` — db.py:846-871 | **DO NOT edit** — historical shape. |

So **three** live copies change (canonical DDL, canonical index line, `_create_indexes`) plus **one new**
`tickets_new` template inside the new migration. The three older `tickets_new` templates are frozen history.

---

## 2. Migration — `_migrate_ticket_type_column`

Mirror `_migrate_ticket_kickoff_columns` (db.py:272-388) **exactly**: schema-shape idempotence probe →
snapshot rows → build `tickets_new` → per-row copy backfilling `ticket_type='coding'` → FK-off /
SAVEPOINT-or-BEGIN-IMMEDIATE / drop+rename / `foreign_key_check` / rollback-on-failure / FK-on.

### 2a. Idempotence guard (schema-shape probe) — recognise the COMPLETE target shape (F1)

Read `sqlite_master.sql` for `tickets` and return early **only when the table is already fully at the new
shape** — `ticket_type` present AND `NOT NULL` AND both enumerating CHECKs gone. A partial prior migration
(column added but an old CHECK retained) must be **rebuilt, not skipped**, or an unmigrated schema is
reported as success on the owner's live DB.

```python
schema_row = conn.execute(
    "SELECT sql FROM sqlite_master WHERE type='table' AND name='tickets'"
).fetchone()
sql = schema_row[0] if schema_row is not None else None
if (
    sql is not None
    and "ticket_type" in sql                       # the new column name (no other identifier contains it)
    and _has_ticket_type_not_null(sql)             # column carries NOT NULL
    and "state IN ('needs_kickoff'" not in sql     # the enumerating state CHECK is gone
    and "ceiling IN ('needs_success'" not in sql   # the enumerating ceiling CHECK is gone
):
    return
```

- The two enumerating-CHECK sentinels are the exact literal substrings the canonical DDL / historical
  `tickets_new` templates emit for those CHECKs (`state IN ('needs_kickoff'` and `ceiling IN ('needs_success'`).
  Their **absence** is what distinguishes a fully-migrated table from a partial one that still carries a
  CHECK. If either is present, the probe falls through and the table is rebuilt — the correct outcome for a
  partial state.
- `_has_ticket_type_not_null(sql)` — a small helper: `PRAGMA table_info(tickets)` is cleaner than a text
  scan for the NOT-NULL bit (`notnull==1` on the `ticket_type` row). Use `PRAGMA table_info` for the NOT-NULL
  check and the `sql` text scan for the CHECK-absence checks; both read the same authoritative shape. (If the
  implementer prefers a pure-text probe, `"ticket_type TEXT NOT NULL" in sql` is equivalent given the DDL
  emits that exact fragment — either is acceptable; the requirement is that all four conditions hold.)
- This mirrors the kickoff probe's spirit (a text probe on the stored DDL, db.py:276-282) but is stricter:
  it recognises the **complete** target, not merely the column's presence. The three shapes reaching this
  migration are "kickoff shape (no ticket_type)", "partial (ticket_type + an old CHECK)", and "complete new
  shape" — only the third early-returns.
- **Partial-shape test required** (see §8-C): build a table with `ticket_type` present but the old `state`
  CHECK retained, run `create_schema`, and assert the migration **rebuilt** it to the CHECK-free shape (not
  skipped).

### 2b. Row copy (backfill) — strictly NAME-based, never positional (F2)

Every value is read by column **name** (`row["col"]`) and every INSERT lists its columns **explicitly**,
exactly like `_migrate_ticket_kickoff_columns` (db.py:337-352). Adding `ticket_type` therefore cannot shift
any other column. Bind the column list and the value tuple in the **same order** so the mapping is
auditable:

```python
rows = conn.execute("SELECT * FROM tickets").fetchall()
conn.execute("DROP TABLE IF EXISTS tickets_new")
conn.execute(<new tickets_new DDL: ticket_type NOT NULL, ceiling NO DEFAULT, no enumerating CHECKs>)
for row in rows:
    conn.execute(
        "INSERT INTO tickets_new ("
        "  id, title, ticket_type, state, priority, deadline, project_id, sprint_item_id,"
        "  sprint_id, recap, ceiling, at_cap, ticket_status, implementer, chat_session_key,"
        "  alias, fields, created_at, updated_at"
        ") VALUES (?, ?, 'coding', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            row["id"], row["title"], row["state"], row["priority"], row["deadline"],
            row["project_id"], row["sprint_item_id"], row["sprint_id"], row["recap"],
            row["ceiling"], row["at_cap"], row["ticket_status"], row["implementer"],
            row["chat_session_key"], row["alias"], row["fields"],
            row["created_at"], row["updated_at"],
        ),
    )
```

- `ticket_type` is the ONLY new value (literal `'coding'` in the column position, PLAN invariant 2); **every
  other column is read by name and copied verbatim.** `fields` JSON is NOT rewritten (unlike the kickoff
  migration, which rebuilt `fields`; here the field shape is unchanged, so copying `row["fields"]`
  byte-for-byte guarantees per-field fidelity — acceptance 1). No `_migrate_*_fields_json` helper is needed.
- The `ceiling` value is copied from `row["ceiling"]` — the DDL no longer supplies a default (F3), but the
  migration always writes an explicit value, so a NULL `ceiling` cannot arise from the copy.

### 2b-envelope. Wrap create-`tickets_new` + copy + swap in ONE try/finally that drops `tickets_new` on ANY failure (F8)

The kickoff template only drops `tickets_new` inside the **swap**'s `except` (db.py:378) — a failure during
the **copy phase** (before the swap block) leaks the scratch table, and a later re-run then hits a stale
`tickets_new` (mitigated only by the leading `DROP TABLE IF EXISTS tickets_new`, but leaving a half-built
table between runs is untidy and can mask a probe bug). Improve on the template: wrap the create + copy + the
whole swap sequence in a single `try/finally` (or `try/except`) whose cleanup drops `tickets_new` on **any**
exception from any phase:

```python
conn.execute("DROP TABLE IF EXISTS tickets_new")
try:
    conn.execute(<new tickets_new DDL>)
    for row in rows:
        conn.execute(<name-based INSERT ... 'coding' ...>)
    <the FK-off / SAVEPOINT-or-BEGIN / DROP+RENAME / foreign_key_check / rollback / FK-on block from §2c>
except BaseException:
    conn.execute("DROP TABLE IF EXISTS tickets_new")
    raise
```

The inner swap block keeps its own rollback/`DROP tickets_new` (§2c) for the transactional guarantee; this
outer envelope is the belt that also cleans up a copy-phase failure. On success, `tickets_new` no longer
exists (it was `RENAME`d to `tickets`), so the cleanup `DROP IF EXISTS` is a harmless no-op.

### 2c. Atomic swap / FK sequence (verbatim mirror of db.py:353-388)

```python
foreign_keys_enabled = bool(conn.execute("PRAGMA foreign_keys").fetchone()[0])
if foreign_keys_enabled and conn.in_transaction:
    raise RuntimeError("ticket type migration requires an autocommit connection")
if foreign_keys_enabled:
    conn.execute("PRAGMA foreign_keys=OFF")
use_savepoint = conn.in_transaction
try:
    if use_savepoint:
        conn.execute("SAVEPOINT ticket_type_swap")
    else:
        conn.execute("BEGIN IMMEDIATE")
    try:
        conn.execute("DROP TABLE tickets")
        conn.execute("ALTER TABLE tickets_new RENAME TO tickets")
        violations = conn.execute("PRAGMA foreign_key_check").fetchall()
        if violations:
            raise RuntimeError(f"foreign key check failed after ticket type migration: {violations!r}")
    except BaseException:
        if use_savepoint:
            conn.execute("ROLLBACK TO ticket_type_swap"); conn.execute("RELEASE ticket_type_swap")
        else:
            conn.rollback()
        conn.execute("DROP TABLE IF EXISTS tickets_new")
        raise
    else:
        if use_savepoint:
            conn.execute("RELEASE ticket_type_swap")
        else:
            conn.commit()
finally:
    if foreign_keys_enabled:
        conn.execute("PRAGMA foreign_keys=ON")
```

Distinct savepoint name `ticket_type_swap` (never reuse `ticket_kickoff_swap`).

### 2d. Composition into `create_schema` (db.py:208-222) — ordering

Insert the call **after** `_migrate_ticket_kickoff_columns(conn)` (db.py:216) and **before**
`_migrate_project_summary_column` / the trailing `_create_indexes`:

```python
    _migrate_ticket_kickoff_columns(conn)
    _migrate_ticket_type_column(conn)        # NEW — after kickoff shape lands, before indexes
    _migrate_project_summary_column(conn)
    ...
    _create_indexes(conn)                     # now creates idx_tickets_type_state
```

Ordering reasoning: the new migration's probe assumes the **kickoff shape** as its input (a row with the
six-slot `fields` incl. `kickoff`, `implementer` column present, `state` incl. `needs_kickoff`). Running it
after the kickoff migration guarantees that precondition on any live DB. `_create_indexes` runs last
(db.py:221) and re-creates both `idx_tickets_state` and the new `idx_tickets_type_state` idempotently on the
freshly-swapped table — so both indexes exist whether the DB was fresh (canonical DDL created them) or
migrated (this call creates them). **`_create_indexes` MUST add the composite** (copy #3 in §1d) — otherwise
the DROP+RENAME swap drops the freshly-built `idx_tickets_type_state` (indexes don't survive a table drop)
and only `idx_tickets_state` would be recreated, leaving the composite absent on migrated DBs.

---

## 3. The enforcement doors — the shared validator

Add ONE pure validator, reused by every door and the startup audit, so a path cannot drift. It lives where
`data.py` (and `db.py` via `coding_bridge`) can reach it without violating F6. **Placement decision:** put
it in a new pure function on the **write/read layer's reach** — either (a) a small helper in `data.py`
itself, or (b) a pure function in `tickets/logic/` that takes a `WorkflowDefinition` (resolved by the caller
through `coding_bridge`). Prefer (b): a pure `validate_registered_ticket(definition, *, state, ceiling,
field_ids_present)` in a new `tickets/logic/ticket_type_guard.py` (imports `coding_bridge` for `views` only,
never `ticket_types`), so both `data.py` and the startup audit call the same function. The exact home is the
implementer's call within these two F6-safe options; the function shape below is fixed.

### 3a. The validator contract

`resolve_and_validate(ticket_type: str, *, state: str, ceiling: str) -> WorkflowDefinition`:
1. `defn = coding_bridge.registry().require(ticket_type)` — raises `PlannerError(not_found, "unknown ticket
   type", {"type_id": ...})` on an unknown type (registry.py:60-67). **Door: unknown type.**
2. `state` must be a linear stage OR the reserved `dropped`: `coding_bridge.views.is_terminal(defn, state)`
   is called defensively (it raises `validation, "state outside the linear order"` for an unknown id —
   views.py:56-61,22-25) — i.e. resolve `views.state_index` for non-dropped, accept `dropped` explicitly.
   Concretely: `if state != defn.dropped_stage.id: views.state_index(defn, state)` → raises for a foreign
   state. **Door: state not in the type's stages.**
3. `ceiling` must be in `views.ceiling_range(defn)` (views.py:136-138): `if ceiling not in
   views.ceiling_range(defn): raise PlannerError(scope_invalid, "ceiling outside the type's range", ...)`.
   **Door: ceiling outside the type's ceiling range.**
4. Return `defn` for the caller to thread onward (decode + engine).

**Field-key policy — STRICT on missing, LENIENT on extra (F4, owner ruling).** Field validation is handled
by the **existing codec** (`fields_codec.fields_from_json`), and its policy is deliberate and inherited by
both the load door and the audit:
- **Strict on:** a **missing** declared field (`_require(key in data)`, fields_codec.py:96-103) and a
  **malformed slot** (non-dict slot, bad proposal, non-str value — fields_codec.py:43-71). Both raise
  `PlannerError(validation, "corrupt ticket fields JSON")`.
- **Lenient on:** extra / unknown top-level keys are **ignored** (fields_codec.py:98-99 comment; the loop
  reads only declared keys). This is intentional and load-bearing: live rows carry a legacy top-level
  `result` key (test_engine_parameterization.py:153-160), so the read path must not reject extras.
- **Why leniency is safe for the audit:** a *misspelled* declared key manifests as a **missing** declared
  field (the correct key is absent), which the strict missing-field check catches. So real corruption is
  caught by "missing", and there is no need — and no ability — to flag an "extra" key. The BRIEF's
  "missing/extra declared field" audit case is therefore reframed to **"missing declared field" + "malformed
  slot"**; the extra-key case is dropped.
- **No new field validator is invented** (earn-its-existence): the codec is the single field door; the load
  path and the audit both decode through it, so they share one policy by construction.

### 3b. `_row_to_ticket` (data.py:66-87) — the load door + definition resolution

Today it hard-codes `coding_bridge.coding_definition()` for the codec (data.py:84) and does not read a type.
Change:
1. Read `ticket_type = row["ticket_type"]`.
2. `defn = <guard>.resolve_and_validate(ticket_type, state=row["state"], ceiling=row["ceiling"])` — this is
   the load-door validation (unknown type / bad state / bad ceiling all raise here, before constructing the
   `Ticket`).
3. Decode: `fields=fields_codec.fields_from_json(row["fields"], defn)` — replaces the coding literal; the
   codec validates the field set against `defn` (for coding, identical to today).
4. Construct the `Ticket`. **`Ticket` gains a `ticket_type: str` field** (contracts.py:224-243, add after
   `title`) so the resolved type is carried on the domain object and available to writers/read models
   without a re-query. This is additive; every `Ticket(...)` construction site must pass it — grep
   `Ticket(` across `src/` and `tests/`; the only production constructors are `_row_to_ticket` and
   `read_ticket_by_session_key` (data.py:490-500, which builds via `_row_to_ticket`? — no, it calls
   `_row_to_ticket(row)` at :500, so it is covered). **Confirm no other production `Ticket(...)`
   constructor** (grep). Test fixtures that build a bare `Ticket(...)` will need the new field — but the
   BRIEF forbids editing existing assertions; adding a required dataclass field breaks fixture
   constructors. **Decision (R3):** give `Ticket.ticket_type` a **default** `= "coding"` on the dataclass so
   existing positional/kw fixture constructors keep working unedited; production always passes the resolved
   value explicitly. A dataclass field with a default after non-default fields requires it be placed last or
   all-defaults-trailing — since `Ticket` has no trailing defaults today, either make it the last field with
   a default, or use `field(default="coding")` positioned last. Place `ticket_type` **last** with
   `default="coding"` to satisfy dataclass ordering without touching other fields. (Cosmetically it is not
   adjacent to `state`, but the DB column ordering and the dataclass field ordering are independent.)

- **`read_ticket_by_session_key` (data.py:487-500)** builds via `_row_to_ticket(row)` at :500 → covered.

### 3c. Engine definition threading — the calibrated depth (Risk R1)

The BRIEF says callers "now pass `definition=registry.require(...)`". The honest reading, given the code:

- **What already accepts `definition=`:** the Tier-1 `machine.*` ops and `admission.check_agent_proposal`.
  Passing the per-row `defn` there for a **coding** row is identical to the default. For a coding-only
  production build, the *observable* behavior is unchanged whether we thread or not.
- **What does NOT accept `definition=`:** every `resolution.decide_*` (resolution.py) and the `machine.*`
  calls made directly from `data.py` (`machine.gating_field` data.py:618, `machine.plan_handoff_status`
  660). Threading the definition *through* resolution would mean adding a `definition=` param to six
  `decide_*` functions and forwarding it into their internal `machine`/`admission` calls.

**Decision:** thread the resolved `defn` **only where a function already accepts `definition=` and is
reachable from a `data.py` writer with the row in hand**, i.e.:
  - `data.py:618` `machine.gating_field(ticket.state)` → `machine.gating_field(ticket.state,
    definition=defn)` where `defn = coding_bridge.require(ticket.ticket_type)` (or reuse `ticket.ticket_type`
    resolved at load — see below).
  - `data.py:660,591,635` `machine.plan_handoff_status(...)` — this function does NOT take `definition`
    today and is coding-specific (it names `needs_plan`/`needs_implementation` literally, machine.py:262).
    Leave it unthreaded this ticket; generalizing it to a registry transition-hook is **explicitly deferred**
    (PLAN §"Transition-effect semantics", and the hook is INERT per coding.py:41). Flag as R1.
  - **`resolution.decide_*` are NOT given a `definition=` param in t_tt02.** Rationale (earn-its-existence):
    (1) a coding row resolves coding by default, so no behavior changes; (2) adding six pass-through params
    with no non-coding **production** caller is speculative machinery the BRIEF's "coding-only" +
    "TicketFields untouched" boundary forbids. **The per-row definition is resolved and *validated* at load
    (§3b) and re-validated before persist (§3d); it is threaded into the Tier-1 lookups a writer calls
    directly, but the resolution engine (`decide_*`), `plan_handoff_status`, and external-work internals
    stay coding-default until t_tt02b threads the definition through them.** This is the precise reading of
    "replacing t_tt01's coding default with per-row resolution": the *resolution* (the `require(type)` call)
    moves from a hard-coded `coding_definition()` to `require(row.ticket_type)`, and that resolved defn
    governs the codec + Tier-1 + the two persist/load validators — the doors — even though production only
    ever resolves `coding`.
  - **Why the deferral is SAFE — the stated invariant (review-F6).** The codec rejects a different **field set**,
    NOT a different **type id**. A coding-**shaped** second type (same six fields, different `type_id`)
    would pass the codec and reach the coding-default `decide_*` / `admission` / `resolve_scope` /
    external-work paths and **silently get coding semantics** — so "a non-coding row is unreachable" is
    FALSE and must not be relied on. Deferral is safe for exactly ONE reason: **`coding_registry()`
    registers `coding` alone** (coding_bridge.py:59-63), so no non-coding definition exists in production to
    resolve. The binding invariant t_tt02 leaves for t_tt02b: **no second PRODUCTION definition may be
    registered until t_tt02b threads the definition through `resolution.decide_*`,
    `machine.plan_handoff_status`, and external-work.** (The coordinator carries the resolution/external-work
    threading into t_tt02b's scope.)

To keep resolution single-sourced: resolve the defn **once** per writer from `ticket.ticket_type` (carried
on the loaded `Ticket`, §3b) via a `coding_bridge.require(ticket.ticket_type)` call, and pass it to the
Tier-1 ops. Do not re-`require` in a loop.

### 3d. `_apply_decision` (data.py:114-156) — the pre-persist door

`_apply_decision` is the sole canonical writer of `state`/`ceiling`/`fields` (guarded by
`test_only_apply_decision_writes_canonical_position`, test_engine_parameterization.py:328). Add, before the
`UPDATE` at data.py:134: resolve `defn = coding_bridge.require(ticket.ticket_type)` and validate the
**prospective** tuple:
  - `new_state` (data.py:118) must be a linear stage or `dropped` for `defn` — reuse the §3a state check on
    `new_state.value`.
  - `new_ceiling` (data.py:119) must be in `views.ceiling_range(defn)` — reuse the §3a ceiling check on
    `new_ceiling.value`.
  - `new_fields` field set is guaranteed by construction (it came from a `TicketFields` struct, coding six)
    — no extra check needed this ticket (t_tt02b generalizes). A round-trip belt-and-braces
    (`fields_codec.fields_to_json(new_fields)` back through `fields_from_json(..., defn)`) is deliberately
    **omitted** here (earn-its-existence: the struct already guarantees the shape; add it only if a diff
    reviewer sees a concrete gap).

This makes `_apply_decision` reject an out-of-registry prospective `(state, ceiling)` before issuing SQL,
which is the enforcement the dropped DB CHECKs used to give. For coding, every legal transition already
lands inside the range, so nothing changes; a bug that proposed an out-of-range state now fails at the
writer with a specific error instead of a silent DB CHECK violation (or, post-CHECK-removal, a silent bad
write). This is the "relaxing DB CHECKs is a real reduction" mitigation (PLAN carried risk).

### 3e. Creation paths — `create_ticket`, external-work create, seed

- **`create_ticket` (data.py:246-335):** add param `ticket_type: str = "coding"` (§6). Before insert:
  `defn = coding_bridge.require(ticket_type)` (raises on unknown type → creation door). The initial
  `(state=needs_kickoff, ceiling=default_ceiling(defn))` is valid by construction; still, pass
  `ticket_type` into the INSERT column list, and set `ceiling` from `registry.default_ceiling` (§5). Because
  the built ticket is then loaded via `_load_ticket`→`_row_to_ticket` (data.py:335), the load door
  re-validates it — a single validation source. So the creation door is primarily "unknown type rejected"
  (via `require`); state/ceiling validity is covered by the load door on read-back.
- **`create_ticket_from_external_work` (data.py:338-427):** same — add `ticket_type: str = "coding"`, resolve
  `require(ticket_type)`, insert the column, set `ceiling` via `default_ceiling`. External work already goes
  through `_apply_decision` (data.py:418,426) so the persist door covers its transitions. The
  `external_work.decide_external_work` internal `coding_bridge.coding_definition()` (external_work.py:47)
  stays coding — external-work genericization is Phase 3 (t_tt03), out of scope; note it (R1).
- **Seed importer (`_import_tickets`, seed/importer.py:207-259):** the raw INSERT (:239-250) must add
  `ticket_type` to its column list and bind `'coding'` (seed is direct SQL, no canonical writer). Before
  the loop or per-row, validate via `coding_bridge.require('coding')` once (cheap; proves the type is
  registered) — but the seed only ever writes `coding`, so the meaningful door is that the inserted
  `(ticket_type='coding', state=ticket.state)` tuple is registry-valid. Reuse the shared validator on the
  parsed `(state)` before insert so a malformed seed state fails with the specific error rather than a bad
  row. `ceiling` in the seed is set to `ticket.state.value` (:247) — unchanged (seed reconciliation logic),
  NOT `default_ceiling`; leave it (it is external-work-style prefix reconciliation). **F6 caveat:** seed
  importer must reach the registry through `coding_bridge` (import it), never `ticket_types`.

### 3f. Note write path — `set_field_user_note` (data.py:890-915)

Already validates the field key against the coding definition (data.py:901-905:
`coding_bridge.has_field(coding_bridge.coding_definition(), str(field))`). Change: resolve the definition
from the **ticket's type** instead of the coding literal — `defn = coding_bridge.require(ticket.ticket_type)`
then `coding_bridge.has_field(defn, str(field))`. For coding this is identical. The note path rewrites the
whole `fields` JSON outside `_apply_decision` (data.py:909-912) — its field-key validation is the door; it
already exists, we only re-point the definition source. **`set_note` (data.py:919-930)** delegates to
`set_field_user_note`, covered.

### 3g. Startup integrity audit — one scan, reuse the validator

Add a function run once at boot, after schema creation. **Placement (F6):** it needs the registry; the
F6-safe home is a `data.py` function `audit_ticket_registry_integrity(conn)` (data.py already imports
`coding_bridge` and is the registry-aware ticket layer; it reaches the registry through `coding_bridge`,
never `ticket_types`).

**Explicit invocation (F8b — decided, not left open):**
- The audit runs **once, in the `panels serve` lifespan** (`server.py` `_lifespan`), **after** the
  registry build (`coding_bridge.coding_registry()`, server.py:114) and the schema is created, and
  **before** the background loops start (server.py:153) — so a corrupt live DB fails boot loudly before any
  worker or readiness loop touches it. Insert the call between server.py:114 and the loop-start block.
- **Seed and CLI paths do NOT run the full scan.** They rely on the **create door and the load door**: the
  seed importer validates each row's `(type, state)` before insert (§3e) and every ticket read anywhere goes
  through `_row_to_ticket`'s load-door validation (§3b). So a corrupt row cannot be *created* by a sanctioned
  writer, and any *pre-existing* corruption surfaces the moment that ticket is loaded. The one full linear
  scan is the belt on top of those braces, placed at the app's real boot path; a second scan on the CLI
  `create_schema` bootstrap would be redundant work on every CLI invocation. Document this division in the
  audit's docstring. (No open question — this supersedes the earlier R5 CLI-coverage flag.)

Behavior:
```python
def audit_ticket_registry_integrity(conn) -> None:
    for row in conn.execute("SELECT id, ticket_type, state, ceiling, fields FROM tickets ORDER BY id"):
        try:
            defn = <guard>.resolve_and_validate(row["ticket_type"], state=row["state"], ceiling=row["ceiling"])
            fields_codec.fields_from_json(row["fields"], defn)   # field-set + slot validation
        except PlannerError as exc:
            raise RuntimeError(f"ticket integrity audit failed: id={row['id']} reason={exc}") from exc
```
- **One linear scan**, `ORDER BY id` so the "first corrupt id" is deterministic (acceptance item 4 wants
  the offending id + reason). It **reuses the exact same validator** as the load door (§3a) plus the codec
  (field door) — no second definition of "valid", so the audit inherits the F4 policy verbatim: **strict on
  unknown type / bad state / bad ceiling / missing declared field / malformed slot; lenient on extra keys**
  (a legacy `result` key does not fail the audit).
- Reports the **first** corrupt ticket's id and the specific reason (unknown type / state / ceiling / field
  from the raised `PlannerError` message+detail).

---

## 4. Definition threading — summary of call-site edits

| Site | Today | After t_tt02 |
|---|---|---|
| `_row_to_ticket` codec (data.py:84) | `coding_definition()` | `resolve_and_validate(row.ticket_type, ...)` → decode with that defn |
| `_apply_decision` (data.py:134, pre-UPDATE) | no validation | validate prospective `(new_state,new_ceiling)` vs `require(ticket.ticket_type)` |
| `file_current_proposal_with_recap` gating (data.py:618) | `machine.gating_field(state)` | `machine.gating_field(state, definition=require(ticket.ticket_type))` |
| `set_field_user_note` field check (data.py:901) | `coding_definition()` | `require(ticket.ticket_type)` |
| `create_ticket` / external create / seed | coding-hardwired | resolve `require(ticket_type)`, insert `ticket_type`, `ceiling=default_ceiling` |
| `machine.plan_handoff_status` (data.py:591/635/660) | coding literals | **unchanged** (deferred; hook INERT) — R1 |
| `resolution.decide_*` (data.py:585/625/656/704/742/749/884) | coding default | **unchanged signature** (deferred to t_tt02b; safe because production registers coding alone — review-F6) — R1 |
| `external_work.decide_external_work` internal defn (external_work.py:47) | `coding_definition()` | **unchanged** (Phase 3 t_tt03) — R1 |

Net: per-row **resolution** is real (the type drives the codec, Tier-1 gating, the note check, and the two
load/persist validators); the coding-only production build is byte-identical; the resolution-engine internals
stay coding-default until t_tt02b threads the definition through them. **Safety rests on: production
registers `coding` alone** (review-F6) — NOT on any claim that a non-coding row is unreachable.

---

## 5. Per-type default ceiling

Replace the literal `TicketState.needs_success.value` used as the *default ceiling at creation*:
- **`create_ticket` INSERT (data.py:310):** currently binds `TicketState.needs_success.value` for `ceiling`.
  → bind `coding_bridge.default_ceiling(ticket_type)` (which for coding returns `"needs_success"` —
  views.py:141-143). Resolve the defn once at the top of the function.
- **`create_ticket_from_external_work` INSERT (data.py:398 and 404):** data.py:398 is `state`
  (`needs_success`), data.py:404 is `ceiling`. The **ceiling** at :404 → `default_ceiling(ticket_type)`. The
  **state** at :398 stays `needs_success`? — external-work create starts at `needs_success` then immediately
  reconciles to `target_state` via `_apply_decision`. For coding the initial ceiling default is
  `needs_success`; the subsequent `position_decision` overwrites ceiling to `target_state`. So :404 →
  `default_ceiling(ticket_type)`; :398 (initial state) is a coding literal that is Phase-3 external-work
  territory — leave it coding this ticket (external-work is coding-only until t_tt03), flag R1.
- **Seed importer (:247):** binds `ceiling = ticket.state.value` (prefix reconciliation), NOT a default —
  leave unchanged (it is intentionally the reconciled state, not the create-time default).
- **DDL `ceiling` default (db.py:71):** **dropped** (§1b, F3) — `ceiling` stays NOT NULL with no default, so
  an omitted ceiling fails loudly instead of inventing coding's value. Every writer sets it explicitly.

The BRIEF's "data.py:303/391/397" map to the live 310/398/404 above (line drift). Only the two **ceiling**
bindings (310, 404) become `default_ceiling`; the state literal and seed reconciled-ceiling are not
create-time defaults and stay.

---

## 6. `create_ticket` write-layer bridge

- `create_ticket(..., ticket_type: str = "coding")` and `create_ticket_from_external_work(...,
  ticket_type: str = "coding")` — a **data-layer default**, so existing fixed-arity call sites (CLI ticket
  create, external-work API, tests) are unedited. Grep confirms `create_ticket(` callers pass keyword args
  and none pass `ticket_type` today; the default preserves them. This mirrors t_tt01's `definition=None`
  bridge and is removed by t_tt03 (mandatory user-facing `--type`).
- **No existing test signature breaks:** the new param is keyword-with-default and appended; positional
  callers are unaffected. Confirmed: all `create_ticket(` / `create_ticket_from_external_work(` callers in
  `tests/` use keywords, and the one bare `Ticket(...)` fixture (test_value_edit_logic.py:53) uses all
  keyword args, so the trailing `Ticket.ticket_type="coding"` default (§3b) needs no edit there.

### 6a. Direct-SQL INSERT fixtures DO need a mechanical edit (F5 — correcting the earlier "zero edits" claim)

`ticket_type NOT NULL` (no DDL default) breaks any test fixture that INSERTs a ticket row via **raw SQL**
omitting `ticket_type`. Grep found these files with `INSERT INTO tickets` that construct rows directly:
- **`tests/unit/test_sprints.py`** (~:46) — add `ticket_type` column + `'coding'` value.
- **`tests/unit/test_days.py`** (~:28) — same.
- **`tests/unit/test_links.py`** (~:16) — same.
- **`tests/unit/test_db.py`** — the migration tests build their OWN pre-migration DDL (`_CURRENT_KICKOFF_
  TICKETS_DDL` etc., which have NO `ticket_type`); those seeds are the *input* to the migration and must
  **stay `ticket_type`-free** to exercise the backfill. Only the migration test's *post*-migration
  assertions read `ticket_type`. So test_db.py's existing inserts are **not** edited to add `ticket_type`
  (they are pre-migration shapes by design); new post-migration assertions are additive.
- **Re-grep at implement time:** `grep -rn "INSERT INTO tickets" tests/` — add `ticket_type='coding'` to
  every direct-SQL insert that targets the **current/new** schema (not a historical pre-migration seed).

These are **mechanical, behavior-preserving** edits — a new column + `'coding'` value, no assertion changes.
**Corrected claim:** Python-call fixtures (`create_ticket`, `Ticket(...)`) are unedited via the trailing
defaults; **direct-SQL INSERT fixtures against the live schema get a mechanical `ticket_type='coding'`.** The
BRIEF's "no existing-assertion edits" holds — these touch column lists, not assertions.

---

## 7. Test-overridable registry (the swap seam for t_tt02x)

Production stays coding-only; tests must be able to resolve a *different* type for the DB/validation doors.
The F6 constraint forbids anything but `coding_bridge` importing `ticket_types`, so the swap must live **in
`coding_bridge`**. Mechanism (choose the minimal one):

- **Add a module-level swappable accessor to `coding_bridge`:** a private `_active_registry: Registry | None`
  and a public `registry() -> Registry` that returns `_active_registry or coding_registry()`, plus a test
  hook `set_registry_for_test(reg: Registry | None) -> None`. Every new door calls `coding_bridge.registry()`
  / `coding_bridge.require(type_id)` (a thin `registry().require(type_id)` passthrough) / `coding_bridge.
  default_ceiling(type_id)` — NOT `coding_definition()` — so a test can install a registry that also
  contains a second type and drive a non-coding **type_id** through the doors.
- **Why a passthrough, not exposing `Registry`:** keeps F6 intact (callers name `coding_bridge`, not
  `ticket_types`) and gives one swap point.
- **What the injectable second-type test DEMONSTRATES (review-F6 — the boundary, not full parameterization):** a
  **coding-shaped** second type (same six fields, different `type_id`, e.g. `"coding_probe"`) registered via
  `set_registry_for_test` proves per-row type resolution, the unknown-type door, and per-type
  `default_ceiling` **without** hitting the codec's coding-bound guard — AND it demonstrates the review-F6 boundary:
  a coding-shaped non-coding type reaches the coding-**default** `decide_*` / `admission` / `resolve_scope`
  paths and gets **coding semantics**, because those paths are not yet definition-threaded. The test should
  make this explicit (assert the coding-default path is taken), NOT claim full engine parameterization — the
  point is to pin exactly why "no second production definition until t_tt02b" is the binding invariant.
- A **structurally-different** `probe` (kickoff/alpha/beta) can only be driven through the *type-id / state /
  ceiling* doors and the audit against **directly-inserted** rows (raw SQL bypassing the codec) — it cannot
  flow through `_row_to_ticket`'s codec (the codec rejects its field set). State this boundary in the tests;
  it is the honest extent of the machinery proof at this tier.

Keep `coding_definition()` for any remaining coding-literal call the ticket does not re-point (e.g.
`external_work.py:47`), so the production default is untouched where deferred.

---

## 8. Tests — every acceptance item as asserted values

All new tests in `tests/unit/test_db.py` (migration/schema/audit) and
`tests/unit/test_engine_parameterization.py` or a new `test_ticket_type_persistence.py` (doors). Reuse the
existing migration-test fixtures (`_CURRENT_KICKOFF_TICKETS_DDL` etc., test_db.py:70+) as the pre-migration
seed shape.

**A. Migration fidelity (acceptance 1) — EVERY column, exact per-row mapping equality (F2)**
- Seed N kickoff-shape rows (varied `title`, `priority`, `deadline`, `state`, `ceiling`, `at_cap`,
  `recap`, `implementer`, `ticket_status`, `fields`, `alias`, relationships, `chat_session_key`,
  timestamps) via `_CURRENT_KICKOFF_TICKETS_DDL` + raw inserts.
- **Snapshot before:** read every row as a `dict(row)` (name→value mapping over ALL columns) keyed by id.
- Run `create_schema`.
- **Snapshot after:** read every migrated row as a `dict(row)` keyed by id.
- Assert, per row: `after[id]` minus the new `ticket_type` key **exactly equals** `before[id]` (full mapping
  equality — this catches a shifted or dropped column that a subset check would miss); AND the removed
  `after[id]["ticket_type"] == "coding"`. This replaces any hand-listed subset of columns.
- Assert `SELECT COUNT(*)` unchanged and the set of ids unchanged.
- Assert the raw `fields` **string** is byte-equal `before[id]["fields"] == after[id]["fields"]` (belt-and-
  braces on JSON preservation; already implied by mapping equality, kept explicit).
- **FK integrity on live-shaped relationships (R2):** the seed MUST include a `day_tickets` row and a `links`
  row referencing a migrated ticket (and a parented ticket via `sprint_item_id`), then assert
  `PRAGMA foreign_key_check` returns `[]` after the DROP+RENAME swap — proving the id-preserving name-copy
  keeps `day_tickets.ticket_id REFERENCES tickets(id)` (db.py:165) intact through the rebuild. Without this,
  a real FK breakage on the owner's live DB (which has `day_tickets` rows) would ship silently.

**B. Schema shape (acceptance 2, 7)**
- After `create_schema`, read `sqlite_master.sql` for `tickets`:
  - assert it **contains** `ticket_type` and that column is `NOT NULL` (`"ticket_type TEXT NOT NULL"` substring
    or a `PRAGMA table_info` row with `notnull=1`, `dflt_value IS NULL`).
  - assert the enumerating `state` CHECK string (`"state IN ('needs_kickoff'"`) is **absent**.
  - assert the enumerating `ceiling` CHECK string (`"ceiling IN ('needs_success'"`) is **absent**.
  - assert the `ceiling` column has **no DEFAULT** (F3): `PRAGMA table_info(tickets)` row for `ceiling` has
    `dflt_value IS NULL` and `notnull==1`; and assert `state` **retains** its default `'needs_kickoff'`
    (`dflt_value == "'needs_kickoff'"`).
  - assert the type-independent checks **present**: `length(title) <= 200`, `priority IN`, `at_cap IN`,
    `ticket_status IN`, `implementer IN`.
  - assert index `idx_tickets_type_state` exists (`PRAGMA index_list(tickets)` / `sqlite_master`) and its
    columns are `(ticket_type, state)`; AND assert `idx_tickets_state` is **retained** (F7 — both indexes
    present, so a `state`-only reader keeps its index).
- **No-later-migration-regression (acceptance 7):** a test parses the **canonical** DDL constant (db.py:16
  `DDL`) and asserts the `tickets` block contains `ticket_type TEXT NOT NULL`, contains neither
  `state IN (` nor `ceiling IN (` enumerating CHECK, and the historical `tickets_new` templates in
  `_migrate_ticket_lifecycle`/`_rebuild_tickets_with_project_id` are NOT reachable as the final shape (assert
  the post-`create_schema` `sqlite_master.sql` never carries `in_progress`/`needs_review` or a retired
  CHECK). This is the "a later rebuild template cannot recreate the retired CHECK/default" guard.

**C. Idempotence + partial-shape safety (acceptance 3; F1)**
- **Full idempotence:** run `create_schema` twice on a migrated DB; assert the second run is a no-op —
  schema SQL identical, row set + every column identical, no error. (Mirror
  `test_create_schema_ticket_lifecycle_migration_is_idempotent`, test_db.py:909.)
- **Partial-shape rebuild (F1 — the new required test):** construct a `tickets` table that has `ticket_type`
  present BUT still carries an old enumerating CHECK (e.g. `ticket_type TEXT NOT NULL` added yet the
  `state IN ('needs_kickoff',...)` CHECK retained — a partial prior migration). Run `create_schema`. Assert
  the migration **rebuilt** it (did NOT early-return): the resulting `sqlite_master.sql` has NO enumerating
  `state`/`ceiling` CHECK, and rows survive. This proves the probe recognises the COMPLETE target shape, not
  merely the column's presence — the failure mode where an unmigrated schema is reported as success.
- **Copy/swap failure safety:** patch the swap to raise mid-way (as
  `test_kickoff_migration_rolls_back_failed_foreign_key_check`, test_db.py:566/852) and assert the
  **original** `tickets` table survives with all rows intact and `tickets_new` is gone after the raised
  error (the F8 single try/finally guarantees the scratch table is dropped on any failure phase, not only a
  swap-phase failure — see §2b/§2c).

**D. Startup audit (acceptance 4)** — one test per corruption case, each asserting the raised error carries
the offending **id** and the specific reason. Insert a corrupt row via raw SQL (CHECKs are gone, so the DB
accepts it), then call `audit_ticket_registry_integrity(conn)` and assert it raises with id + reason:
- unknown `ticket_type` (e.g. `'bogus'`) → reason mentions unknown type + type_id.
- `state` not in the type's stages (e.g. `'needs_alpha'` on `coding`) → reason "state outside the linear
  order" + state.
- `ceiling` outside the type's range (e.g. `'needs_kickoff'`, which is excluded from ceiling_range) → reason
  ceiling range + ceiling.
- **missing** declared field (delete a slot from `fields` JSON) → codec "corrupt ticket fields JSON" (F4
  strict-on-missing). A misspelled key is the SAME case (the correct key is absent).
- **malformed slot** (`fields` slot not a dict / bad proposal / non-str value) → codec validation reason.
- **Lenient-on-extra assertion (F4):** a row with an EXTRA unknown top-level `fields` key (e.g. the legacy
  `result` key) but an otherwise-valid field set **passes** the audit — assert `audit_ticket_registry_
  integrity` does NOT raise for it. This pins the intentional leniency so a future "reject extras" change
  fails loudly.
- Each raising case asserts the **id** appears in the message and that the audit stops at the **first**
  (ORDER BY id) corrupt row.

**E. Validation doors (acceptance 5)** — each door rejects an invalid **registered** ticket with the specific
error:
- **Load door:** raw-insert a row with a bad `state` for coding, then `read_ticket` → asserts the
  `PlannerError(validation, "state outside the linear order")`.
- **Persist door:** unit-test `_apply_decision` with a `Decision` carrying an out-of-range `new_ceiling` →
  asserts the pre-persist validator raises before the UPDATE (assert no row mutation via a post-error read).
- **create door:** `create_ticket(..., ticket_type="bogus")` → `PlannerError(not_found, "unknown ticket
  type")`.
- **external-work create door:** `create_ticket_from_external_work(..., ticket_type="bogus")` → same.
- **seed door:** feed a seed ticket with a state the registry rejects → the importer raises with the
  specific reason (and the transaction rolls back — assert no partial rows).
- **note door:** `set_field_user_note(field=<not a declared field of the type>)` → `PlannerError(validation,
  "unknown ticket field")` (existing test `test_note_on_undeclared_field_rejected_no_side_effect`,
  test_engine_parameterization.py:387, must stay green with the definition re-pointed).

**F. Per-type default ceiling (acceptance 6)**
- `create_ticket(...)` (coding) → assert the created ticket's `ceiling == "needs_success"` AND assert the
  value came from `coding_bridge.default_ceiling("coding")` (assert equality to `registry.default_ceiling`,
  not a literal — call `default_ceiling` in the test and compare).
- With a test-registered **coding-shaped** second type whose ceiling range starts elsewhere (§7), assert its
  created ticket's ceiling equals that type's `default_ceiling` — proving the source is the registry, not a
  constant. (This is the door t_tt02x extends; here assert at least the coding value derives from
  `default_ceiling`.)

**G. Coding parity (acceptance 8)**
- The **entire existing** `./verify` suite passes with **no existing-assertion edits** — the parity proof.
  Specifically: `test_tickets_engine`, `test_ticket_lifecycle`, `test_engine_parameterization`,
  `test_ticket_edit_api`, `test_value_edit_*`, `test_return_for_revision`, `test_chief_external_work`,
  `test_seed`, and the e2e flows must pass unchanged. The `create_ticket` default (`ticket_type="coding"`)
  and the `Ticket.ticket_type` default (`="coding"`) keep Python-call fixtures unedited (§3b R3, §6).
- **Direct-SQL INSERT fixtures ARE edited (mechanically, F5/§6a):** `test_sprints.py`, `test_days.py`,
  `test_links.py` (and any other file `grep -rn "INSERT INTO tickets" tests/` surfaces against the live
  schema) get `ticket_type='coding'` added to their column lists. These are column-list edits, **not
  assertion edits** — so "no existing-assertion edits" still holds precisely. The parity claim is therefore:
  *no behavioral assertion changes; direct-SQL inserts gain one mechanical column.*
- **F6 guard stays green:** `test_no_production_module_imports_ticket_types`
  (test_ticket_type_registry.py:761) must still see `{coding_bridge}` as the sole importer — so `db.py`,
  `data.py`, `server.py`, `seed/importer.py` reach the registry **only through `coding_bridge`**. Add a
  focused assertion or rely on the existing F6 test (it scans the whole tree, so a stray `ticket_types`
  import anywhere fails it).
- **Single-writer guard stays green:** `test_only_apply_decision_writes_canonical_position`
  (test_engine_parameterization.py:328) — the pre-persist validator added to `_apply_decision` writes no
  columns, so the guard holds.

---

## 9. Open risks (sharpest first)

**R1 — Definition-threading depth (RESOLVED — scoping ruled; the binding invariant is review-F6).**
The BRIEF says callers "pass `definition=registry.require(...)`", but `resolution.decide_*` and
`machine.plan_handoff_status` do not accept `definition=` today. This plan threads the resolved defn into
the codec, the Tier-1 gating lookup, the note check, and the load/persist validators — and **leaves the
resolution engine, `plan_handoff_status`, and external-work internals coding-default**; the coordinator has
**carried that threading into t_tt02b's scope.** The deferral is NOT justified by "a non-coding row is
unreachable" (false — a coding-shaped type would reach the coding-default paths). It is safe for exactly one
reason, which t_tt02 must hold as an invariant: **production registers `coding` alone** (coding_bridge.py:59),
so no non-coding definition exists to resolve. **Binding rule left for t_tt02b: no second PRODUCTION
definition may be registered until the definition is threaded through `resolution.decide_*`,
`machine.plan_handoff_status`, and external-work.** The injectable-second-type test demonstrates this
boundary (§7). No open question remains — the scope is set; the residual risk is that someone registers a
second production type before t_tt02b (guard: the review-F6 invariant + the demonstrating test).

**R2 — Migration safety on live data.** The migration `DROP TABLE tickets; RENAME tickets_new` under
`foreign_keys=OFF` then `foreign_key_check` mirrors the landed kickoff migration, which is proven on live
data — but this table is referenced by `day_tickets`, `links` (soft, no FK), `sprint_items`? (tickets
reference items/sprints/projects, not vice-versa; `day_tickets.ticket_id REFERENCES tickets(id)`). Dropping
and recreating `tickets` **breaks `day_tickets`'s FK to the old rowids** momentarily; the FK-off window +
`foreign_key_check` after rename is exactly what catches a mismatch. **The named risk:** if a live DB has
`day_tickets` rows, the check must return `[]` (ids are preserved by name-copy, so it will) — but the test
MUST seed a `day_tickets` (and `links`) row referencing a migrated ticket and assert `foreign_key_check == []`
post-migration, or a real breakage ships silently. Add that to the fidelity test.

**R3 — Adding a field to the `Ticket` dataclass (mitigated + verified).** `Ticket` gains
`ticket_type: str = "coding"` **as the last field with a default**, so a required-field break is avoided.
Verified: the only bare `Ticket(...)` constructor in tests (test_value_edit_logic.py:53) uses **all keyword
args**, and no production `Ticket(...)` constructor exists besides `_row_to_ticket` (always passes the
resolved value). `Ticket` is a plain `@dataclass` (no frozen/ordering constraint), so a trailing default is
valid. Residual: re-grep `Ticket(` at implement time in case a new all-positional constructor landed.

**R4 — `ceiling` DDL default (RESOLVED by F3).** Owner ruled: **drop** the `ceiling` `DEFAULT 'needs_success'`,
keep `ceiling NOT NULL` (§1b). `needs_success` is coding's default, not type-independent; with the CHECK
gone, a defaulted `ceiling` would silently invent coding semantics for any type. An omitted `ceiling` now
fails loudly (NOT NULL); every sanctioned writer sets the per-type default. `state DEFAULT 'needs_kickoff'`
is kept (genuinely universal bookend). No open question.

**R5 — Validator + audit placement and invocation (RESOLVED by F8b).** The shared validator is a
pure module importing only `coding_bridge` (F6-import-safe); the audit is a `data.py` function reaching the registry
via `coding_bridge`. Invocation is decided (§3g, F8b): the full scan runs **once in the `panels serve`
lifespan** after the registry build and before background loops; seed/CLI paths rely on the create door
(validate-before-insert) and the load door (validate-on-read), so they need no separate scan. No open
question.

**R6 — Direct-SQL test fixtures need a mechanical column add (F5, tracked not open).** `ticket_type NOT NULL`
(no default) breaks raw-SQL ticket inserts in `test_sprints.py`, `test_days.py`, `test_links.py` (and any
other `grep -rn "INSERT INTO tickets" tests/` against the live schema); each gets a mechanical
`ticket_type='coding'` (§6a). These are column-list edits, not assertion edits, so the parity claim holds
precisely. The implementer must re-grep at implement time so a newly-added direct-SQL fixture is not missed —
a forgotten one fails loudly at insert (NOT NULL), so this is self-catching, not silent.
