# AD02 implementation plan — deep Worker-type workflow interpretation

## Outcome and hard boundary

AD02 replaces the current shallow `ticket_types -> views -> Registry forwarders -> coding_bridge`
stack with one behavior-bearing Worker-type definition. A Ticket continues to store an immutable,
required `worker_type` and an authoritative plain-string `stage`. Code resolves that Worker type only
when it needs to interpret Stage or field relationships; a plain read, return, filter, grouping, or
universal `needs_kickoff` / `done` / `dropped` comparison remains registry-free.

This is a deletion replacement, not a compatibility migration in Python:

- add `src/planner/worker_types/` as the only Worker-type domain;
- delete the complete `src/planner/ticket_types/` tree;
- delete `src/planner/tickets/logic/coding_bridge.py`;
- delete `src/planner/tickets/logic/ticket_type_guard.py` without adding a renamed guard;
- remove `CodingStage`, `FieldName`, every `CODING_*` lifecycle constant, and
  `machine.FIELD_GATES`; and
- add no alias module, re-export, property, default definition, free-view namespace, thin registry
  forwarder, or coding-shaped fallback under a new name.

Stage ids, field ids, Worker profiles, manifests, validation errors, event order and payloads, scope,
external-work behavior, Review/board/sprint behavior, CLI behavior, frontend behavior, and persisted
values stay exact. Historical `ticket_type` names remain only inside the sealed pre-v18 migration
recognition code and its old-schema fixtures; they are storage inputs, not a live internal API.

There is one necessary forward database migration. The current `tickets.fields` default silently
creates coding's six fields even though `worker_type` is required. That is an implicit coding default
and therefore violates this ticket. AD02 removes it with schema version 19 while copying every existing
`fields` JSON value byte-for-byte.

## Locked Worker-type contract

### `worker_types/contracts.py`

Use descriptive names throughout. The public skeleton is:

```python
@dataclass(frozen=True, slots=True)
class StageDefinition:
    id: str
    label: str
    gating_field: str | None
    is_terminal: bool


@dataclass(frozen=True, slots=True)
class FieldDefinition:
    id: str
    label: str


@dataclass(frozen=True, slots=True)
class WorkerProfile:
    specialist_skill: str
    model: str | None
    reasoning_effort: str | None
    toolset_profile: str


@dataclass(frozen=True, slots=True)
class TransitionHook:
    old_stage: str
    new_stage: str
    implementer: str
    effect: str


@dataclass(frozen=True, slots=True)
class WorkerTypeDefinition:
    worker_type: str
    label: str
    stages: tuple[StageDefinition, ...]
    dropped_stage: StageDefinition
    fields: tuple[FieldDefinition, ...]
    worker_profile: WorkerProfile
    transition_hooks: tuple[TransitionHook, ...]
    supports_prefix_reconciliation: bool

    def stage_ids(self) -> tuple[str, ...]: ...
    def stage_index(self, stage: str) -> int: ...
    def stage_definition(self, stage: str) -> StageDefinition: ...
    def is_known_stage(self, stage: str) -> bool: ...
    def is_terminal(self, stage: str) -> bool: ...
    def gating_field(self, stage: str) -> str | None: ...
    def stage_gated_by(self, field: str) -> str: ...
    def field_ids(self) -> tuple[str, ...]: ...
    def has_field(self, field: str) -> bool: ...
    def field_definition(self, field: str) -> FieldDefinition: ...
    def advance_target(self, stage: str) -> str | None: ...
    def ceiling_range(self) -> tuple[str, ...]: ...
    def default_ceiling(self) -> str: ...
    def first_worker_stage(self) -> str: ...
    def completed_stage(self) -> str: ...
    def validate_ticket_position(self, stage: str, ceiling: str) -> None: ...
    def transition_effect(
        self, implementer: str, old_stage: str, new_stage: str
    ) -> str | None: ...
    def reconciliation_field_order(self) -> tuple[str, ...]: ...
```

The tuples above are the only stored workflow declarations. Methods derive their answers from those
tuples on demand; do not add cached mutable maps or a second hand-written table. Preserve the existing
error contract:

- an unknown/non-linear Stage passed to `stage_index` or `stage_definition` raises
  `PlannerError(validation, "stage outside the linear order", {"stage": stage})`;
- `is_known_stage` includes the exceptional `dropped` Stage;
- `is_terminal`, `gating_field`, and `advance_target` recognize `dropped` and return the existing
  terminal answers (`True`, `None`, and `None`);
- `stage_gated_by` raises the existing `"field gates no stage"` validation error;
- `field_definition` raises the existing unknown-ticket-field validation error; and
- `completed_stage()` returns the validated linear terminal (`done`), while `dropped_stage` remains
  outside `stage_ids()` and `ceiling_range()`.

