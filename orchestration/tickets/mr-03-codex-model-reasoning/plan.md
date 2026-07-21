# MR-03 implementation plan — Codex model and reasoning selection

## Boundary and dependency

Implement only the Codex registration over MR-01's reviewed generic Ticket employee-
configuration contracts. MR-01 must already own Ticket persistence, the atomic pristine-
Kickoff writer, REST catalog shape/cache, ACP child configuration operations, lifecycle ordering,
binding compare-and-set, and the Kickoff UI. This ticket must use those public seams rather than
creating a second catalog, writer, session owner, or provider-neutral abstraction.

The owner boundary is explicit:

- Worker, Model, and Reasoning remain separate controls **inside Kickoff**, beside approval.
- A Worker type's defaults seed one new Ticket once. This ticket does not consult them again.
- There is no reset-to-default action, per-backend remembered matrix, header control,
  conversation control, or post-Kickoff edit surface.
- A null Ticket model/reasoning value means “leave the first Codex session at its native value.”
  It is not a reset to the Worker type's original defaults.
- The stored pair becomes historical first-session launch provenance after binding. It is not
  live Codex state, is not shown as current after Kickoff, and is never reapplied to a bound
  session, replacement child, or later explicit New Conversation.

Do not edit `web/`, Ticket/Worker-type domain code, shared ACP lifecycle code, schema, docs, the
pinned npm package, or any Hermes/Claude file. Integration owns shared docs and real browser
dogfood after MR-02 through MR-04 land.

## Existing seam and provider truth

The production registration is built in `src/planner/conversation/codex_backend.py` from the
project-local `@agentclientprotocol/codex-acp` 1.1.4 entrypoint. The pinned adapter advertises:

- one select option whose semantic category is `model` (currently option id `model`);
- one model-dependent select option whose semantic category is `thought_level` (currently option
  id `reasoning_effort`), omitted when the selected model has no supported efforts;
- a complete refreshed `configOptions` response after `session/set_config_option`;
- `session/close` for retiring a temporary discovery session.

The category is the contract; the current option ids above are test evidence, not values Panels
may hard-code. Model and effort values, labels, descriptions, current values, and availability
come only from the live adapter response for the current Codex account.

## Production change

Modify only `src/planner/conversation/codex_backend.py`.

1. In `build_codex_employee_backend_registration().materialize`, construct the
   `SdkAcpEmployeeChildFactory` once and continue returning that same factory for durable Codex
   work.
2. Attach MR-01's generic stable-config-options provider to the materialized Codex registration,
   using the same locked `AgentBackendDefinition`, raw SDK child factory, and repository root.
   Configure the provider by the semantic categories `model` and `thought_level`; do not pass or
   branch on `reasoning_effort`.
3. The provider must be the single server-lifetime Codex catalog/application object. Its generic
   MR-01 behavior is therefore used unchanged:
   - catalog discovery creates and initializes a synthetic, unpublished Codex child and calls
     `session/new` without touching a Ticket binding;
   - a backend-only request maps the initial model and reasoning options; a request for a candidate
     model validates that exact advertised model, sets the response's `model`-category option,
     consumes the returned full option list, and maps reasoning from that refreshed list;
   - successful results are cached by `(codex, candidate_model_or_null)` for this server lifetime;
   - the temporary ACP session is closed with `session/close` and the child process is closed in
     `finally`; a close/configuration error is a failed catalog request and is not cached;
   - only the first unbound session validates and applies an explicit model, consumes the returned
     option list, then validates and applies an explicit reasoning value, all before first-binding
     publication or a prompt. Once a binding exists, loads and replacement children trust the ACP
     session's own state; a later explicit New Conversation does not reapply the historical pair.
4. Keep values exact. Do not translate aliases, clamp effort, choose a “closest” value, or retry
   with Codex's current/default value. A missing/duplicate semantic option, unavailable explicit
   value, rejected set request, or disappeared refreshed effort is MR-01's visible configuration
   failure: retire the candidate child, persist no new binding, and send no prompt.
5. Ignore Codex config options in other semantic categories. This ticket enables only Model and
   Reasoning; it does not expose collaboration mode, agent mode, Fast, skills, or commands through
   employee configuration.

If MR-01's final public type names differ from its plan, use the landed names and semantics. Do not
add a Codex-shaped duplicate of the generic provider merely to preserve a provisional name.

## Focused tests

Add only `tests/unit/test_codex_employee_configuration.py`. Keep the tests at the registration/
provider boundary with a scripted ACP child factory injected through MR-01's generic seam; do not
require network access or a real Codex login.

Cover these cases:

1. Materializing the Codex registration exposes exactly one configuration provider backed by the
   same pinned definition/factory as durable work, while all existing confinement, version, and
   executable-probe behavior remains unchanged.
2. Discovery reads model and reasoning by semantic category even when the scripted option ids are
   deliberately non-Codex strings. It preserves exact values, labels, descriptions, and initial
   current/native values and ignores unrelated options.
3. Candidate-model discovery records this order: initialize, new session, set the discovered
   model-category option, read the returned refreshed options, close that session, close the child.
   The reasoning catalog comes only from the refreshed response.
4. Repeating the same backend/model catalog request uses the server-lifetime cache and spawns no
   second child; a different candidate model has a different cache key.
5. An absent reasoning category reports Reasoning unsupported. An unavailable model, duplicate
   model category, malformed option, set failure, or close failure returns a visible catalog error,
   caches nothing, and still retires the child process.
6. On the first unbound session, explicit values are applied in exact model-then-reasoning order
   before the scripted first-binding publication barrier and first prompt. Use different option ids
   in the refreshed response to prove semantic re-resolution. A disappeared effort rejects the
   candidate with no binding and no prompt; null values send no config request.
7. After a binding exists, load, replacement-child, and later explicit-New-Conversation paths send
   no model or reasoning config request, even when the Ticket retains non-null historical launch
   values. They trust the ACP session's own current configuration.

MR-01's generic tests remain responsible for the Ticket writer/binding race and browser placement;
do not duplicate those cases here. Run the existing `tests/unit/test_codex_backend.py` as a
non-edited regression.

## Focused gate and completion evidence

After the implementation is settled, run once:

```sh
.venv/bin/ruff check src/planner/conversation/codex_backend.py tests/unit/test_codex_employee_configuration.py
.venv/bin/mypy src/planner/conversation
.venv/bin/pytest tests/unit/test_codex_backend.py tests/unit/test_codex_employee_configuration.py
```

Have one fresh sub-agent review this two-file production/test diff against
`orchestration/acp-model-selection/contract.md`. Resolve only concrete findings. The orchestrator
owns the one final `./verify` and real Safari dogfood: a fresh Codex Ticket selects a non-native
advertised model/reasoning pair inside Kickoff, freezes after first work, persists the exact pair,
creates one binding, and completes its first real prompt with adapter evidence showing that exact
pair was applied before the turn. The pair is thereafter treated only as historical launch
provenance and is not presented as the worker's current state.

## Exact write scope and parallelism

- modify `src/planner/conversation/codex_backend.py`
- add `tests/unit/test_codex_employee_configuration.py`

MR-03 may run in parallel with MR-02 and MR-04 after MR-01 because none of those tickets may edit
these two files. Shared catalog, lifecycle, UI, docs, fixtures, and integration repairs belong to
MR-01 or final integration, not this ticket.
