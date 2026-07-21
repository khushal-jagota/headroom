# ACP migration — implementer brief

You are the agent implementing this migration. This document is your brief: it gives the goal, the
architecture, what to adopt vs. build, the process to follow, and the acceptance bar. It does **not**
prescribe the code — implementation details are yours (see "Left to you"). Read `CLAUDE.md` and
`PRINCIPLES.md` for the project's system map and standing rules before starting.

## The system you're working in

**Panels** is a single-user planning/agent tool — personal, runs on one VPS; don't build for
multi-tenant or scale. Python/FastAPI backend in `src/planner/`; Svelte/Vite frontend in `web/`.
It runs AI "employees" — one per ticket/task — each backed by an embedded agent runtime called
**Hermes** (a separate git checkout at `~/.hermes/hermes-agent`). The frontend has a conversation
pane where the user talks to and watches an employee work.

**The current conversation system — what you're replacing.** Today the server embeds Hermes and
relays its native event frames to a browser pane. The pane's live client is
`web/src/lib/neutralPane.ts`, driven by a hand-rolled event vocabulary
(`src/planner/hermes_backend/neutral_vocabulary.py`). There are two non-ACP backend variants (an
older multiplexed gateway and a newer one-child-per-employee "relay").

It has a concrete, motivating bug: **on refresh, the model's "thinking" (reasoning) spills into the
transcript as plain text.** While live, the stream separates thinking into a collapsed disclosure;
but on refresh the pane rebuilds history from Hermes's *raw, flat* session history, which bakes
reasoning into the transcript. ACP fixes this by construction (see "The bar").

## Goal

Move the worker conversation surface onto **ACP (Agent Client Protocol)** — the standard protocol
(created by Zed) between a chat UI and a coding agent — so that:

- (a) we stop fighting Hermes-specific history handling (the thinking-spill above);
- (b) the conversation UI is **legible and user-in-the-loop** (see "The bar");
- (c) we can add other agents — **Codex and Claude Code** — as worker backends by swapping the
  agent binary instead of rewriting.

**End state: a single ACP-based system.** The current non-ACP system is removed once the new one is
proven — not maintained alongside it.

**Adopt for the hard seam, build our specifics around it.** Conversation rendering (thinking, tool
calls, diffs, streaming, history, compaction) is the hard, easy-to-get-subtly-wrong part. Adopt a
proven typed transcript layer for it and build only what's genuinely ours on top.

## Architecture

- **The Panels server is the ACP Client.** In ACP there are two roles: the *client* is the UI-side
  host that spawns the agent and services its requests; the *agent* is the coding-agent program that
  talks to the model. **Here the server is the client — not the browser.** The server spawns one
  agent subprocess per employee and speaks ACP JSON-RPC over stdio to it. The agent talks to the
  model; the server never does.
- **The browser is a thin viewer over our own websocket — it does not speak ACP.** Two hops, two
  protocols: `browser ⇄ (our websocket) ⇄ server ⇄ (ACP over stdio) ⇄ agent`.
