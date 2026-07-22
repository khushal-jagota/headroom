# Worker types

Every Ticket has a required **Worker type**. The Worker type says what kind of work the
Ticket contains, which Stages it moves through, which fields those Stages settle, and
which specialist skill guides its worker.

There is no default Worker type. Creating a Ticket requires the caller to choose one.
Panels stores that choice on the Ticket for its whole life. A read returns the Ticket's
stored Stage and Worker type as they are; it does not substitute coding behavior or ask a
registry to reinterpret them.

Four Worker types ship today:

- **`coding`** handles product and repository work.
- **`new_worker`** designs and lands a new kind of worker.
- **`exploration`** turns an under-defined premise into a grounded answer, then applies
  only the follow-up the user approves.
- **`initiative_planning`** works out the shared top-level how for a confirmed direction,
  then creates the bounded Tickets that carry it.

Tests also register **`probe`**. It has deliberately unfamiliar Stage and field names so
the test suite catches code that still assumes every Ticket is coding-shaped. It is not a
shipped Worker type.

## The definition owns the workflow

Each Worker type is one immutable `WorkerTypeDefinition`. The definition contains:

- its id and human label;
- the ordered Stages, including the field and default ownership mode of each
  non-terminal Stage (`worker`, `user`, or `paired`);
- the separate `dropped` terminal Stage;
- the ordered fields carried by its Tickets;
- the worker profile, including the specialist skill and the default Employee backend,
  model, and reasoning effort copied onto a new Ticket;
- whether work completed outside Panels may be reconciled as a settled field prefix.

The definition also answers the workflow questions that used to be spread across Ticket
constants and free helper views. Its methods find a Stage or field, return Stage order and
advance targets, identify gates and terminals, calculate the default ceiling and first
working Stage, validate a Ticket position, and provide the field order used for
external-work reconciliation.

The shipped `coding` definition defaults every non-terminal Stage to worker ownership.
`new_worker` starts with worker-owned Kickoff, then uses paired ownership for Understanding
before returning to worker-owned Stages, Thinking, Drafting, and Closeout. `exploration`
uses paired ownership for Understanding and Answer, where the user and worker establish
the frame and reach the decision together; its other non-terminal Stages default to worker
ownership. `initiative_planning` uses paired ownership for Question Answers, where
consequential cross-Ticket choices are settled with the user; its other non-terminal
Stages default to worker ownership. Every Worker type chooses deliberately for each
Stage; it does not inherit that choice from registry order or another definition.

This makes the definition the one authority for both the data and behavior of that
workflow. Ticket contracts still own universal Ticket facts such as status, per-Ticket
ownership overrides, and scope, but they do not define a coding lifecycle.

_Code paths:_ `src/planner/worker_types/contracts.py` contains the immutable declaration
types and behavior. `src/planner/worker_types/coding.py`,
`src/planner/worker_types/new_worker.py`,
`src/planner/worker_types/exploration.py`, and
`src/planner/worker_types/initiative_planning.py` contain the four shipped definitions.

## Validation and the narrow registry

`WorkerTypeRegistry` validates every definition when the registry is built. It checks the
shared structural rules: kickoff comes first, `done` is the one linear terminal,
`dropped` sits outside the line, every non-terminal Stage gates one declared field, every
field is gated once, every non-terminal Stage declares a valid default ownership mode,
terminal Stages declare none, worker skills and toolsets are known, and the default
Employee backend is registered in the same application composition.

A malformed definition therefore stops application composition instead of failing only
when a Ticket happens to reach the bad part of its workflow.

The registry has only three jobs:

- list the registered Worker type ids;
- require one definition by id; and
- serialize one definition for the served manifest.

It does not forward lifecycle behavior. Code that has a definition calls that definition
directly.

_Code path:_ `src/planner/worker_types/registry.py`.

## Explicit resolution at boundaries

Code that changes workflow state resolves the Ticket's own Worker type at the boundary.
It imports the configured registry directly, calls `require(ticket.worker_type)`, and
passes the resulting `WorkerTypeDefinition` explicitly into the framework-free rule that
needs it. The rule never reaches into global application configuration and never guesses
`coding`.

This pattern makes the dependency visible:

```python
definition = configured_worker_type_registry().require(ticket.worker_type)
result = resolve_value(..., worker_type_definition=definition)
```

A boundary that already knows the intended Worker type, such as Ticket creation, resolves
that definition once and derives the initial Stage, field map, and ceiling from it. A
boundary that processes several Tickets resolves each Ticket's stored Worker type. Ticket
read paths also resolve the definition because current default and effective ownership are
derived from the stored Worker type, Stage, and override map rather than persisted twice.

There is no compatibility bridge or special coding seam. The application boundary,
definition, and framework-free rule are the whole path.

_Code paths:_ `src/planner/worker_types/configuration.py` supplies the configured
registry. Ticket, sprint, seed, runtime, and API boundaries import it where workflow
behavior is needed. Rules under `src/planner/tickets/logic/` receive
`worker_type_definition` explicitly.