`validate_ticket_position(stage, ceiling)` is the one definition-owned integrity operation for a
stored/prospective Ticket position. It accepts a linear Stage or the exceptional `dropped`, requires
the ceiling to belong to `ceiling_range()`, and preserves the exact current errors: invalid Stage uses
`"stage outside the linear order"`; invalid ceiling uses `scope_invalid`,
`"ceiling outside the type's range"`, and
`{"worker_type": self.worker_type, "ceiling": ceiling}`. It does not resolve a registry id; the
boundary must first call `registry.require(worker_type)` and then call this method on that explicit
definition.

`reconciliation_field_order()` returns the gating field of each non-terminal Stage in Stage order.
This moves `external_work._gate_field_order` behind the definition and prevents callers from reading
the raw Stage table to reconstruct behavior. `default_ceiling()` remains the first linear Stage
(`needs_kickoff`); `first_worker_stage()` remains the second Stage. These are distinct operations.

Keep the manifest TypedDicts in this file, renamed descriptively:
`WorkerTypeManifestStage`, `WorkerTypeManifestField`, and `WorkerTypeManifest`. Their JSON keys and
value shapes remain exactly the existing `worker_type`, `label`, `stages`, `dropped`, `advance`,
`fields`, `ceiling_range`, `default_ceiling`, and `worker_profile_id` contract. Do not serialize
`supports_prefix_reconciliation`, model, reasoning effort, or toolset.

### `worker_types/registry.py`

The registry is deliberately narrow:

```python
class WorkerTypeRegistry:
    __slots__ = ("_definitions",)

    def __init__(
        self,
        definitions: Iterable[WorkerTypeDefinition],
        *,
        known_skills: frozenset[str],
        known_toolset_profiles: frozenset[str],
    ) -> None: ...

    def registered_worker_types(self) -> tuple[str, ...]: ...
    def require(self, worker_type: str) -> WorkerTypeDefinition: ...
    def manifest(self, worker_type: str) -> WorkerTypeManifest: ...
```

Construction validates every definition in insertion order, rejects duplicate `worker_type` values,
and stores a defensive `MappingProxyType` copy. Keep validation private to this module: constructing a
`WorkerTypeRegistry` is the public validation door. Port the current deterministic R0-R20 order and
the exact messages/details, changing only internal `type_id` reads to `definition.worker_type`.
Reference catalogs remain injected; the package still imports no `minds` configuration.

`manifest()` serializes directly from the required definition and its methods. It may build the
`advance` object by iterating `stage_ids()` and calling `advance_target()`, but it does not acquire its
own successor/order logic. There is no public serializer function and no `build_registry` factory.
The public registry method set contains no `definition_for`, lifecycle view, field, gate, terminal,
successor, ceiling, or transition-effect forwarding method.

### Definition modules and package facade

- `worker_types/coding.py` declares the complete coding definition directly as immutable
  `StageDefinition` / `FieldDefinition` tuples. It does not import Ticket lifecycle declarations.
  Keep the exact order
  `needs_kickoff -> needs_success -> needs_approach -> needs_plan -> needs_implementation ->
  needs_closeout -> done`, exceptional `dropped`, the six matching fields, the
  `panels-worker-coding` profile, prefix reconciliation flag, and the existing
  `(khushal, needs_plan, needs_implementation) -> user_takeover` hook. Export only
  `CODING_WORKER_TYPE_DEFINITION`.
- `worker_types/new_worker.py` declares its existing novel order and fields directly and exports only
  `NEW_WORKER_TYPE_DEFINITION`. It reuses universal string ids by value, not by importing a coding
  enum. Keep its `panels-worker-new-worker` profile, empty hooks, and prefix support exact.
- `worker_types/configuration.py` is application composition, not lifecycle interpretation. It owns
  the injected production skill/toolset catalogs, the ordered production tuple
  `(CODING_WORKER_TYPE_DEFINITION, NEW_WORKER_TYPE_DEFINITION)`, one eagerly validated
  `PRODUCTION_WORKER_TYPE_REGISTRY`, and the active test seam:

  ```python
  def configured_worker_type_registry() -> WorkerTypeRegistry: ...
  def install_worker_type_registry_for_test(registry: WorkerTypeRegistry) -> None: ...
  def restore_production_worker_type_registry_for_test() -> None: ...
  ```

  The configured variable starts as the production registry and test installation replaces that
  variable directly; there is no `if missing, use coding` path. Production code calls this only at a
  boundary and then calls `require(explicit_worker_type)`. Pure semantic functions never call it.
- `worker_types/__init__.py` is the sole public package facade. Export the contract types,
  `WorkerTypeRegistry`, production definitions/registry, and the three configuration functions. Do
  not expose private validation or a free interpretation namespace.

