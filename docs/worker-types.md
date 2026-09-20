# Worker types

Every Ticket has a required **Worker type**. The Worker type says what kind of work the
Ticket contains, which Stages it moves through, which fields those Stages settle, and
which specialist skill guides its worker.

There is no default Worker type. Creating a Ticket requires the caller to choose one.
Panels stores that choice on the Ticket for its whole life. A read returns the Ticket's
stored Stage and Worker type as they are; it does not substitute coding behavior or ask a
registry to reinterpret them.

Fourteen Worker types are seeded into a new database:

- **`coding`** handles product and repository work.
- **`general`** is the catch-all, chosen when no specialist type fits. It runs a
  deliberately minimal lifecycle — do the task, then land its consequences — for
  arbitrary work.
- **`debugging`** understands a reported software bug, diagnoses its structural cause,
  and defines the implementation handoff without implementing it.
- **`new_worker`** designs and lands a new kind of worker.
- **`amend_worker`** changes an existing Worker type in place: its Stages, ownership
  modes, runtime defaults, or skill guidance.
- **`exploration`** is a worker for exploring something undefined and making it clearer.
- **`research`** answers an already-framed question with sourced evidence and synthesis,
  without deciding or implementing.
- **`initiative_planning`** works out the shared top-level how for a confirmed direction,
  then creates the bounded Tickets that carry it.
- **`initiative_review`** reviews a delivered initiative as one combined result, captures
  the user's feedback, and creates the agreed follow-up work.
- **`product_design`** designs holistic product flows and implementation-ready interactive
  artifacts before handing implementation to a coding Ticket.
- **`planning-day`** reviews the previous Day, agrees the top-level direction with the
  user, shows the exact proposed Ticket changes, and commits the approved Day.
- **`planning-midday-check`** compares the morning intent with current execution at
  14:30, agrees any useful intervention, carries it out, and records the result.
- **`planning-sprint`** reviews the current sprint and plans the next at the final-day
  boundary, with canonical writes deferred until Consequences.
- **`personal`** represents user-owned work, with optional explicit agent support.

Tests also declare **`probe`**. Its Stage and field names are deliberately unfamiliar,
apart from the two the rules fix: the `brief` field it opens with and the `consequences`
field every Worker type declares. Even the Stage that gates its Consequences carries a name
of its own. So the test suite catches code that still assumes every Ticket is
coding-shaped. It is not a shipped Worker type.

## Where a Worker type is declared

A Worker type is one row in the `worker_types` table, holding the whole declaration as a
single document. It is read whole, written whole, and checked whole, because its parts
mean nothing apart: stages that gate fields the type does not declare are not a partly
valid Worker type, they are not one at all.

Opening a database loads its Worker types into the process, and that is what the rest of
Panels reads. Declaring or changing one is therefore a write, not a release: the change is
in force as soon as it is stored.

Each Worker type is one immutable `WorkerTypeDefinition` once loaded. The definition
contains:

- its id and human label;
- the ordered Stages, including the field and ownership mode of each
  non-terminal Stage (`worker` or `user`);
- the ordered fields carried by its Tickets;
- the worker profile, including the specialist skill and the default Employee backend,
  model, and reasoning effort copied onto a new Ticket.

The definition also answers the workflow questions that used to be spread across Ticket
constants and free helper views. Its methods find a Stage or field, return Stage order and
advance targets, identify gates and terminals, calculate the default ceiling and first
working Stage, and validate a Ticket position.

The shipped `coding` and `debugging` definitions assign every non-terminal Stage to
worker ownership.
`general` does the same: its Brief, Work Done, and Consequences are all worker-owned.
`amend_worker` uses user ownership for Amendment, the one Stage with a decision in it:
what changes, and what that does to the live Tickets of the Worker being amended. Its
Drafting and Consequences are worker-owned.
`new_worker` starts with a worker-owned Brief,
then uses user ownership for Purpose and Boundaries before the worker-owned Stages and
What Good Looks Like at Each Stage, assigns the user again for Model and Effort, then
returns to worker-owned Drafting and Consequences. `research` keeps all four of its non-terminal Stages worker-owned, because its question
arrives already framed and it runs unattended. `exploration`
uses user ownership for Understanding and Answer, where the user and worker establish
the frame and reach the decision together; its other non-terminal Stages use worker
ownership. `initiative_planning` uses user ownership for Question Answers, where
consequential cross-Ticket choices are settled with the user; its other non-terminal
Stages use worker ownership. `product_design` uses user ownership for Wireframe
and Design, while its Best Guess and Open Options Stage and its handoff are
worker-owned. `planning-day` uses user ownership for Today's Direction, where the Worker
and user agree on the most important work.
Previous Day Review, Day Changes, and Consequences are worker-owned. The Worker derives Day
overview fields without user input. `planning-midday-check` keeps its Stages worker-owned,
but its
Agreed Intervention Stage deliberately pauses through user-help before any approved
intervention is performed in Consequences. `planning-sprint` also keeps its four non-terminal Stages
worker-owned, but Review and Next Sprint deliberately pause through user-help until the
user explicitly releases the conversation. Every Worker type chooses deliberately for
each Stage; it does not inherit that choice from registry order or another definition.

