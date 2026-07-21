# Ticket Kickoff employee configuration contract

Status: frozen for MR-01 planning, subject only to a concrete plan-review correction.

## Product boundary

Worker, Model, and Reasoning are one Ticket's employee setup. They are not Worker-type
identity, global preferences, header controls, or conversation controls.

Each Worker type supplies three starting values:

- default Employee backend;
- default Employee model, which may be null for the backend's native default;
- default Employee reasoning effort, which may be null for the backend's native
  default or because the backend does not support it.

Ticket creation copies those values once. From that point onward, only the Ticket's
stored values matter. There is no reset-to-Worker-type-default action, no per-backend
memory, and no restoration when a user switches back to a previously selected Worker.

## Kickoff experience

The controls appear inside the Kickoff section, adjacent to the Kickoff approval flow:

1. Worker
2. Model
3. Reasoning, only when the selected backend/model supports it

They use existing restrained form primitives. There is no new header treatment, card
system, preset vocabulary, or advanced mode.

The configuration is editable only while the existing pristine-Kickoff predicate is
true: Kickoff has not started and no Employee session/binding exists. Once frozen, the
setup controls disappear. The stored model and reasoning values are launch inputs, not
a claim about the Employee's current live configuration, so the post-Kickoff UI does
not present them as current settings.

While a catalog is loading, Worker remains legible and the dependent field shows a
quiet loading state. A catalog failure is local to the setup, retains the saved values,
offers Retry, and fabricates no options. Stale responses from an earlier Worker/model
selection cannot win.

## Canonical Ticket values

Before first binding, the Ticket owns one atomic launch configuration:

- `employee_backend`: required registered backend key;
- `employee_launch_model`: nullable backend-native model identifier requested for the
  first session;
- `employee_launch_reasoning_effort`: nullable backend-native effort identifier
  requested for the first session.

Null means "use the backend/session default," not "unknown." Existing Tickets migrate
both new values to null so existing sessions are not silently retargeted. Model and
reasoning remain stored after binding as the historical Kickoff request, but they are
not a live mirror and may legitimately differ from later agent-side changes.

One canonical writer accepts the complete trio. Separate UI controls call that writer
with a complete candidate snapshot. The writer validates the candidate against the
current backend catalog, reuses the existing pristine-Kickoff and no-binding rule, and
commits one event-producing transition.

When Worker changes, backend-specific Model and Reasoning values that cannot apply are
replaced with that backend's native defaults. This is dependency normalization, not a
return to the Worker type's defaults. The user can then choose any advertised values.
When Model changes, a still-supported explicit Reasoning value is retained; otherwise
Reasoning becomes the backend/model default.

The first-binding transaction compares the complete Ticket configuration with the
configuration used to prepare the session. Whichever commits first wins: a subsequent
Kickoff edit is rejected after binding, while a session prepared from stale values is
not published. Model and reasoning are not duplicated into the binding table; the
Ticket remains their canonical owner.

## Configuration catalogs

Catalog discovery is a product REST resource, not an ACP browser action. Merely opening
Kickoff or its selectors must not create a Ticket's Employee session or binding.

The catalog is keyed by backend and optional candidate model. It reports:

- available model identifiers, labels, and optional descriptions;
- the backend's current/native model;
- whether Reasoning exists for the effective model;
- available reasoning identifiers, labels, and optional descriptions;
- the backend/model's current/native reasoning value when one is exposed.

Catalogs come from the selected backend, not a frontend inventory. Results may be
cached for the server lifetime. Codex and Claude discovery sessions are temporary and
must be closed. Hermes discovery is a narrow, read-only adapter over the exact pinned
Hermes installation and current configured provider; it must not modify Hermes files.

## ACP application

The durable employee resolver carries the Ticket's requested Kickoff model and
reasoning along with its backend only while there is no binding. The first actual ACP
session is configured before its initial binding:

1. create the first session;
2. validate and apply an explicit model, if present;
3. consume the refreshed configuration state;
4. validate and apply explicit reasoning, if present;
5. prove the Ticket launch configuration is still the same during first binding;
6. only then allow the first prompt.

Once a binding exists, loading or replacing the child trusts the ACP session's own
configuration state. Panels does not reapply the stored Kickoff values, because the
agent may have changed them after launch. A later explicit New Conversation likewise
does not turn the historical Kickoff request into a live preference; any future setup
for later conversations is a separate product decision.

If two first-session candidates race and one binding wins, the loser adopts the
winner only after re-resolving it as a bound Employee with no launch inputs. It never
publishes the winner with the loser's stale requested model/reasoning and never
reconfigures the winning session.

Codex and Claude use stable `session/set_config_option`; selection is based on semantic
categories rather than assuming the two adapters use the same option ID. Hermes uses
its existing legacy `session/set_model` extension and rejects non-null reasoning.

An unavailable model or reasoning value is a visible configuration failure. The child
is retired, no new binding is persisted, no prompt is sent, and Panels never silently
substitutes another explicit value. Null/default selections continue with the backend's
reported native state.

## Defaults and existing Worker types

The existing Worker-profile `model` and `reasoning_effort` fields are unused and may be
renamed to make their default semantics explicit. Production Worker types retain their
current Hermes backend choice and backend-native model unless the owner supplies new
concrete defaults; this feature does not silently change which agent handles existing
work.

Chief of Staff has no Ticket Kickoff or Worker type and is outside this contract.

## Required proof

- Fresh schema and v26-to-v27 preservation with nullable new values.
- Worker-type defaults copy once; later edits do not consult or restore them.
- Exact-body atomic writer validation and every freeze boundary.
- Writer-versus-first-binding race in both commit orders.
- Catalog discovery creates no Ticket session or binding.
- Worker/model dependency normalization and Hermes Reasoning omission.
- Model-before-reasoning ordering using refreshed options.
- Browser-first and first-automatic-work paths apply the same launch configuration
  before first binding; bound-session load/replacement paths do not reapply it.
- Invalid/disappeared selections produce no binding or prompt.
- Browser loading, retry, stale-response, reload, approval/freeze, and read-only states.
- Real first-prompt dogfood for Hermes, Codex, and Claude.
