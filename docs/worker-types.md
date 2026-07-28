# Worker types

Every Ticket has a required **Worker type**. The Worker type says what kind of work the
Ticket contains, which Stages it moves through, which fields those Stages settle, and
which specialist skill guides its worker.

There is no default Worker type. Creating a Ticket requires the caller to choose one.
Panels stores that choice on the Ticket for its whole life. A read returns the Ticket's
stored Stage and Worker type as they are; it does not substitute coding behavior or ask a
registry to reinterpret them.

Five Worker types ship today:

- **`coding`** handles product and repository work.
- **`new_worker`** designs and lands a new kind of worker.
- **`exploration`** is a worker for exploring something undefined and making it clearer.
- **`initiative_planning`** works out the shared top-level how for a confirmed direction,
  then creates the bounded Tickets that carry it.
- **`product_design`** designs holistic product flows and implementation-ready interactive
  artifacts before handing implementation to a coding Ticket.

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
before worker-owned Stages and Thinking, pairs again for Runtime Defaults, then returns to
worker-owned Drafting and Closeout. `exploration`
uses paired ownership for Understanding and Answer, where the user and worker establish
the frame and reach the decision together; its other non-terminal Stages default to worker
ownership. `initiative_planning` uses paired ownership for Question Answers, where
consequential cross-Ticket choices are settled with the user; its other non-terminal
Stages default to worker ownership. `product_design` uses paired ownership for Wireframe
and Design, while its Direction and handoff are worker-owned. Every Worker type chooses
deliberately for each Stage; it does not inherit that choice from registry order or
another definition.

This makes the definition the one authority for both the data and behavior of that
workflow. Ticket contracts still own universal Ticket facts such as status, per-Ticket
ownership overrides, and scope, but they do not define a coding lifecycle.

_Code paths:_ `src/planner/worker_types/contracts.py` contains the immutable declaration
types and behavior. `src/planner/worker_types/coding.py`,
`src/planner/worker_types/new_worker.py`,
`src/planner/worker_types/exploration.py`,
`src/planner/worker_types/initiative_planning.py`, and
`src/planner/worker_types/product_design.py` contain the five shipped definitions.

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
contains `coding`, `new_worker`, `exploration`, `initiative_planning`, and
`product_design`; its order is also the manifest order.

Which agent backends exist is not this composition's business. It is the conversation
system's closed set of three — `hermes`, `codex`, and `claude` — and a Worker type naming
anything else is refused when the registry validates it. There is one door that turns a
name into a backend, and every part of Panels that reads one goes through it. A Worker
type's starting backend, model, and Reasoning choice belong to its own definition rather
than to a global fallback.

Tests build an explicit Worker-type registry as one exact configuration value. This can
include the additional `probe` Worker type without changing production configuration. The
probe names a real backend of its own, deliberately not one the shipped types name, so
that "a Worker type may run on a different agent" stays under test.

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

`GET /api/worker-types` calls the Worker-type registry's `manifest` method for each
definition. It does not list the agent backends: what backends this machine has, which
models each offers, and which reasoning efforts each of those takes are one answer, and
it comes from `GET /api/conversation/backends`. Every
Worker-type entry contains the label, Stages, gates, advance map, fields, ceiling range,
default ceiling, specialist skill id, and default Employee backend, model, and reasoning
effort. Every Stage also carries its default ownership mode; terminal Stages carry none.
The Ticket response supplies the current Stage's default and effective ownership, so
clients do not reconstruct the rule.

The frontend derives one lifecycle per Worker type from this served manifest. It renders a
Ticket against the entry matching the Ticket's stored `worker_type`. Coding, `new_worker`,
`exploration`, `initiative_planning`, and `product_design` Tickets therefore show their
own Stage spines without frontend type tables.

During pristine Kickoff, the Kickoff section shows the Ticket's launch setup beside its
approval flow: Worker, Model, and Reasoning when that Worker and model support it. The
controls start with the values already copied onto the Ticket. Their model and reasoning
choices come from the selected backend, not from a frontend list. Once a session or
binding exists, or the Ticket moves beyond Kickoff, the controls disappear. They do not
move into the header or become a display of the worker's current settings.

_Code paths:_ `src/planner/core/server.py` serves the registry manifest;
`web/src/lib/lifecycle.ts` derives the frontend lifecycle.

## Managed settings and the Agents page

The registry remains the immutable workflow definition. A managed source beside the
database owns the ownership default for each existing non-terminal Stage and every
editable skill. `data/skills` is the one live skill home for Codex, Claude, and Hermes.
The packaged `src/planner/skills` tree seeds a new home only; it is never changed by
the product and does not replace a managed edit. Hermes contains symlinks to the
managed home, never copied overlays.

