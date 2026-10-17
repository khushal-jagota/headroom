# Backlog & Ideas

Workers and Ideas are separate views. A Worker is committed work. An Idea preserves
a possibility. Explicit Ticket backlog defers automatic work without changing assignment.

```
Backlogged Ticket ──► resume ──► ordinary readiness and approvals
Idea: remembered possibility
```

## Workers and explicit backlog

The standalone Workers page is removed. Its former `#/backlog` address redirects to
Workspace, including old project-filtered links. There is no global project/type list
or browser New Ticket form. API and CLI creation remain available. A request to Chief
still follows the ordinary Ticket workflow.

Chief shows only its own children beside its conversation. Ordinary orchestrators retain
their own children, including settled work. Scoped filters provide Active, Backlogged,
Done, and Cancelled views. Chief results remain bounded and paginated. Each row
opens the existing individual Ticket. Review and direct-message discovery remain separate.
The Cancelled view reads existing terminal history, with retained proposals, provenance,
conversations, and artifacts. It does not offer an action to cancel current work.

Backlog ticket on the Ticket page prevents automatic progression. Resume ticket restores
ordinary readiness checks. Messages, proposals, approvals, assignment, history, and
artifacts remain available. A delivered turn continues. Parent Settle remains independent.
Backlog no longer means an unscheduled Ticket or an assignment change.

## Ideas

The opposite: its whole job is to catch a thought before you lose it, so the box to
write one is always open at the very top and is the biggest thing on the page. Type a
title and press Enter and it is saved; a longer note and a project are optional.
The project list is the same data-backed catalog used by Tickets, Sprint Items, and
ideas.
Below, the ideas are listed newest first. An idea with no note is one line. An idea
with a note gets a small arrow that opens its detail.

_Code paths:_ `web/src/components/ChiefConversation.svelte`,
`web/src/components/SprintItemWorkspace.svelte`, `web/src/routes/IdeasRoute.svelte`. Tickets live in `src/planner/tickets/`. Sprint Items hold shared context independently of Sprint commitments. Sprint Items and ideas
live in `src/planner/sprints/`.

## Handoffs

- **Feedback** (`feedback.md`) — the loose capture path available from every page.
- **Days** (`days.md`) — historical Day records.
- **Orchestrators** (`sprints.md`) — assignment and independent settlement.
- **Projects** (`projects.md`) — where the project list comes from.

## Deferred

- **No idea → backlog conversion, and no archive.** An idea can't be promoted into a
  backlog item or filed away; that was left out on purpose for now. Trigger: a product
  decision that ideas should convert or archive in place.

---

_Last verified: 2026-10-16._
