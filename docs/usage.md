# Usage

Usage is where the owner sees what the agents on this host can still spend, and what they have already spent, without opening each provider's own dashboard. It also sets up the backends those agents run on. Headroom does not bill anyone. Codex, Claude and Antigravity use the logins on the host, and their plan limits and invoices stay with those providers.

## What it is made of

Three tabs: Limits, Tokens, and Cost.

- **Limits** is allowance: how much each provider says is left.
- **Tokens and Cost** are recorded spend: what the agents have used.

The two numbers come from different places and do not mix. Recorded usage comes from every machine. Each machine scans its own transcripts and sends the server only its totals, so raw transcripts never leave the machine. The browser reads each machine's summary and merges them into one set of totals and charts.

```
 provider login ──► Limits (saved observation, refreshed on request)
 home transcripts ──► home summary ───────────────┐
 joined machine transcripts ─► machine summary ───┴─► browser merge ─► totals and charts
```

## Allowance and spend

Limits belong to the provider account, not the machine. Opening Limits reads the last observation Headroom saved on every machine for each backend that reports allowance. It calls no provider and scans nothing, and an old observation keeps its timestamp. Each observation keeps the account, by email, that the machine's backend card named when the observation was read. A later sign-in to another account does not relabel it. Antigravity's email comes from its CLI login file. The page merges the observations that name one account into one entry: it shows the freshest reading and lists the machines where that account is signed in. Different accounts of one provider show as separate segments of its bar. An observation without an email stays its machine's own. Refresh is the explicit provider read on every available machine at once. An unavailable machine is not asked: it keeps its last observation, and the page says so. It starts no agent turn, and a failed refresh keeps the last good observation. Panels also saves the latest refresh that did not succeed for each backend and machine, with its time, until the next success clears it. Limits shows it as one plain line, such as "Antigravity on Mac 1: not signed in", next to the last good figures and their age, so a failing provider never looks like one with no limits. Each backend refreshes on its own, so one backend that fails does not stop the others. Failed, signed out, and unavailable stay different outcomes, and an unconfirmed sign-in is shown as unknown. Each backend's driver supplies its own allowance reading. Each backend's name and chart colour come from its driver. Each window carries the provider's own id and a label, as in T3 Code. The reset time is optional: a bucket the provider names no reset for shows no countdown. A window can name a model or a model family: Antigravity's 5-hour and weekly windows come from Google's quota service, one set per family, such as Gemini, or Claude and GPT. Panels saves the windows as the provider reports them. Model pickers match a named window to every model of that family when they show it, so a Fable window shows on each Fable model.

Tokens and Cost are totalled from the transcripts of Codex, Claude, Antigravity and Grok Build on each machine, including sessions never started in Headroom, with prices refreshed separately. Each backend's driver names where its transcripts live and the parser that reads them, so each machine reads them without running an agent. Antigravity's parser reads its local session databases. That path never asks a provider for allowance. Grok Build appears in the scan but is a usage source, not a driver or a conversation backend, so its name and colour are kept with the usage page. See [Conversations](conversations.md).

Tokens and Cost read a summary when the selected window first opens. Refresh reads it again. Background changes do not trigger transcript scans. Provider filters use the loaded summary.

The home summary is read on the server. A joined machine's summary comes through that machine's controls, as a limits refresh does, and the machine prices its own records. The merge follows T3 Code: a transcript folder that two machines report with the same host, path and volume counts once. A machine that is offline, slow, or on older machine code does not hold the page. Its totals are left out, and a notice names it. The Sources list names the machine of each folder when more than one machine contributes.

Costs use each request’s token counts and model prices before the server combines totals. Long prompts use the applicable higher rates. A model missing from the price table shows as unpriced. Claude cache writes use their reported cache duration.

Codex totals also include automatic compactions from matched response records. Copies count once, and compactions that normal usage events already cover add no extra spend. A scan cache upgrade causes the next summary read to rebuild historical totals from local transcripts.

## Backend setup

Backend cards describe the tools installed on the selected machine. Backend settings offer a machine choice. Ticket cards use the machine assigned to the Ticket. Sign-in and allowance refresh run there, and provider credentials stay there. The server keeps allowance observations separately for each machine and backend. An unavailable machine retains its own last observation and timestamp, and its actions report unavailable. Settings → Backends lists, in the server's order, each backend whose versions Panels checks (today Codex, Claude, then Antigravity), and any other backend Panels can sign in. A backend whose driver can sign in from Panels (today, Codex and Antigravity) offers sign-in there when its account is signed out or a sign-in is under way; a signed-in account shows no sign-in. Codex sign-in shows a code and link. Antigravity's opens Google's page and takes the address Google returns to in a paste box, so it works from any device. Claude cannot be signed in from Panels. When its login is missing, its row shows `claude auth login` to copy, to run in the owner's own terminal on that machine (over SSH if it has no screen), and a Check again button. A Codex login that Panels cannot confirm, such as an expired token that no turn has refreshed, offers a secondary Re-sign in button. Panels finds each CLI on the service PATH first. If the PATH misses, it looks for a file of that name, owned by the running account, in the usual folders of nvm, fnm, volta, mise, bun, pnpm, a `prefix=` in `~/.npmrc`, and `~/.npm-global`. It never runs a login shell. On macOS and for a joined machine, `PLAN_CLAUDE_EXECUTABLE` and `PLAN_ANTIGRAVITY_EXECUTABLE` in the machine service still name a CLI anywhere else. A new macOS home install leaves `CLAUDE_CONFIG_DIR` unset for Claude, so Claude keeps reading the login saved in the Keychain. An existing install keeps the value it already has until it is installed again. With isolated providers, Claude logs in to the provider folder of that install, so run `claude auth login` with `CLAUDE_CONFIG_DIR` set to that folder. Antigravity on a headless Linux machine needs `agy` to save its login in a Secret Service keyring (for example `gnome-keyring` or `pass-secret-service`). Panels gives `agy` only `HOME`, `LOGNAME`, `PATH`, `SHELL`, `TERM` and `USER`. A keyring session address such as `DBUS_SESSION_BUS_ADDRESS`, and `AGY_ADC_AUTH=true`, do not reach `agy` through the service environment today. Treat headless Antigravity sign-in as open work until Panels passes them. Claude supplies its model list through its CLI, and Headroom keeps no Claude model table; Antigravity's models and thinking levels come from agy's own lists. Each machine reads its own lists. See [Executor](executor.md). Panels saves each complete list it reads, one for each machine, and model checks read the saved list. Until a machine has a complete saved list for a backend, its models are not checked against it, and the backend decides.

Model pickers read saved models and capabilities for the selected machine. A warm read calls no machine and writes no observation. Missing facts, explicit Refresh, and changes to the machine or provider trigger discovery. Reconnection refreshes the machine facts. The server saves changed capability values and keeps each machine separate.

The catalog also lets models be enabled or disabled. A disabled model is refused wherever a Ticket or message would use it. When a model leaves every saved complete list, its switch-off is deleted, because the model is refused as not offered. [Conversations](conversations.md) describes the picker and those refusals.

## Where it shows up

The Usage label in the shell, at `#/backends`, and the same page inside the Workspace.

## Code

`server/conversation/usage/` and `web/src/routes/BackendsRoute.svelte`. Settings and sign-in are `web/src/components/settings/BackendSettings.svelte` and `web/src/components/conversation/BackendSignInBlock.svelte`, with the session in `web/src/lib/conversation/backendSignInSession.ts`.

_Last verified: 2026-11-26._