This makes the definition the one authority for both the data and behavior of that
workflow. Ticket contracts still own universal Ticket facts such as status and scope,
but they do not define a coding lifecycle.

_Code paths:_ `src/planner/worker_types/contracts.py` contains the immutable declaration
types and behavior. `src/planner/worker_types/store.py` reads and writes the rows. The
migration `worker_types_in_database` carries a frozen copy of the fourteen definitions a
new database is seeded with.

## Validation at the write door

Nothing may be stored that is not a Worker type. Text arriving at the store is decoded
strictly first: every section must be present, no section may be unknown, and every value
must be of the right kind. A decoded definition then meets the same rules that used to run
when the modules were imported.

`WorkerTypeRegistry` validates every definition when the registry is built, and the store
runs those same rules before a row is written. They check the
shared structural rules: an optional Brief stage and field appear together first,
`done` is the one terminal, every non-terminal Stage gates one declared field, every
field is gated once, every type declares a `consequences`, every non-terminal Stage declares
a valid ownership mode,
terminal Stages declare none, worker skills and toolsets are known, and the default
Employee backend is one of the three the conversation system has.

A malformed definition is therefore refused at the moment somebody tries to store it,
instead of failing when a Ticket happens to reach the bad part of its workflow.

### Tickets in flight

A Ticket names its Stage, and its saved text is keyed by field id. So a rewrite that
removes a Stage an unfinished Ticket is standing on, or a field an unfinished Ticket holds
text in, is refused, and the error names those Tickets. Neither loss is recoverable, and
neither is the kind of thing to discover afterwards. Adding Stages, reordering them, and
changing labels are all free. A finished Ticket's text in a removed field goes dark; that
is accepted loss, and history is not rewritten.

### Every Worker type declares a Consequences Stage

A Worker type ends by landing what it produced, so every Worker type declares a `consequences`
field. A type that declares none has nowhere to land its work, and nobody learns that
until a Ticket reaches the end of its Stages. So the write door refuses it, and the error
says which requirement it missed.

The rule is one line: the declared fields must include `consequences`. The rules above finish
it, because a declared field must be gated, and gated only once. So `consequences` gates
exactly one Stage. Which Stage, and what that Stage is called, are the type's own choice.
The seeded types all put it last before `done` and all call it `needs_consequences`, but
neither is required.

The other names the runtime fixes are the `done` terminal and, for a type that opens with
a Brief, a `brief` field paired with a first `needs_brief` Stage. They are in the
rules above. Nothing else is a well-known name. The probe Worker type in the test suite
names its other Stages and fields unfamiliarly, and that is what keeps the rest of Panels
from assuming coding's shape.

### What the registry does with them

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
read paths also resolve the definition because ownership comes directly from
the stored Worker type and Stage rather than from duplicated Ticket state.

There is no compatibility bridge or special coding seam. The application boundary,
definition, and framework-free rule are the whole path.

_Code paths:_ `src/planner/worker_types/configuration.py` supplies the configured
registry. Ticket, sprint, runtime, and API boundaries import it where workflow
behavior is needed. Rules under `src/planner/tickets/logic/` receive
`worker_type_definition` explicitly.

## What this process is running

`src/planner/worker_types/configuration.py` holds the Worker types in force for the
process. Nearly a hundred places ask what a Worker type is, most of them nowhere near a
database connection, so the answer is held for the process rather than passed down to
every caller. It is loaded at exactly two moments, which are the only two at which it can
change: when a database is opened, and when a type is written.

The known specialist skills are the skills stored in the same database. Adding a Worker
type therefore does not mean editing a catalogue of names somewhere else.

Row order is manifest order.

