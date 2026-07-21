# ACP migration reference study

Date: 2026-07-19

This is the evidence behind the migration plan. It records what was observed in working clients,
what was verified in source, and where the donors are incomplete. It is not a second specification;
`direction.md` remains the owner brief.

## Pinned reference set

| Reference | Pin used for this study | Role in Panels |
| --- | --- | --- |
| ACP v1 specification | `d84fc79db997c9720f3ae7db6c9c34100f767f66` | Protocol contract |
| ACP Python SDK | `0.11.0`, commit `3b3a5e35a2750110d65216fdb7c899ba4127e495` | Server-side client transport and generated types |
| `zvzuola/acp-components` | commit `525a9d83c5ace577ac0417bf82bf983da4042663` | Browser reducer and React view donor |
| `formulahendry/acp-ui` | `0.1.16`, commit `cd9c3cb464a4b321bff652101953a64c07473e31` | Secondary behavior sanity check only |
| Codex ACP adapter | `1.1.4`, commit `3196231fea06a576ea08c8e0a0ad8cf912d1e011` | First non-Hermes backend candidate |
| Claude Agent ACP adapter | `0.59.0`, commit `cc5d5e8b80f59ad96dfb05bba858b4197fade8f8` | Second non-Hermes backend candidate |
| Gemini CLI ACP | `0.51.0`, commit `8d951de3855750d5f8219d65ae22524b606133b6` | Gated backend candidate |
| local Hermes | `0.18.2` | First backend; source inspected read-only |

The `acp-components` npm package is behind the inspected source. Panels must pin the source revision
or vendor only the framework-free core with its license and provenance. It must not float to the
package's latest branch or copy the ACP unions into a Panels-owned parallel type system.

## Protocol facts that shape the architecture

- ACP over stdio is newline-delimited UTF-8 JSON-RPC. Standard output is protocol-only; diagnostics
  belong on standard error.
- The server should use the Python SDK's stdio transport and agent connection rather than owning a
  second JSON-RPC implementation.
- `session/load` replays the transcript as typed `session/update` calls before the load response.
  The response itself is not the transcript. `session/resume` reattaches but does not promise
  history replay in ACP v1. A browser refresh therefore uses `load`, with reducer state reset before
  awaiting it.
- The prompt RPC remains pending for the whole turn. ACP v1 has cancel but no standard steer or
  queue primitive. Those delivery choices are Panels policy behind a backend capability/strategy.
- Updates are ordered on the wire, but the Python SDK can start client callbacks concurrently. A
  callback must enqueue and return; one consumer per employee/session serially reduces and
  broadcasts updates.
- A plan update is a full snapshot and replaces the old plan. Tool calls are reconciled by
  `toolCallId`. Message parts are grouped by `messageId` where present and by a deterministic turn
  fallback where an agent omits it.
- Permission requests are reverse calls tied to a tool call. A pending permission must resolve
  exactly once and must be cancelled or denied when the browser disconnects, the child dies, the
  prompt is cancelled, the request times out, or the server shuts down.
- Filesystem reverse calls require absolute paths checked against the employee workspace roots.
  Terminal handles require an employee/session-scoped registry with wait, output, kill, release,
  child-death, and shutdown cleanup.
- Capability negotiation is a promise: Panels advertises only services it actually implements.
  Unknown typed updates produce an observable conversation error and diagnostic record; they are
  never flattened into assistant text.

## What to adopt and what to correct

The useful part of `acp-components/core` is its typed session state: distinct user message,
assistant message, thought, tool call, plan, permission, usage, mode, command, and session-info
state, plus typed replay. Panels will retain that distinction through its websocket.

The donor still needs corrections at the pinned revision:

- plan updates must replace the snapshot rather than append;
- thought disclosures stay closed by default, including while streaming;
- adjacent text may be batched for rendering, but text must flush before a non-text update;
- disconnect and cancellation must settle permission state;
- the browser composer must not simply become disabled during an active prompt;
- the browser must display terminal/raw-content updates or a clear unsupported-content error;
- `SkillView`'s private `_acp/skills/list` extension is not an ACP contract. Skills stay agent-side;
  slash commands come from `available_commands_update`.

The React view files are markup and interaction donors, not a design system. Their whole-block
red/green diff treatment is insufficient; Panels needs a real line diff. The permission donor's
first-action focus is also unsafe as a default.

## Hands-on Zed study

Zed `1.11.3` was installed from the official Homebrew cask and run against a safe temporary folder,
not the Panels repository. A Codex ACP session was exercised through the real Agent Panel.

Observed behavior:

- Reasoning appears as a separate **Thinking** disclosure and is closed by default.
- A tool is one compact transcript row. Expanding it exposes its input and output; a file tool can
  open the referenced file.
- File creation is rendered as an inline diff rather than prose about a diff.
- Long-running shell work streams terminal state in place.
- Sending while work is active queues by default and shows a visible `1 Queued Message` row with
  **Clear All** and **Send Now**. **Send Now** interrupts the current work, inserts a visible
  interruption boundary, and begins the new human message.
