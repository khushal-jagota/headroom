# ACP-07 Codex backend definition

## Outcome

A fresh Ticket may select `codex` through the generic ACP-07 worker control. Its human conversation
and every Automatic Employee step use one `ConversationEmployee`, one Codex ACP child, and one
durable ACP session. Codex is a backend definition behind the existing catalog; it is not a second
gateway, transcript, worker field, or UI path.

Source work is gated on both ACP-06 legacy deletion and the generic ACP-07 selector/catalog slice
being integrated. The Codex registration is added only after the exact adapter pin passes the common
backend conformance contract below.

## Exact backend process contract

- Backend key: `codex`.
- The repository owns a production-only Node manifest and lockfile. The adapter and every transitive
  package, including its `@openai/codex` binary package, are frozen by that lockfile and installed
  with `npm ci`; runtime never uses `npx`, a floating global package, or the ambient `codex` command.
- The definition resolves an absolute Node executable and the locked adapter's absolute
  `dist/index.js`. Its argv is exactly `(node_executable, codex_acp_entrypoint)`.
- `initialize` must return protocol v1, agent name `@agentclientprotocol/codex-acp`, the exact locked
  adapter version, and `loadSession=true`. Any mismatch closes the child and makes Codex unavailable;
  it is not accepted with a warning.
- Cwd is the Ticket's first workspace root. The generic registry supplies the remaining roots as ACP
  additional directories. The definition contains no repository-name or Ticket-type branch.
- Inherited environment is limited to the SDK defaults (`HOME`, `LOGNAME`, `PATH`, `SHELL`, `TERM`,
  `USER`) plus optional `CODEX_HOME`, `CODEX_API_KEY`, and `OPENAI_API_KEY`. Ambient `CODEX_PATH`,
  `CODEX_CONFIG`, `MODEL_PROVIDER`, `DEFAULT_AUTH_REQUEST`, `INITIAL_AGENT_MODE`, `NO_BROWSER`, and
  `APP_SERVER_LOGS` do not leak through.
- Overrides are `NO_BROWSER=1`, `INITIAL_AGENT_MODE=agent`, and
  `DEFAULT_AUTH_REQUEST={"methodId":"api-key"}`. The adapter log directory is one explicit absolute
  Panels data path, supplied as `APP_SERVER_LOGS=<database-parent>/codex-acp-logs`. Existing ChatGPT
  authentication under `CODEX_HOME` remains usable; otherwise the API-key request uses the inherited
  key. Missing authentication fails visibly before a session is bound and never opens a browser from
  the server.
- `CODEX_PATH` stays absent so the adapter starts the Codex version frozen in its own lockfile.

## Declared behavior

- Reverse services are `filesystem=False`, `terminal=False`, `permission=True`. Codex executes its
  own tools and sends typed tool/terminal/diff updates; its command, file-change, and broader
  permission requests cross the existing ACP permission broker with the adapter's exact ordered
  options. Panels does not advertise filesystem or terminal reverse calls it will not receive.
- Native steer is unavailable. `supports_steer=False` keeps Steer visibly disabled. Queue and Send
  Now remain the generic broker operations and must pass unchanged.
- `session/load` must finish replay before returning and preserve typed user, assistant, thought,
  tool, diff, and terminal state. The pinned 1.1.4 adapter's one known exception is accepted and
  tested as upstream behavior: live plans use ACP `plan`, while a stored plan reloads as an ordinary
  `agent_message_chunk` prefixed `Plan:`. Panels does not parse that prose or read private state to
  reconstruct a plan. This refresh-presentation limitation does not prevent the worker from running.
- The durable Codex thread ID is the ACP session ID. Reload, browser refresh, child restart, human
  chat, and Automatic Employee work resolve that same binding. A missing stored thread fails closed;
  it never silently creates a replacement.
- Compaction is an opaque lifecycle. The Codex strategy consumes only the adapter's stable,
  namespaced `contextCompaction` tool start/completion updates and the command's asynchronous
  completion/error path, producing one content-free Panels `compacting -> compacted` transition or
  the exact failure. It does not parse display prose, read Codex's private session files, wait for a
  summary, or add a quiet-period timer.
- Codex compacts the existing thread/session. `session/fork` is not required and compaction does not
  replace the durable binding. The existing 300-second emergency breaker may end a stuck local wait,
  but must identify itself as a Panels timeout rather than invent a Codex rejection.

## Registration and worker proof

The Codex builder returns one `EmployeeBackendRegistration` and is added once after Hermes in the
generic production tuple. The manifest order becomes `hermes, codex`; all Worker defaults and Ticket
overrides continue to use the catalog without a Codex conditional.

Required proof:

1. Definition tests assert argv, environment confinement, identity/version, cwd/additional roots,
   reverse capabilities, unavailable steer, and fail-closed executable/auth/session behavior.
2. The common backend suite passes required live/load ordering, grouping, tool reconciliation,
   permission settlement, durable refresh, Queue/Send Now, visible unavailable Steer, content-free
   compaction lifecycle, callback order, and unknown-update rejection. One separate qualification
   assertion freezes the exact live-typed/stored-prose plan difference without changing other
   backends' replay contract.
3. A real disposable Ticket is changed to `codex` before Kickoff, then frozen. Through actual Panels
   Computer Use, one human Ticket prompt and one real `EmployeeStepRunner` step complete on the same
   backend key and ACP session ID. The step receives the real worker-context prompt through
   `AcpStepGateway`; a Panels transcript row is not treated as worker delivery.
4. Computer Use also proves typed thought after hard refresh, a tool/terminal or diff, exact
   permission allow and reject, Queue, Send Now, unavailable Steer, `/compact`, process restart and
   typed replay. No backend-specific frontend code is allowed.

## Current qualification result

The local Codex CLI is `0.144.6`, but no `codex-acp` executable is installed. The locally cached
published adapter is `@agentclientprotocol/codex-acp` `1.1.4`; its package depends on
`@agentclientprotocol/sdk ^1.2.1` and `@openai/codex ^0.144.4`.

Version `1.1.4` is the current activation candidate under this contract. Its inspected implementation:

- advertises `loadSession=true` and keeps compaction on the same thread;
- emits the exact namespaced compaction lifecycle metadata Panels needs; and
- replays a stored plan as an `agent_message_chunk` while live plans use the ACP `plan` update.

Missing fork and summary are not defects: ACP does not standardize compaction, Codex exposes a
complete lifecycle without either, and Panels never displays backend context. The plan replay
difference is a known upstream presentation limitation, not a dispatch blocker. Implementation may
pin 1.1.4 and must prove its actual process/session/permission/automatic-work behavior before
registration; no Panels shim around Codex private state or prose parsing is permitted.

## Non-goals

No Chief selector, model picker, Worker workflow change, backend migration, history transfer, Gemini,
Codex-specific UI/copy, browser ACP client, second automatic-work gateway, or Hermes checkout change.
Do not run the canonical `./verify`; ACP-10 owns that single final gate.