Which agent backends exist is not this composition's business. It is the conversation
system's closed set of three — `hermes`, `codex`, and `claude` — and a Worker type naming
anything else is refused when the registry validates it. There is one door that turns a
name into a backend, and every part of Panels that reads one goes through it. A Worker
type's starting backend, model, and Reasoning choice belong to its own definition rather
than to a global fallback.

Tests store the `probe` Worker type in their own database, through the same door
production writes through. The probe names a real backend of its own, deliberately not one
the seeded types name, so that "a Worker type may run on a different agent" stays under
test.

No registry position means “default.” Order is composition and presentation order only.

## The served manifest and frontend

`GET /api/worker-types` calls the Worker-type registry's `manifest` method for each
definition. It does not list the agent backends: what backends this machine has, which
models each offers, and which reasoning efforts each of those takes are one answer, and
it comes from `GET /api/conversation/backends`. Every
Worker-type entry contains the label, Stages, gates, advance map, fields, ceiling range,
default ceiling, specialist skill id, and default Employee backend, model, and reasoning
effort. Every Stage also carries its ownership mode; terminal Stages carry none.
Clients combine the Ticket's Worker type and current Stage with this manifest to derive
the Stage's declared ownership.

`panels worker-type list` exposes this same response at the command line. Its normal
output lists the registered identifiers in registry order, while `--json` preserves the
complete manifest for automation. The CLI does not maintain its own Worker-type list.

## Declaring and changing a Worker type

There is one write door, `POST /api/worker-types`, and it takes the whole record. An
optional `skill` block carries the specialist skill's description and body, so declaring a
new Worker and declaring the skill it names happen together rather than as two writes that
can half-happen: a Worker type may only name a skill that already exists.

At the command line, `panels worker-type show <type>` prints the stored record in the
shape `panels worker-type save` takes back on stdin, and
`panels worker-type skill <type> --description "..."` replaces just the skill text.

Declaring a Worker type is a direct operation. A worker cannot declare one.

The frontend derives one lifecycle per Worker type from this served manifest. It renders a
Ticket against the entry matching the Ticket's stored `worker_type`. Every stored type,
including `general` and `personal`, therefore shows its own Stage spine without frontend
type tables, and a type declared today shows one without a frontend change.

During a pristine Brief, one launch picker shows the Ticket's backend, model, and Reasoning
choice beside its approval flow. The picker starts with the values already copied onto the
Ticket. Its model and reasoning choices come from the backend catalogue, not from a frontend
list. Once a conversation exists, or the Ticket moves beyond the Brief, the control
disappears. It does not move into the header or become a display of current worker settings.

_Code paths:_ `src/planner/core/server.py` serves the registry manifest;
`web/src/lib/lifecycle.ts` derives the frontend lifecycle.

## Skills and the Config page

Every managed skill's text is a row in `managed_skills`, and that row is the authority.
`data/skills/<name>/SKILL.md` is a copy Panels writes from it, because an agent reads a
file rather than a table, and each agent home links to that file. Opening a database
writes the copies, so the files always say what the rows say. Nothing edits a copy.

The packaged `src/planner/skills` tree is only what a database with no skills in it is
seeded from. After that it is not consulted, so a shipped skill and a stored one can
differ, and the stored one is what runs.

A Worker type owns its specialist skill. `PATCH /api/skills/{skill-name}` refuses one and
points at the Worker, so each skill has exactly one editor. Native homes use symlinks to
managed skills, never copied overlays. Codex and Claude select all Panels skills, and the
Hermes home links every skill in the managed home.

The browser navigation and settings page is **Config** at `#/config`. It has exactly two
quiet, whitespace-separated sections. Every destination is a whole-row link showing
only its human-readable name, its current managed skill description, and a restrained
arrow. The index does not show structural ids, skill names, launch settings, Stage
counts, or configuration labels, and it keeps the same one-column order on narrow
screens.

- **Config** contains Chief of Staff, the Sprint Item supervisor, and the shared Worker
  role skill. Chief
  opens at `#/config/chief-of-staff` with one picker for its launch defaults
  and its canonical editable skill. It has no Ticket lifecycle or Stage table. Worker
  skill opens at `#/config/worker-skill`. It is shown as an Agent-like configurable
  role because it guides every Ticket worker, although it is not an independent
  runtime. Its name is read-only; its description and Markdown body edit the canonical
  skill through the shared skills home. It has no independent launch, model, reasoning,
  or Stage controls. Sprint Item supervisor opens at
  `#/config/sprint-item-supervisor`. It uses the same canonical skill editor and has no
  global launch or Stage controls. Each Sprint Item owns its supervisor launch snapshot.