Adding a third production type is then: add its definition module and specialist skill, add the skill
id to the injected catalog, and add one definition to the production tuple. No Ticket engine module,
registry method, manifest serializer, or frontend lifecycle helper changes.

## Remove the parallel Ticket lifecycle authority

In `tickets/contracts.py` delete `CodingStage`, `FieldName`, `CODING_STAGE_ORDER`,
`CODING_EMPLOYEE_STAGE_ORDER`, `CODING_GATING_FIELD_BY_STAGE`, and
`CODING_NEXT_STAGE_BY_STAGE`. Ticket contracts retain only Ticket-owned shapes such as `AtCap`,
`Implementer`, `TicketStatus`, `TicketFields`, and request/row contracts. Make all Stage and field
parameters plain `str`. Remove coding-only dynamic keys (`success`, `approach`, `plan`,
`implementation`, `closeout`) from `ReconcileTicketFromExternalWorkBody`; that TypedDict contains
only its fixed wire keys, while API marshalling carries definition-declared field values separately.

Delete `machine.FIELD_GATES`, enum re-wrap helpers, overloads, and the shallow machine wrappers
`stage_index`, `is_terminal`, `gating_field`, `advance_target`, and `validate_ceiling`. Basic
interpretation is called on `WorkerTypeDefinition`; Ticket-specific composite rules remain in
`machine.py` with a required, descriptively named keyword-only parameter and no default:

```python
def field_is_passed(field: str, stage: str, *,
    worker_type_definition: WorkerTypeDefinition) -> bool: ...
def auto_accept_target(stage: str, ceiling: str, field: str, *,
    worker_type_definition: WorkerTypeDefinition) -> str | None: ...
def at_or_beyond_ceiling(stage: str, ceiling: str, *,
    worker_type_definition: WorkerTypeDefinition) -> bool: ...
def resolve_scope(new_stage: str, next_ceiling: NextCeiling | None, at_cap: AtCap | None, *,
    worker_type_definition: WorkerTypeDefinition) -> ScopePair: ...
def has_pending_gating_proposal(stage: str, fields: TicketFields, *,
    worker_type_definition: WorkerTypeDefinition) -> bool: ...
def has_pending_parked_proposal(ticket: Ticket, *,
    worker_type_definition: WorkerTypeDefinition) -> bool: ...
def plan_handoff_status(implementer: Implementer | None, old_stage: str,
    new_stage: str | None, *, worker_type_definition: WorkerTypeDefinition
) -> TicketStatus | None: ...
```

The same required keyword is added to semantic functions in:

- `admission.check_agent_proposal` and `admission.check_recap_writable`;
- `resolution._accept_gating_proposal`, `decide_file_proposal`, `decide_accept`,
  `decide_edit_value`, `decide_return_for_revision`, and `decide_scope_change`; and
- `external_work.decide_external_work` plus its private prefix helper.

`resolution.decide_stage_jump` and `decide_drop` do not take a definition: after ingress validation,
they only compare equality with the universally validated ids `needs_kickoff`, `done`, and `dropped`.
Use those string values directly. This is a direct universal rule, not Stage-order interpretation.

## Resolution and threading by production call site

### Ticket persistence and resolution engine

Delete `tickets/logic/ticket_type_guard.py` and add no replacement module or forwarding function.
Ticket-position validation belongs to `WorkerTypeDefinition.validate_ticket_position`; every boundary
does the two explicit operations itself: `definition = registry.require(worker_type)`, then
`definition.validate_ticket_position(stage, ceiling)`. This keeps registry lookup at the boundary and
workflow interpretation on the definition, with no shallow renamed seam.

In `tickets/data.py`:

- `_row_to_ticket`, `_load_ticket`, `read_ticket`, and `read_ticket_by_session_key` continue to copy
  stored `worker_type`, `stage`, `ceiling`, and fields directly. They do not resolve a definition.
- Add `_load_ticket_and_worker_type_definition_for_write`. It gets the configured registry, calls the
  registry's `require` once for the row, calls `definition.validate_ticket_position`, validates
  declared field storage, and returns `(Ticket, WorkerTypeDefinition)`. `_load_ticket_for_write` may
  delegate and discard the definition for
  status-only writers. Semantic writers use the pair and do not perform a second lookup in the same
  transaction.
- `_apply_decision` explicitly resolves `ticket.worker_type` through the configured registry and calls
  `validate_ticket_position` for the prospective Stage/ceiling before SQL. `_active_blocker_stage`
  remains a direct universal `done`/`dropped` comparison.
- `create_ticket` and `create_ticket_from_external_work` resolve the explicitly supplied
  `worker_type` before opening their write. Build fields from `definition.field_ids()`, obtain kickoff
  and initial/first-worker Stage from definition methods, and get the ceiling from
  `definition.default_ceiling()`. No creation value comes from a coding constant or schema default.
