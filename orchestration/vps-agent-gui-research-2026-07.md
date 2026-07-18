# VPS coding-agent GUI research — July 2026

## Conclusion

The useful boundary is a structured, resumable agent protocol, not an emulated terminal.
Panels should own the worker processes and the authenticated browser connection, while each
provider remains the authority for its conversation history. A browser reconnect should load
the provider's durable session and then join the live event stream; it should never replay a
prompt to reconstruct state.

The most important finding is that one common internal protocol is now realistic:

- Hermes 0.18.2 has a native `hermes acp` stdio server.
- Claude Code can be exposed through the maintained
  `@agentclientprotocol/claude-agent-acp` adapter, built on the official Claude Agent SDK.
- Codex CLI can be exposed through the maintained `@agentclientprotocol/codex-acp` adapter,
  which translates ACP to `codex app-server`.

This deserves a focused conformance spike before Panels builds more provider-specific
translation. The browser should still speak a small Panels-owned vocabulary over an
authenticated WebSocket. ACP is the server-to-worker boundary; ACP's remote transport is not
yet the part to rely on.

## The most useful implementations

### Emdash — best architecture reference

Repository: <https://github.com/generalaction/emdash>

Emdash has a process-owning ACP runtime rather than a terminal parser. Its architecture splits
responsibility into a session manager, one session cell per conversation, a pure transcript
reducer, permission handling, prompt queues, live models, and provider process connections.
It uses `claude-agent-acp`, `codex-acp`, and native `hermes acp`. Remote machines are reached
through an SSH-forwarded workspace daemon. This is the closest reference for the shape Panels
is moving toward, although Emdash itself is a desktop/worktree product rather than a hosted web
application.

Useful files in the researched checkout:

- `agents/architecture/acp-runtime.md`
- `agents/architecture/workspace-server.md`
- `packages/runtime/src/acp-agents/`
- `packages/core/src/acp/reducer/`
- `packages/plugins/src/agents/impl/{hermes,claude,codex}/index.ts`

### Yep Anywhere — best ready-made self-hosted web product

Repository: <https://github.com/kzahel/yepanywhere>

Yep Anywhere is mobile-first, self-hosted, and deliberately keeps provider-owned history as the
source of truth. Its server owns one process per session, maintains a rolling replay buffer, and
serves initial history plus live updates to reconnecting clients. Claude uses the official Agent
SDK; Codex uses `codex app-server` over stdio. It defaults to loopback and documents Tailscale,
reverse-proxy, and end-to-end encrypted relay deployments. This is the strongest candidate to
deploy immediately and the best small web reference to borrow from.

Useful files:

- `ARCHITECTURE.md`
- `packages/server/src/sdk/providers/claude.ts`
- `packages/server/src/sdk/providers/codex.ts`
- `docs/project/connection-matrix.md`
- `SECURITY.md`

### CloudCLI / Claude Code UI — best full browser IDE reference

Repository: <https://github.com/siteboon/claudecodeui>

CloudCLI provides chat, files, Git, terminal, MCP and settings in a responsive web app. Its
current Claude integration uses the Claude Agent SDK and its Codex integration uses the official
Codex SDK, so the chat is structured rather than scraped from a TUI. It is a useful feature and
UI reference, but its larger attack surface and AGPL licence make it less attractive as the
foundation for Panels. It should be placed behind TLS, authentication, and a private network or
carefully configured reverse proxy before VPS exposure.

### Claude Code Web and AgentAPI — useful negative/escape-hatch references

- <https://github.com/vultuk/claude-code-web> is the clean literal-terminal version:
  `node-pty` + WebSocket + xterm.js, with multiple sessions and an output buffer.
- <https://github.com/coder/agentapi> turns an interactive TUI into an HTTP API by sending
  keystrokes into an in-memory terminal and diffing terminal snapshots to infer messages.

These approaches are useful for login/setup and a raw emergency terminal. They are a poor
correctness boundary for chat: terminal layout changes, ANSI state, resize, multi-client input,
approval prompts, scrollback truncation and provider upgrades all become application semantics.

### Mobile relay products

- <https://github.com/slopus/happy> and <https://github.com/happier-dev/happier> demonstrate
  encrypted remote/mobile control and session handoff.
