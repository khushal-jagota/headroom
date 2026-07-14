# AD02 — Deep Worker workflow interpretation

## Objective

Make the Worker-type domain the single authority for interpreting a Ticket workflow.

- Rename the internal `ticket_types` domain to `worker_types`; no rejected internal name or compatibility
  import remains.
- Replace the shallow `WorkflowDefinition` plus free-view/forwarder stack with one immutable,
  behavior-bearing `WorkerTypeDefinition` resolved from the Ticket's required `worker_type`.
- Remove the parallel coding lifecycle declarations and derive every order, gate, successor, terminal,
  scope, transition-effect, field, manifest, and Worker-profile answer from that definition.
- Delete `tickets/logic/coding_bridge.py` and all implicit coding-definition defaults.

This is an internal architecture replacement. It must not change a Stage id, field id, Worker profile,
manifest payload, proposal/scope/transition rule, external-work rule, UI behavior, or persisted value.

## Binding decisions

- `CONTEXT.md`: Worker type and directly stored Stage.
- `D-worker-type-language`: the sibling domain is `worker_types`, never `ticket_types`.
- `D-worker-type-immutable`: the selected Worker type does not change after creation.
- `D-stored-stage-is-authoritative`: resolving a definition is required only when interpreting a Stage.
- `D-architecture-review-ranking`: interpretation is concentrated behind the sibling domain boundary.
- `PRINCIPLES.md`: one module manifest/registry, zero module-specific conditionals in core, pure rules,
  descriptive names, and one canonical source for each rule.

## Contract boundary

The delegated plan must specify and the orchestrator will lock the exact declarations in:

- new `src/planner/worker_types/contracts.py`;
- new `src/planner/worker_types/registry.py` and package facade;
- the coding and `new_worker` Worker-type definition modules;
- `src/planner/tickets/contracts.py`, which must cease declaring coding lifecycle authority; and
- the Worker-type manifest serialization contract, whose existing public JSON shape is unchanged.

The intended contract is:

1. `WorkerTypeDefinition` has the descriptive identity field `worker_type`, not `type_id`.
2. A resolved definition is immutable and owns the pure operations that interpret its stages and fields.
   Callers do not reach into tables, import a free `views` namespace, or call registry forwarding methods
   for `stage_index`, `gating_field`, `advance_target`, `default_ceiling`, and similar answers.
3. `WorkerTypeRegistry` validates definitions at construction and owns only registration, lookup, ordered
   enumeration, and manifest serialization. It does not duplicate definition behavior as per-id methods.
4. The production registry contains `coding` and `new_worker`; the test seam can install a registry that
   also contains `probe` without a coding-named bridge.
5. Every rule that interprets Stage relationships takes a resolved `WorkerTypeDefinition` explicitly.
   There is no optional definition parameter and no fallback to coding. Boundaries may resolve a Ticket's
   stored `worker_type` once and thread the definition through pure logic.
6. Reading, returning, displaying, grouping, or directly comparing a stored Stage does not resolve a
   Worker type. Field JSON may likewise be decoded as stored data without a definition; validation or
   workflow interpretation must use the explicit definition.

## Parallel authority to delete

The plan must inventory and remove, rather than alias, every duplicate coding workflow declaration:

- `CodingStage`, `FieldName`, `CODING_STAGE_ORDER`, `CODING_EMPLOYEE_STAGE_ORDER`,
  `CODING_GATING_FIELD_BY_STAGE`, and `CODING_NEXT_STAGE_BY_STAGE` in Ticket contracts;
- `machine.FIELD_GATES`;
- coding definition construction that derives from those parallel declarations;
- `planner.ticket_types`, `WorkflowDefinition`, generic `Registry`, and `type_id` internal vocabulary;
- the `Registry` methods that merely forward to free views;
- `tickets/logic/coding_bridge.py`, its re-exports, its coding-named singleton/accessors, and its test
  override; and
- every `definition: ... | None`, `definition=None`, `definition or coding_definition()`, or equivalent
  implicit-coding semantic path.

Universal stored ids such as `needs_kickoff`, `done`, and `dropped` may be compared directly only where
the rule is genuinely defined as universal by Worker-type validation. An intentionally coding-only test
or seed mapping may name a coding Stage string, but it must not become another executable order or map.

## Planning task

Produce `plan.md` only. Do not edit implementation or contract files.

The plan must:

1. Inventory every production, test, skill, doc, and generated path affected by the package and contract
   replacement, including import-graph guards and test-only `probe` registry installation.
2. Give the exact `WorkerTypeDefinition` and `WorkerTypeRegistry` public skeleton, including each pure
   interpretation operation and the production/test registry composition seam.
3. Trace every current lifecycle interpretation call site. For each, state where the Ticket's Worker type
   is resolved, how the definition is threaded, and why a direct Stage read does or does not need it.
4. Remove all coding defaults without replacing them with a differently named global fallback.
5. Preserve the exact manifest, validation-error, runtime, persistence, external-work, Review, board,
   sprint, CLI, frontend, and Worker-specialist behavior.
6. Define a bounded implementation allowlist. Package moves must be explicit deletes plus adds; no
   compatibility package, module, import alias, property, or forwarding method is allowed.
7. Replace parity tests between duplicate authorities with tests of the single definition itself. Include
   both shipped Worker types and the non-coding `probe` through engine, persistence, read-model, runtime,
   and external-work paths.
8. Add static assertions that fail if the old package/bridge/defaults/tables or a second lifecycle
   authority returns.

## Acceptance

- `src/planner/worker_types/` is the only Worker-type domain; `src/planner/ticket_types/` does not exist.
- The coding and `new_worker` definitions are the only executable declarations of their Stage order,
  field gates, successors, terminals, scope range, transition effects, and Worker profile.
- A semantic operation cannot be called without a resolved Worker-type definition; there is no implicit
  coding behavior.
- The Registry contains no lifecycle forwarding API and `coding_bridge.py` is deleted.
- All production interpretation paths resolve the Ticket row's stored `worker_type`; direct Stage reads
  remain registry-free.
- Existing Worker-type manifest JSON and every product-visible behavior remain exact.
- Adding a third production Worker type still means its definition/specialist plus one registration entry,
  without changing Ticket engine logic.
- Static architecture tests prove the deleted names, bridge, defaults, and parallel tables remain absent.
- The full canonical `./verify` passes once after implementation and review fixes.

## Out of scope

- Changing Worker type after Ticket creation.
- Changing any Stage or field id, lifecycle order, Worker profile, transition hook, scope rule, or external-
  work capability.
- Renaming or redesigning Automatic Employee-step eligibility, Review, Chat, Markdown, or frontend
  resources; those belong to AD03–AD09.
- Combining the readiness loop with the Employee step runner.
