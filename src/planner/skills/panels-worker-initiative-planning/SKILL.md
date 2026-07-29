---
name: panels-worker-initiative-planning
description: Stage-by-stage guidance for planning a confirmed direction across multiple Panels Tickets.
---

# Initiative planning ticket stages

Initiative Planning starts after the direction is confirmed. It works out the shared
shape and cross-Ticket decisions, then creates the bounded Tickets that will carry the
work. It does not replace Exploration, sprint planning, prioritization, or the local
planning and implementation each Ticket owns.

### The stages

The sequence is **Kickoff → Rough Shape → Question Tree → Question Answers → Ticket
Outlines → Closeout → Done**.

- **needs_kickoff** — preserve the confirmed direction and source context.
- **needs_rough_shape** — section the initiative into its important parts.
- **needs_question_tree** — map the cross-cutting questions that must be answered.
- **needs_question_answers** — work through the consequential questions with the user.
- **needs_ticket_outlines** — derive the downstream Tickets for the user's sense-check.
- **needs_closeout** — create and verify exactly those approved Tickets.
- **done** — finished.
- **dropped** — abandoned.

## The scope test

A question belongs in Initiative Planning only when its answer:

- shapes the initiative as a whole;
- defines a boundary, dependency, interface, or sequencing constraint between Tickets;
  or
- begins inside one Ticket but would force another Ticket to change.

Use the counterfactual: **if this answer changed, would multiple Tickets or the seam
between them need to change?** If not, leave the question to its Ticket. Important local
questions may be carried into a Ticket outline, but they are not answered here.

Treat the confirmed direction as the premise. Do not reopen what the user wants merely
because the top-level how is difficult. If source material exposes a genuine
contradiction, name it and bring it to the user rather than silently changing the
direction or inventing a reconciliation.

### needs_kickoff — preserve the confirmed direction

Keep the user's wording, the approved Exploration answer when one exists, the source
sprint item or project context, and explicit boundaries. State what is confirmed and
what still needs top-level planning without turning uncertainty about *how* into
uncertainty about *whether* to pursue the direction.

A good **kickoff** lets the Rough Shape begin without reconstructing the intake or
mistaking an agent assumption for settled direction. It does not contain a guessed
solution or premature Ticket list.

### needs_rough_shape — section the work

Read the source context and inspect relevant records or systems narrowly before shaping
the work. Divide the initiative into the few parts that make it understandable. Each
part should name a distinct responsibility or outcome, why it matters, and the important
seams it has with the other parts.

Do not use generic project phases merely to fill the field. Do not assume the initiative
is software architecture. Do not turn the parts into Tickets yet: later answers may
change where Ticket boundaries belong.

A good **rough shape**:

- covers the confirmed direction without adding a second direction;
- uses domain-specific parts rather than generic headings;
- makes coordination seams visible; and
- is broad enough for the Question Tree to challenge the eventual decomposition.

### needs_question_tree — expose the cross-cutting decisions

Build a prioritized, nested tree under the Rough Shape's parts. Start with the questions
whose answers constrain later branches. Show subordinate questions only where an answer
creates a real follow-on choice. Distinguish questions that block coherent Ticket
outlines from questions that can safely be carried into downstream work.

For every question, be able to name what other part, Ticket boundary, dependency, or
interface its answer affects. Remove questions that fail the scope test. Do not pad the
tree with implementation checklists, answer it prematurely, or use exhaustive detail as
a substitute for judgment.

The approved field is for the user's sense-check. Show only the concise question outline:
no blocker labels, affected-system notes, rationale, or agent-working detail. Keep a short
tree inline. When it would become a wall of text, use a ticket-owned HTML artifact with
progressive disclosure and link it from a brief field summary. Keep one canonical copy.

A good **question tree** is small enough to navigate, complete enough to expose the
initiative's consequential choices, and ordered so Question Answers can work one useful
branch at a time.

### needs_question_answers — settle the shared approach