- **Workers** links the configured Worker types. Each supporting line comes from that
  Worker's managed specialist skill. A Worker opens at
  `#/config/workers/<worker-type>` with its launch defaults, read-only Stage ownership
  table, and specialist skill editor. Worker identity and lifecycle structure stay read-only.

Legacy `#/workers` and `#/workers/<worker-type>` addresses redirect to `#/config` and
`#/config/workers/<worker-type>`.

Every edit here reads the current record, changes one part of it and writes the whole
thing back, so each one holds the write lock across that read and write. Two edits landing
together would otherwise each write what it read, and the slower one would undo the
faster one's field.

The row commits first and the file copy is written from it afterwards. If recording the
skill's version history then fails, the owner's edit still stands: the row is the
authority, and the copy is rewritten from it on every open.

What a Worker type launches on is part of its record rather than a separate setting, so
there is no second copy to keep in step and nothing to reconcile. The Chief is not a
Worker type and keeps its own single row.

Every editable skill name is read-only. Description and Markdown body are ordinary
direct edits that save, fail, and retry independently. Successful skill edits refresh
the configured planner Hermes home without changing existing conversations.
Codex and Claude Code use the same managed home.

`GET /api/workers` serves the Config-page destinations and Chief settings. The Chief
entry also carries its current `conversation_id`, whether it is working or needs the
owner, and the sequence where its latest turn ended. The Workspace Chief row uses those
conversation-owned signals without turning them into managed settings.
`GET /api/workers/{id}` composes Worker registry structure with managed settings.
`GET /api/skills` serves the shared skills home used for role skills and specialist
descriptions on the index. Worker and Chief endpoints edit skill description and body
or launch defaults. The generic
`PATCH /api/skills/{skill-name}` endpoint edits the Worker and Sprint Item supervisor
role skills. A saved change announces itself, and any Config screen on display refetches
what it is showing.

Approval derives its initial ceiling from the Worker definition's advance target for the
new Stage. A valid terminal Stage is the fallback when no further advance exists.
`No further` remains a choice for one approval.

_Code paths:_ `src/planner/managed_skills.py` holds the skill rows and writes the copies,
`src/planner/worker_settings/` composes what the Config page reads and edits, and
`src/planner/tickets/data.py`, `src/planner/environments/hermes_home.py`, and
`web/src/routes/ConfigRoute.svelte` are unchanged by where the answers come from.

## The Ticket owns its launch setup

Each Worker type supplies the Worker, Model, and Reasoning values used to start a new
Ticket. Creation copies the trio once. From then on, the Ticket owns it; changing the
Worker type's defaults later does not change existing Tickets, and switching a Ticket's
Worker does not restore an earlier set of choices.

The complete trio may be edited in the Brief controls only while the Ticket is still
at a pristine Brief and has no conversation yet. One save replaces the whole setup.
Changing Worker names the new backend's own model in the same save, because a model name
belongs to the backend that gave it and means nothing to another one; Reasoning is cleared,
since it belonged to the model being replaced. Changing Model retains an explicit Reasoning
choice only when the new model still supports it. Starting the Ticket's first conversation,
or any move past the Brief, removes those controls.

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

The unified launch picker uses a durable catalog for the backend and candidate model. A
catalog stays fresh for 24 hours across a server restart. Catalogue refresh belongs on the
Backends screen with the rest of the machine state. A failed ordinary read keeps the saved
picker value visible and offers a retry.

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
`web/src/components/WorkerConfigurationSetup.svelte` renders the Brief controls.

## How a worker finds its specialist

The launched base role is `panels-worker`. It knows how to work one Ticket step at a time,
but it does not contain the substance of every Worker type.

The worker runs `panels worker my-ticket`. The default response contains a header and
the Ticket part manifest. The header includes the Ticket's stored Worker type and the
specialist skill named by its `WorkerTypeDefinition`. The worker can pass one
comma-separated part list to expand only the needed fields. It loads the specialist
skill with `skill_view` and follows its Stage-specific guidance.

Panels starts the Ticket's conversation the first time it has something to send, and uses
that same one afterwards. The Ticket names it in `tickets.conversation_id` and nothing else
owns that name. A restart changes nothing: the conversation is the record, and the backend
process is started again under it when there is a reason to.

