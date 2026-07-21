# MR-04 implementation plan — Claude model and reasoning selection

## Boundary and dependency

Implement only the Claude registration over MR-01's reviewed generic Ticket employee-
configuration contracts. MR-01 must already own Ticket persistence, the atomic pristine-Kickoff
writer, REST catalog shape/cache, ACP child configuration operations, lifecycle ordering, binding
compare-and-set, and the Kickoff UI. Use those public seams; do not introduce another catalog,
writer, session owner, or provider-neutral interface.

The owner boundary is explicit:

- Worker, Model, and Reasoning remain separate controls **inside Kickoff**, beside approval.
- A Worker type's defaults seed one new Ticket once. This ticket does not consult them again.
- There is no reset-to-default action, per-backend remembered matrix, header control,
  conversation control, or post-Kickoff edit surface.
- A null Ticket model/reasoning value means “leave the first Claude session at its native value.”
  It is not a reset to the Worker type's original defaults.
- The stored pair becomes historical first-session launch provenance after binding. It is not
  live Claude state, is not shown as current after Kickoff, and is never reapplied to a bound
  session, replacement child, or later explicit New Conversation.

Do not edit `web/`, Ticket/Worker-type domain code, shared ACP lifecycle code, schema, docs, the
pinned npm package, or any Hermes/Codex file. Integration owns shared docs and real browser dogfood
after MR-02 through MR-04 land.

## Existing seam and provider truth

The production registration and its compaction-normalizing child decorator live in
`src/planner/conversation/claude_backend.py`, using the project-local
`@agentclientprotocol/claude-agent-acp` 0.60.0 entrypoint. The pinned adapter advertises:

- one select option whose semantic category is `model` (currently option id `model`);
- a model-dependent select option whose category is `thought_level` (currently option id `effort`),
  present only when the selected model supports effort;
- a complete refreshed `configOptions` response after `session/set_config_option`;
- `session/close` for retiring a temporary discovery session.

The semantic categories are the contract. Panels must not hard-code `effort`, infer effort from a
model name, or use Claude's fuzzy alias resolution. It offers and persists only exact values that
the current Claude configuration advertises. Other options—including Mode, Fast, and custom
Agent—are unrelated to Ticket Worker/Model/Reasoning and remain out of scope.

## Production change

Modify only `src/planner/conversation/claude_backend.py`.

1. In `build_claude_employee_backend_registration().materialize`, keep one
   `ClaudeAcpEmployeeChildFactory` around the one SDK factory and continue using it for durable
   Claude work and startup preflight.
2. Attach MR-01's generic stable-config-options provider to the materialized Claude registration,
   using the same locked definition, decorated child factory, and repository root. Configure it by
   semantic categories `model` and `thought_level`; do not pass or branch on the option id `effort`.
   MR-01's child protocol/configuration methods must be forwarded through the existing Claude
   decorator exactly once, just like new/load/prompt; do not bypass the decorator with a downcast
   to the SDK connection.
3. The provider must be the single server-lifetime Claude catalog/application object. Its generic
   MR-01 behavior is used unchanged:
   - catalog discovery creates and initializes a synthetic, unpublished Claude child and calls
     `session/new` without creating a Ticket session binding;
   - a backend-only request maps the initial model and optional effort options; a request for a
     candidate model validates that exact advertised model, sets the response's `model`-category
     option, consumes the returned full option list, then maps `thought_level` only if present;
   - successful results are cached by `(claude, candidate_model_or_null)` for the server lifetime;
   - the temporary session is closed through `session/close` and its child is closed in `finally`;
     a close/configuration error is visible and not cached;
   - only the first unbound session validates/applies an explicit model, consumes the refreshed
     option list, then validates/applies explicit reasoning if the effective model advertises it,
     all before first-binding publication or a prompt. Once a binding exists, loads and replacement
     children trust the ACP session's own state; a later explicit New Conversation does not
     reapply the historical pair.
4. Preserve exact adapter semantics. The advertised effort value `default`, when present, is an
   explicit backend value and must remain distinct from Ticket null (send no effort request). Do
   not translate aliases, clamp effort, pick another model, or silently restore the native value.
   Missing/duplicate semantic options, unavailable explicit values, a disappeared effort option,
   or rejected calls are MR-01's visible configuration failure: retire the candidate child,
   publish no binding, and send no prompt.
