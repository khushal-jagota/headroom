# Usage

Usage is where Khushal sees what the agents on this host can still spend, and what they have already spent, without opening each provider's own dashboard. It also sets up the backends those agents run on. Headroom does not bill anyone. Codex and Claude use the logins on the host, and their plan limits and invoices stay with those providers.

## What it is made of

Four tabs: Limits, Tokens, Cost, and Config.

- **Limits** is allowance: how much each provider says is left.
- **Tokens and Cost** are recorded spend: what the agents have used.
- **Config** is the same editor as Settings, so model availability and skill text sit next to the limits they affect. [Skills](skills.md) owns the editor.

The two numbers come from different places and do not mix. Recorded usage comes from one local server. The browser calculates its totals and charts directly from that server's summary.

```
 provider login ──► Limits (saved observation, refreshed on request)
 local transcripts ─► server summary ─► browser totals and charts
```

## Allowance and spend

Opening Limits reads the last observation Headroom saved for Codex and Claude. It calls no provider and scans nothing, and an old observation keeps its timestamp. Refresh is the explicit provider read. It starts no agent turn, and a failed refresh keeps the last good observation. Failed, signed out, and unavailable stay different outcomes, and an unconfirmed sign-in is shown as unknown. Hermes has no allowance source here.

Tokens and Cost are totalled from the transcripts of Codex, Claude, and Grok Build on this host, including sessions never started in Headroom, with prices refreshed separately. That path never asks a provider for allowance. Grok Build appears in the scan but is not a conversation backend. See [Conversations](conversations.md).

## Backend setup

Backend cards read the tools installed on this host. Codex can sign in from the screen, while Claude and Hermes keep their own login commands. Claude supplies its model list through its CLI, and Headroom keeps no Claude model table. Each machine reads its own lists. See [Executor](executor.md).

The catalog also lets models be enabled or disabled. A disabled model is refused wherever a Ticket or message would use it. [Conversations](conversations.md) describes the picker and those refusals.

## Where it shows up

The Usage label in the shell, at `#/backends`, and the same page inside the Workspace.

## Code

`src/planner/conversation/usage/` and `web/src/routes/BackendsRoute.svelte`, with sign-in in `web/src/lib/conversation/backendSignIn.ts`.

_Last verified: 2026-11-04._
