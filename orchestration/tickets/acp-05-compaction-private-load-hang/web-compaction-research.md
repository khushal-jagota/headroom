# ACP compaction, fork, and load research

Researched 2026-07-20 against ACP main `01beb5fb`, Zed main `8f6f8a54`, Hermes main
`8f33e396`, and `agent-client-protocol` Python SDK `0.11.0`.

## Verdict

The durable-fork idea is justified for the current Hermes adapter, but the contract's **same-child
`fork -> private load`** sequence is an unnecessary and race-prone client invention. `session/fork`
creates the new Hermes session and attempts its persistence synchronously before returning. A subsequent
load does not make the fork durable or “switch” the Hermes process to it. A load on a **fresh child** is
useful because it proves the fork is visible outside the source process, obtains a canonical typed
replay, and validates the Hermes summary; a same-child load can merely read the fork from memory.

The live failure is explained exactly by authoritative source. Hermes schedules a fork-scoped
`available_commands_update` after the `session/fork` response. Panels opens a private response epoch
before its subsequent load request is observed and treats any update in that interval as fatal. There is
no ACP rule that makes the interval quiet. A delay cannot fix a notification that Hermes intentionally
schedules after the response.

Recommended correction: fork on the source child, then initialize a **fresh unpublished successor
child**, register its private sink for the fork session, and load the fork on that child. Validate the
typed replay, CAS the durable binding, then publish the already-loaded child as generation N+1. This is
the same pre-registration shape used by Zed for loads and removes all cross-RPC notification ambiguity.

## What ACP does and does not define

