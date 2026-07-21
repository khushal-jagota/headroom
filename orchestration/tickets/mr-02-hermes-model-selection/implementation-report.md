# MR-02 — Hermes model selection implementation

Status: **IMPLEMENTED — focused gates green; real dogfood intentionally deferred to final integration**

## Intent

Hermes now supplies its current configured provider's model catalog to Ticket Kickoff and
applies an explicit saved model to only the first unbound ACP session. It still advertises
no Reasoning selection. The stored Ticket value remains launch history: bound loads,
recovery, compaction, and later New Conversation paths do not reapply it.

## What changed

- Added a standalone Python-3.11-compatible probe which runs under the exact resolved
  Hermes interpreter, source root, and Panels-owned `HERMES_HOME`. It uses Hermes's
  read-only config loader and curated/live provider helper, creates no ACP session, and
  emits one strict JSON result. Panels discards probe stderr and maps timeout, process,
  malformed, missing-provider, and empty-result failures to the existing calm catalog
  error.
- Added the Hermes employee configuration adapter. It preserves catalog order and
  descriptions, encodes values as `normalized-provider:model`, inserts an omitted active
  model once at the front, reports no Reasoning capability, and relies on MR-01's
  server-lifetime catalog cache.
- Added a separate backend-neutral legacy model capability. The SDK bridge sends exactly
  `session/set_model` with `{"sessionId": ..., "modelId": ...}`; the role-skill wrapper
  forwards it unchanged. Hermes rejects non-null Reasoning before any RPC and sends no
  model request for a null selection.
- Wired the adapter into the existing Hermes materialization using the same interpreter,
  source root, home, and backend definition as the durable child. No registry, Ticket,
  frontend, Codex, Claude, dependency, or Hermes-checkout code changed for MR-02.
- Extended the deterministic ACP peer only with exact legacy-method recognition and an
  isolated audit channel, allowing the real SDK child test to prove
  `new_session -> set_model -> prompt` and the exact camel-case payload.

## Focused proof

```text
.venv/bin/pytest -q \
  tests/unit/test_acp_employee_child.py \
  tests/unit/test_role_skill_kickoff.py \
  tests/unit/test_hermes_acp_backend.py \
  tests/unit/test_hermes_employee_configuration.py

.....................................................................    [100%]
69 passed
```

The provider tests cover exact catalog mapping, active-model insertion and deduplication,
same-installation registration, cache reuse, no Ticket session/binding creation, no
Reasoning, malformed/non-zero/timeout/missing-provider/empty failures, null-model behavior,
reasoning-before-RPC rejection, request failure before binding, and exactly one model
application across first binding, recovery replacement, compaction, New Conversation, and
bound reload.

```text
.venv/bin/ruff check <all MR-02 changed Python files>
All checks passed!

.venv/bin/mypy --strict src/planner/conversation
Success: no issues found in 31 source files

node web/tests/employee-configuration-setup.test.mjs
employee-configuration-setup.test.mjs: all assertions passed
```

The unchanged browser assertion confirms the frozen Kickoff controls disappear and no
post-Kickoff current Model/Reasoning setting is rendered. Per dispatch, `./verify` and real
Hermes dogfood were not run; the final settled multi-provider integration owns both.