- `reconcile_ticket_from_external_work` threads the definition returned by the write load into
  `decide_external_work`.
- `audit_ticket_registry_integrity` obtains the configured registry once, calls `require` and
  `validate_ticket_position` for each row, and validates its declared fields with that definition.
  Plain reads still do not inherit this boot-audit behavior.
- `file_proposal` and `file_current_proposal_with_recap` load the Ticket plus definition and pass it
  through resolution, admission, gating-field lookup, and handoff-status lookup.
- `accept_proposal`, `edit_field_value`, `return_for_revision`, and `change_scope` do the same for
  their respective required semantic decision functions.
- `write_recap` passes the loaded definition to `check_recap_writable`.
- `set_field_user_note` validates `definition.has_field(field)` from the loaded pair.
- `set_stage` validates the requested Stage through the loaded definition before invoking the
  definition-free universal jump rule. `drop_ticket` needs no definition beyond the normal write-load
  integrity door because its rule compares only universal terminal ids.
- `start_run_if_runnable` changes its injected guard type to receive the already-resolved definition:
  `Callable[[Connection, Ticket, WorkerTypeDefinition], bool]`. Status-only finish/error paths discard
  the definition after the normal write-load validation.

`fields_codec.fields_from_json(raw)` remains the registry-free stored-data decoder. Remove its
optional definition argument and Worker-type import. Add a separately named declared-field validation
path (sharing the slot parser) that accepts explicit `field_ids: tuple[str, ...]`, requires every
declared field, ignores unknown legacy top-level keys exactly as the current audit/write path does,
and returns `TicketFields`. There is no optional field vocabulary and no coding fallback.

### HTTP and application composition

In `tickets/api.py`, replace all bridge types and calls with `WorkerTypeDefinition` and the configured
registry:

- `_require_create_worker_type` reads `registered_worker_types()` only to report the exact known-type
  error; creation still requires the request's explicit `worker_type`.
- Rename `_resolve_worker_type` to `_ticket_and_worker_type_definition`; it directly reads the Ticket,
  resolves `ticket.worker_type`, and returns both. This is used by field, note, value, accept, scope,
  direct-Stage, and external-work ingress.
- `_validate_field`, `_external_field_keys`, `_marshal_external_*`, `_external_values`,
  `_validate_external_stage`, and `_parse_next_ceiling` call definition methods. They never inspect
  `.stages` or `.fields` directly.
- External-work creation resolves the request's explicit Worker type before interpreting its Stage or
  field keys. Reconciliation resolves the stored Ticket Worker type. The data writer resolves again
  inside its transaction intentionally: ingress validation and transactional write admission are two
  separate trust/TOCTOU boundaries.
- `GET /tickets/by-session` resolves the stored Worker type only to select
  `worker_profile.specialist_skill`; the rest of the Ticket response is a direct read.

In `core/server.py`, accessing `PRODUCTION_WORKER_TYPE_REGISTRY` at import/composition validates the
two shipped definitions before serving. Lifespan audit uses the configured registry. `/api/worker-types`
iterates `registered_worker_types()` and calls `manifest()`; its envelope, order, and entries remain
byte-for-byte equivalent. A test-installed registry is still visible in-process.

### Runtime

- `runtime/readiness.is_runnable` takes required
  `worker_type_definition: WorkerTypeDefinition`. It calls definition methods for terminal/gate checks
  and passes the same definition to composite machine rules.
- `ticket_readiness_loop.poll_once` keeps its SQL `done`/`dropped` prefilter as a direct universal
  stored-value comparison, then resolves each candidate Ticket's stored Worker type before the full
  readiness predicate.
- `employee_step_runner.ready_on_today` receives the definition from
  `start_run_if_runnable` and passes it to readiness. After a successful claim, the runner resolves the
  claimed Ticket's Worker type for `_next_step_prompt(ticket, *, worker_type_definition=...)`; the
  prompt gets its gating field from that definition. This second lookup is after the claim and belongs
  to a new execution phase, not an implicit fallback. Revision guidance that does not interpret Stage
  remains unchanged.

### Read models, Review, board, and sprint

- `ticket_json`, `list_tickets`, ticket detail, event serialization, Stage query filters, blocker-row
  serialization, and stored fields serialization are direct reads and remain registry-free.
- `tickets/views.copy_text` resolves the Ticket's Worker type because definition field order determines
  the rendered sections.
- `tickets/views.board_view` resolves each row's Worker type for gate, field label, Stage label,
  pending proposal, and completed-Stage answers. It calls `field_definition()` and other methods rather
  than reading definition tuples. Preserve the existing board baseline exactly by explicitly resolving
  the registered `coding` definition for its initial empty column order; novel Stage columns are still
  appended only when encountered. This is an intentional read-model presentation choice, not a
  missing-type fallback.
