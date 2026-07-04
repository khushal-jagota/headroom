# The Planner — what it is and how it works

This document is written as the system is built, stage by stage. It is for a smart reader who does not write code.

## What the planner is

The planner is a personal planning system that runs entirely on one computer. It replaces a folder of markdown files with a small database and a web page. It keeps track of four kinds of things:

- **Sprints** — two-week pushes with a written kickoff (why this sprint, what the bet is) and a written review at the end.
- **Sprint items** — the meaningful chunks of work a sprint is made of.
- **Tickets** — individual pieces of work small enough to hand to an AI agent.
- **Days** — one page per day: a morning brief, a plan for the day, and the day's ticket list.

There is also a simple list of **ideas** — things worth remembering that aren't work yet.

The human uses a web page. AI agents use a command-line tool. That split is deliberate: every decision that matters — approving work, granting permissions, closing things out — exists only in the web page, so an agent cannot take a decision that belongs to the human.

## The one rule everything follows

Agents never change the record directly. An agent that wants to move work forward files a **proposal**. A piece of code called the resolution engine is the only thing that can turn a proposal into a real value or move a ticket to its next stage. Some proposals are approved automatically (when the human has granted room in advance), and everything else waits for the human. Every change, by anyone, is written down in an append-only event log — nothing is ever edited in place or deleted.

## How far an agent may go: the ceiling and the at-cap rule

Every ticket carries a permission with two parts:

- **The ceiling** — how far along its pipeline agents may push this ticket on their own.
- **At the cap** — what agents may do once the ticket reaches that ceiling: either **stop** (don't even suggest anything) or **propose** (draft the next step and park it for approval).

Below the ceiling, agent proposals are accepted automatically and the ticket advances. At the ceiling, the at-cap rule decides. And whenever the human approves a step, they must say in the same breath how far the agent may go next — the system refuses an approval that doesn't answer that question. New tickets start with the tightest sensible grant: the agent may draft a success condition, and nothing moves without approval.

## A ticket's life

A ticket moves through fixed stages: first it needs a **success condition** (what does done mean?), then an **approach** (how, roughly?), then a **plan** (concretely, step by step), then the work happens (**in progress**), then the result waits for checking (**needs review**), and finally it is **done**. A ticket can also be **dropped** at any point — only by the human. Each stage has exactly one blank to fill; filling it (and having that accepted) is what moves the ticket one stage forward. The human can always jump a ticket anywhere; agents never can.

## Status of the build

- **Stage 1 — contracts (done):** the skeleton exists: the database's table definitions, the exact vocabulary (every state, status, priority, and permission value), the shapes of every stored object, the configuration file with every tunable number, the interfaces where outside services plug in (agent spawning, the morning-brief writer, chat), and the full list of web addresses and command-line verbs the finished system will answer to. None of the behavior exists yet — this stage fixes the shapes so everything built later has to agree with everything else.
- **Stage 2 — the measuring stick (done):** one command, `./verify`, runs every check the specification demands — code style, type checking, the unit tests, a build check, and the browser tests — and prints a scoreboard of all 36 acceptance items ending `VERIFY: N/36 PASS`. It was built before the implementation on purpose: right now it honestly reports 0/36, and the rest of the build is the work of turning each line green. It also refuses to count tests that have been switched off or hollowed out — it scans for that and fails loudly.
- Stages 3–7 (the behavior, the server, the screens, the browser tests, and the live dogfood) are documented below as they complete.
