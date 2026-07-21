# ACP compaction primary-source audit

Audited 2026-07-20 against pinned current source revisions:

- ACP specification: [`fa5de574`](https://github.com/agentclientprotocol/agent-client-protocol/tree/fa5de574af6ffdb19a5ccea3cace35f7b9fdba9e)
- Hermes Agent: [`3ef6bbd2`](https://github.com/NousResearch/hermes-agent/tree/3ef6bbd201263d354fd83ec55b3c306ded2eb72a)
- `@agentclientprotocol/codex-acp` 1.1.4: [`301e0f3d`](https://github.com/agentclientprotocol/codex-acp/tree/301e0f3ddb385d04cdbc276b5ba91145243f692a)
- `@agentclientprotocol/claude-agent-acp` 0.60.0: [`d9bd36d8`](https://github.com/agentclientprotocol/claude-agent-acp/tree/d9bd36d8b06764d63656d0387ad9430ec9fcb27d)

## Conclusion

Panels' deliberately small UX is compatible with the real integrations: show that compaction
started, then that it finished, or show the exact available failure. Compaction context and summaries
are backend-private and are not required client output. A five-minute watchdog is compatible as a
Panels emergency breaker, because ACP specifies no compaction deadline, but the watchdog must report
its own truth (`Panels stopped waiting after 300 seconds`) rather than claim the backend rejected the
compaction.

ACP v1 does not standardize compaction. It standardizes advertised slash commands and prompt turns.
The three adapters therefore need small backend-specific observers which normalize their actual
signals into the shared content-free lifecycle. None of the inspected adapters changes the active
session ID merely because it compacts.

## What ACP v1 standardizes

ACP slash commands are discoverable UI affordances, not protocol methods. An agent may advertise
commands with `available_commands_update`, but the client runs one by sending the command text as an
ordinary `session/prompt` user message ([pinned slash-command specification](https://github.com/agentclientprotocol/agent-client-protocol/blob/fa5de574af6ffdb19a5ccea3cace35f7b9fdba9e/docs/protocol/v1/slash-commands.mdx#L6-L10), [running a command](https://github.com/agentclientprotocol/agent-client-protocol/blob/fa5de574af6ffdb19a5ccea3cace35f7b9fdba9e/docs/protocol/v1/slash-commands.mdx#L71-L90)).

A prompt turn may emit any number of `session/update` notifications while work proceeds. It is
terminal only when the agent responds to the original `session/prompt` with a `StopReason`
([prompt-turn sequence](https://github.com/agentclientprotocol/agent-client-protocol/blob/fa5de574af6ffdb19a5ccea3cace35f7b9fdba9e/docs/protocol/v1/prompt-turn.mdx#L18-L54), [completion rule](https://github.com/agentclientprotocol/agent-client-protocol/blob/fa5de574af6ffdb19a5ccea3cace35f7b9fdba9e/docs/protocol/v1/prompt-turn.mdx#L215-L229)). The protocol sets no maximum prompt duration.

The stable v1 capability and method schema contains no compaction operation, compaction result,
summary field, or rule about session identity during compaction
([stable schema](https://github.com/agentclientprotocol/agent-client-protocol/blob/fa5de574af6ffdb19a5ccea3cace35f7b9fdba9e/schema/v1/schema.json)). `session/fork` is present only in
the unstable schema and is explicitly marked unstable
([unstable fork capability](https://github.com/agentclientprotocol/agent-client-protocol/blob/fa5de574af6ffdb19a5ccea3cace35f7b9fdba9e/schema/v1/schema.unstable.json#L2798-L2803), [unstable fork request](https://github.com/agentclientprotocol/agent-client-protocol/blob/fa5de574af6ffdb19a5ccea3cace35f7b9fdba9e/schema/v1/schema.unstable.json#L7010-L7053)). Stable `session/load` is the optional persistence/replay operation; when supported, replay must finish before its response
([load contract](https://github.com/agentclientprotocol/agent-client-protocol/blob/fa5de574af6ffdb19a5ccea3cace35f7b9fdba9e/docs/protocol/v1/session-setup.mdx#L83-L104), [replay completion](https://github.com/agentclientprotocol/agent-client-protocol/blob/fa5de574af6ffdb19a5ccea3cace35f7b9fdba9e/docs/protocol/v1/session-setup.mdx#L134-L188)).

Implementation implication: generic Panels code can model `compacting | compacted | failed`, but it
cannot infer those states from an ACP-native compaction message because no such message exists.
Observation belongs in the backend strategy.

## Current adapter behavior

| Backend | Started | Finished | Failed | Session identity | Summary/client context |
| --- | --- | --- | --- | --- | --- |
| Hermes ACP | No backend start event for explicit `/compact`; Panels should emit `compacting` immediately before the prompt. | Local handler returns `Context compressed: old -> new messages` plus token counts, then the ordinary prompt returns `end_turn`. | `Compression failed: <exception>`; disabled, unavailable, and empty cases are also returned as ordinary text with `end_turn`, so `end_turn` alone is not success. | The handler deliberately disables Hermes' internal session split and compacts the same ACP ID. | Counts only. No summary contract. |
| `codex-acp` | Modern Codex events map a `contextCompaction` item start to an ACP tool call titled `Context compacting`, status `in_progress`; Panels may also emit immediately for explicit `/compact`. | Item completion maps to `Context compacted`, status `completed`. The command implementation also waits for the asynchronous Codex `thread/compacted` or completed `contextCompaction` notification before returning. A legacy `thread/compacted` path emits fixed prose. | A rejected `thread/compact/start`, child/process failure, or Codex error is the failure path. There is no compaction-summary payload and no separate typed compaction-failure item in the inspected generated type. | Compaction is invoked with `threadId: sessionId` and completion is correlated to that same thread. | Fixed lifecycle prose/tool metadata only; no summary. |
| Claude Agent ACP | The long-lived SDK consumer maps `status: "compacting"` to `Compacting...`. | `compact_result: "success"` maps to `Compacting completed.`; the ordinary prompt settles after the SDK result. | `compact_result: "failed"` maps to `Compacting failed: <compact_error>` when a reason exists; terminal SDK errors reject the prompt with their structured/text reason. | Status updates and the prompt remain on the existing session. Fork is a separate, explicit unstable operation which creates a new session. | The adapter intentionally keys completion from `compact_result`, not `compact_boundary`. Generated summary user messages are not forwarded as conversation messages. |

### Hermes

Hermes advertises `/compact` and intercepts it locally as an ordinary prompt
([command advertisement and dispatch](https://github.com/NousResearch/hermes-agent/blob/3ef6bbd201263d354fd83ec55b3c306ded2eb72a/acp_adapter/server.py#L453-L463), [prompt interception](https://github.com/NousResearch/hermes-agent/blob/3ef6bbd201263d354fd83ec55b3c306ded2eb72a/acp_adapter/server.py#L1356-L1367)). `_cmd_compact` performs the compression synchronously, restores the agent's database handle, keeps the ACP session ID, saves the session, and returns count/token prose. Exceptions are converted to `Compression failed: ...` text rather than an ACP error
([implementation and exact outcomes](https://github.com/NousResearch/hermes-agent/blob/3ef6bbd201263d354fd83ec55b3c306ded2eb72a/acp_adapter/server.py#L1936-L1987)).

Therefore the Hermes observer should:

1. emit `compacting` before submitting the explicit command;
2. treat `Context compressed:` as the positive adapter proof;
3. preserve and display the exact disabled/unavailable/no-op/failure response instead of treating any
   `end_turn` as successful compaction;
4. never wait for or extract a summary marker.

### Codex

`codex-acp` recognizes `/compact` and awaits `runCompact`
([command dispatch](https://github.com/agentclientprotocol/codex-acp/blob/301e0f3ddb385d04cdbc276b5ba91145243f692a/src/CodexCommands.ts#L190-L216)). `runCompact` first installs a
completion waiter, sends `thread/compact/start`, and resolves only on an asynchronous
`thread/compacted` notification or an `item/completed` whose item is `contextCompaction`
([wait-before-start ordering](https://github.com/agentclientprotocol/codex-acp/blob/301e0f3ddb385d04cdbc276b5ba91145243f692a/src/CodexAppServerClient.ts#L497-L500), [completion predicate](https://github.com/agentclientprotocol/codex-acp/blob/301e0f3ddb385d04cdbc276b5ba91145243f692a/src/CodexAppServerClient.ts#L1030-L1035)).

The modern item mapping already provides clean lifecycle metadata: `{ contextCompaction: true }`,
`Context compacting`/`in_progress`, then `Context compacted`/`completed`
([mapping](https://github.com/agentclientprotocol/codex-acp/blob/301e0f3ddb385d04cdbc276b5ba91145243f692a/src/CodexToolCallMapper.ts#L227-L263)). The legacy notification becomes only the fixed text `Context compacted to fit the model's context window.`
([legacy mapping](https://github.com/agentclientprotocol/codex-acp/blob/301e0f3ddb385d04cdbc276b5ba91145243f692a/src/CodexEventHandler.ts#L431-L433)). Neither path exposes a summary.

Implementation implication: consume the compaction-tagged tool updates into the Panels lifecycle
instead of rendering them as generic tools. Keep reading notifications until the command prompt
settles. If the command start fails or the child dies, surface that exact error. If no terminal signal
arrives, only the 300-second Panels watchdog may end the wait, and its message must identify the local
watchdog rather than invent a Codex failure.

### Claude Agent / Claude Code

There is no Anthropic-native `claude acp` integration in the inspected official Claude Code product.
The official Claude Code repository's ACP request is closed as `not planned`
([Anthropic issue #6686](https://github.com/anthropics/claude-code/issues/6686)). The maintained integration used here is the ACP project's adapter, `@agentclientprotocol/claude-agent-acp`, which states that it wraps Anthropic's official Claude Agent SDK
([adapter README](https://github.com/agentclientprotocol/claude-agent-acp/blob/d9bd36d8b06764d63656d0387ad9430ec9fcb27d/README.md), [pinned package dependencies](https://github.com/agentclientprotocol/claude-agent-acp/blob/d9bd36d8b06764d63656d0387ad9430ec9fcb27d/package.json)). It is first-party to the ACP project, not an Anthropic-provided ACP server.

The adapter keeps a long-lived SDK consumer so it can forward messages that arrive between prompt
turns, not only while `session/prompt` is awaiting
([consumer contract](https://github.com/agentclientprotocol/claude-agent-acp/blob/d9bd36d8b06764d63656d0387ad9430ec9fcb27d/src/acp-agent.ts#L1668-L1689)). Its compaction handling is explicit:
`status: compacting` emits `Compacting...`; `compact_result: success` emits `Compacting completed.`;
and `compact_result: failed` emits `Compacting failed` plus `compact_error` when supplied
([exact lifecycle mapping](https://github.com/agentclientprotocol/claude-agent-acp/blob/d9bd36d8b06764d63656d0387ad9430ec9fcb27d/src/acp-agent.ts#L2331-L2368)). The code comments state that `compact_result`, not `compact_boundary`, is the manual-compaction terminal signal. `compact_boundary` is used to refresh usage only
([boundary handling](https://github.com/agentclientprotocol/claude-agent-acp/blob/d9bd36d8b06764d63656d0387ad9430ec9fcb27d/src/acp-agent.ts#L2370-L2403)). Generated summary user messages are skipped by the feed's user-message filter
([user-message filtering](https://github.com/agentclientprotocol/claude-agent-acp/blob/d9bd36d8b06764d63656d0387ad9430ec9fcb27d/src/acp-agent.ts#L3436-L3455)).

Implementation implication: normalize those exact lifecycle messages and suppress them as ordinary
assistant prose. Do not wait for `compact_boundary`, do not parse a generated summary, and do not fork
the session.

## Hermes-only persistence and fork work

Hermes' compaction persistence needs a narrow backend strategy. During manual compaction it sets
`agent._session_db = None` specifically to suppress Hermes' normal compression-driven session split,
then restores the handle before `save_session`
([manual-compaction persistence handling](https://github.com/NousResearch/hermes-agent/blob/3ef6bbd201263d354fd83ec55b3c306ded2eb72a/acp_adapter/server.py#L1956-L1973)). The session manager, however, avoids replacing message rows when that restored agent is judged to own the same database
([persistence guard](https://github.com/NousResearch/hermes-agent/blob/3ef6bbd201263d354fd83ec55b3c306ded2eb72a/acp_adapter/session.py#L453-L493)). This combination creates a Hermes-specific risk that the in-memory compressed history is not the history recovered by a fresh process.

Hermes' explicit fork deep-copies that current in-memory history into a newly persisted session with a
new ID
([fork implementation](https://github.com/NousResearch/hermes-agent/blob/3ef6bbd201263d354fd83ec55b3c306ded2eb72a/acp_adapter/session.py#L242-L270)). A controlled post-compaction
fork/rebind can therefore be a Hermes durability workaround if the conformance test proves it is
needed. It must not become any of the following:

- a generic ACP compaction requirement;
- a requirement that every worker backend advertise unstable `session/fork`;
- evidence that compaction normally changes session IDs;
- a reason to read, store, or render backend summaries.

Codex compacts its existing thread, and the Claude adapter compacts its existing SDK session. For
them, requiring a fork would add an unsupported lifecycle transition rather than improve conformance.

## Panels acceptance implications

The smallest correct contract is:

1. **Started:** emit one content-free `compacting` boundary before an explicit compact prompt, or on a
   backend's automatic-compaction start signal.
2. **Finished:** emit one content-free `compacted` boundary only on the backend-specific positive
   proof above. A summary is neither proof nor required output.
3. **Failed:** emit `failed` with the exact display-safe backend/transport reason. A watchdog expiry is
   explicitly a Panels wait failure, not a fabricated backend failure.
4. **Asynchrony:** keep consuming ordered `session/update` notifications until the prompt response;
   tolerate completion taking multiple minutes and notifications arriving through the adapter's
   long-lived stream.
5. **Identity:** retain the existing ACP session binding across compaction. Only the Hermes strategy
   may perform a proven persistence fork and atomically rebind to its returned ID.
6. **Presentation:** consume adapter lifecycle prose/tool metadata into the lifecycle indicator. Never
   expose a compaction summary or make one expandable.

This is enough to satisfy the owner requirement: the user knows compaction is happening and knows
when it has finished, without turning backend context into product-visible conversation content.
