# Usage

Usage is where Khushal sees what the agents on this host can still spend, and what they have already spent, without opening each provider’s own dashboard. It is also a second door into the same Config editor as Settings, so model availability and skill text sit next to the limits they affect.

Headroom does not bill anyone. Codex and Claude use the logins on the host. Their plan limits and invoices stay with those providers. The numbers here are readings Headroom was able to take from those logins and from local transcripts.

## Allowance and recorded spend

The Limits tab shows the last provider observations Headroom has saved for Codex and Claude. Opening the tab reads those saved observations. It does not scan transcripts, price anything, or call the provider. A timestamp on an old observation stays visible. A missing observation does not get an invented time. Hermes has no allowance source on this screen.

Refresh on Limits is the explicit provider read. It does not start an agent turn and does not spend a Ticket. A failed refresh keeps the previous successful observation. Failed, signed-out, and unavailable stay different outcomes. A logged-out backend shows how to sign in rather than a stale ring. If a turn is running, a Codex sign-in result that cannot be confirmed stays unknown. Unknown is neither signed out nor plenty left. Subscription reset credits appear only when the provider observation actually has that field.

Tokens and Cost are a different number. Their Refresh updates prices and the totals from local Codex, Claude, and Grok Build transcripts on the server, including sessions that were not started inside Headroom, for 24 hours, 7 days, 30 days, and 90 days. That refresh does not ask the providers for allowance. Grok Build appears in the transcript scan. It is not a conversation backend. The conversation backends remain Hermes, Codex, and Claude. See [Conversations](conversations.md).

Codex can be signed in from the screen. Claude and Hermes keep their own login commands. The backend cards read this host's installed tools through the same snapshot service
used on the Mac. Claude supplies its models through the CLI handshake; Panels keeps
no written Claude model table. Ticket composers use their Ticket's machine, with the
Mac's last list retained on the VPS while it is offline. See [Executor](executor.md).
Models can be enabled or disabled in the catalog the composers use. A disabled model is blocked in the picker, at Ticket creation, when saving a Ticket configuration and when dispatching a message. Automatic Worker steps show a blocker until that model is enabled or the choice changes.

## What feeds the page

Provider responses feed allowance. Transcript files on the host feed recorded usage. The managed backend catalog feeds which models can be chosen. Config edits on this page are the same skill and Worker-type writes as Settings. They feed future conversations and future Tickets, not the chart behind them.

## Where it shows up

The shell label is Usage. The address is `#/backends`, with Tokens, Cost, Limits, and Config. The same page can sit inside the Workspace at `#/workspace/utility/usage`. Composer menus show the model and effort in force for the conversation you are in. They are the local consequence of this catalog, described in [Conversations](conversations.md).

## Pointers

`src/planner/conversation/usage/` and `web/src/routes/BackendsRoute.svelte`. Codex sign-in from the UI lives under `web/src/lib/conversation/backendSignIn.ts`.

_Last verified: 2026-10-31._
