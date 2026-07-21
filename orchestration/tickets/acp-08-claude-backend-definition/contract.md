# ACP-08 functional Claude Code ACP worker backend

## Outcome

`claude` becomes one registered `employee_backend` for fresh Tickets. A user may select it in the
generic ACP-07 Kickoff control, and both human Ticket Chat and an Automatic Employee step use the
same durable Claude ACP session through the existing conversation hub, broker, binding repository,
and step gateway.

This is a backend activation, not a second worker system. It adds no Claude-specific UI, Chief
selector, model selector, transcript path, chat table, or automatic-work path. It does not change
Gemini or the read-only Hermes checkout.

Source work waits for ACP-06 and ACP-07 to be integrated. In particular, the production backend
catalog, stored `Ticket.employee_backend`, and generic Kickoff selector must exist before `claude`
is registered.

## Pinned runtime definition

1. Add one exact Claude dependency to the project-local shared backend manifest:
   `@agentclientprotocol/claude-agent-acp@0.60.0`. Its published dependency closure includes exact
   `@agentclientprotocol/sdk@1.2.1` and `@anthropic-ai/claude-agent-sdk@0.3.215`; that Agent SDK embeds
   Claude Code `2.1.215` through its exact platform package. Record the npm shasum
   `981d5baa477c66d8bef75313f29509e4f9abd29e` and integrity
   `sha512-+ZZCJukpKdEY+/O982UCtgGHOY+MKa/JPpZ34v25ITawRyQyg3cqqOGo3M+9TsA4D+T/NXb+kT3zUB1uQZhY+Q==`
   in implementation evidence. Node must be version 22 or newer. The same `agent_backends` manifest
   and lockfile also own ACP-07's exact Codex dependency; ACP-08 must preserve that entry and update
   the single generated lock rather than create or replace a Claude-only manifest.
2. Production resolves an absolute Node executable and the absolute locked adapter entrypoint
   `<repo>/agent_backends/node_modules/@agentclientprotocol/claude-agent-acp/dist/index.js` at
   startup. The exact argv is `(resolved_node, resolved_adapter_entrypoint)`; no shell, `npx`, global
   package lookup, or unpinned executable is allowed.
3. Do not set `CLAUDE_CODE_EXECUTABLE`: the adapter must use the Claude Code binary embedded by its
   pinned Agent SDK. The locally installed `claude` is not the production dependency.
4. The definition key is `claude`. One initialize-only production preflight runs through the exact
   locked argv, confined worker environment, and first-root cwd during the FastAPI lifespan before
   the app yields or any catalog/manifest route accepts traffic. It has one 30-second absolute
   deadline, creates no ACP session and sends no prompt, validates agent name
   `@agentclientprotocol/claude-agent-acp`, version `0.60.0`, protocol version 1, prompt image/context
   support and `loadSession: true`, then closes the transient child. A missing
   file, Node below 22, spawn/initialize/close error, deadline expiry, identity/version mismatch, or
   missing capability aborts startup. The immutable catalog is never served in a partially validated
   state, and ordinary employee children remain lazy.
5. The only inherited environment names are `HOME`, `LOGNAME`, `PATH`, `SHELL`, `TERM`, and `USER`.
   The generic child still injects exact `PLAN_ACTOR=worker` and `PLAN_TICKET_ID=<ticket id>` and
   scrubs ambient values. Do not inherit provider API keys, `CLAUDE_CODE_EXECUTABLE`, a custom Claude
   config directory, or Hermes variables. Existing native Claude authentication in the process
   user's keychain or the default `HOME/.claude` config directory remains available without Panels
   modifying either store; missing or expired authentication is a visible backend/session error,
   never a fallback to Hermes.
6. `working_directory_for(employee)` is the Ticket employee's first absolute workspace root. The
   remaining roots continue through ACP `session/new` and `session/load` as
   `additionalDirectories`; there is no Claude-only cwd or filesystem allowlist.

## Panels worker role and session ownership

7. Claude must receive the existing Panels worker role without modifying user-global Claude config.
   A Claude-specific child decorator adds the same `_meta.systemPrompt` to new and load
   requests. It preserves the adapter's `claude_code` preset and appends a short instruction to read
   the absolute repository `skills/panels-worker/SKILL.md`, use `PLAN_TICKET_ID`, and read the named
   `skills/<specialist>/SKILL.md` directly wherever the Panels skill says `skill_view`. No skill text
   is copied into Python and no `.claude` files or workspace symlinks are created.
8. The generic registry remains the owner of process and session lifecycle. New creates one adapter
   UUID; attach/restart loads the stored ID; compaction retains that same ID and durable binding.
   The decorator may add only the frozen session metadata. It must not maintain a
   second session map, invoke `claude --resume`, or translate Panels chat rows into model context.
9. ACP-07's selected `Ticket.employee_backend` resolves the same `ConversationEmployee` for the hub
   and `AcpStepGateway`. A proof must send one human prompt, then one Automatic Employee prompt, and
   then another human prompt through the same current binding. The latter two turns must recall a
   nonce from the first turn, and the automatic step must use the normal Panels worker CLI to read the
   Ticket and file its proposal.