## Production and test composition

Application composition lives in `src/planner/worker_types/configuration.py`. It owns the
catalogs of known specialist skills and toolset profiles, the ordered tuple of shipped
definitions, and the production registry built from them. The shipped tuple currently
contains `coding`, `new_worker`, `exploration`, and `initiative_planning`; its order is
also the manifest order.

The same composition owns the ordered production Employee-backend catalog. Its exact
keys are `hermes`, `codex`, and `claude`; Gemini is not registered. Every shipped Worker
type currently starts with `hermes`, its native model, and no Reasoning choice. Those
three starting values belong to the Worker-type definition rather than to a global
fallback.

Tests build an explicit Employee-backend catalog and Worker-type registry as one exact
configuration value. This can include the additional `probe` Worker type and fake backend
definitions without changing production configuration. The registry retains the same
catalog instance it was validated against, so the two authorities cannot drift.

No registry position means “default.” Order is composition and presentation order only.

## The one-time seed boundary

The retained legacy importer is run directly, not through the server or `panels`:

```text
python -m planner.seed --source <dir> --worker-type <id>
```

The command requires an explicit Worker type. The importer resolves that exact registry
definition once, then uses its Stage order and field set to validate every imported
Ticket. It stores the same Worker type on every Ticket in that run. It never defaults to
`coding`, uses registry order, or infers a type from the source document. If a legacy
Stage is incompatible with the selected definition, the whole import rolls back.

There is no `/api/seed` route or `panels seed` command.

## The served manifest and frontend

`GET /api/worker-types` lists the configured Employee backends in stable catalog order,
then calls the Worker-type registry's `manifest` method for each definition. Every
Worker-type entry contains the label, Stages, gates, advance map, fields, ceiling range,
default ceiling, specialist skill id, and default Employee backend, model, and reasoning
effort. Every Stage also carries its default ownership mode; terminal Stages carry none.
The Ticket response supplies the current Stage's default and effective ownership, so
clients do not reconstruct the rule.

The frontend derives one lifecycle per Worker type from this served manifest. It renders a
Ticket against the entry matching the Ticket's stored `worker_type`. Coding, `new_worker`,
`exploration`, and `initiative_planning` Tickets therefore show their own Stage spines
without frontend type tables.

During pristine Kickoff, the Kickoff section shows the Ticket's launch setup beside its
approval flow: Worker, Model, and Reasoning when that Worker and model support it. The
controls start with the values already copied onto the Ticket. Their model and reasoning
choices come from the selected backend, not from a frontend list. Once a session or
binding exists, or the Ticket moves beyond Kickoff, the controls disappear. They do not
move into the header or become a display of the worker's current settings.

_Code paths:_ `src/planner/core/server.py` serves the registry manifest;
`web/src/lib/lifecycle.ts` derives the frontend lifecycle.

## Managed settings and the Workers screen

The registry remains the immutable workflow definition. A managed source beside the database owns
only the ownership default for each existing non-terminal Stage and the existing specialist skill's
description and Markdown body. The Workers screen exposes those settings without allowing Worker
identity, Stage structure, fields, or skill identity to change.

A Ticket captures the managed ownership default when it enters a Stage. Later global changes affect
only future entries; the Ticket's explicit Stage override still wins. Settings writes use validated
candidates, atomic replacement, one writer lock per Worker, and a last-known-good revision. A failed
event write restores both the managed source and the live Hermes materialization.

The skill name is read-only. Description and Markdown body are ordinary direct edits that save, fail,
and retry independently. Successful skill edits refresh the configured planner Hermes home without
changing existing Employee session ids. Codex and Claude Code continue to use the repository skill
source exposed through their native project links.

`GET /api/workers` serves the compact index. `GET /api/workers/{id}` composes registry structure with
managed settings. `worker_settings_changed` invalidates only `workers` and the matching
`worker:<id>` browser resource.

_Code paths:_ `src/planner/worker_settings/`, `src/planner/tickets/data.py`,
`src/planner/conversation/hermes_backend_configuration.py`, and
`web/src/routes/WorkersRoute.svelte`.

## The Ticket owns its Employee launch setup

Each Worker type supplies the Worker, Model, and Reasoning values used to start a new
Ticket. Creation copies the trio once. From then on, the Ticket owns it; changing the
Worker type's defaults later does not change existing Tickets, and switching a Ticket's
Worker does not restore an earlier set of choices.

The complete trio may change only while the Ticket is still at pristine Kickoff, has no
Employee session, and has no durable conversation binding. One save replaces the whole
setup. Changing Worker resets Model and Reasoning to that backend's native defaults.
Changing Model retains an explicit Reasoning choice only when the new model still supports it.
The first Employee demand or any move past Kickoff freezes the setup.