- <https://github.com/K9i-0/ccpocket> demonstrates a self-hosted bridge with native mobile
  clients for Claude and Codex.
- <https://github.com/MobileCLI/mobilecli> is a generic terminal/phone bridge.

They are useful references for pairing, relay security, wake/reconnect and mobile UX, but less
directly reusable for Panels than Emdash or Yep Anywhere.

## Primary protocol surfaces

### ACP

The Agent Client Protocol standardizes an editor/client talking to a coding agent using JSON-RPC
and streaming session updates. Local agents normally run as stdio subprocesses. Remote support
exists conceptually but is still described as work in progress, which is why Panels should put
its own authenticated browser transport around an internal ACP client.

Primary sources:

- <https://agentclientprotocol.com/get-started/introduction>
- <https://github.com/agentclientprotocol/agent-client-protocol>
- <https://github.com/agentclientprotocol/claude-agent-acp>
- <https://github.com/agentclientprotocol/codex-acp>

The current Claude and Codex adapters cover streaming, tools, permissions, cancellation,
session load/resume and structured user elicitation. The exact capability set is negotiated and
must be tested rather than assumed.

### Codex app-server

Primary source: <https://github.com/openai/codex/blob/main/codex-rs/app-server/README.md>

`codex app-server` is the native rich-client interface. It offers threads, turns and items;
thread start/resume/fork/list/read; streaming deltas; turn interruption; diffs; and bidirectional
approval requests. Stdio is the supported transport. Its WebSocket transport is experimental,
so a VPS host should spawn it over stdio and expose its own authenticated WebSocket downstream.

### Claude Agent SDK and headless CLI

Primary sources:

- <https://platform.claude.com/cookbook/claude-agent-sdk-05-building-a-session-browser>
- <https://code.claude.com/docs/en/headless>

The Agent SDK has durable on-disk sessions and official list/read/resume helpers, including a
documented recipe for a session browser. The CLI also exposes newline-delimited `stream-json`,
but the SDK is the richer integration because it provides callbacks for tool permission and
session lifecycle rather than requiring the host to interpret output records itself.

Anthropic also now offers Managed Agents with self-hosted workers:
<https://platform.claude.com/docs/en/managed-agents/self-hosted-sandboxes>. An outbound-only VPS
worker polls Anthropic's queue and executes tools locally, while Anthropic owns sessions and the
event control plane. This is an elegant Claude-only option for API-funded workers, but it is not
a provider-neutral replacement for Hermes/Codex and it is not a self-hosted control plane.

### Claude CLI, headless mode, Agent SDK and ACP: actual parity

These are four presentations of substantially the same Claude Code engine, not four different
agents. The important distinction is who owns the interaction and which product conveniences are
exposed. Anthropic describes `claude -p` as a query through the SDK which then exits, while the
Agent SDK is the programmable embedding surface. `claude-agent-acp` is a maintained translation
layer over that SDK; ACP does not itself add Claude features.

| Surface | What it provides | What the host must provide or loses |
|---|---|---|
| Interactive `claude` CLI | Anthropic's complete terminal product: prompt editor, menus and pickers, built-in commands, permission dialogs, session naming/resume/branch, checkpoints and rewind, background-agent views, Remote Control, Chrome connection and terminal-specific UX. | Requires a real terminal and a person attached to it. Its screen is not a stable application protocol. |
| `claude -p` | The same agent loop for scripts, with text, JSON or streaming JSON output; CLI flags select tools, model, permissions, MCP, settings, resume and output shape. It normally handles a query and exits. Streaming input can support a longer conversation, but the caller still owns the process and JSON plumbing. | No TUI, menus or interactive permission prompt. A headless integration must pre-authorize, use hooks/callback-like deferral, or implement the input round trip. |
| Claude Agent SDK | The richest supported embedding surface: streaming input/output, persistent sessions, continue/resume/fork, programmatic tools and MCP, permission callbacks, `AskUserQuestion`, hooks, subagents, settings sources and file checkpoint APIs. It can load the normal user/project/local settings, `CLAUDE.md`, rules, skills and custom commands. | It is a library, not a GUI or daemon. The application owns process supervision, durable session-id binding, rendering, approvals, reconnect and authentication UX. Agent teams are explicitly a CLI feature rather than an SDK option. |
| `claude-agent-acp` | Adapts the Agent SDK to structured ACP messages. Current v0.59.0 advertises new/list/load/resume/fork/close/delete, history replay, cancellation, modes/model/effort configuration, streamed text/thinking/tool activity, permission requests, `AskUserQuestion` and MCP elicitation, images/context, TODOs, diffs, terminal tool output, additional directories, client MCP servers, and a changing slash-command catalogue. | Only negotiated and mapped ACP features reach the client. It is not the Claude terminal UI. Panels must supply the session browser, dialogs, diff/terminal views, auth terminal and remote transport. |

