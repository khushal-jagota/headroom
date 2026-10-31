# The product

Headroom is an agent harness whose job is to reduce mental load. The agents run on one host. Khushal uses a browser, including from a phone, to give work, answer questions, read results, and approve the points he chose to keep. The priorities, plans, conversations, and approvals stay together so attention goes to deciding what is worth doing.

The product was formerly called Panels. The command is still `panels`, deployment paths still say Panels, and a few on-screen sentences still say Panels (the home-screen notification hint is one). Those names are the installation. Headroom is the product.

## Why it is shaped this way

Different work needs different attention. A coding task can run a long way and stop for a reviewable package. A research task can work a framed question. An exploration can pause to think with the person. A design task can bring a wireframe and a visual pass before any implementation Ticket exists. Headroom keeps each of those as a Ticket with its own stages, and it keeps the person’s involvement as an explicit boundary on that Ticket: the ceiling.

The record of the work lives on the host, in one SQLite database. Codex, Claude Code, and Hermes are the agents Headroom can run. They use the logins already on that host, and their providers’ limits and billing still apply. Headroom does not replace those subscriptions and does not sandbox the machine. A Ticket gate is a workflow boundary. It says how far this piece of work may go before a person looks. It is not an operating-system permission.

## A piece of work, from first thought to done

```text
Idea (optional) ──► Ticket under Chief or a Manager
                         │
                         ├─ Brief, then the stages of its Worker type
                         ├─ each stage fills one blank
                         │     below the ceiling: it settles and advances
                         │     at the ceiling: it parks for a person
                         ├─ the Worker’s only input is its conversation
                         └─ Consequences, then Done

Khushal meets that work in Review, on the Ticket, and in the conversation.
A phone notification is a separate edge when something newly needs him.
```

An Idea is only a remembered possibility. It does not become a Ticket by itself. A Ticket is the committed unit: one Worker type, one stage ladder, one active conversation, one assignment to Chief or an ordinary Manager.

The Worker proposes the text of the current stage. The proposal resolver is the only writer that can make that text canonical and move the stage. Below the ceiling the proposal settles on its own. At the ceiling it waits. Review is the owner’s queue for those waits, together with messages that need him. A proposal addressed to Chief or a Manager is delivered into that supervisor’s conversation as a real prompt. Approving still happens through the resolver. A reply in chat is not an approval.

When the stage is Consequences, the applicable merge, deploy, follow-up, and bookkeeping happen, and the Worker proposes a verified report. Done is the end of the ladder. Backlog is a separate pause: it stops the next automatic Worker step and leaves the Ticket, its messages, and its approvals in place.

## What feeds the day, and what the day feeds

Khushal, Chief, and Managers create Tickets and send messages. A daily schedule can create an ordinary Ticket at one local minute; the schedule stops there, and the same readiness loop that starts every other Ticket sends the first prompt. Worker steps, approvals, and messages all land in conversations. Settled stage text, recaps, artifacts, and Review state are what the screens read back.

The browser is the main human surface. Workspace is home: Chief, Managers, and the Ticket you are inspecting. Review is the attention pass. Usage, Ideas, Feedback, VPS, and Settings are the other destinations. The `panels` command is the same record through HTTP, used by Khushal and by the Workers themselves. A Worker files proposals and sends messages. It does not approve its own gate.

## Where it shows up

Add the site to a phone’s home screen. Dictate into a conversation or a plan. A notification arrives when a chosen kind of attention flips on, which needs HTTPS, and on iPhone needs the installed home-screen app. Open a plan, document, image, or HTML prototype beside the conversation and tell the agent what to change without losing the task. For coding work, a trusted local dev server can be opened through Headroom on the tailnet. See [Previews](previews.md).

The host has to stay running and reachable. One machine holds the database. Agents still call their model providers from that machine.

## Pointers

Product copy for a new checkout is the repository [README](../README.md). Domain code lives under `src/planner/`, grouped by Tickets, Managers, Ideas, Projects, conversation, and runtime. The browser app is `web/`. The server entry is `panels serve`.

_Last verified: 2026-10-30._