- **Use ACP's content vocabulary as the browser wire too** — `agent_message_chunk` /
  `agent_thought_chunk` / `tool_call` / `plan` + content blocks — rather than a parallel hand-rolled
  vocabulary, **plus a thin Panels envelope** for what ACP doesn't model for a viewer:
  employee/entity routing, connection/readiness lifecycle, optimistic human echo. **Pin ACP v1** and
  update on your own cadence; ACP's `initialize` capability negotiation makes a stable pin safe
  (additive changes can't reach you until you advertise them).
- **Reverse-calls are answered server-side.** ACP agents call *back* to the client for filesystem
  (`fs/*`), terminal (`terminal/*`), and permission (`session/request_permission`). `fs/*` and
  `terminal/*` resolve on the server/VPS, where the repos and shell live. `request_permission` maps
  to Panels' existing **gate/approval flow**: the server relays it to the browser as an approval
  affordance and turns the user's answer back into the ACP outcome.
- **Skills stay agent-side; commands ride the protocol.** Each agent loads its own role skills; you
  just choose which agent + config to spawn per employee (ACP has no skills primitive — that's fine).
  Slash **commands** ride ACP's `available_commands_update` (the agent advertises its catalog; the
  client renders the palette).
- **On the VPS:** one server holds N ACP clients + their agent subprocesses; browsers connect over
  our websocket. This is the same per-employee shape Panels already has — the change is that the
  server↔agent wire becomes ACP instead of Hermes-native frames.

## What to adopt

- **Official ACP Python SDK** (`agent-client-protocol`, PyPI, Apache-2.0) — the server's ACP-client
  plumbing: stdio transport, `session/update` handling, permission brokers, "wrap a CLI over stdio."
  Use it server-side; don't hand-roll JSON-RPC.
- **`acp-components/core`** (`github.com/zvzuola/acp-components`, a framework-agnostic TypeScript
  state layer) — the browser-side typed transcript layer. It models thought / message / tool_call /
  plan as **distinct typed parts** and does **typed `session/load` replay** (the thinking-spill fix).
  Point its `AcpTransport` seam at our websocket. **Consume from git** — the npm publish is stale and
  it's pre-1.0, so read the code.
- **Port `acp-components`' React views into Svelte** (keep Svelte) — `ThoughtView`, `ToolCallCard`
  (+ diff), `PlanView`, `DiffView`, `PermissionPrompt`, `StreamingIndicator`, the usage ring,
  `SkillView`. Port the markup/logic, not the framework.
- **Backends:** Hermes via its own `acp_adapter` (already in the Hermes checkout) first; then Codex
  (`@agentclientprotocol/codex-acp`) and Claude Code
  (`@agentclientprotocol/claude-agent-acp`) — each a binary swap behind the same ACP interface.
  Gemini was explicitly removed from the owner delivery scope on 2026-07-20.

## What you build yourself (to the Zed bar)

Two affordance-critical pieces **no reference client provides** — build them first-party. The server
(as ACP client, holding the live session and the in-flight `session/prompt`) is the right place for
both.

1. **Compaction signifier.** "Compaction" = when the agent summarizes and drops earlier context as it
   nears a token limit. ACP has no notification for it, so the **server detects/emits** it; the UI
   renders a **"Context Compacted"** entry in the transcript, expandable to inspect the summary, plus
   a live "compacting…" status. Never silent.
2. **Steer vs queue.** When the user sends a message while a turn is running, they must know whether
   it **steers** the current turn (interrupt at the next step) or is **queued** for the next — as an
   **explicit, legible choice**, not blocked and not a guess. The composer offers **Steer /
   Send Now / Queue**; a queued message shows a **visibly pending chip**; the UI confirms which
   happened. The server owns turn boundaries, so it can do this correctly.

## How to work (the process)

You own this back-to-front and may use sub-agents. Work **in place — no worktree, no branch.** Panels
is allowed to be broken/down while you build; there is no interim to protect. In order:

1. **Study the references by computer use** — actually run and operate them (Zed's agent panel,
   `acp-ui`, the agent CLIs) and *watch how they behave*: collapsed thinking, compaction, steer vs
   queue, permission prompts. Read the code too (`acp-components`, `acp-ui`, the ACP Python SDK, and
   Hermes's `acp_adapter` — read-only), but the affordance bar is learned by *seeing* it work.
2. **Follow the decisions in this brief.**
3. **Get Hermes working first** — the first backend, end to end: server-as-ACP-client → Hermes
   `acp_adapter` → browser.
4. **Test by computer use until confident it works** — experience it as a user: drive the real UI,
   see it render, feel the affordances. **Computer use (seeing and interacting), not just headless
   browser automation.**
5. **Meet the bar of the references** (Zed especially — the affordances in "The bar").
6. **Preserve the existing design decisions that still make sense under ACP; drop the rest** (see
   "What exists").
7. **Once fully tested and confident, rip out the old non-ACP system** so there is one working way to
   interact (see "What exists").

**Verification is by computer use.** The bar is experiential — you cannot assert "the user can always
tell what the agent is doing" or "steer-vs-queue is never a guess" from a headless test — so judge it
by seeing and using it, both the references and your build. Keep `./verify` + browser automation for
regression, and use Codex reviews (`/codex-cli`) on intricate logic. **You need a computer-use-capable
environment for this** — confirm it's available before relying on it.

## The bar (acceptance criteria)

- **The user can always tell what the agent is doing** — a persistent, legible status (working /
  thinking / compacting / waiting).
- **Compaction is explicitly signified, never silent** — and expandable to inspect.
- **Steer vs queue** — a mid-turn send tells the user which will happen *and* confirms which did;
  never blocked, never a guess.
- **Thinking & tools hidden by default but always expandable** — and **thinking survives a refresh
  without spilling** into plain transcript text. Achieve this via typed `session/load` replay
  forwarded to the browser as typed `agent_thought_chunk`; **do not flatten reasoning into message
  content.**
- **Permission/approval is clear, explicit, user-driven** (`request_permission` ↔ the gate flow).
- **No silent state changes** — anything that alters what the agent is doing has a visible signifier.

## What exists — reuse, preserve, remove, don't touch

- **Reuse:** the per-employee child registry at `src/planner/minds/employee_child_registry.py`. It
  already spawns and manages one agent subprocess per employee; the ACP client rides this seam.
- **Preserve (still valid under ACP):** the server as the **single source of truth for canonical
  product data** (tickets, board, gates) — the live conversation stream is a separate live socket,
  not a canonical store, and stays that way; the employee/entity model; the gate/approval flow
  (→ `request_permission`).
- **Remove at the end (once the new system is proven):** the current non-ACP conversation system —
  both non-ACP backend variants, the hand-rolled event vocabulary
  (`src/planner/hermes_backend/neutral_vocabulary.py`), the current pane's flat history rehydration
  and its client (`web/src/lib/neutralPane.ts`), and the local patches on the Hermes checkout.
  Result: one ACP path.
- **Don't touch:** the Hermes checkout (`~/.hermes/hermes-agent`) except **read-only** — no writes,
  no git commands there under any circumstance.

## References

- **Zed** — the affordance **north-star**: collapsible thinking, tool cards, diff review, plan,
  permission prompts, the "Context Compacted" entry, steer / send-now / stop. Match its legibility,
  not its code (Rust/GPUI). `zed.dev/acp`, `zed.dev/docs/ai/agent-panel`.
- **acp-ui** (`github.com/formulahendry/acp-ui`, Vue) — a complete working web ACP client; secondary
  UX reference; its ACP-over-websocket web build mirrors our client-over-websocket shape.
- **acp-components** (`github.com/zvzuola/acp-components`) — the code / state-model donor.
- **ACP spec** — `agentclientprotocol.com` (pin v1).

## Left to you to decide

- Exact component inventory and the Svelte port specifics.
- The envelope's precise schema — which ACP updates cross to the browser verbatim vs. enveloped.
- Phase order within the build (beyond "Hermes first").
- Whether any piece justifies reopening the question of migrating the frontend off Svelte. Default:
  **keep Svelte.**