Primary Anthropic references: the
[CLI reference](https://code.claude.com/docs/en/cli-usage),
[SDK session model](https://code.claude.com/docs/en/agent-sdk/sessions),
[SDK feature mapping](https://code.claude.com/docs/en/agent-sdk/claude-code-features),
[approval and user-input callbacks](https://code.claude.com/docs/en/agent-sdk/user-input), and
[file checkpointing API](https://code.claude.com/docs/en/agent-sdk/file-checkpointing). The ACP
claims above are from the maintained
[`claude-agent-acp` README](https://github.com/agentclientprotocol/claude-agent-acp) and its
[v0.59.0 adapter source](https://github.com/agentclientprotocol/claude-agent-acp/blob/cc5d5e8b80f59ad96dfb05bba858b4197fade8f8/src/acp-agent.ts), inspected at commit
`cc5d5e8b80f59ad96dfb05bba858b4197fade8f8`.

The high-value parity details for Panels are:

- **Normal coding behaviour is preserved.** The adapter starts the SDK with the `claude_code`
  system-prompt and tool presets, enables partial-message streaming, and loads `user`, `project`
  and `local` setting sources. Existing `CLAUDE.md`, rules, permissions, hooks, skills, subagents,
  plugins/custom commands and configured MCP servers therefore continue to influence the agent.
  ACP-supplied MCP servers are merged in as well. This is meaningfully closer to normal Claude
  Code than recreating its tool loop with the Messages API.
- **Sessions are native Claude sessions.** The SDK persists the conversation to disk; resume uses
  its Claude session id and fork creates a new session from the old history. ACP can list sessions,
  replay history on load and resume after its process is recreated. Sessions created by `-p` or
  the SDK do not appear in Claude's interactive `/resume` picker, although the CLI can still
  resume them directly by id. Sessions persist conversation, not arbitrary workspace state.
  Sources: [CLI session management](https://code.claude.com/docs/en/sessions) and
  [SDK sessions](https://code.claude.com/docs/en/agent-sdk/sessions).
- **Permissions and questions are proper structured pauses.** CLI-native prompts become ACP
  `session/request_permission` calls. `AskUserQuestion` becomes ACP form elicitation and the
  selected answers are returned to the SDK. If the ACP client does not advertise form
  elicitation, the adapter deliberately disables `AskUserQuestion`; this is a real negotiated
  capability gap, not graceful text-chat parity. MCP form/URL elicitations are similarly gated.
- **Commands are filtered, not a cloned TUI.** The adapter publishes the SDK's supported command
  catalogue and updates it when commands/skills change. Custom commands are supported, but CLI UI
  commands whose value is their terminal workflow do not thereby acquire that UI. Panels should
  implement session selection, configuration, MCP management and login through its own controls
  rather than assuming `/resume`, `/config`, `/mcp` or `/login` menus will render remotely.
- **Fork is present; checkpoint rewind is not currently mapped.** Interactive Claude exposes the
  `/rewind` menu for restoring code, conversation or both. The SDK exposes
  `enableFileCheckpointing` and `rewindFiles()`. The inspected ACP adapter registers fork but no
  checkpoint/rewind request, so ACP clients cannot currently offer full CLI rewind parity without
  an adapter extension. Claude checkpointing itself only tracks edits made by its edit tools, not
  Bash or external changes. Source: [Claude checkpointing](https://code.claude.com/docs/en/checkpointing).
- **Subagents work; the full agent-team product is not an SDK/ACP guarantee.** The adapter streams
  foreground and background subagent activity, tasks and their permission requests. Anthropic
  separately documents agent teams as a CLI-only, experimental multi-instance feature with its
  own task list, mailbox and terminal/tmux interaction. Panels should model its own workers rather
  than depend on that UI. Source: [agent teams](https://code.claude.com/docs/en/agent-teams).
- **Chrome and Remote Control are adjacent CLI products.** `claude --chrome` connects to a Chrome
  extension on the machine running Claude and uses that visible browser's login state; ACP on a
  headless VPS does not magically connect to the user's laptop Chrome. Claude Remote Control is
  Anthropic's own terminal/browser/mobile relay and is not an ACP capability. It requires a
  full-scope claude.ai login; the inference-only `CLAUDE_CODE_OAUTH_TOKEN` from `setup-token` cannot
  create Remote Control sessions. Sources: [Chrome integration](https://code.claude.com/docs/en/chrome),
  [Remote Control](https://code.claude.com/docs/en/remote-control), and
  [authentication](https://code.claude.com/docs/en/authentication).
- **The ACP process is connection-scoped.** The current adapter keeps one long-lived SDK query
  consumer per loaded session so background output and queued prompts continue to stream. On ACP
  connection disposal it tears down every live query. Therefore a browser disconnect must end
  only Panels' browser connection, not Panels' stdio ACP connection. After a Panels/adapter crash,
  native history can be loaded and the session resumed, but an in-flight turn should be treated as
  an uncertain delivery rather than silently submitted again.

The practical conclusion is: ACP can provide a very close GUI for everyday Claude Code work—chat,
edits, Bash, MCP, skills, hooks, subagents, approvals, questions, history, resume and fork—but it is
not pixel-for-pixel or feature-for-feature CLI remoting. Keep a separately authorized PTY for
login/recovery, and explicitly decide whether Panels needs equivalents for the missing CLI product
features (especially checkpoint rewind, agent-team controls, Chrome and Remote Control).

## Recommended Panels shape

```text
browser/mobile
    |
    | authenticated Panels requests + snapshot/live event stream
    v
Panels server
    |-- worker registry and process supervision
    |-- provider session-id binding
    |-- reconnect cursor/replay buffer
    |-- UI projection and audit facts only
    |-- approval/elicitation request correlation
    |
    +-- stdio ACP --> hermes acp
    +-- stdio ACP --> claude-agent-acp --> Claude Agent SDK / Claude Code
    +-- stdio ACP --> codex-acp --> codex app-server
```

## Claude Code CLI, Agent SDK and ACP feature map

The layers are easy to conflate:

1. Interactive `claude` is the agent engine plus Anthropic's terminal product UI.
2. `claude -p` is a non-interactive, normally one-shot command surface over that engine.
3. Claude Agent SDK embeds the same agent loop, tools and context management in Python or
   TypeScript and adds programmatic lifecycle callbacks.
4. `claude-agent-acp` is a maintained adapter from the Agent SDK to the portable ACP vocabulary.
5. Panels is the ACP client; a feature is usable only if the adapter carries it and Panels renders
   or controls it.

Primary references: [Agent SDK overview](https://code.claude.com/docs/en/agent-sdk/overview),
[CLI reference](https://code.claude.com/docs/en/cli-reference),
[SDK sessions](https://code.claude.com/docs/en/agent-sdk/sessions), and
[`claude-agent-acp`](https://github.com/agentclientprotocol/claude-agent-acp).

| Capability | Interactive CLI | Agent SDK | Current `claude-agent-acp` consequence |
|---|---|---|---|
| Agent loop and Claude Code system prompt | Native | Same loop; caller must select the `claude_code` preset for exact coding defaults | Adapter selects the preset by default |
| Read, Edit, Write, Glob, Grep, Bash, web and orchestration tools | Native | Same built-in tools | Structured ACP tool calls and updates |
| Streaming text, thinking, tools and results | TUI renders them | Typed async message stream | Translated to ACP session updates; optional raw SDK messages are a Claude-specific extension |
| `CLAUDE.md`, rules and settings | Loads user/project/local sources | Opt-in through `settingSources` | Adapter explicitly loads user, project and local sources |
| Skills and most slash commands | Native command palette | Discovered through settings; SDK reports supported commands | Adapter advertises and forwards most commands; filters `clear`, `cost`, `keybindings-help`, `login`, `logout`, `output-style:new`, `release-notes`, and `todos` |
| Hooks | Shell/prompt/agent/HTTP/MCP hooks plus `/hooks` browser | Shell hooks from settings and in-process callback hooks | Hooks run, and the adapter merges its own projection hooks; no `/hooks` management UI unless Panels builds one |
| MCP | Configured in settings, plugins or CLI | Stdio/HTTP/SSE and in-process SDK MCP servers | Client-supplied stdio/HTTP/SSE servers merge with configured servers |
| Permissions | Interactive terminal prompt and permission modes | `canUseTool` callback and permission modes | Structured request/response plus mode selector; Panels must correlate and render it |
| Clarifying questions | `AskUserQuestion` terminal UI | Pauses inside `canUseTool` | Mapped to ACP form elicitation when the client advertises support; otherwise the adapter disables the tool |
| Models, effort, fast mode and custom main agent | CLI menus/flags | Query controls | Adapter exposes session configuration options for all four where supported |
| Plan mode and task list | TUI plan/task views | Mode plus task and subagent events | Adapter emits mode and plan updates for a GUI to render |
| Subagents | Native, including background work | Supported | Adapter contains explicit lifecycle handling and keeps a turn open for its spawned subagents where necessary |
| Agent teams | CLI product feature | Not directly configurable through SDK options | Not provided as ordinary ACP functionality |
| Images and context attachments | Paste/drop/@ references | Typed content blocks | ACP image, embedded context, resource and additional-directory support |
| Bash terminal output | Inline TUI terminal | Tool messages and lifecycle events | Interactive/background terminal representation when the ACP client supports terminal output; otherwise formatted tool output |
| Session persistence | Local JSONL, `/resume` picker and naming UI | Local JSONL plus list/read/info/rename, resume and fork APIs | ACP list/load/resume/fork/close/delete with history replay; Panels supplies the sidebar |
| CLI/SDK interoperability | Interactive sessions appear in picker | SDK sessions can be resumed by ID but do not appear in the CLI picker | Record and display provider session IDs in Panels; do not rely on the CLI picker |
| Continue/compact/fork | Native commands and flags | Continue/resume/fork options | Resume/fork are protocol operations; `/compact` is forwarded as a command |
| Prompt queueing and cancel | Interactive queue/interrupt behavior | Persistent streaming input supports both | Adapter advertises prompt queueing and implements ACP cancellation; exact mid-turn UX is client-owned |
| Usage and context | `/cost`, `/context`, status line | Result usage/model usage | Adapter emits context-window, cost and rate-limit usage updates even though `/cost` itself is filtered |
| Checkpoint rewind | Automatic checkpoints and interactive `/rewind` menu | SDK has `rewindFiles()` when checkpointing was enabled | Current adapter has internal message-id bookkeeping but exposes no ordinary ACP rewind operation; this is a real GUI gap |
| Structured JSON output, budgets and max turns | Primarily `-p` flags | First-class SDK options | Possible through Claude-specific adapter metadata, not a portable first-class ACP control |
| Custom in-process Python/TypeScript tools | Not the CLI's normal extension path | First-class SDK feature | An external ACP client cannot send function objects; use MCP or modify/configure the adapter process |
| Chrome integration | `--chrome` and extension UI | Can be assembled through SDK/tool configuration | Not a stock ACP control in the current adapter; use MCP/browser tooling or add a Claude-specific option |
| Remote Control | Native Anthropic-hosted remote UI | Not the purpose of the SDK | ACP/Panels is an alternative remote-control path, not a bridge into Claude Remote Control |
| Login, setup, doctor, update, config and keybinding screens | Native terminal workflows | Outside the agent loop | Keep a separately authorised setup terminal or SSH path; the adapter supports terminal-auth handoff |

The practical parity target for Panels is therefore:

- **Build now:** session sidebar/new/load/resume/fork, structured streaming, tool and terminal cards,
  approval and elicitation bands, cancel/queue, model/mode/effort/fast/agent controls, command and
  skill palette, plans, diffs, images, usage and reconnect.
- **Keep as a separate setup terminal:** login/logout, trust/setup, doctor/update and unusual
  provider recovery.
- **Explicit later features:** checkpoint/rewind, Chrome, configuration/hook management screens,
  CLI background-session administration and agent teams.

That gives ordinary coding-session parity without pretending to clone every terminal-product menu.

The server should own the process, not the browser. Closing a tab must not stop a turn. A server
restart may kill a live process, but the replacement process should resume the recorded provider
session id and load native history. Durable filesystem state belongs in the workspace; a shell's
in-memory environment must not be treated as durable.

Panels' database should store the worker/provider binding, lifecycle facts, request correlation
and any deliberately product-visible transcript projection. It must not become the canonical
model conversation. This matches the existing rule that a `chat_messages` row is not delivery to
the worker.

For reconnect, use one atomic attach operation that establishes the live subscription and returns
a history snapshot or cursor. Updates need stable provider/session/turn/item ids and a monotonic
connection sequence so a client can deduplicate snapshot/live overlap. Never automatically retry
an uncertain prompt submission; return an honest delivery state.

Approvals and questions are server-initiated requests, not chat messages. Bind every response to
the worker, provider session, turn and request id. Reject stale responses after restart or after a
new turn supersedes them.

## ACP gap found locally

The installed Hermes 0.18.2 passes `hermes acp --check` and advertises load, resume, fork, list,
history replay, images, models/modes, commands, cancellation and permission requests. The current
Hermes ACP adapter does not appear to wire the Hermes `clarify` callback into ACP elicitation.
The Claude and Codex ACP adapters implement the newer, currently unstable elicitation surface.

Therefore the next decision should come from a conformance spike, not an immediate rewrite:

1. Exercise the same scripted scenario against all three ACP agents: new, prompt, streamed text,
   tool activity, approval allow/deny, user question, interrupt, disconnect/reattach, process kill,
   resume/history, and a second turn.
2. Record capability negotiation and raw frames as fixtures.
3. Confirm whether Hermes clarification can be represented by the ACP elicitation extension in an
   upstream Hermes release. Until then, keep the existing Hermes clarification path or a narrow
   adapter extension.
4. Only after the matrix passes should Panels replace its Hermes-specific translator with an ACP
   client. Keep the Panels browser vocabulary small; do not expose every provider feature merely
   because ACP can carry it.

## Deployment posture

### Subscription authentication

ACP does not choose billing or authentication; each adapter inherits the underlying CLI/SDK
identity. Codex can use a ChatGPT subscription login stored by Codex on the VPS. Claude can
technically use a personal subscription OAuth login or `CLAUDE_CODE_OAUTH_TOKEN`. Anthropic
announced a separate Agent SDK credit for June 15, 2026 but then **paused that change**: as of this
research date, Agent SDK, `claude -p`, and third-party Agent SDK app usage still draw from the
subscription's normal usage limits, and the proposed separate credit is not available. Keep
`ANTHROPIC_API_KEY` unset when subscription usage is intended because it takes precedence in
non-interactive mode and incurs API billing. Primary sources:
[current subscription/SDK update](https://support.claude.com/en/articles/15036540-use-the-claude-agent-sdk-with-your-claude-plan)
and [authentication precedence](https://code.claude.com/docs/en/authentication).

Anthropic's current policy distinction matters: a person may use subscription OAuth for ordinary
Claude Code/Agent SDK use, but a developer offering a product or service to other users may not
offer Claude.ai login or route their Free/Pro/Max credentials. A private, single-user Panels
deployment can use the owner's credential; any multi-user or distributed product should use
Claude Console API keys or a supported cloud provider instead. The OAuth token is a long-lived
secret and belongs in protected host credential storage, never in the browser or repository.

For a single-user VPS, bind the app to loopback and reach it through Tailscale, or put it behind a
TLS reverse proxy with strong authentication. Do not publish a bare worker WebSocket. Keep CLI
credentials on the VPS, use SSH or a short-lived raw terminal for provider login, and never send
provider credentials to the browser. Run workers as an unprivileged Unix user; for multiple trust
boundaries use a separate user or container and mount only the required repository and agent home.

PTY access should be an explicitly separate capability from structured chat. It is useful for
authentication, setup and recovery, but it grants an interactive shell and must be authorised as
such.

## Practical recommendation

- To get a good remote interface running now: test Yep Anywhere behind Tailscale.
- To learn the reusable architecture: study Emdash's ACP runtime/session cells and Yep Anywhere's
  provider-owned history plus reconnect path.
- For Panels: pause before adding Claude/Codex-specific translators and run the three-provider ACP
  conformance spike. The likely end state is one ACP worker boundary, one Panels browser protocol,
  and a raw terminal only as a separate escape hatch.