- A network command produced an inline, blocking permission request with concrete outcomes:
  **Allow Once**, **Allow for Session**, a command-prefix allow rule, and **Reject**. The conversation
  retained **Awaiting Confirmation** until an answer was chosen.
- Typing `/` opened the agent-advertised command palette.
- `/compact` immediately produced a persistent **Context compacting** status. Compaction is treated
  as a conversation event, not an invisible implementation detail.

Zed supplies the affordance and legibility bar. Panels should copy the clarity of state changes,
not Zed's visual system.

## Hands-on acp-ui study

`acp-ui 0.1.16` was run locally with its websocket bridge spawning `hermes acp` in a safe temporary
folder. The trace proved a browser-to-bridge ACP flow, Hermes v1 initialization, `session/new`,
model/mode updates, usage, and Hermes command discovery (`help`, `model`, `tools`, `context`,
`reset`, `compact`, `steer`, `queue`, `version`). The processes were stopped after the study.

Its visual design is not a reference for Panels. Per the owner, the ACP UI is unattractive and must
not pull this work into a broad redesign. It is useful only as a behavioral/protocol comparison:
collapsed thought, blocking permission, visible disconnect, and robust NDJSON frame splitting. Its
browser-as-ACP architecture, flattened display shortcuts, missing terminal view, and fixed timeout
do not carry over.

## Current backend behavior

### Hermes first

Command: `hermes acp` (Hermes `0.18.2`, currently built against ACP SDK `0.9.0`).

Useful behavior:

- emits live thought separately from assistant messages;
- emits typed load replay before returning from `session/load`;
- replays user, thought, message, tools, and todo/plan state;
- advertises `/steer` and `/queue` vendor commands;
- reports usage and session provenance.

Known constraints Panels must normalize or expose:

- the adapter enables an unstable protocol mode internally;
- it also replays on `resume`, which ACP v1 does not require;
- it can omit message IDs;
- stored non-text replay is incomplete and partial replay can still return success;
- manual `/compact` returns token/count prose, not the summary;
- automatic compression is observable after the fact through
  `_meta.hermes.sessionProvenance`, reason `compression`.

For explicit `/compact`, Panels can enter `compacting` before sending the command, then perform a
controlled `session/load` capture after the turn settles to extract the compressed summary without
broadcasting a duplicate replay. For automatic Hermes compression, provenance triggers the same
controlled capture and a visible `Context Compacted` boundary. Detection must be a backend strategy,
not a Hermes conditional in the generic conversation core.

The Hermes checkout remains strictly read-only. Panels will remove its dependence on the old local
patches; physically reverting patches in that separate checkout is outside this repository's
authorized actions.

### Codex

Command candidate: `npx -y @agentclientprotocol/codex-acp@1.1.4`.

This is the cleanest first swap after Hermes: stable IDs, typed replay before load response, rich
tool/diff/terminal metadata, and visible compaction tool metadata.

### Claude Agent

Command candidate: `npx -y @agentclientprotocol/claude-agent-acp@0.59.0` (Node 22).

It has typed load replay and stable IDs, and exercises reverse filesystem, terminal, and permission
services. Prompt queueing and compaction details are adapter-specific capabilities, not new core
branches.

### Gemini

Command candidate: `npx -y @google/gemini-cli@0.51.0 --acp`.

The inspected version starts `streamHistory()` from `session/load` without awaiting it and can
return the response before replay completes. That breaks the typed-refresh invariant. Panels must
gate Gemini behind a conformance test or a fixed pin. It must not add an arbitrary quiet period or
pretend the backend is available while replay correctness is unknown.

## Resulting boundaries

- One `conversation/` domain owns Panels conversation contracts and orchestration.
- One backend definition supplies the executable, environment, declared capabilities, and the
  narrow strategies ACP does not standardize (steer, compaction observation).
- One ACP child/client per employee uses the existing per-employee ownership and durable session
  binding primitives.
- One typed update stream crosses one websocket in a thin routing/sequence envelope.
- The browser reducer owns live typed transcript state; canonical ticket/board/project data remains
  in the existing server-backed resource cache.
- ACP permission state is transient conversation state. It can reuse the visual language of Panels
  gates, but it is not a canonical ticket-field proposal and must not use that writer path.

## Minimum conformance probes

Every registered backend must pass the same scripted contract suite:

1. typed thought replay completes before `session/load` returns;
2. live and replayed thought never becomes assistant text;
3. grouping works with stable message IDs and the documented missing-ID fallback;
4. plan updates replace prior plan state;
5. tool updates reconcile by tool-call ID;
6. permission cancellation and child-death cleanup resolve once;
7. refresh reloads the same durable employee session without drift;
8. steer and queue either have a declared implementation or are visibly unavailable;
9. compaction emits live and completed states with an inspectable summary;
10. unknown or partial updates surface a visible error rather than silent transcript corruption.

