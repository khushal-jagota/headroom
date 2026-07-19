# ACP migration — direction brief

Top-level decisions for moving the worker chat surface onto ACP (Agent Client Protocol).
This is intent + shape + what to use + the bar — **not** a fleshed-out implementation plan.
An implementer agent takes it from here and decides the details; the sections marked
"left to the implementer" are deliberately open.

Context: single-user personal tool, deployed on a VPS. Don't over-build for multi-tenant or scale.

## Sequencing decision (settled)

- **ACP is the primary effort.** The relay redesign is **frozen at P1** (the per-child registry —
  landed, and it carries over: "one agent subprocess per employee" is exactly what the ACP client
  needs). **Do not start P2** (legacy onto the registry), **S3b** (revert the local Hermes patches),
  or the flag-rename. P2's rationale (keep both paths while deciding) is spent now that the direction
  is ACP; S3b is deferred to final cleanup (reverting now re-exposes the identity leak on the
  unported legacy path, and per-child topology + the stock `acp_adapter` make the patches moot for
  ACP anyway).
- **The interim daily-driver is not load-bearing.** Leave whatever is currently running as-is; do
  not invest in it. ACP replaces both legacy and relay; at the very end, delete both and revert the
  Hermes patches together.

## Execution model

- **One driving agent owns this back-to-front, using sub-agents.** It (1) **studies the reference
  implementations first** (clone/read `acp-components` + `acp-ui` + the ACP **Python SDK**; read
  Hermes's own `acp_adapter` read-only; study Zed's docs as the affordance bar), (2) turns this brief
  into a concrete **phased plan**, (3) implements it in **verifiable vertical slices**, and (4)
  **dogfoods its own implementation to prove it meets the bar** — not just compiles, but behaves as
  intended (status legibility, compaction signifier, steer-vs-queue, no thinking-spill on refresh,
  permission clarity).
- Work in an **isolated worktree/branch**; keep `main` and the interim running. Follow the project
  operating model (contract-scoped tickets, sub-agent implementers, Codex reviews, `./verify` gates,
  per-slice commits). Never touch the Hermes checkout destructively.
- **Verification is against the bar below, demonstrated by running it** — the acceptance test is "I
  can always tell what the agent is doing, and steer-vs-queue and compaction are never silent,"
  proven in e2e/dogfood, not asserted.

## What we want

- Put the worker conversation surface on **ACP as the standard protocol**, so we (a) stop fighting
  Hermes-specific history handling, (b) get a legible, user-in-the-loop conversation UI, and
  (c) can add **Codex / Claude Code / Gemini** as worker backends by swapping the agent binary
  instead of rewriting.
- **Adopt for the hard seam, build our specifics around it.** Conversation rendering (thinking,
  tool calls, diffs, streaming, history, compaction) is the hard, easy-to-get-subtly-wrong part.
  We lift a proven typed transcript layer for it and build only what's genuinely ours on top.

## What it looks like

- **Server-as-ACP-client.** The Panels server *is* the ACP Client. It spawns one agent subprocess
  per employee (via the per-child registry we already built) and speaks ACP JSON-RPC over stdio to
  it. The agent talks to the model; the server never does.
- **Browser = thin viewer over our own websocket (not ACP).** Two hops, two protocols:
  `browser ⇄ (our wire) ⇄ server ⇄ (ACP over stdio) ⇄ agent`. The browser never speaks ACP.
- **Use ACP's vocabulary end-to-end.** The browser wire carries ACP's content model
  (`agent_message_chunk` / `agent_thought_chunk` / `tool_call` / `plan` + content blocks) rather
  than a parallel hand-rolled vocabulary, **plus a thin Panels envelope** for what ACP doesn't model
  for a viewer: employee/entity routing, connection/readiness lifecycle, optimistic human echo.
  **Pin a stable ACP version (v1); update on our own cadence** and fix what a bump breaks. ACP's
  `initialize` capability negotiation makes a stable pin first-class — additive changes can't reach
  us until we advertise them.
- **Reverse-calls answered server-side.** `fs/*` and `terminal/*` resolve on the VPS, where the
  repos and shell live. `session/request_permission` maps to our **gate/approval flow**: the server
  relays it to the browser as an approval affordance and turns the answer back into the ACP outcome.
- **Skills stay agent-side.** Each agent loads its own role skills; we choose which agent + config
  to spawn per employee. ACP has no skills primitive, and that's fine. **Commands** ride
  `available_commands_update` (the agent advertises its catalog; the client renders the palette).
