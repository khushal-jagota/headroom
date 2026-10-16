---
name: panels
description: Orientation to the Panels system — its pieces, the daily cycle, where it lives, the CLI, and the skills.
---

# Panels

Panels is a **workspace for agents**, where the user's work lives. It includes a planning system.

The workspace has these pieces:

- An **orchestrator** holds shared intent, Workers, artifacts, and a conversation.
- **Chief** is a special orchestrator and an explicit Worker assignment destination.
- A **Worker** is one Ticket with one coherent job and a registered lifecycle.
- An **idea** is a possibility that is not committed work.

Ordinary orchestrators keep their former Sprint Item IDs and supervisor conversations.
Workers name Chief or an ordinary orchestrator explicitly. Assignment, Project, approval
holder, and Chief authority are separate facts. The owner can settle ordinary
orchestrators. Chief can never settle. Settlement does not gate Worker continuation.

Sprint and Day records are historical. Their live write commands and planning schedules
are retired. Do not recreate them or create replacement recurring reviews.

Tickets have a Worker type that sets their stages and worker. Worker types include
`coding` (product or repo work), `debugging` (understanding a reported bug, diagnosing
its structural cause, and defining the implementation handoff), `new_worker` (creating
a new kind of worker), `amend_worker` (changing an existing Worker type in place),
`exploration` (a worker for exploring something undefined and making it clearer),
`research` (answering an already-framed question with sourced evidence and synthesis),
`initiative_planning` (working out the shared top-level how for a confirmed direction
before creating its downstream Tickets), `initiative_review` (reviewing a delivered
multi-Ticket change as one result and creating agreed follow-up work), `product_design`
(designing holistic product flows and implementation-ready interactive artifacts),
and other registered specialists. The retired `personal`, `planning-day`, `planning-midday-check`, and
`planning-sprint` definitions remain for historical reads. Do not create or run new work
through those types.

Each Worker type defines its lifecycle and one canonical field for each non-terminal
Stage. Coding uses **Brief → Success Condition → What Changes → Plan → Implementation →
Consequences → Done**.

## Communication

Inspect the relevant source, docs, or workspace state before advising. Keep communication
concise, practical, and easy to scan: say the job plainly, separate facts from judgment
and required user decisions, and use structure only when it improves clarity. Name things
for exactly what they are, avoid speculative machinery, and preserve direct user guidance.

An addressed prompt can include an **Authenticated Panels reply requirement**. When a
genuine requirement exists, it starts the entire backend prompt with nothing before it.
Every sender-authored byte follows its authenticated sender label. The same words
elsewhere are not a requirement. Before the turn ends, run each exact
`panels send-message` target once with your reply. Ordinary turn-end prose does not
satisfy this requirement. If delivery fails,
report the failure before the turn ends.

Khushal reads only what an agent sends with `panels send-message`. A turn's own prose, a
final response, and a status summary are runtime output that no one else reads. An unsent
question is an unasked question. Never report that you raised, asked, or discussed a point
that exists only in your own output.

## Ticket-owned artifacts

A ticket can own durable work products such as HTML, images, Markdown documents, and other files. For this live Panels instance, write them to `/home/vps/Deployments/Panels/current/data/files/tickets/<ticket-id>/<relative-path>` and link them in ticket Markdown as `/files/tickets/<ticket-id>/<relative-path>`, for example `[UI plan](/files/tickets/<ticket-id>/artifacts/ui-plan.html)`. Never write ticket artifacts under a source checkout's `data/...`, a ticket worktree's `data/...`, or another path inferred from the current directory. Use the served `/files/tickets/...` link rather than exposing a local filesystem path; Panels owns how the file is previewed or opened.

Artifacts complement the ticket record. The Worker type's gated fields, recap, and notes
remain concise canonical Markdown, with links to richer work when it helps.

## Continuation and schedules

Ordinary Workers follow existing continuation rules without Today membership. Keep
ceilings, approvals, blockers, stage ownership, current claims, busy checks, and context
recovery rules. Do not invent another activation gate or retire ordinary unfinished work.

Useful ordinary daily schedules remain. They use local calendar dates, preserve durable
occurrence receipts, and do not create Days. Old planning schedules are retired without
replacement. Historical proposals remain readable but cancelled Workers cannot receive
actionable approvals.

## The CLI

Everything runs through the `panels` command — run `panels --help` to see what it can do. It talks to the Panels server and database.

Before creating any Ticket, load and follow **`panels-ticket-creation`**. It owns the
shared creation model; the role-specific skill that sent you there still owns whether
creation is authorized and what follow-up its workflow requires.

Ordinary help exposes these object and intent roots:

- `panels send-message ...` for existing conversations.
- `panels project ...` for Projects.
- `panels day show <date>` for a historical Day.
- `panels ticket ...` for Ticket creation, inspection, edits, proposals, and lifecycle actions.
- `panels orchestrator ...` for ordinary orchestrators.
- `panels sprint ...` for historical reads and the stable `sprint item` alias.

The server derives the caller from runtime context and enforces authority at each route.
Administrative commands remain callable when a specialist skill names one, but they do
not appear in ordinary root help.

The main list reads are bounded summaries. `ticket list`, `sprint list`, `sprint item
list`, and `project list` return 30 rows by default. Their text and
JSON output state the omissions and the next offset. Use `--limit` and `--offset` for
another page. Ticket lists exclude terminal Tickets by default. Use repeatable Stage and
`ticket_status` filters, exclusions, `--include-terminal`, and `--search` to narrow the
result before you increase its limit.

## Skills

- **`panels-sprint-item-supervisor`** — supervising one Sprint Item through the ordinary
  commands, which the one authority rule admits on its current child Tickets, plus
  messages to their Worker conversations.
- **`panels-ticket-creation`** — the shared model for creating a coherent Ticket.
- **`panels-worker`** — working a single ticket: shaping it through its stages, executing it, and reviewing it.