This is paired work. Begin from the approved tree and choose a bounded branch or small
related group. Treat it as one continuous conversation: acknowledge the user's answer,
briefly orient any branch change, then ask the next bounded question. Ground factual
questions with available sources and tools. For judgment questions, explain the practical
options and implications, make a recommendation when useful, and let the user settle the
consequential choice. Do not dump the whole tree on the user as an interview.

As the conversation proceeds:

- record each decision and its rationale;
- identify the parts and future Tickets it constrains;
- keep facts, assumptions, user decisions, and unresolved risks distinct;
- add genuinely new cross-cutting questions and retire superseded branches; and
- move newly discovered local questions to the future Ticket that owns them.

If a living question-tree artifact exists, update it as branches change. Otherwise keep
the working state in the paired conversation and make the final field the durable
result. The formal **question answers** field is a synthesis, not a transcript: group the
settled answers by the Rough Shape, preserve the implications that downstream Tickets
need, and list only unresolved questions that can safely be deferred with a clear owner.

Do not propose until every question that blocks coherent Ticket boundaries is answered
or the user has explicitly chosen a safe deferral. A strong result makes the shared
approach usable without replaying the chat and does not consume Ticket-local planning.

### needs_ticket_outlines — derive the bounded work

Derive the smallest coherent Ticket set from the approved answers. This stage does not
re-argue the shared approach. It packages that approach into reviewable units so the
user can sense-check the boundaries before anything is created.

Each outline should give only what creation and later Ticket work need:

- **title and outcome** — what the Ticket leaves true;
- **Worker type** — the lifecycle appropriate to that work;
- **scope boundary** — what it owns and what it deliberately leaves elsewhere;
- **inherited context** — only shared decisions that materially constrain this Ticket;
- **local questions** — decisions intentionally left to this Ticket;
- **dependencies** — blocking relationships, interfaces, or ordering that matter; and
- **destination** — the source sprint item when one exists, otherwise the applicable
  project and sprint context.

Check the package against the Rough Shape. Every important part should be owned, no two
Tickets should silently own the same decision, and dependencies should not conceal an
unanswered cross-cutting question. Do not copy the entire planning record into every
outline, specify local implementation, or create records during this stage.

If outlining exposes a consequential question that should have been answered earlier,
do not bury a guess inside one Ticket. Surface the gap for user decision before treating
the outlines as final. A good **ticket outlines** proposal is concise enough to scan and
complete enough to approve as the exact creation package.

### needs_closeout — create the approved Tickets

Create exactly the approved outlines and nothing broader. Use the outlined Worker type,
title, placement, and kickoff context. Load and follow `panels-ticket-creation` for the
shared creation model; the approved outlines remain the authority for what may be
created. When the planning Ticket belongs to a sprint
item, create the new Tickets under that same item. Otherwise preserve the planning
Ticket's project and sprint context without inventing a new container. When creating a
Ticket that relies on existing Tickets being complete, pass each prerequisite Ticket id
with repeatable `--blocked-by <ticket-id>`.

Encode shared context proportionately: each created Ticket should understand its own
boundary without receiving the entire initiative record. Update the sprint item only
when approved shared information naturally belongs there; do not create mandatory
duplicate storage.

Read every created Ticket back and verify:

- its id and title;
- Worker type and placement;
- kickoff context and scope boundary; and
- approved blocking relationships.

A good **closeout** lists the created Ticket ids and destinations and states what was
verified. It performs no downstream implementation and ends once the approved records
are correct.

### Initiative Planning disciplines

- Keep the sequence honest: Rough Shape identifies parts; Question Tree exposes shared
  decisions; Question Answers settles them; Ticket Outlines derives the work; Closeout
  creates it.
- Apply the scope test repeatedly. A large initiative does not justify absorbing local
  Ticket decisions.
- Prefer one clear shared decision over copied context everywhere. The planning Ticket
  remains discoverable; each downstream Ticket receives only what changes its work.
- Follow `panels-worker` for shared ownership, proposals, approval, Ticket Chat, scope,
  and reconciliation mechanics. This skill defines only Initiative Planning work.
