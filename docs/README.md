# Headroom

Headroom is the workspace where Khushal directs agents, reviews what they propose, and keeps the work that is actually committed. The app and the `panels` command still carry the old Panels name.

These pages are a top-level view of each system: what it is for, how it works, and roughly how. Read the page that owns your question, then go to the code for the details. [`AGENTS.md`](AGENTS.md) holds the rules for editing them.

The word list lives in [`CONTEXT.md`](../CONTEXT.md), the engineering rules in [`PRINCIPLES.md`](../PRINCIPLES.md), and the visual rules in [`DESIGN.md`](../DESIGN.md). Step-by-step procedures live under [`ops/`](../ops/). Historical ticket plans live under `orchestration/tickets/` and are not this manual.

## Start here

- [The product](product.md) — what Headroom is for, and how a piece of work moves through it.
- [Decisions that must stay put](invariants.md) — the cross-cutting rules a later change has to respect on purpose.

## The work

- [Ticket lifecycle](tickets.md) — one bounded piece of work, its stages, proposals, and the ceiling that decides who must look.
- [Worker types](worker-types.md) — the workflows Headroom ships, and where each one pulls a person in.
- [Worker steps](worker-steps.md) — how a ready Ticket receives its next prompt.
- [Schedules](schedules.md) — Tickets created at an exact time.
- [Skills](skills.md) — the instructions an agent actually reads, and who may change them.

## People, attention, and screens

- [Managers and Chief](managers.md) — shared direction, child Tickets, and the conversations that supervise them.
- [Review](review.md) — the pass through everything waiting on Khushal.
- [Goals](goals.md) — outcomes with a deadline, their pages, and whether they were hit.
- [Measures](measures.md) — dated numbers, the sources that collect them, and datasets.
- [Historical attention](attention.md) — where Khushal's recorded involvement went between two dates.
- [Ideas](ideas.md) — possibilities kept before they become work.
- [Projects](projects.md) — the catalog that classifies work and points at a folder.
- [Feedback](feedback.md) — notes captured from any page for an agent to handle.
- [Design](design.md) — how the visual principles show up, and the shared page kit.
- [Live screens](live-updates.md) — why an open screen moves after a write.
- [Usage](usage.md) — allowance, recorded spend, and backend setup.
- [Notifications](notifications.md) — which events can reach a phone.
- [Native iOS compatibility](ios-compatibility.md) — how the server refuses unsupported app builds.
- [Who may act](authority.md) — the rule behind every guarded call.

## Talking to an agent

- [Conversations](conversations.md) — the only path into an agent, its notebook, and honest send fates.
- [Credentials](credentials.md) — named API keys that scripts use without a secret reaching an agent.
- [Mac execution](executor.md) — Tickets whose agents run on the Mac while the VPS keeps the record.
- [The command line](cli.md) — how people and Workers act through `panels`.

## Operating a host

- [Environments](environments.md) — live, staging, Ticket, and local servers on one user account.
- [Deployment](deployment.md) — how a push to `main` becomes a sealed release, and how the owner installs it.
- [Backups](backups.md) — verified snapshots and the stopped restore.
- [Previews](previews.md) — one tailnet link to a Ticket's own dev server.
- [VPS](vps.md) — machine measurements and the status summary.
- [Diagnostic history](diagnostic-history.md) — the event record, kept until the owner decides to expire it.

_Last verified: 2026-11-04._
