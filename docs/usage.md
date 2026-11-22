# Usage

Usage is where Khushal sees what the agents on this host can still spend, and what they have already spent, without opening each provider's own dashboard. It also sets up the backends those agents run on. Headroom does not bill anyone. Codex, Claude and Antigravity use the logins on the host, and their plan limits and invoices stay with those providers.

## What it is made of

Three tabs: Limits, Tokens, and Cost.

- **Limits** is allowance: how much each provider says is left.
- **Tokens and Cost** are recorded spend: what the agents have used.

The two numbers come from different places and do not mix. Recorded usage comes from one local server. The browser calculates its totals and charts directly from that server's summary.

```
 provider login ──► Limits (saved observation, refreshed on request)
 local transcripts ─► server summary ─► browser totals and charts
```

## Allowance and spend

Opening Limits reads the last observation Headroom saved for each backend that reports allowance. It calls no provider and scans nothing, and an old observation keeps its timestamp. Refresh is the explicit provider read. It starts no agent turn, and a failed refresh keeps the last good observation. Failed, signed out, and unavailable stay different outcomes, and an unconfirmed sign-in is shown as unknown. Each backend's driver supplies its own allowance reading. Each backend's name and chart colour come from its driver. A window can carry a scope label in place of a model: Antigravity's 5-hour and weekly windows come from Google's quota service, one set per model family, such as Gemini, Claude and GPT, or Gemini 4 Argon.

Tokens and Cost are totalled from the transcripts of Codex, Claude, Antigravity and Grok Build on this host, including sessions never started in Headroom, with prices refreshed separately. Each backend's driver names the parser that reads its transcripts; Antigravity's reads its local session databases. That path never asks a provider for allowance. Grok Build appears in the scan but is a usage source, not a driver or a conversation backend, so its name and colour are kept with the usage page. See [Conversations](conversations.md).

Tokens and Cost read a summary when the selected window first opens. Refresh reads it again. Background changes do not trigger transcript scans. Provider filters use the loaded summary.

Costs use each request’s token counts and model prices before the server combines totals. Long prompts use the applicable higher rates. A model missing from the price table shows as unpriced. Claude cache writes use their reported cache duration.

Codex totals also include automatic compactions from matched response records. Copies count once, and compactions that normal usage events already cover add no extra spend. A scan cache upgrade causes the next summary read to rebuild historical totals from local transcripts.

## Backend setup

Backend cards read the tools installed on this host. Settings → Backends lists, in the server's order, each backend whose versions Panels checks (today Codex, Claude, then Antigravity), and any other backend Panels can sign in. A backend whose driver can sign in from Panels (today, Codex and Antigravity) offers sign-in there when its account is signed out or a sign-in is under way; a signed-in account shows no sign-in. Codex sign-in shows a code and link. Antigravity's opens Google's page and takes the address Google returns to in a paste box, so it works from any device. Claude keeps its own login command. Claude supplies its model list through its CLI, and Headroom keeps no Claude model table; Antigravity's models and thinking levels come from agy's own lists. Each machine reads its own lists. See [Executor](executor.md). Panels saves each complete list it reads, one for each machine, and model checks read the saved list. Until a machine has a complete saved list for a backend, its models are not checked against it, and the backend decides.

The catalog also lets models be enabled or disabled. A disabled model is refused wherever a Ticket or message would use it. When a model leaves every saved complete list, its switch-off is deleted, because the model is refused as not offered. [Conversations](conversations.md) describes the picker and those refusals.

## Where it shows up

The Usage label in the shell, at `#/backends`, and the same page inside the Workspace.

## Code

`server/conversation/usage/` and `web/src/routes/BackendsRoute.svelte`. Settings and sign-in are `web/src/components/settings/BackendSettings.svelte` and `web/src/components/conversation/BackendSignInBlock.svelte`, with the session in `web/src/lib/conversation/backendSignInSession.ts`.

_Last verified: 2026-11-18._
