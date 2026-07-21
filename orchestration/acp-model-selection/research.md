# ACP model and reasoning selection research

Date: 2026-07-21

## Question

What model and reasoning controls can Panels offer at Ticket Kickoff for Hermes,
Codex, and Claude Code, and where should their available values come from?

This began as research. The owner decisions reached from it are recorded below.

## Settled owner direction

- The Kickoff section shows separate **Worker**, **Model**, and **Reasoning** controls
  beside the approval flow. They are not Ticket-header controls or ongoing
  conversation controls. A combined Setup/preset control is deferred.
- Each Worker type supplies the default Worker, model, and reasoning selection for a
  new Ticket. Those values seed the Ticket exactly once and can then be edited
  independently before Kickoff starts. There is no reset-to-default action and no
  special restoration when the user switches back to a Worker.
- When Hermes is selected, Kickoff omits Reasoning because the pinned Hermes ACP
  adapter does not offer a functional reasoning control. Hermes still has a
  Worker-type model default and an editable model selection.
- Persisted model and reasoning values are the requested first-session setup, not a
  live mirror. After the first binding, ACP owns the session state; Panels neither
  reapplies those values on load nor presents them as current settings.

## Executive finding

Panels can offer a Ticket-level worker backend, model, and reasoning selection. The
worker backend choice already exists. Codex ACP and Claude ACP both expose their live
model and effort catalogs as stable ACP session configuration options and accept
changes through `session/set_config_option`. Hermes ACP exposes a live model catalog
and model switching through the older ACP model surface, but the pinned adapter does
not expose reasoning as either a working ACP config option or an advertised command.

ACP model/effort configuration is technically applied immediately after
`session/new`, because the new-session response is where the agent reports the exact
options supported by the current installation, account, provider, and selected
model. Panels can perform those changes before the first work prompt, so the product
experience can still be a Kickoff choice.

The catalogs should therefore come from the live backend wherever possible. A
Panels-owned short list can be a presentation layer, but it should not be the source
of truth for whether a model or effort is available.

## The three different concepts

The product vocabulary needs to keep these distinct:

1. **Worker backend**: the agent program Panels runs: Hermes, Codex, or Claude Code.
2. **Model**: the model that agent program uses for this session.
3. **Reasoning / effort**: a model-dependent depth setting.

An underlying API provider is a fourth concept. It is usually implicit for Codex and
Claude Code, but Hermes can route models through providers such as OpenRouter,
OpenAI, or Anthropic. Panels does not have to expose that fourth concept in the main
Kickoff UI.

## ACP protocol capability

ACP v1 says session config options are the preferred session-level configuration
surface. Agents can identify options semantically as `model`, `model_config`, and
`thought_level`; clients set a value using `session/set_config_option`; and the
response returns the complete option list again so dependent choices can change. For
example, changing a model may replace the available reasoning levels.

`session/new` itself contains workspace and MCP configuration, not model or effort.
The agent may return the initial model/config state in its response. The reliable
sequence is therefore:

1. Create the ACP session.
2. Read the agent's live configuration/model state.
3. Validate and set the selected model.
4. Read the refreshed options and set the selected effort.
5. Send the first role-and-work prompt.

Primary sources:

- [ACP session setup](https://agentclientprotocol.com/protocol/v1/session-setup)
- [ACP session config options](https://agentclientprotocol.com/protocol/v1/session-config-options)

## Pinned backend matrix

| Worker backend | Model selection | Reasoning selection | Dynamic dependency behavior | Main limitation |
| --- | --- | --- | --- | --- |
| Codex ACP 1.1.4 | Stable config option `model` | Stable config option `reasoning_effort` | Selecting a model rebuilds the effort choices and retains/falls back to a supported effort | Available models depend on the live Codex catalog/account |
| Claude ACP 0.60.0 | Stable config option `model` | Stable config option `effort` when the model supports it | Selecting a model rebuilds effort and Fast-mode availability | Model names and availability depend on the configured Anthropic/Bedrock/Vertex/Foundry/gateway environment |
| Hermes ACP at `047ba829` | ACP model state plus `session/set_model`; values encode `provider:model` | Not exposed by the pinned ACP adapter | Model switching may rebuild the Hermes agent for a different provider/model | Hermes core supports reasoning, but this pinned ACP adapter does not expose a session reasoning control |

### Codex

The pinned adapter builds model options from Codex's live available-model response and
builds effort options from the selected model's supported efforts. It validates both
values. The current Codex documentation describes Sol as the complex/open-ended
model, Terra as the everyday model, and Luna as the fast/repeatable model. Current
reasoning choices include Low, Medium, High, Extra High, and Max where supported.
Those names are examples rather than a safe hard-coded universal inventory.

Sources:

- Local pinned adapter:
  `agent_backends/node_modules/@agentclientprotocol/codex-acp/dist/index.js`
- [Current Codex model guidance](https://learn.chatgpt.com/docs/models)

### Claude Code

The pinned adapter advertises `model`, conditional `effort`, and conditional Fast mode
as ACP config options. It discovers the models available to the current Claude Code
configuration. Claude Code itself can use aliases or provider-specific model names,
and the supported effort levels depend on the chosen model. The public catalog and
alias resolution change over time and differ across Anthropic, Bedrock, Vertex,
Foundry, and gateways.

Sources:

- Local pinned adapter:
  `agent_backends/node_modules/@agentclientprotocol/claude-agent-acp/dist/acp-agent.js`
- [Claude Code model configuration](https://code.claude.com/docs/en/model-config)

### Hermes

The pinned Hermes ACP adapter returns a model state containing a curated catalog for
the currently configured provider. Its model IDs encode the provider and model, and
`session/set_model` can switch both. It deliberately leaves `config_options` empty and
its generic config-option handler only stores unknown values; it does not apply model
or reasoning behavior.

Hermes core supports global and session reasoning and current public Hermes docs
describe `/reasoning`, but that command is not implemented or advertised by the
pinned ACP adapter. Panels therefore cannot honestly offer a backend-native Hermes
reasoning picker today without a newer upstream adapter or a Panels-specific process/
configuration workaround.

Sources:

- Local pinned adapter:
  `/Users/khushaljagota/.hermes/hermes-agent/acp_adapter/server.py`
- [Hermes configuration and reasoning](https://hermes-agent.nousresearch.com/docs/user-guide/configuration/)
- [Hermes ACP integration](https://hermes-agent.nousresearch.com/docs/user-guide/features/acp/)

## Current Panels state

- Ticket Kickoff already persists and renders a worker backend selector. The choice
  freezes after the first employee demand/session binding or movement beyond Kickoff.
- `WorkerProfile` already contains `model` and `reasoning_effort` fields, but every
  production Worker type currently sets them to `None`; they are not served in the
  manifest, persisted on a Ticket, or applied to an ACP session.
- The browser ACP reducer already stores `config_option_update` state, but Panels has
  no user control or outbound request for setting a config option and does not yet
  promote the initial new/load configuration state into the product surface.

Relevant local files:

- `src/planner/worker_types/contracts.py`
- `src/planner/tickets/contracts.py`
- `src/planner/conversation/sdk_child.py`
- `web/src/lib/acp/conversationState.ts`
- `web/src/routes/TicketRoute.svelte`
- `docs/worker-types.md`

## Product shapes available for discussion

### Native controls

Show Worker, Model, and Reasoning separately, populated from the selected backend's
live ACP session. This is the most transparent and least opinionated shape, but may
show a long model catalog.

### Curated combinations

Show a small Panels-owned list of backend-specific combinations such as Codex Terra
High or Codex Sol Medium. This is simple, but the mappings must be maintained and
validated against the live backend. A universal Fast/Balanced/Deep vocabulary would
imply equivalence across agent programs that does not actually exist.

### Curated first, native advanced

Show a small backend-specific recommended list, plus an Advanced path containing the
live model and effort selectors. Persist the resolved backend-specific values and
fail visibly if a stored selection is no longer offered. Hermes would initially show
model plus Backend default for reasoning rather than pretending parity.

## Decisions still open

- Show every live model versus a short recommended subset plus Advanced.
- Whether Hermes should show only the configured provider's advertised catalog or
  eventually expose an advanced provider selector.
- The concrete default backend/model/effort values for each Worker type.