The browser navigation and settings page is **Agents** at `#/agents`. It has exactly two
quiet, whitespace-separated sections. Every destination is a whole-row link showing
only its human-readable name, its current managed skill description, and a restrained
arrow. The index does not show structural ids, skill names, launch settings, Stage
counts, or configuration labels, and it keeps the same one-column order on narrow
screens.

- **Agents** contains Chief of Staff and the shared Worker role skill. Chief
  opens at `#/agents/chief-of-staff` with Backend, Model, and Reasoning launch defaults
  and its canonical editable skill. It has no Ticket lifecycle or Stage table. Worker
  skill opens at `#/agents/worker-skill`. It is shown as an Agent-like configurable
  role because it guides every Ticket worker, although it is not an independent
  runtime. Its name is read-only; its description and Markdown body edit the canonical
  skill through the shared skills home. It has no independent launch, model, reasoning,
  or Stage controls.
- **Workers** links the configured Worker types. Each supporting line comes from that
  Worker's managed specialist skill. A Worker opens at
  `#/agents/workers/<worker-type>` with its launch defaults, Stage ownership table, and
  specialist skill editor. Worker identity and lifecycle structure stay read-only.

Legacy `#/workers` and `#/workers/<worker-type>` addresses redirect to `#/agents` and
`#/agents/workers/<worker-type>`.

A Ticket captures the managed ownership default when it enters a Stage. Later global
changes affect only future entries; the Ticket's explicit Stage override still wins.
Settings writes use atomic replacement and one writer lock per Worker or Chief. These
files live beside the database rather than in it, so the writer announces the change
itself once the new file is in place; if that fails, the canonical file is put back and
every backend continues to see the prior revision.

Every editable skill name is read-only. Description and Markdown body are ordinary
direct edits that save, fail, and retry independently. Successful skill edits refresh
the configured planner Hermes home without changing existing Employee session ids.
Codex and Claude Code use the same managed home.

`GET /api/workers` serves the Agents-page destinations and Chief settings.
`GET /api/workers/{id}` composes Worker registry structure with managed settings.
`GET /api/skills` serves the shared skills home used for the Worker role and specialist
descriptions on the index. Worker and Chief endpoints edit skill description and body
or launch defaults; the shared
`PATCH /api/skills/{skill-name}` endpoint edits the Worker role skill. A saved change
announces itself, and any Agents screen on display refetches what it is showing.

_Code paths:_ `src/planner/worker_settings/`, `src/planner/tickets/data.py`,
`src/planner/environments/hermes_home.py`, and `web/src/routes/AgentsRoute.svelte`.

## The Ticket owns its launch setup

Each Worker type supplies the Worker, Model, and Reasoning values used to start a new
Ticket. Creation copies the trio once. From then on, the Ticket owns it; changing the
Worker type's defaults later does not change existing Tickets, and switching a Ticket's
Worker does not restore an earlier set of choices.

The complete trio may be edited in the Kickoff controls only while the Ticket is still
at pristine Kickoff and has no conversation yet. One save replaces the whole setup.
Changing Worker names the new backend's own model in the same save, because a model name
belongs to the backend that gave it and means nothing to another one; Reasoning is cleared,
since it belonged to the model being replaced. Changing Model retains an explicit Reasoning
choice only when the new model still supports it. Starting the Ticket's first conversation,
or any move past Kickoff, removes those controls.

A backend this machine reported no models for has no model to name, so a save that switches
to it names none and is refused. That is the honest end of it: the alternative is a Ticket
whose worker runs on something nobody chose.

Hermes offers Model but not Reasoning. Codex and Claude Code offer Model, and their
Reasoning choices depend on the selected model.

Every conversation is started on a named model. Panels never lets a backend pick one for
itself: that is a value nobody chose, nobody here can see, and the tool may change it from
under us. A Ticket holding no model — one set up before this was so — has chosen nothing
that can be run, so it starts on its Worker type's own backend and model instead of on a
model picked for it. A saved launch setting that names no model is repaired the same way,
to the whole set of values its Worker type or the Chief ships with, and the settings file
is rewritten so the setting on the screen is the setting that runs.

Leaving Reasoning at its native value means the backend chooses its own default, which is
a real answer because some models take none. A missing or unavailable explicit value fails
visibly instead of silently selecting something else.