- `panels-worker-coding` guides coding Tickets.
- `panels-worker-general` guides `general` Tickets.
- `panels-worker-debugging` guides `debugging` Tickets.
- `panels-worker-new-worker` guides `new_worker` Tickets.
- `panels-worker-amend-worker` guides `amend_worker` Tickets.
- `panels-worker-exploration` guides `exploration` Tickets.
- `panels-worker-research` guides `research` Tickets.
- `panels-worker-initiative-planning` guides `initiative_planning` Tickets.
- `panels-worker-initiative-review` guides `initiative_review` Tickets.
- `panels-worker-product-design` guides `product_design` Tickets.
- `panels-worker-planning-day` guides `planning-day` Tickets.
- `panels-worker-planning-midday-check` guides `planning-midday-check` Tickets.
- `panels-worker-planning-sprint` guides `planning-sprint` Tickets.
- `panels-worker-personal-task` guides `personal` Tickets.

For `new_worker`, the visible lifecycle after the universal Brief is
Purpose and Boundaries, Stages, What Good Looks Like at Each Stage, Model and Effort,
Drafting, Consequences, Done. Purpose and Boundaries is user-owned, and so is Model and
Effort, which approves an explicit registered backend, advertised model, and supported
reasoning effort before Drafting records them in the Worker profile. At Purpose and
Boundaries, Panels sends one automatic opening turn into the Ticket's conversation. Human
conversation continues in that same conversation, and a Purpose and Boundaries proposal
waits for approval before the Ticket advances to Stages.

The packaged skill tree seeds missing entries in the managed `data/skills` home. That
managed home remains authoritative after seeding. Codex and Claude provisioning links
all managed Panels skills without replacing unrelated user skills. Hermes uses the
separate `PLANNER_SKILL_NAMES` allowlist. That allowlist currently omits
`panels-worker-general`, although the `general` Worker type names it. The
first real prompt in a new Ticket conversation tells the selected backend to use the
installed `panels-worker` role; the role then finds this Ticket's specialist. A new
specialist must therefore be known to Worker type configuration and available through
the selected backend's provisioned skill home.

_Code paths:_ `src/planner/skills/panels-worker/SKILL.md`, the specialist skills under
`src/planner/skills/`,
`src/planner/tickets/api.py`, `src/planner/cli/main.py`, and
`src/planner/skill_sources.py`.

## Adding a Worker type

One new Worker type needs one definition and one production registration path:

1. Write the specialist `SKILL.md` under `src/planner/skills/<name>/`, with guidance for
   each working Stage.
2. Add one definition module under `src/planner/worker_types/`. Construct an immutable
   `WorkerTypeDefinition` with its ordered Stages, fields, worker profile, starting
   Employee backend/model/reasoning values. Give every non-terminal Stage a deliberate
   ownership mode; `done` has none. Novel Stage and field ids are plain strings.
3. In `src/planner/worker_types/configuration.py`, add the specialist skill to the known
   skills catalog and add the definition to `_PRODUCTION_WORKER_TYPE_DEFINITIONS`. Do not
   register it anywhere else.
4. Add the skill directory name to `PLANNER_SKILL_NAMES` in
   `src/planner/environments/hermes_home.py`, so startup provisions it into the
   worker's Hermes home.
5. Confirm that ordinary Ticket creation lists the new type. The base Worker discovers
   its specialist through `panels worker my-ticket`; it has no manual specialist list.
6. Restart Panels and provision the production skill homes. Composition validates the
   registry before the Worker type becomes live.

The shared kickoff and completion ids are structural rules, not imported lifecycle
constants. The new definition still declares them directly: `needs_brief` gating
`brief`, and terminal `done`.

## Ordinary field completion

Worker definitions declare each Stage's ownership and gating field. The ordinary value
writer uses those declarations to let a direct user complete only the unset gate of the
current user-owned Stage. No definition carries separate reconciliation capability.

## Handoffs

- **Tickets and gates** (`tickets-and-gates.md`) explains scope, proposals, resolution,
  Stage ownership, scope, and approval.
- **Worker orchestration** (`worker-orchestration.md`) explains how a Ticket's next
  worker step gets started and how the worker reaches its specialist.
- **The frontend** (`frontend.md`) explains the screens driven by the served manifest.
- **The command-line tool** (`cli.md`) explains ordinary and worker commands.

## Deferred

- **General Worker on Hermes.** The Hermes allowlist does not expose
  `panels-worker-general`. Trigger: before a `general` Ticket uses Hermes, add its
  specialist skill to `PLANNER_SKILL_NAMES`.

---

_Last verified: 2026-08-16._