- `_approval_digest` resolves each serialized Ticket's own `worker_type` to find its gating field.
  `_overdue_digest` keeps direct universal `done`/`dropped` comparisons. Review remains Ticket/running-
  worker focused and otherwise unchanged.
- `sprints/data.read_item` resolves each child row's Worker type before
  `_child_stage_in_progress(definition, stage)` interprets terminality and the first-worker threshold.
- `sprints/views.item_tickets` resolves each row for pending-proposal interpretation. `item_rollup`
  preserves its existing coding baseline by explicitly resolving `coding` for the initial keys, then
  counts any stored novel Stage directly. Sprint status logic's universal `done`/`dropped` equality
  remains registry-free.
- `core/links.py`, `tickets/data._active_blocker_stage`, the readiness SQL prefilter, and closed-status
  read helpers continue to compare stored `done`/`dropped` directly. Worker-type validation makes those
  ids universal; none asks an order/gate/successor question.

### Seed and CLI

`seed/contracts.py` changes `ParsedTicket.stage` and `READINESS_MAP` values to plain strings. This map
is intentionally coding-only legacy-ingress translation from historical markdown headings; it is not
an executable order, gate, successor, or terminal table. `seed/importer.py` explicitly resolves
`coding` through the configured registry and calls
`coding_definition.validate_ticket_position(ticket.stage, ticket.stage)` before writing. It must not
retain the hardcoded six-key coding fields dictionary. Instead it constructs
`TicketFields.empty(coding_definition.field_ids())`, overlays only the legacy seed values it actually
owns (`kickoff=ticket.body`, `success=ticket.success`, `approach=ticket.approach`) via the shared
`fields_codec.with_slot` / `FieldSlot` path, and serializes through `fields_codec.fields_to_json`.
The three overlay names are intentional translations from the old coding-only seed format; the full
field set and all empty remaining slots come solely from the resolved definition.

