# Shared ACP layer

A library, not a driver. `create_acp_child_factory(flavor, dependencies)` supplies a backend child for any agent that speaks the Agent Client Protocol over stdio. Antigravity is its one flavor. Hermes still runs its own child (`server/drivers/hermes/runtime/`) over this transport.

## The backend child

`AcpBackendChild` launches the flavor's command with the shared environment (`environment.ts`: six inherited variables, the flavor's own values, then the conversation identity; nothing else of the server's environment, credentials included, reaches the agent). It sends `initialize`, then `session/new` or `session/load`, then sets the model and the reasoning effort through the session's config options, by category: `model` first, then `thought_level`, each against the options the previous reply returned. Prompts carry the sender label inside the composed text. A one-piece message that names an advertised command, and the core's `/compact`, go to the agent as typed.

What every child of this base does, whatever its flavor:

- No steer. `ACP_CHILD_CAPABILITIES` says so, and a steer is refused with `backend_cannot_steer`; the message waits for the next prompt.
- Stop sends `session/cancel` and waits for the prompt's own reply, which ends the turn as interrupted.
- A child whose agent ended while idle answers the next prompt with `NeedsRebind` (`failed_child_recovery`), so the core starts a fresh child for it.
- A tool call still open when its turn ends is finished then, because Panels never ends a tool call by itself.
- `usage_update.used` is the context size; the prompt reply's usage is the turn's tokens.

## Flavor hooks

`flavor.ts` holds the contract. Each optional hook exists because the Antigravity flavor uses it against agy 1.5.10:

- `requires_sign_in(error)`: a signed-out agent does not start (`BackendSpawnFailed`, not a lost session) and a signed-out prompt fails its turn, each with the flavor's sign-in text.
- `prompt_inactivity_timeout_ms`: a turn whose agent sends nothing for this long fails, and its child is not reused. A turn waiting on a person or on an open tool call is not silent.
- `watch_prompt_outcome(session_id)`: taken before the prompt is written and read after an `end_turn`, for an agent that reports some failures only outside the ACP stream.

T3 Code's other hooks were not built, because agy 1.5.10 as Panels launches it does not need them. The reasons are recorded with the Antigravity driver Ticket.

## The connection

`AcpClient.begin_request` separates the completed stream write from the eventual response. Permission handlers return `{answer}` after admission so an unanswered request cannot block later notifications. `response_written` observes completion of that answer's actual stream write.

The connection pins `@agentclientprotocol/sdk` to **2.6.0**. It uses ACP **v1**, with the `/unstable` entrypoint because Panels consumes prompt usage and session-info updates. This is not experimental wire v2.

The public SDK app starts asynchronous handlers without awaiting their completion, and responses bypass those handlers. This implementation therefore uses the SDK's own lower-level Connection and generated validators through version-pinned internal file URLs. An admission gate ahead of Connection orders inbound notifications, requests, and responses. The SDK still owns framing, protocol validation, request correlation, cancellation, and responders. Every SDK upgrade requires a new review of `client.ts`, its internal paths, and its ordering tests. Reply admission also waits for asynchronous prompt-ending and session-binding effects. Raw SDK response promises stay independent, so the ending reporter can consume its reply without a circular wait. Slow sink callbacks have no deadline. Shutdown and wire failure release their gates.

`AcpLauncher` seals new starts before shutdown. It owns its child through `lifecycle/owned_processes.ts`, whose bounded TERM/KILL cleanup it follows. A direct-child exit allows buffered ACP output to drain in order, within 15 seconds. Stop does not wait for descendant-held output pipes before it observes direct-child exit. Launch and cleanup errors use backend exception types and name the agent by the label its caller gives.

Strict whole-update validation uses the SDK's exported JSON schema before best-effort parsing. The Manager accepted the 15-second control-request deadlines and strict numeric-field validation on November 12, 2026, as recorded in `server/DEVIATIONS.md`. Regular persistence callbacks have no deadline. A configuration request that times out settles that request only and needs a fresh child; startup reports it as a session load failure.

## Evidence

The connection tests prove ordered split-frame handling, parked permissions and actual response writes, independent request timeout, broken-write settlement, and unchanged import environment. Launcher tests prove startup/shutdown races, failed-reader cleanup, and stdin errors. The fast-exit test needs one synthetic Node child because memory streams cannot prove OS child exit versus buffered output. `child.test.ts` runs the real child against a scripted ACP peer for each hook and each base rule above.