5. A model whose refreshed options contain no `thought_level` reports Reasoning unsupported, so
   the generic Kickoff view omits the control. Do not manufacture a “Reasoning” inventory from
   Claude model names or global documentation.
6. Preserve `ClaudeBackendStartupPreflight`: it stays initialize-only, one-shot, and independent
   of lazy catalog discovery. A catalog lookup never repurposes the preflight child and a failed
   lookup never changes backend readiness.

If MR-01's final public type names differ from its plan, use the landed names and semantics. Do not
add a Claude-shaped duplicate of the generic provider merely to retain a provisional name.

## Focused tests

Add only `tests/unit/test_claude_employee_configuration.py`. Test at the registration/provider
boundary with a scripted ACP child factory injected through MR-01's generic seam; do not require
network access or a real Claude login.

Cover these cases:

1. Materializing the Claude registration exposes exactly one configuration provider backed by the
   same decorated factory as durable work, while its startup preflight, locked package validation,
   confinement, and compaction normalization remain unchanged.
2. Discovery identifies model and reasoning by semantic categories even when scripted option ids
   are deliberately not `model`/`effort`. It preserves exact values, labels, descriptions, and
   initial current/native values; Mode, Fast, Agent, and unrelated options are ignored.
3. Candidate-model discovery records initialize, new session, model set, refreshed-option read,
   session close, then child close. A supporting model exposes only its refreshed effort list; a
   non-supporting model omits Reasoning entirely.
4. Repeated requests for the same backend/model use the server-lifetime cache and spawn no second
   child; distinct candidate models do not share an effort catalog.
5. The explicit effort value `default` round-trips unchanged, while Ticket null sends no config
   request. Unknown aliases, missing/duplicate model categories, malformed options, set failures,
   and close failures return a visible error, cache nothing, and retire the child process.
6. On the first unbound session, explicit values are applied in exact model-then-effort order
   before the scripted first-binding publication barrier and first prompt. Change the effort option
   id and choices in the model-set response to prove semantic re-resolution. A selected effort that
   disappears, or any explicit reasoning on a model without `thought_level`, produces no binding
   and no prompt.
7. After a binding exists, load, replacement-child, and later explicit-New-Conversation paths send
   no model or effort config request, even when the Ticket retains non-null historical launch
   values. They trust the ACP session's own current configuration.
8. The Claude child decorator forwards MR-01's configuration and temporary-session-close calls to
   its delegate without changing values, while its existing ingress normalization remains active.

MR-01's generic tests remain responsible for the Ticket writer/binding race and browser placement;
do not duplicate those cases. Run the existing `tests/unit/test_claude_acp_backend.py` as a
non-edited regression.

## Focused gate and completion evidence

After the implementation is settled, run once:

```sh
.venv/bin/ruff check src/planner/conversation/claude_backend.py tests/unit/test_claude_employee_configuration.py
.venv/bin/mypy src/planner/conversation
.venv/bin/pytest tests/unit/test_claude_acp_backend.py tests/unit/test_claude_employee_configuration.py
```

Have one fresh sub-agent review this two-file production/test diff against
`orchestration/acp-model-selection/contract.md`. Resolve only concrete findings. The orchestrator
owns the one final `./verify` and real Safari dogfood: a fresh Claude Ticket selects a non-native
advertised model/effort pair inside Kickoff, freezes after first work, persists the exact pair,
creates one binding, and completes its first real prompt with adapter evidence showing that exact
pair was applied before the turn; a model without effort shows no Reasoning control. The pair is
thereafter treated only as historical launch provenance and is not presented as the worker's
current state.

## Exact write scope and parallelism

- modify `src/planner/conversation/claude_backend.py`
- add `tests/unit/test_claude_employee_configuration.py`

MR-04 may run in parallel with MR-02 and MR-03 after MR-01 because none of those tickets may edit
these two files. Shared catalog, lifecycle, UI, docs, fixtures, and integration repairs belong to
MR-01 or final integration, not this ticket.
