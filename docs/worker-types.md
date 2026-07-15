# Worker types

Every Ticket has a required **Worker type**. The Worker type says what kind of work the
Ticket contains, which Stages it moves through, which fields those Stages settle, and
which specialist skill guides its worker.

There is no default Worker type. Creating a Ticket requires the caller to choose one.
Panels stores that choice on the Ticket for its whole life. A read returns the Ticket's
stored Stage and Worker type as they are; it does not substitute coding behavior or ask a
registry to reinterpret them.

Three Worker types ship today:

- **`coding`** handles product and repository work.
- **`new_worker`** designs and lands a new kind of worker.
- **`exploration`** turns an under-defined premise into a grounded answer, then applies
  only the follow-up the user approves.

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
- the worker profile, including the specialist skill;
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
ownership. Every Worker type chooses deliberately for each Stage; it does not inherit that
choice from registry order or another definition.

This makes the definition the one authority for both the data and behavior of that
workflow. Ticket contracts still own universal Ticket facts such as status, per-Ticket
ownership overrides, and scope, but they do not define a coding lifecycle.

_Code paths:_ `src/planner/worker_types/contracts.py` contains the immutable declaration
types and behavior. `src/planner/worker_types/coding.py`,
`src/planner/worker_types/new_worker.py`, and
`src/planner/worker_types/exploration.py` contain the three shipped definitions.

## Validation and the narrow registry

`WorkerTypeRegistry` validates every definition when the registry is built. It checks the
shared structural rules: kickoff comes first, `done` is the one linear terminal,
`dropped` sits outside the line, every non-terminal Stage gates one declared field, every
field is gated once, every non-terminal Stage declares a valid default ownership mode,
terminal Stages declare none, and worker skills and toolsets are known.

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
contains `coding`, `new_worker`, and `exploration`; its order is also the manifest order.

Tests use the explicit configuration test seam to install a registry containing the
additional `probe` definition, then restore the production registry. Production code and
test code therefore exercise the same boundaries and definition behavior without making
test-only Worker types part of the shipped catalog.

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

`GET /api/worker-types` lists the configured registry and calls its `manifest` method for
each definition. Every entry contains the Worker type label, Stages, gates, advance map,
fields, ceiling range, default ceiling, and specialist skill id. Every Stage also carries
its default ownership mode; terminal Stages carry none. The Ticket response supplies the
current Stage's default and effective ownership, so clients do not reconstruct the rule.

The frontend derives one lifecycle per Worker type from this served manifest. It renders a
Ticket against the entry matching the Ticket's stored `worker_type`. Coding, `new_worker`,
and `exploration` Tickets therefore show their own Stage spines without frontend type
tables.

_Code paths:_ `src/planner/core/server.py` serves the registry manifest;
`web/src/lib/lifecycle.ts` derives the frontend lifecycle.

## How a worker finds its specialist

The launched base role is `panels-worker`. It knows how to work one Ticket step at a time,
but it does not contain the substance of every Worker type.

The worker runs `panels worker my-ticket`. That response includes the Ticket's stored
Worker type and the specialist skill named by its `WorkerTypeDefinition`. The worker loads
that skill with `skill_view` and follows its Stage-specific guidance:

- `panels-worker-coding` guides coding Tickets.
- `panels-worker-new-worker` guides `new_worker` Tickets.
- `panels-worker-exploration` guides `exploration` Tickets.

For `new_worker`, the visible lifecycle after universal Kickoff is
Understanding, Stages, Thinking, Drafting, Closeout, Done. Understanding is paired:
Panels dispatches one automatic opening turn into the durable Employee session, ordinary
Ticket Chat continues that same session, and an Understanding proposal waits for approval
before the Ticket advances to Stages.

Startup provisions the listed skill directories into the planner Hermes home. A new
specialist must therefore be both known to Worker type configuration and included in the
planner skill list.

_Code paths:_ `skills/panels-worker/SKILL.md`, the specialist skills under `skills/`,
`src/planner/tickets/api.py`, `src/planner/cli/main.py`, and
`src/planner/minds/config.py`.

## Adding a Worker type

One new Worker type needs one definition and one production registration path:

1. Write the specialist `SKILL.md` under `skills/<name>/`, with guidance for each working
   Stage.
2. Add one definition module under `src/planner/worker_types/`. Construct an immutable
   `WorkerTypeDefinition` with its ordered Stages, fields, worker profile, and
   reconciliation support. Give every non-terminal Stage a deliberate default ownership
   mode; `done` and `dropped` have none. Novel Stage and field ids are plain strings.
3. In `src/planner/worker_types/configuration.py`, add the specialist skill to the known
   skills catalog and add the definition to `_PRODUCTION_WORKER_TYPE_DEFINITIONS`. Do not
   register it anywhere else.
4. Add the skill directory name to `PLANNER_SKILL_NAMES` in
   `src/planner/minds/config.py`, so startup provisions it into the worker's Hermes home.
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

_Last verified: 2026-07-15._