Hermes offers Model but not Reasoning. Codex and Claude Code offer Model, and their
Reasoning choices depend on the selected model. Leaving Model or Reasoning at its native
value means the backend chooses its own default. A missing or unavailable explicit value
fails visibly instead of silently selecting something else.

The stored model and reasoning are requests for the first session, not a live settings
mirror. After the Ticket binds a session they remain only as the historical Kickoff
request, while human conversation and Automatic Employee work use the same stored
backend and durable ACP session.

_Code paths:_ `src/planner/conversation/backend_catalog.py` owns the ordered backend
catalog; `src/planner/worker_types/configuration.py` composes it with the Worker-type
registry; `src/planner/tickets/data.py` stores and freezes the Ticket setup; and
`web/src/components/EmployeeConfigurationSetup.svelte` renders the Kickoff controls.

## How a worker finds its specialist

The launched base role is `panels-worker`. It knows how to work one Ticket step at a time,
but it does not contain the substance of every Worker type.

The worker runs `panels worker my-ticket`. That response includes the Ticket's stored
Worker type and the specialist skill named by its `WorkerTypeDefinition`. The worker loads
that skill with `skill_view` and follows its Stage-specific guidance.

Panels opens or resumes the Ticket's durable ACP conversation. The conversation binding
owns the Employee-to-session relationship and records the Ticket's selected backend. The
Ticket mirrors its session id, and one ACP session cannot belong to two Employees. Human
and Automatic Employee prompts use that same backend and binding. Restart resumes it
rather than reconstructing identity from terminal state.

- `panels-worker-coding` guides coding Tickets.
- `panels-worker-new-worker` guides `new_worker` Tickets.
- `panels-worker-exploration` guides `exploration` Tickets.
- `panels-worker-initiative-planning` guides `initiative_planning` Tickets.

For `new_worker`, the visible lifecycle after universal Kickoff is
Understanding, Stages, Thinking, Drafting, Closeout, Done. Understanding is paired:
Panels dispatches one automatic opening turn into the durable Employee session, human
conversation continues that same session, and an Understanding proposal waits for approval
before the Ticket advances to Stages.

The repository exposes the same skill source at Codex's and Claude Code's native project
skill locations, while startup links the listed skills into the planner Hermes home. The
first real prompt in a new Ticket conversation tells the selected backend to use the
installed `panels-worker` role; the role then finds this Ticket's specialist. A new
specialist must therefore be known to Worker type configuration and available through
the backend skill links.

_Code paths:_ `skills/panels-worker/SKILL.md`, the specialist skills under `skills/`,
`src/planner/tickets/api.py`, `src/planner/cli/main.py`, and
`src/planner/conversation/hermes_backend_configuration.py`.

## Adding a Worker type

One new Worker type needs one definition and one production registration path:

1. Write the specialist `SKILL.md` under `skills/<name>/`, with guidance for each working
   Stage.
2. Add one definition module under `src/planner/worker_types/`. Construct an immutable
   `WorkerTypeDefinition` with its ordered Stages, fields, worker profile, starting
   Employee backend/model/reasoning values, and reconciliation support. Give every
   non-terminal Stage a deliberate default ownership mode; `done` and `dropped` have
   none. Novel Stage and field ids are plain strings.
3. In `src/planner/worker_types/configuration.py`, add the specialist skill to the known
   skills catalog and add the definition to `_PRODUCTION_WORKER_TYPE_DEFINITIONS`. Do not
   register it anywhere else.
4. Add the skill directory name to `PLANNER_SKILL_NAMES` in
   `src/planner/conversation/hermes_backend_configuration.py`, so startup provisions it
   into the worker's Hermes home.
5. Announce the Worker type at both agent front doors: add the specialist to
   `panels-worker` and describe the new type in `panels-chief-of-staff`.
6. Restart Panels. Composition validates the complete registry and startup provisions the
   skills before the Worker type becomes live.

The shared kickoff, completion, and drop ids are structural rules, not imported lifecycle
constants. The new definition still declares them directly: `needs_kickoff` gating
`kickoff`, terminal `done`, and the separate terminal `dropped`.

## Work completed outside Panels

External-work reconciliation is definition-driven too. The Chief supplies the target
Stage and the complete settled field prefix for that Stage. Named coding CLI options are
conveniences; repeatable `--field-file FIELD=PATH` carries fields belonging to any Worker
type. Panels resolves the Ticket's definition and validates the Stage, supplied fields,
prefix, and reconciliation support before changing state.

## Handoffs

- **Tickets and gates** (`tickets-and-gates.md`) explains scope, proposals, resolution,
  Stage ownership and per-Ticket overrides, scope, and approval.
- **The employee runtime** (`employee-runtime.md`) explains how a worker owns one Ticket
  step and reaches its specialist.
- **The frontend** (`frontend.md`) explains the screens driven by the served manifest.
- **The command-line tool** (`cli.md`) explains the worker and Chief commands.

---

_Last verified: 2026-07-21._