- Stable ACP v1 defines no compaction method or compaction `session/update`. A backend-advertised
  `/compact` command is therefore an ordinary command/prompt turn; how compaction is represented and
  persisted belongs to the backend or client. The stable v1 session setup contains `new`, `load`,
  `resume`, and `close`, but not fork or compact ([ACP v1 session setup](https://github.com/agentclientprotocol/agent-client-protocol/blob/01beb5fb5eec60e9f516a80d85eb03594bac61e3/docs/protocol/v1/session-setup.mdx)).
- `session/load` has one strong ordering rule: the agent **must replay the entire conversation as
  `session/update` notifications, and only after all entries have streamed may it respond**
  ([normative load text](https://github.com/agentclientprotocol/agent-client-protocol/blob/01beb5fb5eec60e9f516a80d85eb03594bac61e3/docs/protocol/v1/session-setup.mdx#loading-a-session)).
- `session/fork` is still an ACP RFD rather than a stable v1 method. Its stated purpose is cloning context
  so additional work does not pollute the original. It returns a new session ID and may return load-like
  configuration. The RFD does not require a history replay, a subsequent load, or any notification
  barrier around the fork response ([fork RFD](https://github.com/agentclientprotocol/agent-client-protocol/blob/01beb5fb5eec60e9f516a80d85eb03594bac61e3/docs/rfds/session-fork.mdx)).
- `session/update` notifications identify a session, not the RPC that caused them. There is no protocol
  correlation field that lets a client prove “this update belongs to fork” versus “this update belongs
  to the following load” when both operations use the same child and destination session.

**Inference:** the contract is right to use a capability-gated standard extension instead of Hermes
private APIs, but “official ACP” should not be read as “stable ACP v1,” and ACP supplies no basis for the
same-child quiet-period assumption.

## How reference Zed represents compaction

Zed's first-party agent owns compaction above ACP. It stores a native `Message::Compaction`, replays it as
a context-compaction thread event, and turns a summary compaction back into a future model message saying
that the previous conversation was compacted
([native message/request representation](https://github.com/zed-industries/zed/blob/8f6f8a54b9933059f1963af601369e0555e32649/crates/agent/src/thread.rs#L199-L255),
[replay](https://github.com/zed-industries/zed/blob/8f6f8a54b9933059f1963af601369e0555e32649/crates/agent/src/thread.rs#L1514-L1550),
[completed summary insertion](https://github.com/zed-industries/zed/blob/8f6f8a54b9933059f1963af601369e0555e32649/crates/agent/src/thread.rs#L3187-L3210)).
Those are Zed's internal thread/message types, not ACP `SessionUpdate` variants. Its external ACP update
handler processes standard message, thought, tool, plan, metadata, command, mode, config, and usage
updates; compaction is not an ACP update arm
([external update handler](https://github.com/zed-industries/zed/blob/8f6f8a54b9933059f1963af601369e0555e32649/crates/acp_thread/src/acp_thread.rs#L2538-L2640)).

Zed's ACP load path creates and registers the destination thread **before** awaiting `session/load`,
specifically so replay notifications sent inside the RPC can find it. Its regression test sends replay
updates before the load response
([pre-registration](https://github.com/zed-industries/zed/blob/8f6f8a54b9933059f1963af601369e0555e32649/crates/agent_servers/src/acp.rs#L1147-L1244),
[regression test](https://github.com/zed-industries/zed/blob/8f6f8a54b9933059f1963af601369e0555e32649/crates/agent_servers/src/acp.rs#L4003-L4072)).
Its ACP connection has no post-command `fork -> load` compaction flow. Zed either persists its own native
compaction message or relies on the external agent's ordinary session persistence.

## What Hermes `/compact`, `session/fork`, and `session/load` do

### `/compact`

Hermes deliberately keeps the ACP `state.session_id` stable. `_cmd_compact` temporarily sets the live
agent's `_session_db` to `None` to prevent the generic compressor from splitting/rotating the SQLite
session, replaces `state.history` with the compressed history, calls `save_session`, and returns only the
human-readable `Context compressed: ...` text
([Hermes compact source](https://github.com/NousResearch/hermes-agent/blob/8f33e39682ae27963c93c87ea13bd0a3c2a55ed8/acp_adapter/server.py#L1909-L1960)).
It emits no typed ACP compaction update and does not expose the summary in its completion text.

The subsequent generic save detects that the restored live agent owns the same DB and avoids replacing
its messages, because replacement would destroy archived compaction rows
([persistence ownership branch](https://github.com/NousResearch/hermes-agent/blob/8f33e39682ae27963c93c87ea13bd0a3c2a55ed8/acp_adapter/session.py#L412-L506)).

**Inference, confirmed by the observed fresh reload:** ACP `/compact` leaves the compressed history in
the live `SessionState`, but the original ACP session's active persisted history can remain the old,
uncompressed history. The call to `save_session` is not, in this path, proof that the compressed list was
rewritten durably.

### `session/fork`

Hermes creates a new UUID, makes a new agent, deep-copies the source's current in-memory history, stores
the new state, and calls persistence before returning
([SessionManager fork](https://github.com/NousResearch/hermes-agent/blob/8f33e39682ae27963c93c87ea13bd0a3c2a55ed8/acp_adapter/session.py#L253-L280)).
Because the new agent does not yet own persisted history, this fork takes the replacement path and writes
the copied compressed history in the normal case. `_persist` logs and swallows persistence exceptions,
however, so a successful fork response is not by itself a durable-visibility proof. The ACP handler then
schedules an `available_commands_update` for the new session and returns the fork response
([fork handler](https://github.com/NousResearch/hermes-agent/blob/8f33e39682ae27963c93c87ea13bd0a3c2a55ed8/acp_adapter/server.py#L1229-L1249),
[post-response scheduler](https://github.com/NousResearch/hermes-agent/blob/8f33e39682ae27963c93c87ea13bd0a3c2a55ed8/acp_adapter/server.py#L1734-L1741)).
Fork does not replay the copied history.

**Inference:** for current Hermes, fork is the protocol operation that creates and attempts to persist the
compressed snapshot. A same-child load is redundant as a durability check because `get_session` can
return the new in-memory state. A fresh-child load is the useful cross-process durability proof.

### `session/load`

Hermes resolves the named session, updates its cwd/MCP state, awaits full history replay, schedules
command/usage metadata, and then responds
([load handler](https://github.com/NousResearch/hermes-agent/blob/8f33e39682ae27963c93c87ea13bd0a3c2a55ed8/acp_adapter/server.py#L1133-L1178)).
This replay-before-response behavior is an upstream fix for a real interoperability bug: issue
[#12285](https://github.com/NousResearch/hermes-agent/issues/12285) reported missing history, and merged
PR [#26957](https://github.com/NousResearch/hermes-agent/pull/26957) replaced deferred replay with inline
awaited replay, citing Zed's pre-registration behavior. PR
[#26943](https://github.com/NousResearch/hermes-agent/pull/26943) separately fixed thought/message/tool
replay ordering.

Hermes's `SessionManager` holds multiple states by session ID, and every prompt names its session.
`session/load` does not move a single global “current session” pointer. Therefore loading the fork on the
source child is not required to activate it; it is only Panels' chosen way to observe a normalized replay.

## Python SDK callback ordering

In SDK 0.11.0, `send_request` queues the outgoing JSON-RPC message and then notifies raw observers; the
receive loop notifies raw observers before dispatching the message; notifications are queued to the
typed handler while responses resolve their request future
([connection source](https://github.com/agentclientprotocol/python-sdk/blob/0.11.0/src/acp/connection.py#L137-L212),
[release](https://github.com/agentclientprotocol/python-sdk/releases/tag/0.11.0)).

Panels is therefore correct to keep an ordered response-consumption barrier: a load response may resolve
while previously observed notification callbacks are still draining. What is incorrect is treating the
period before the next outgoing load observer as guaranteed update-free. The SDK and ACP make no such
guarantee, and an unrelated/post-response notification can already be queued when the next operation's
private epoch is opened.

## Contract comparison and live evidence

The [ACP-05 contract](../acp-05-durable-compaction-fork/contract.md) is supported in these respects:

- wait for `/compact` prompt response consumption before capture;
- require advertised fork capability;
- use Hermes fork as the durable snapshot, not private DB access;
- validate exactly one structural summary from typed replay before CAS/publication;
- preserve CAS ownership and leave an unused fork rather than inventing deletion.

It should be amended in these respects:

- Frozen behavior 3 (“the same child privately loads that fork”) is not required by ACP, Hermes, or Zed
  and creates the proven race.
- Frozen behavior 5 (“child generation and record identity remain unchanged”) prevents the clean
  isolation boundary. A successful durable binding transition should publish a fresh already-loaded
  child as generation N+1.
- Frozen behavior 9 assumes the source child was switched by load and may need restoration. With a fresh
  capture child, the source child is never loaded to the fork. Failure cleanup can discard the candidate
  child and either retain the source runtime or retire it and attach the unchanged authoritative binding;
  no same-child switch-back is needed.

The real Chief run on source `735aea7e-f76d-49bf-a4b9-0576846a5489`, generation 10, reported
`Context compressed: 8 -> 7 messages ~20,399 -> ~30,696 tokens`. It then spent the full 300-second outer
budget in `private fork load`; the binding stayed at generation 10, the uncertain child was invalidated,
and restoration itself timed out. The persisted candidate `7667eb9a-63fc-4ff4-a15c-3dc4ea9e9d76`
contains seven messages.

A minimal direct trace establishes causality:

1. `session/fork` responds with candidate `6ab03c51...`.
2. Hermes sends `session/update` for that candidate with `available_commands_update`.
3. Only then does Panels observe its outgoing `session/load` request.
4. Panels raises `AcpSessionUpdateCallbackMismatch: private ACP capture received an update before its
   load request`, and the child connection closes.

A fresh child loads the same candidate in about 1.6 seconds. A one-second quiescence delay after loading
the source does not help; the update is emitted by fork itself after its response. Thus this is not slow
Hermes compaction or a genuinely hanging `session/load`. The immediate callback mismatch kills the child,
and the lifecycle/restore path is what eventually surfaces at 300 seconds.

## Recommended corrected architecture

1. On the leased generation-N source child, complete `/compact` and its existing ordered prompt barrier.
2. Call capability-gated `session/fork(source_session_id)` on that same child. The returned fork ID is the
   intended compressed candidate. Do not open a load-private epoch on this child or yet assume the
   candidate is durably visible.
3. Create and initialize a fresh unpublished candidate child. Before sending its load, register a private
   destination keyed to the exact fork session ID, as Zed registers its destination thread before the RPC.
4. On the candidate child, call `session/load(fork_id)`. This proves cross-process durable visibility.
   Capture replay notifications in order; after the response, wait through the last raw notification
   ordinal so typed callbacks are fully consumed.
5. Normalize the Hermes replay and require exactly one valid summary. On success, CAS binding N -> N+1,
   publish the candidate child/record as the N+1 runtime, and emit browser `reset -> replay -> ready ->
   queue/compaction` in the existing atomic order. Retire the source child.
6. On load/validation/CAS failure, close the unpublished candidate. Do not publish the fork. Because the
   source child was never loaded to the fork, there is no same-child restoration step. If runtime and
   authoritative old persistence must be made identical after a failed compact, retire the source and
   attach the unchanged N binding on a clean child.
7. Keep the 300-second outer lifecycle budget as a last-resort guard, but propagate callback/child death
   immediately. Do not use sleeps, quiescence windows, or a longer deadline as the fix.

If process cost rules out a fresh child, the minimum weaker repair is to route private epochs by exact
session ID, tolerate/buffer pre-request candidate metadata, and add explicit fork-operation consumption.
That still cannot correlate a delayed fork update with the following load, because ACP provides no RPC
origin on notifications. The fresh-child boundary is the simpler and stronger design.