- **On the VPS:** one server holds N ACP clients + their agent subprocesses; browsers connect from
  anywhere over our websocket. This is the current relay topology deployed remotely — the only change
  vs today is hop-1's wire becomes ACP instead of Hermes-native frames. The per-child registry and
  the pane carry over.

## Things to use (adopt)

- **Official ACP Python SDK** (`agent-client-protocol`, PyPI, Apache-2.0) — the server's ACP-client
  plumbing: stdio transport, `session/update` handling, permission brokers, "wrap a CLI over stdio."
  Adopt wholesale server-side; don't hand-roll JSON-RPC.
- **`acp-components/core`** (`zvzuola/acp-components`, framework-agnostic TS state layer) — the
  browser-side typed transcript layer. Models thought / message / tool_call / plan as **distinct
  typed parts**, and does **typed `session/load` replay** (the thinking-spill fix, see the bar).
  Point its `AcpTransport` seam at our websocket. **Consume via git** — the npm publish is ~2 months
  behind HEAD; it's pre-1.0, so read the code.
- **Port `acp-components`' React views into Svelte** — `ThoughtView`, `ToolCallCard` (+ diff),
  `PlanView`, `DiffView`, `PermissionPrompt`, `StreamingIndicator`, the usage ring, `SkillView`.
  We keep Svelte; port markup/logic, not the framework.
- **Backends:** Hermes via its `acp_adapter` first; then Codex (`@agentclientprotocol/codex-acp`),
  Claude Code (`@agentclientprotocol/claude-agent-acp`), Gemini (`gemini --experimental-acp`) —
  binary swaps behind the same ACP interface.

## What we build ourselves (to the Zed bar)

The two affordance-critical pieces **neither donor provides** — build them first-party. Our
server-as-ACP-client, which holds the live session and the in-flight `session/prompt`, is the right
place for both (better positioned than a browser-only client).

1. **Compaction signifier.** ACP has no compaction notification, so the **server detects/emits** it.
   The UI renders a Zed-style **"Context Compacted"** entry in the transcript, expandable to inspect
   the summary, plus a live "compacting…" status. Never silent.
2. **Steer vs queue.** A mid-turn send must be an **explicit, legible choice** — not blocked, not a
   guess. While a turn is in flight, the composer offers **Steer** (interrupt at next step) /
   **Send Now** / **Queue**; a queued message shows as a **visibly pending chip**; the UI confirms
   which happened. We own turn boundaries server-side, so we can do this properly.

## References — hold ourselves to these

- **Zed** — the affordance **north-star**: collapsible thinking, tool cards, diff review, plan,
  permission prompts, the "Context Compacted" entry, and steer / send-now / stop. Match its
  legibility, not its code (Rust/GPUI). `zed.dev/acp`, `zed.dev/docs/ai/agent-panel`.
- **acp-ui** (`formulahendry/acp-ui`, Vue) — a complete working web ACP client; **secondary UX
  reference**, and its ACP-over-websocket web build mirrors our relay idea.
- **acp-components** — the code / state-model donor.
- **ACP spec** — `agentclientprotocol.com` (pin v1).

## The bar (acceptance criteria)

- **Always know what the agent is doing** — a persistent, legible status (working / thinking /
  compacting / waiting).
- **Compaction is explicitly signified, never silent** — and expandable to inspect.
- **Steer vs queue** — a mid-turn send tells you which will happen *and* confirms which did; never
  blocked, never a guess.
- **Thinking & tools hidden by default but always expandable** — and **thinking survives a refresh
  without spilling** into plain transcript text (achieved via typed `session/load` replay forwarded
  as typed `agent_thought_chunk`; **do not flatten reasoning into message content**).
- **Permission/approval is clear, explicit, user-driven** (`request_permission` ↔ our gate flow).
- **No silent state changes** — anything that alters what the agent is doing has a visible signifier.

## Left to the implementer

- Exact component inventory and the Svelte port specifics.
- The envelope's precise schema — which ACP updates cross to the browser verbatim vs. enveloped.
- Migration sequencing — which backend/pane first, and how it coexists with the current relay
  during cutover.
- Whether any piece justifies reopening the React-migration question. Default: **keep Svelte**.

## Notes / things to get right

- The thinking-spill fix is **conditional**: the server must forward the agent's `session/load`
  replay to the browser as typed thought, not flattened rows.
- The browser holding a transcript (via `acp-components/core`) is consistent with today — the
  conversation pane is already a browser live-client with local stream state (`neutralPane.ts`).
  The "server is the single source of truth" principle governs canonical product data (tickets,
  board, gates), **not** the live conversation stream, which has always been a separate live socket.