## Turn, compaction, and reverse-service semantics

10. Claude does not implement native ACP steering. The definition declares `supports_steer=False`;
    the generic browser capability disables Steer. Queue and Send Now remain first-party broker
    behavior, including normal cancellation and recovery. Prompt queueing advertised in Claude
    `_meta` is not relabelled as Steer.
11. The Claude child decorator converts only the adapter control chunks `Compacting...`,
    `\n\nCompacting completed.`, and `\n\nCompacting failed<reason>` from agent-message text into
    non-transcript session metadata carrying the exact original text. The generic ingress and prompt
    barrier still admit them, but they are not collected as worker output or rendered as a second
    assistant-message lane. All other Claude message chunks are unchanged.
12. The Claude turn strategy owns a state machine keyed by the exact employee/session/backend/binding
    generation. Each key retains a monotonically increasing compaction ordinal and one phase:
    `idle`, `compacting`, `completed`, or `failed`. An exact start in `idle` increments the ordinal,
    allocates identity `<employee>:<session>:<generation>:<ordinal>`, enters `compacting`, and returns
    one automatic observation. Repeated starts return that same identity for broker deduplication.
    The first exact completed or failed control for that identity wins; duplicates and a contradictory
    later terminal control are ignored. Failure retains the exact adapter text with only its two
    leading separator newlines removed.
13. The broker still creates an explicit `/compact` boundary before sending the prompt. Its first
    Claude start observation is adopted as that boundary's observation identity, so no automatic
    duplicate is published. The strategy waits for the state machine's terminal control, publishes
    content-free completion or the stored exact failure, and returns that binding-generation state
    to `idle` while retaining its ordinal so a later compaction is distinct. It never forks, reloads,
    or extracts context as part of compaction.
14. Capture uses the existing five-minute compaction observation budget. The worker remains visibly
    compacting during that interval. Taking longer than 60 seconds is not a failure. An explicit ACP
    error, process exit, adapter `Compacting failed...` result, or expiry of the five-minute
    observation budget is reported with its concrete cause. A budget expiry says Panels stopped
    waiting after 300 seconds; it does not invent a backend rejection or switch backends.
15. Claude-generated compacted context remains backend-private. Panels neither extracts it from SDK
    messages nor renders it. The adapter's generated summary user message stays suppressed exactly as
    upstream implements it; the durable session ID remains unchanged.
16. The definition freezes
    `ReverseServiceCapabilities(filesystem=False, terminal=False, permission=True)`. Panels advertises
    neither reverse filesystem nor reverse terminal to this adapter and does not advertise its private
    `_meta.terminal_output` extension. Permission crosses the shared Panels broker. Claude Code's
    internal Bash remains an ordinary typed tool call with visible standard tool content; it is not
    labelled a Panels reverse terminal. The common harness retains its generic reverse-service probes,
    while the Claude lane proves this exact truthful capability tuple and real permission behavior.

## Required proof

- Definition tests freeze argv, environment, cwd/additional roots, identity/version/capabilities,
  role metadata on new/load, fixed reverse capabilities, no native Steer, and fail-closed
  composition. The production lifespan proof asserts the initialize-only preflight completes and
  closes before route exposure, consumes no session/model call, and aborts within its one deadline on
  every mismatch or hang.
- The shared conformance harness runs the definition's real stdio process for initialize, session
  new/load, typed updates, permissions, cancellation, and compaction. Paid prompts are used only
  in the separately labelled live proof; deterministic protocol tests must not consume model work.
- One real Computer Use dogfood uses the running Panels app and a disposable fresh Ticket—not direct
  API seeding for the interaction—to select `claude`, send human Chat, allow one Automatic Employee
  step, inspect its proposal and transcript, reload/restart Panels, and continue the same session.
  Evidence records the selected backend and one durable session ID retained through compaction.
- Focused Ruff, strict Mypy, affected backend/conformance/composition tests, and the one actual
  Computer Use proof pass. ACP-10 owns the single canonical `./verify` run.

Read-only inspection of the published `0.60.0` adapter confirms that it does not issue standard ACP
filesystem or terminal reverse calls. That is represented by the frozen false capabilities above,
not treated as a dispatch blocker. If server-owned reverse terminals are requested later, they need a
different conformant adapter/version and a separate contract.

## Source boundary

Once the dependency gates and initialize preflight contract are available, implementation is limited
to:

- the one shared project-local `agent_backends` package manifest/lock and its `.gitignore` entry,
  preserving ACP-07's exact Codex dependency;
- one Claude definition/turn-strategy/decorating factory module plus exports;
- the single ordered production backend registration point delivered by ACP-07;
- focused backend, shared-conformance, composition/e2e tests and the live dogfood evidence;
- employee-runtime/install documentation and this ticket's report/review artifacts.

Do not alter generic Ticket/Kickoff UI, Chief composition, Worker-type semantics, the broker or binding
contracts, the generic transcript, Hermes source, or unrelated dependencies unless a failed frozen
contract proves a separately ticketed generic defect.
