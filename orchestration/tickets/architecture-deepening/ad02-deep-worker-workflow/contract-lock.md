# AD02 contract lock

The orchestrator generated this lock after the corrected implementation plan passed independent
review. The implementation must consume these names and boundaries exactly. It may fill in behavior,
rewire consumers, and implement the migration, but it must not add aliases, optional definitions,
registry forwarding methods, alternate field authorities, or coding defaults.

## Worker-type contracts

All five types are frozen, slot-backed dataclasses:

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

The `stages`, `dropped_stage`, `fields`, and `transition_hooks` declarations are the only executable
workflow data. Methods derive behavior from them. There are no cached mutable lookup tables, free
workflow views, or parallel lifecycle constants.

`validate_ticket_position` is definition-owned. A boundary first calls
`registry.require(worker_type)`, then calls the method. No `ticket_type_guard.py`,
`worker_type_guard.py`, or equivalent forwarding function exists.

`WorkerTypeManifestStage`, `WorkerTypeManifestField`, and `WorkerTypeManifest` preserve the current
JSON keys exactly: `worker_type`, `label`, `stages`, `dropped`, `advance`, `fields`,
`ceiling_range`, `default_ceiling`, and `worker_profile_id`.

## Registry and composition

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

Those are the registry's only public methods. Construction is the public validation door and
preserves the current deterministic R0-R20 errors. Production composition contains coding then
new_worker. The explicit test seam is:

```python
def configured_worker_type_registry() -> WorkerTypeRegistry: ...
def install_worker_type_registry_for_test(registry: WorkerTypeRegistry) -> None: ...
def restore_production_worker_type_registry_for_test() -> None: ...
```

There is no missing-value fallback and no implicit coding registry or definition.

## Ticket and engine boundary

- `CodingStage`, `FieldName`, every `CODING_*` lifecycle table, and `machine.FIELD_GATES` are deleted.
- Stage and field ids are plain strings.
- Direct Ticket/Stage/field reads, returns, filters, grouping, and universal comparisons do not
  resolve a Worker type.
- Every rule that interprets Stage order, gates, successors, ceilings, fields, or transition effects
  receives a required `worker_type_definition=` parameter or calls a method on an already resolved
  definition. There is no optional definition parameter.
- Ticket creation requires `worker_type`, builds every field slot from
  `definition.field_ids()`, and obtains its initial Stage and ceiling from the definition.
- Stored-field decoding is registry-free. Declared-field validation is a separate operation that
  requires a resolved definition.

## Seed and Chief ingress

- The historical seed label-to-Stage map remains an explicit coding-only translation.
- Seed resolves the coding definition, constructs `TicketFields.empty(definition.field_ids())`, and
  overlays only kickoff, success, and approach through the shared field codec. It does not declare
  the complete coding field set.
- Both Chief external-work commands retain their named coding convenience flags and add repeatable
  `--field-file FIELD=PATH` input for definition-specific fields.
- Duplicate keys, including named/generic collisions, fail locally before a source is read or a
  request is sent. The API remains authoritative for Worker-type field and Stage validity.

## SQLite contract

- Schema version is 19.
- Canonical `tickets.fields` is `TEXT NOT NULL` with no default.
- `_V19_TICKETS_TABLE_SQL`, `_tickets_table_is_v19`, and
  `_migrate_tickets_to_v19_contract` replace their v18 final-shape counterparts.
- One lock-held forward rebuild recognizes every previously supported historical shape plus canonical
  v18. Existing canonical `fields` text is copied explicitly and byte-for-byte; the migration never
  derives it from Worker type.
- Fresh v19 creation does not rebuild. Reopening v18 rebuilds once. Reopening v19 is idempotent.
  Copy, swap, and foreign-key failures roll back schema, rows, events, `user_version`, and FK mode.
- Every sanctioned current writer supplies fields. Omitting fields raises `sqlite3.IntegrityError`.

## Deletion rule

`src/planner/ticket_types/`, `tickets/logic/coding_bridge.py`, and
`tickets/logic/ticket_type_guard.py` are deleted. No compatibility package, module, import alias,
property, renamed guard, serializer/validator namespace, or forwarding method replaces them.

Any discovered need to change this skeleton or cross the reviewed implementation allowlist returns to
the orchestrator before work continues.
