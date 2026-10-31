# Headroom

Headroom is the workspace where Khushal directs agents, reviews what they propose, and keeps the work that is actually committed. The app and the `panels` command still carry the old Panels name. These pages describe the product as it runs today.

Read the page that owns the question. Each focused page says what the thing is, how it works, what it takes in and sends on, and where a person meets it. Code pointers sit at the end.

The word list for Ticket, Stage, Review, Conversation, and the rest lives in [`CONTEXT.md`](../CONTEXT.md). Standing engineering rules live in [`PRINCIPLES.md`](../PRINCIPLES.md). The visual rules live in [`DESIGN.md`](../DESIGN.md). Historical ticket plans live under `orchestration/tickets/` and are not this manual.

## Start here

- [The product](product.md) — what Headroom is for, and how a piece of work moves through it.
- [Decisions that must stay put](invariants.md) — the rules a later change has to respect on purpose.

## The work

- [Tickets](tickets.md) — one bounded piece of work, its stages, and where it can run.
- [Proposals and gates](proposals.md) — the single door that settles a stage and the ceiling that decides who must look.
- [Proposal pages](proposal-pages.md) — a stage blank filed as a page, on the same kit a goal page uses.
- [Worker types](worker-types.md) — the workflows Headroom ships, and where each one pulls a person in.
- [Worker steps](worker-steps.md) — how a ready Ticket receives its next prompt, and why nothing watches the turn end.
- [Schedules](schedules.md) — exact-time supply of ordinary Tickets.
- [Skills](skills.md) — the instructions an agent actually reads, and who may change them.

## People, attention, and screens

- [Managers and Chief](managers.md) — shared direction, child Tickets, and the conversations that supervise them.
- [Review](review.md) — the pass through everything waiting on Khushal.
- [Ideas](ideas.md) — possibilities kept before they become work.
- [Goals](goals.md) — Chief goal pages, explicit displays, checklists, charts, and independent completion and active states.
- [Project measures](measures.md) — dated values, retained datasets, sources, credentials, and collection.
- [Projects](projects.md) — the catalog that classifies work and points at a folder.
- [Feedback](feedback.md) — notes captured from any page for an agent to handle.
- [Design](design.md) — the quiet visual system and the shared page kit.
- [Live screens](live-updates.md) — why an open screen moves after a write, and what does not.
- [Usage](usage.md) — allowance, recorded spend, and backend setup.
- [Notifications](notifications.md) — which edges can reach a phone, and how a device is enabled.
- [Who may act](authority.md) — one sentence for every guarded call.

## Talking to an agent

- [Conversations](conversations.md) — the only path into an agent, the notebook, and honest send fates.
- [The command line](cli.md) — how people and Workers act through `panels`.

## Operating a host

- [Run locally](running-locally.md) — one checkout, one loopback server, one database.
- [Environments](environments.md) — live, staging, and Ticket servers on one user account.
- [Deployment](deployment.md) — how a push to `main` becomes a retained release, and how that release replaces the app.
- [Updates](updates.md) — the owner’s now-or-idle install, and what a lost runner does not do.
- [Archive runtime](archive-runtime.md) — the private Python the sealed release carries, and the host it accepts.
- [Backups](backups.md) — verified snapshots and the stopped restore.
- [Previews](previews.md) — one tailnet link to a Ticket’s own dev server.
- [VPS](vps.md) — machine measurements, and the smaller status summary.
- [Maintenance](maintenance.md) — diagnostic history, which waits for an explicit owner decision.

_Last verified: 2026-10-31._
