# The product

Headroom is an agent harness whose job is to reduce Khushal's mental load. Agents do the work. Khushal gives work, answers questions, reads results, and approves only the points he chose to keep, from a browser on a laptop or a phone. Priorities, conversations, plans, and approvals sit in one place, so his attention goes to deciding what is worth doing. The installation and its command still carry the old name, Panels.

## Who is in it

- **Khushal**, the owner. He stands above everything.
- **Chief**, his top-level agent. It also stands above everything, creates and routes work, and sets [goals](goals.md).
- **Managers**, agents that each hold one direction and the Tickets that serve it. See [Managers and Chief](managers.md).
- **Workers**, one agent per Ticket, doing that Ticket's work.
- **Goal agents**, one per goal, keeping its page current.

Every agent is a [conversation](conversations.md) running Codex, Claude Code, or Hermes. What may act on what is one rule: you may act on anything strictly below you. See [Who may act](authority.md).

## A piece of work, from first thought to done

```text
Idea (optional) ──► Ticket under Chief or a Manager
                         │
                         ├─ Brief, then the stages of its Worker type
                         ├─ the Worker proposes the text of each stage
                         │     below the ceiling: it settles and advances
                         │     at the ceiling: it waits for a person
                         ├─ the Worker's only input is its conversation
                         └─ Consequences, then Done

Waits addressed to Khushal reach him in Review.
Waits addressed to Chief or a Manager arrive in that agent's conversation.
```

An [Idea](ideas.md) is a remembered possibility. It never becomes work by itself. A [Ticket](tickets.md) is the committed unit: one piece of work for one Worker, with a ladder of stages that its [Worker type](worker-types.md) declares. People, Chief, and Managers create Tickets.

When a Ticket is ready, the [worker-step loop](worker-steps.md) sends the Worker one prompt and stops watching. The Worker answers by filing a proposal for the current stage. The Ticket's ceiling decides whether that proposal settles on its own or waits for a person. [Review](review.md) is Khushal's queue of the waits addressed to him, together with messages that need him. Consequences is where merges, follow-up Tickets, and a verified report happen. Installing a release stays Khushal's action. Done ends the ladder.

## Where it runs

One host, the VPS, holds the SQLite database, the server, and the loops that start work. Most agents run there too, using the logins already on that machine, and their providers' limits and billing still apply. A Ticket can instead be pinned to Khushal's Mac, where its agent runs while the VPS keeps the record. See [Mac execution](executor.md).

The server answers every request on one event loop, so work there holds up every request. A request's database work, and that of a worker step the readiness loop starts, therefore runs on a worker thread. A write that waits on the database lock, or a long read, then delays only its own request or step. Live conversation state stays on the loop, because it belongs to the loop.

The browser is the main surface: the Workspace with Chief, Managers, and Tickets; Review; and the pages for goals, ideas, measures, and settings. The `panels` command reaches the same records over HTTP, for Khushal and for the agents themselves. A phone [notification](notifications.md) arrives when something newly needs him.

## Decisions that shape it

- **The person's involvement is an explicit setting on each Ticket.** The ceiling says how far the work may go before someone looks, so different kinds of work stop at different points. See [Ticket lifecycle](tickets.md).
- **Gates are workflow, not a sandbox.** They govern the work record. They do not confine an agent's process, files, network, or credentials.
- **Headroom uses existing subscriptions.** It runs the agents' own command-line tools under the logins already on the machine. It does not replace them.

The cross-cutting rules are listed in [Decisions that must stay put](invariants.md).

## Code

- `src/planner/`: the server, grouped by system (`tickets/`, `managers/`, `runtime/`, `conversation/`, and others). The server starts with `panels serve`.
- `web/`: the browser app.
- `src/planner/cli/`: the `panels` command.

_Last verified: 2026-11-04._
