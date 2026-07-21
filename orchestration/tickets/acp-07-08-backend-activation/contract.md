# ACP-07/08 shared backend activation

## Outcome

Panels exposes one immutable production Employee-backend catalog in exact order `hermes`, `codex`,
`claude`. The existing Ticket selector, durable binding, human conversation, permission broker, and
Automatic Employee gateway consume that same catalog and do not branch on provider names. Claude's
one initialize-only startup preflight completes before routes or background work are admitted; Codex
and ordinary Claude children remain lazy.

This is the serial shared-file integration for the already-frozen ACP-07 Codex and ACP-08 Claude
provider definitions. It adds no provider-specific UI, gateway, binding, transcript, compatibility
path, model picker, Gemini work, or Hermes behavior change.

## Shared contract

- `EmployeeBackendBuildContext` carries the resolved absolute Panels `repository_root` as well as the
  existing data directory and optional Hermes home.
- `MaterializedEmployeeBackendRegistration` has one optional async `startup_preflight`. Static/test
  registrations and Hermes use `None`.
- `ConversationComposition` retains materialized preflights in catalog order and exposes one async
  method which awaits each exactly once, serially. It does not spawn them through
  `AcpEmployeeRegistry`.
- Production lifespan order is: audit storage -> build definitions/composition -> await all startup
  preflights -> publish `app.state.conversation` -> start runtime loops -> expose routes.
- A preflight failure closes admission and shuts the composition down under the existing single
  shutdown deadline, leaves `app.state.conversation` unset, starts no Employee runtime, and re-raises.
- Production registration order is exactly Hermes, Codex, Claude. Chief remains Hermes and every
  current Worker-type default remains Hermes. A selected unavailable backend fails visibly; it never
  falls back to another provider.
- Aggregate availability remains a live `any(materialized.is_executable())` read. Claude reports
  executable only after its successful preflight. Codex uses its exact locked artifact/version probe.
- Claude's preflight directly owns one transient initialize-only child for a synthetic Ticket rooted
  at `repository_root`, validates the frozen identity/capabilities within one 30-second deadline,
  rejects unexpected updates/permissions, and always closes or force-closes the child inside that
  budget. It never creates, loads, binds, or prompts a session.
- The settled generic in-place compaction seam remains the only non-Hermes compaction integration.
  Codex and Claude never enter Hermes fork/private-load/CAS/rebind.

## Allowed shared files

- `src/planner/conversation/backend_catalog.py`
- `src/planner/conversation/composition.py`
- `src/planner/core/server.py`
- `src/planner/conversation/__init__.py` only for package exports required by existing convention
- `tests/unit/test_acp_conversation_composition.py`
- `tests/unit/test_server_shutdown_process.py`
- `tests/unit/test_worker_type_manifest_endpoint.py`
- `tests/unit/test_worker_type_registry.py`
- `tests/e2e/test_acp_conversation.py` only for the common production-catalog assertion
- this ticket's reports

Provider modules/tests, Ticket/Worker schema, selector UI, binding repository, registry, permission
broker, Automatic Employee gateway, package manifest, runtime ports, docs, and Hermes checkout are
outside this shared slice.

## Acceptance

Focused tests prove exact production order/defaults, same catalog identity, preflights once in order
without registry spawn, exact server startup ordering, preflight-failure cleanup before runtime/routes,
and lazy ordinary provider children. Ruff, strict Mypy, the affected unit/e2e tests, and diff check
pass. One focused independent integrated review follows both provider slices. Do not run `./verify`;
ACP-10 owns the single frozen-tree gate.