Delete CLI `_FIELDS`: it currently makes `worker note` reject valid non-coding fields and is therefore
an executable second field vocabulary. `worker note` resolves the Ticket id, fetches its detail and
served Worker-type manifest, derives the non-kickoff field list, and formats the current coding error
text from that list (so coding's message stays exact while `new_worker`/future fields work). Existing
manifest-driven approval behavior remains unchanged. Extend the CLI verb e2e test with a shipped
`new_worker` Ticket and a `stages` note, while retaining the existing coding note assertion.

Both Chief external-work commands gain repeatable
`--field-file FIELD=PATH` (`click` `multiple=True`) so any definition-declared field can be carried by
the CLI. Parse each occurrence with one `split("=", 1)`, require a non-empty field and path, preserve
occurrence order, and read the path with the existing `-`/file helper. Keep the named
`--success-file`, `--approach-file`, `--plan-file`, `--implementation-file`, and
`--closeout-file` options as coding conveniences and feed them through the same assembled field-source
map.

Duplicate handling is locked and happens before reading a source or making an HTTP request: process
provided named flags in their existing stable order, then generic occurrences in command-line order;
reject the first repeated key (including a named/generic collision) with
`field file provided more than once: <field>`. Malformed `FIELD=PATH` input fails locally before send.
The CLI does not fetch a definition or decide whether a key belongs to a Worker type; it only rejects
syntactic/duplicate ambiguity. The API remains authoritative for the resolved type's declared fields,
target Stage, settled prefix, and exact validation errors.

Extend `test_chief_external_work_cli.py` to prove both commands carry novel shipped fields: create a
`new_worker` Ticket at `needs_thinking` with `--field-file stages=PATH`, then reconcile it to
`needs_drafting` with `--field-file thinking=PATH`. Assert the settled prefix and Stage returned by the
real server. Also cover repeated generic keys and named/generic collisions, asserting deterministic
local failure and no request/write. Retain every existing named coding-option assertion.

## SQLite schema version 19 — remove the implicit coding fields default

Make this a forward rebuild, not an in-place textual edit:

1. Bump `SCHEMA_VERSION` from 18 to 19.
2. In canonical `DDL`, declare `fields TEXT NOT NULL` with no default.
3. Replace the current final-shape constants/helpers with `_V19_TICKETS_TABLE_SQL`,
   `_tickets_table_is_v19`, and `_migrate_tickets_to_v19_contract`. The v19 matcher expects the fields
   column default to be `None`; every other canonical constraint remains exact.
4. Preserve the consolidated migration's lock/FK/savepoint/rollback algorithm and all historical v18
   input recognition. A canonical v18 database (including the coding-shaped default) is a recognized
   rebuild input. Copy its `fields` text explicitly and byte-for-byte into `tickets_new`; do not decode,
   normalize, or choose fields from `worker_type` during migration.
5. Every production insert already supplies fields; keep that explicit. An insert that omits fields now
   fails `sqlite3.IntegrityError` rather than manufacturing coding slots.
6. Fresh v19 creation does not rebuild. Reopening v18 rebuilds once and preserves rows/events/FKs;
   reopening v19 is idempotent. A forced copy/swap/FK failure rolls the schema and rows back and restores
   FK mode.

Update sanctioned fresh-schema fixtures that relied on the old default in `test_days.py`,
`test_links.py`, `test_sprints.py`, `test_worker_type_stage_contracts.py`, relevant fresh/event cases in
`test_db.py`, and any helper in `test_worker_type_persistence.py` to supply explicit fields JSON. Old
pre-v18 input DDL fixtures retain their historical defaults because they are migration inputs, not the
canonical schema. Add an isolated fresh-v19 omission test expecting `IntegrityError`.

## Tests and static architecture locks

### Replace duplicate-authority parity tests

Rename `tests/unit/test_ticket_type_registry.py` to `test_worker_type_registry.py` by delete + add.
Rewrite it around the new public contract:

- all definition parts and the definition itself are frozen/slot-backed;
- coding methods return the exact current Stage order, fields, gates, successors, terminals, ceiling
  range/default, first worker Stage, transition effect, reconciliation order, profile, and valid Ticket
  positions; invalid Stage/ceiling cases assert the complete preserved errors directly against
  `validate_ticket_position`;
- `new_worker` methods return its exact distinct answers;
- no assertion compares the definition to a Ticket enum, coding map, or `FIELD_GATES`;
- all R0-R20 negative cases construct `WorkerTypeRegistry` and preserve complete errors;
- registry order is `("coding", "new_worker")`, unknown lookup and defensive copy behavior remain
  exact, and manifests for both shipped types are complete exact dictionaries/JSON round trips; and
- the public registry method set is exactly construction, `registered_worker_types`, `require`, and
  `manifest` (dunder methods ignored).

Update `tests/support/probe.py` to define `PROBE_WORKER_TYPE_DEFINITION` with the new contracts. Its test
registry contains `(coding, new_worker, probe)` in that order and injects all three shipped/test skill
ids. `install_probe_registry` and `uninstall_probe_registry` call the non-coding configuration seam;
they never import or mention a coding bridge. Update manifest/type-list expectations accordingly.

In the seed tests, install a valid test definition under the `coding` id with one additional gated
field, import a legacy seed Ticket, and assert that extra definition-declared slot is present and empty
while kickoff/success/approach carry the legacy values. This behavior test, plus the AST authority
guard, proves the importer derives the complete field set instead of preserving a copied six-key map.

Keep and adapt the existing engine, persistence, ingress, generic field storage, external-work,
go/no-go, board, copy-text, Review queue, sprint, runtime readiness, employee runner, and worker-profile
tests. Together they must drive:

- coding through the full existing lifecycle with exact events/errors;
- `new_worker` through its shipped definition and manifest;
- probe through creation, persistence/reload, proposal/acceptance/scope/transition effect, direct Stage
  ingress, field storage, external-work prefix reconciliation, board/Review/sprint views, readiness,
  prompt selection, and worker-profile lookup; and
- direct Ticket reads, Stage filtering, grouping, and fields decoding without registry lookup, while
  boot/write validation still rejects unknown Worker types or invalid relationships.

Replace the old overload mypy fixtures with positive strict-mypy calls that pass
`worker_type_definition=` explicitly and operate on strings. Add runtime signature tests (or AST tests)
showing every semantic machine/admission/resolution/external-work/readiness function has a required
`WorkerTypeDefinition` parameter; an omitted definition must raise Python `TypeError`, never run coding.

### Static deletion guards

Put the guard suite in `test_worker_type_registry.py` and make it inspect AST/path structure, not only
grep comments:

- `src/planner/ticket_types/`, `coding_bridge.py`, `ticket_type_guard.py`, and
  `worker_type_guard.py` do not exist;
- no import resolves to `planner.ticket_types` and no production/test identifier refers to
  `WorkflowDefinition`, generic `Registry`, `type_id`, `CodingStage`, `FieldName`, any listed
  `CODING_*` lifecycle constant, `FIELD_GATES`, `coding_definition`, `coding_registry`, or
  `build_registry` (the guard's own forbidden-string literals and sealed DB legacy recognizer are
  explicit exceptions);
- no `views.py`, public free serializer/validator, registry lifecycle forwarder, or compatibility
  package/module exists under `worker_types`;
- all function parameters named `worker_type_definition` in semantic modules have no default, and AST
  finds no optional-definition annotation, `definition=None`, `definition or ...`, `.get("coding")`,
  or equivalent coding fallback;
- `tickets/contracts.py` defines no Stage/field lifecycle enum/order/map and `machine.py` defines no
  lifecycle table;
- `WorkerTypeRegistry`'s public method names are locked to the narrow skeleton;
- only definition modules contain executable shipped Stage/field orders; coding strings elsewhere are
  allowed only as universal equality, legacy migration/seed input, exact fixtures, error assertions,
  or the retained named Chief convenience options; seed field-map construction must call the coding
  definition's `field_ids()` and may not declare the complete coding field set; and
- package outbound imports remain restricted to stdlib, `planner.core.contracts`, and the Ticket
  `Implementer`/`TicketStatus` contract leaf. Harden relative-import resolution as in the existing
  guard.

The schema guard separately asserts the canonical and v19 target SQL contain `fields TEXT NOT NULL`
with no default and that sanctioned current-schema inserts always supply fields.

## Live docs, skill, frontend source, and generated output

- Rewrite `docs/worker-types.md` to describe the behavior-bearing definition, direct boundary imports,
  explicit resolution, production/test composition, no coding default, new package paths, and the
  one-registration recipe. Remove the obsolete seam/views diagrams and the claim that coding is a
  creation default.
- Update `skills/panels-worker-new-worker/SKILL.md` to name `WorkerTypeDefinition`,
  `src/planner/worker_types/`, and the production configuration tuple/catalog. Keep its Stage guidance
  and front-door announcement steps unchanged.
- Update `skills/panels-chief-of-staff/SKILL.md` external-work instructions to use repeatable
  `--field-file FIELD=PATH` for definition-specific/non-coding fields, retain the named coding flags as
  conveniences, forbid duplicate keys across the two forms, and show a `new_worker` novel-field
  example. The skill must still require the complete settled prefix for the target Stage and defer
  validity to Panels' API response.
- Update the two stale comments in `web/src/lib/lifecycle.ts` from `ticket_types`/free views to the
  served `worker_types` manifest. Do not change TypeScript shapes or behavior.
- Run the normal frontend build after the source comment update. Commit `web/dist/index.html` and only
  the generated hashed JS/CSS replacement if the build actually changes them; remove the superseded
  hash. Font assets are out of scope. Browser behavior remains covered by `./verify`, not inspection.

Historical redesign plans and completed ticket artifacts under `orchestration/` are history and are not
rewritten. The new AD02 plan/ticket remain allowed to name deleted structures as deletion requirements.

## Serial implementation order

1. Add RED contract/static/signature/schema tests, including the old-path deletions, narrow registry
   surface, required-definition signatures, and fields-default removal. Do not add aliases to keep old
   tests temporarily green.
2. Add the new Worker-type contracts, behavior methods, narrow registry, exact manifest serializer,
   two definition modules, production/test configuration, and facade. Lock this contract before moving
   consumers.
3. Delete the old package and bridge; remove Ticket enum/maps and machine table/wrappers. Convert pure
   semantic functions to required definitions and string ids.
4. Move persistence/write boundaries to explicit `registry.require` plus
   `definition.validate_ticket_position`, and thread the returned definition through
   engine/admission/external-work paths. Split direct field decoding from declared-field validation;
   do not insert a renamed guard between the boundary and definition.
5. Convert API, server, runtime, read models, sprints, seed, actions, and CLI. Check every call in the
   call-site inventory; no optional definition may remain.
6. Implement the v19 schema rebuild and update only current-schema fixtures that depended on the old
   default. Exercise fresh/reopen/idempotence/rollback/omission tests before continuing.
7. Rewrite the registry/probe/genericity tests to assert the single authority; update strict mypy and all
   affected behavior tests without weakening exact outputs.
8. Update live docs/skill/frontend comments, rebuild frontend artifacts if changed, and run targeted
   format/type/unit/e2e commands chosen by the implementer. Do not run the canonical `./verify` inside
   the implementation ticket; the orchestrator runs it once after Codex implementation review fixes.

## Bounded implementation allowlist

Only the following paths may change in AD02. A newly discovered required path stops implementation and
returns to the orchestrator for an allowlist decision.

**Explicit deletes**

- `src/planner/ticket_types/__init__.py`
- `src/planner/ticket_types/contracts.py`
- `src/planner/ticket_types/coding.py`
- `src/planner/ticket_types/new_worker.py`
- `src/planner/ticket_types/registry.py`
- `src/planner/ticket_types/logic/__init__.py`
- `src/planner/ticket_types/logic/validation.py`
- `src/planner/ticket_types/logic/views.py`
- `src/planner/ticket_types/logic/manifest.py`
- `src/planner/tickets/logic/coding_bridge.py`
- `src/planner/tickets/logic/ticket_type_guard.py`
- `tests/unit/test_ticket_type_registry.py`

**Explicit adds**

- `src/planner/worker_types/__init__.py`
- `src/planner/worker_types/contracts.py`
- `src/planner/worker_types/registry.py`
- `src/planner/worker_types/configuration.py`
- `src/planner/worker_types/coding.py`
- `src/planner/worker_types/new_worker.py`
- `tests/unit/test_worker_type_registry.py`
- this `plan.md`

**Production modifications**

- `src/planner/core/db.py`
- `src/planner/core/server.py`
- `src/planner/cli/main.py`
- `src/planner/runtime/readiness.py`
- `src/planner/runtime/ticket_readiness_loop.py`
- `src/planner/runtime/employee_step_runner.py`
- `src/planner/seed/contracts.py`
- `src/planner/seed/importer.py`
- `src/planner/sprints/data.py`
- `src/planner/sprints/views.py`
- `src/planner/tickets/actions.py`
- `src/planner/tickets/api.py`
- `src/planner/tickets/contracts.py`
- `src/planner/tickets/data.py`
- `src/planner/tickets/views.py`
- `src/planner/tickets/logic/admission.py`
- `src/planner/tickets/logic/external_work.py`
- `src/planner/tickets/logic/fields_codec.py`
- `src/planner/tickets/logic/machine.py`
- `src/planner/tickets/logic/resolution.py`

`src/planner/core/links.py` and `src/planner/sprints/logic/status.py` are inspected but not modified:
their direct universal stored-Stage comparisons are correct.

**Test modifications**

- `tests/support/probe.py`
- `tests/typing/tt01_overload_cases.py`
- `tests/typing/tt02b_field_seam_cases.py`
- `tests/e2e/test_chief_external_work_cli.py`
- `tests/e2e/test_cli_verbs.py`
- `tests/unit/test_authctx_routes.py`
- `tests/unit/test_board_view.py`
- `tests/unit/test_chat_seed.py`
- `tests/unit/test_chief_external_work.py`
- `tests/unit/test_copy_text_type_driven.py`
- `tests/unit/test_days.py`
- `tests/unit/test_db.py`
- `tests/unit/test_employee_step_runner.py`
- `tests/unit/test_engine_parameterization.py`
- `tests/unit/test_external_work_generic.py`
- `tests/unit/test_generic_field_storage.py`
- `tests/unit/test_go_no_go_gate.py`
- `tests/unit/test_links.py`
- `tests/unit/test_new_worker_type.py`
- `tests/unit/test_probe_type.py`
- `tests/unit/test_queues_approval_type_driven.py`
- `tests/unit/test_readiness_actions.py`
- `tests/unit/test_return_for_revision.py`
- `tests/unit/test_sprint_item_status_buckets.py`
- `tests/unit/test_sprint_views_type_driven.py`
- `tests/unit/test_sprints.py`
- `tests/unit/test_ticket_delete.py`
- `tests/unit/test_ticket_edit_api.py`
- `tests/unit/test_ticket_lifecycle.py`
- `tests/unit/test_ticket_readiness_loop.py`
- `tests/unit/test_tickets_engine.py`
- `tests/unit/test_type_driven_ingress.py`
- `tests/unit/test_value_edit_api.py`
- `tests/unit/test_value_edit_logic.py`
- `tests/unit/test_worker_context.py`
- `tests/unit/test_worker_my_ticket.py`
- `tests/unit/test_worker_type_manifest_endpoint.py`
- `tests/unit/test_worker_type_persistence.py`
- `tests/unit/test_worker_type_stage_contracts.py`

Tests not listed above may be run but not edited. In particular, a broad replacement across all tests
is not permitted.

**Live explanatory/generated modifications**

- `docs/worker-types.md`
- `skills/panels-chief-of-staff/SKILL.md`
- `skills/panels-worker-new-worker/SKILL.md`
- `web/src/lib/lifecycle.ts`
- `web/dist/index.html` and `web/dist/assets/index-*.js` / `index-*.css` only if emitted differently by
  the normal frontend build; no other `web/dist` asset may change.

PROGRESS.md and decisions.md are orchestrator-owned and excluded from the implementation agent's
allowlist. The orchestrator updates them after plan review, implementation review, integration, and the
single canonical verification run.

## Completion claim

After implementation review findings are addressed, the orchestrator runs `./verify` exactly once and
records its full output. AD02 is complete only when that run is clean and the static guards prove there
is one Worker-type package, one executable definition per shipped Worker type, no coding default in
Python or SQLite, no compatibility surface, and no semantic operation callable without an explicitly
resolved Worker-type definition.
