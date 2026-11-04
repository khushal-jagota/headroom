# Worker types

A Worker type is a way of paying attention. Coding, research, debugging, and designing a flow do not want the same stages or the same moments of human judgment. The type is chosen when a Ticket is created, never changes afterward, and gives that Ticket its shape.

## What a type declares

- **The ladder.** Every type starts at Brief and ends with Consequences, then Done. Consequences is where the follow-through happens: merge, deploy, follow-up Tickets, bookkeeping, and a report. The stages between are the type's own argument for how that kind of work should unfold.
- **Stage ownership.** A worker-owned stage is the Worker's to draft. A user-owned stage is collaborative: the Worker gets one automatic opening turn, then the person and the Worker continue in the same conversation until the gate is filled. Ownership does not decide whether a proposal waits. The [ceiling](tickets.md) does.
- **A specialist skill**, the instructions this kind of Worker reads on top of the shared Worker skill. See [Skills](skills.md).
- **Launch defaults**: the backend, model, and reasoning a new Ticket copies as its starting setup. Khushal can change them in Settings. A change affects future Tickets only.

## The shipped types

User-owned stages are marked with `*`.

| Type | For | Stages between Brief and Consequences |
| --- | --- | --- |
| Coding | Product and repository work | What Changes, Implementation |
| General | Work that needs no special process | Work Done |
| Debugging | Finding a cause and defining a fix, without implementing it | Problem Understanding, Root Cause, Proposed Fix |
| Research | A framed question and a sourced synthesis | Research Plan, Findings |
| Exploration | An unclear question worked through with the person | Understanding\*, Research Plan, Findings, Answer\*, Proposed Follow-up |
| Product design | Flows and interactive artifacts before a coding handoff | Wireframe, Design |
| Initiative planning | Turning a confirmed direction into a set of Tickets | Rough Split into Parts, Question Tree, Question Answers\*, Proposed Tickets |
| Initiative review | Reviewing a delivered initiative and agreeing follow-up | Review, Feedback\*, Follow-ups |
| New worker | Designing and landing another Worker type | Purpose and Boundaries\*, Stages, What Good Looks Like at Each Stage, Model and Effort\*, Drafting |
| Amend worker | Changing an existing type in place | Amendment\*, Drafting |

The user-owned stages are where the person's view is the product. Exploration does not race ahead of a shared picture, and does not declare its answer alone. Planning settles open questions with Khushal before Tickets are proposed. On types with no user-owned stage, a person stays involved through the ceiling, for example by stopping a coding Ticket at What Changes.

A few retired types remain readable on old Tickets and schedules. They cannot be used for new work.

## Changing a type

The database rows are the registry. A file shipped with the code seeds an empty database and is not read again. The server and the browser both read the rows, and the browser renders whatever ladder it is sent, with no special case per type.

An amendment can add or remove stages even while unfinished Tickets use them. A Ticket on a removed stage moves back to the nearest earlier stage that survives, and so does a ceiling. This is the one place a stage moves outside the proposal resolver. It only ever moves a Ticket backward, so a change never grants a stage by accident. Stored text is kept, and nothing is approved on anyone's behalf. The definition and every affected Ticket are saved together. If any Ticket cannot move safely, the whole save is refused. The save reports each Ticket's old and new position.

The New worker and Amend worker types are how these changes are usually made: a Worker drafts the definition and a person decides it.

## Decisions that shape it

- **The type is fixed per Ticket.** A Ticket's stages are a promise about how its work will be reviewed. Switching type mid-flight would break that promise.
- **Ownership and the ceiling are separate.** Ownership says who drafts a stage. The ceiling says whether the result waits for someone.
- **Defaults are copied, not referenced.** A Ticket owns its launch setup from creation, so changing a type's defaults never changes running work.
- **Amendments move backward or refuse.** A removed stage never pushes a Ticket forward past a gate a person did not approve.
- **Adding a type is a product decision, not a code change**: a new ladder and a specialist skill, saved as a new definition.

## Code

- `src/planner/worker_types/`: definitions, the registry, the amendment rules (`logic/migration.py`), and the save (`store.py`).
- `src/planner/worker_types/shipped_worker_types.json`: the seed.
- `web/src/routes/ConfigRoute.svelte`: the Settings screen for types and their defaults.

_Last verified: 2026-11-04._
