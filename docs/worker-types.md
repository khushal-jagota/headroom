# Worker types

A Worker type is a way of paying attention. Coding, research, debugging, and designing a flow do not want the same stages or the same moments of human judgment. A type supplies a reusable preset. Each new Ticket owns the stages it copies from that preset. Custom work can define its own stages without a new type.

## What a type declares

- **The ladder.** Every type starts at Brief and ends with Consequences, then Done. Consequences is where the follow-through happens: merge, follow-up Tickets, bookkeeping, and a report. Deployment stays a separate owner action. The stages between are the type's own argument for how that kind of work should unfold.
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

A few retired types remain readable on old Tickets. They cannot be used for new work.

## Changing a type

The database rows are the registry. A file shipped with the code seeds an empty database and is not read again. The server and the browser both read the rows, and the browser renders whatever ladder it is sent, with no special case per type.

A preset edit changes future Tickets. Existing Tickets keep their stages, fields, ownership, approvals, guidance, and current position. Their Workers follow the Ticket's own guide, not the type's current skill.

Removing a type stops new Tickets of it. Existing Tickets keep running on their own copy, and its specialist skill stays until no unfinished Ticket names it. A type with an unfinished Ticket that saved no model cannot be removed, because that Ticket launches from the type's defaults.

Preset instructions sit beside their stages as structured data. Panels assembles that record into the specialist skill, so the stage editor and skill share one source. Instructions remain ordinary prose, with room for guidance across the workflow.

The New worker and Amend worker types are how these changes are usually made: a Worker drafts the definition and a person decides it.

## Decisions that shape it

- **The Ticket owns its stages.** A preset records where the initial workflow came from. It does not control an existing Ticket.
- **Ownership and the ceiling are separate.** Ownership says who drafts a stage. The ceiling says whether the result waits for someone.
- **Defaults are copied, not referenced.** A Ticket owns its launch setup from creation, so changing a type's defaults never changes running work.
- **Creation has two routes.** Select and adapt a preset, or define stages, ownership, results, and instructions directly. Review the whole workflow before creation.
- **Adding a type is a product decision, not a code change**: a new ladder and a specialist skill, saved as a new definition.

## Code

- `src/planner/worker_types/`: workflow definitions, preset registry, guidance assembly, and storage.
- `src/planner/worker_types/shipped_worker_types.json`: the seed.
- `web/src/routes/ConfigRoute.svelte`: the Settings screen for types and their defaults.

_Last verified: 2026-11-08._