The Backend, Model, and Reasoning menus use a durable catalog for that backend and candidate
model. A catalog stays fresh for 24 hours across a server restart. The user can choose
**Refresh** in either shared setup surface to rediscover it immediately. A failed rediscovery
keeps the last catalog in the database but reports the failure instead of presenting stale
choices as a successful refresh.

The stored backend, model and reasoning are the Ticket's last-chosen values. They start
as the Worker type's defaults and are kept up to date with what its conversation
actually runs on, so a fresh conversation starts from where the last one ended.

Chief settings use the same managed authority for Backend, Model, and Reasoning. A new Chief
conversation is started on the then-current trio. An existing one continues on what it was
started with, including after a server restart.

Access is not a launch setting and is never copied onto a Ticket. Every conversation runs
under full access inside its workspace folder; how each backend realises that belongs to its
adapter and appears nowhere else.

_Code paths:_ `src/planner/conversation/production_backends.py` composes the three real
agents; `src/planner/worker_types/configuration.py` joins them to the Worker-type
registry; `src/planner/tickets/data.py` stores and freezes the Ticket setup; and
`web/src/components/WorkerConfigurationSetup.svelte` renders the Kickoff controls.

## How a worker finds its specialist

The launched base role is `panels-worker`. It knows how to work one Ticket step at a time,
but it does not contain the substance of every Worker type.

The worker runs `panels worker my-ticket`. That response includes the Ticket's stored
Worker type and the specialist skill named by its `WorkerTypeDefinition`. The worker loads
that skill with `skill_view` and follows its Stage-specific guidance.

Panels starts the Ticket's conversation the first time it has something to send, and uses
that same one afterwards. The Ticket names it in `tickets.conversation_id` and nothing else
owns that name. A restart changes nothing: the conversation is the record, and the backend
process is started again under it when there is a reason to.

- `panels-worker-coding` guides coding Tickets.
- `panels-worker-new-worker` guides `new_worker` Tickets.
- `panels-worker-exploration` guides `exploration` Tickets.
- `panels-worker-initiative-planning` guides `initiative_planning` Tickets.
- `panels-worker-product-design` guides `product_design` Tickets.

For `new_worker`, the visible lifecycle after universal Kickoff is
Understanding, Stages, Thinking, Runtime Defaults, Drafting, Closeout, Done. Understanding
and Runtime Defaults are paired. Runtime Defaults approves an explicit registered backend,
advertised model, and supported reasoning effort before Drafting records them in the
Worker profile. Understanding:
Panels sends one automatic opening turn into the Ticket's conversation, human
conversation continues in that same conversation, and an Understanding proposal waits for
approval before the Ticket advances to Stages.

The repository exposes the same skill source at Codex's and Claude Code's native project
skill locations, while startup links the listed skills into the planner Hermes home. The
first real prompt in a new Ticket conversation tells the selected backend to use the
installed `panels-worker` role; the role then finds this Ticket's specialist. A new
specialist must therefore be known to Worker type configuration and available through
the backend skill links.

_Code paths:_ `src/planner/skills/panels-worker/SKILL.md`, the specialist skills under
`src/planner/skills/`,
`src/planner/tickets/api.py`, `src/planner/cli/main.py`, and
`src/planner/environments/hermes_home.py` (linked in by `src/planner/core/server.py` at
startup).

## Adding a Worker type

One new Worker type needs one definition and one production registration path:

1. Write the specialist `SKILL.md` under `src/planner/skills/<name>/`, with guidance for
   each working Stage.
2. Add one definition module under `src/planner/worker_types/`. Construct an immutable
   `WorkerTypeDefinition` with its ordered Stages, fields, worker profile, starting
   Employee backend/model/reasoning values, and reconciliation support. Give every
   non-terminal Stage a deliberate default ownership mode; `done` and `dropped` have
   none. Novel Stage and field ids are plain strings.
3. In `src/planner/worker_types/configuration.py`, add the specialist skill to the known
   skills catalog and add the definition to `_PRODUCTION_WORKER_TYPE_DEFINITIONS`. Do not
   register it anywhere else.
4. Add the skill directory name to `PLANNER_SKILL_NAMES` in
   `src/planner/environments/hermes_home.py`, so startup provisions it into the
   worker's Hermes home.
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
- **Worker orchestration** (`worker-orchestration.md`) explains how a Ticket's next
  worker step gets started and how the worker reaches its specialist.
- **The frontend** (`frontend.md`) explains the screens driven by the served manifest.
- **The command-line tool** (`cli.md`) explains the worker and Chief commands.

---

_Last verified: 2026-07-26._
