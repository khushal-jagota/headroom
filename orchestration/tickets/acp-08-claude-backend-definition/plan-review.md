# ACP-08 Claude backend plan review

> Owner amendment, 2026-07-20: the historical summary/fork capture finding below is superseded by
> `orchestration/acp-migration/compaction-primary-source-audit.md`. The current contract keeps the
> reviewed per-generation lifecycle deduplication and exact failures, but completion is content-free
> on the same session; fork capability and summary extraction are no longer preflight or dispatch
> requirements.

## Verdict

**NOT READY** until the three findings below are incorporated. The proposed terminal preflight is
not a real ACP-conformance blocker: this exact adapter is conformant when it truthfully declares
`filesystem=False`, `terminal=False`, and `permission=True`.

This was a read-only review. No package was installed, no model was called, no tests were run, and no
product source was changed. I inspected the published npm tarballs for
`@agentclientprotocol/claude-agent-acp@0.59.0` and
`@anthropic-ai/claude-agent-sdk@0.3.207`, plus the installed Claude Code binary.

## P0 — The reverse-terminal dispatch blocker is overstated and contradicts the frozen capability model

The published adapter has no standard ACP `terminal/*` request. Its `ClientConnection` implements
only standard `session/request_permission`, `fs/read_text_file`, and `fs/write_text_file` client
requests. Neither filesystem method is called elsewhere in the published adapter. Bash executes
inside the embedded Claude Code runtime. When a client advertises the adapter-specific
`_meta.terminal_output` extension, Bash is represented with `_meta.terminal_info`,
`_meta.terminal_output`, and `_meta.terminal_exit`; otherwise its output remains a standard typed
tool-call content block. Panels currently advertises standard `ClientCapabilities.terminal`, not
that private metadata capability.

That is not a protocol failure. ACP-00 says `ReverseServiceCapabilities` are independently declared,
ACP-02 says filesystem and terminal are composed only when the selected backend declares them, and
the frozen common ten-probe harness contains no requirement that every backend originate filesystem
or terminal reverse calls. The Codex backend contract already applies the same correct rule.

Required correction:

- Freeze Claude as `filesystem=False`, `terminal=False`, `permission=True` for this pin.
- Remove Gate 0's stop-on-missing-terminal rule and the claimed need for an owner waiver. Correct the
  ACP-08-specific wording in the contract/program plan that calls reverse terminal mandatory; it is
  stricter than, and inconsistent with, the frozen generic contract.
- Test that Panels advertises neither filesystem nor terminal, that real permission requests cross the
  shared broker, and that Bash still arrives as a typed tool call with visible output. Do not advertise
  the adapter's private terminal extension and do not call that extension a reverse terminal.

If the owner separately requires server-owned reverse terminals from Claude, `0.59.0` cannot provide
that feature. It is not a reason to withhold an otherwise functional Claude worker backend.

## P1 — The plan promises pre-advertisement capability validation without a production mechanism

The package really initializes as protocol v1, name
`@agentclientprotocol/claude-agent-acp`, version `0.59.0`, with image and embedded-context prompt
support, `loadSession: true`, and `sessionCapabilities.fork`. However, the settled Panels child only
validates protocol/name/version/load during lazy child initialization and merely records whether fork
was present. It does not validate image/context support, and the current synchronous composition does
not start an ACP child before the catalog is served.

The plan says identity and all capabilities fail startup before `claude` is advertised, but names no
startup/lifespan preflight or state transition that makes that true. A one-time development probe and
an executable `--version` check do not prove the runtime process that production will advertise.

Required correction: name one bounded, no-model production startup preflight through the exact locked
argv and confined environment, before the catalog/manifest accepts traffic. It must perform ACP
initialize, validate protocol/name/version/image/context/load/fork, close the probe child, and abort
startup on mismatch. Keep the ordinary employee child lazy and keep the catalog immutable; do not
advertise first and remove the key after a failed first Ticket demand.

## P1 — Claude compaction needs an exact per-generation state machine, not the stated stateless strategy

The exact adapter emits ordinary `agent_message_chunk` text `Compacting...`, then either
`Compacting completed.` or `Compacting failed<reason>`. The inspectable summary is not carried by
those status updates; after load/fork it is replayed as a synthetic user message beginning with the
prefix frozen in the contract. The prefix is present in both embedded Claude Code `2.1.207` and the
installed `2.1.215` binary.

Panels' broker accepts only a `state="compacting", trigger="automatic"` observation and deduplicates
by the strategy's `boundary_id`. Identical status text supplies no stable boundary identity. The plan's
“stateless apart from exact-generation compaction observation” does not say how multiple compactions
in one generation remain distinct, how duplicate start/completion frames coalesce, or how the exact
`Compacting failed...` cause reaches capture instead of becoming the less useful “summary missing”.

Required correction: freeze a small strategy-owned state machine keyed by exact binding generation:
one idle→compacting transition allocates one observation identity; repeated start frames coalesce;
completion permits the normal fork/load capture; failure records the exact adapter reason and makes
capture return the visible failed result without pretending a summary exists; settlement returns the
generation state to idle so a later compaction is distinct. Explicit `/compact` must adopt the same
observation without creating a second boundary. Define what happens to the adapter's three status
text chunks so they do not accidentally become a second, misleading compaction transcript lane.

## Verified package/runtime facts

- npm shasum `8fe5de711a2c481d3d2c37d8e7f51083a2e83d12` and integrity
  `sha512-GejLH5qxsI5IoSDfhyOVDEsRNxqi6y0Rcj5FstVeOwMACSht/bUXII0HILbzOQNoA5qlyZle3FRvf+CAjD7Rpg==`
  match the contract; the published package records git head
  `30b7c06f7640fb6a0530ba18f85e26fe2bc08882`.
- Its exact dependencies are ACP SDK `1.2.1` and Claude Agent SDK `0.3.207`; the latter records
  embedded Claude Code `2.1.207`. Local Node is `v22.22.3`. The ambient `claude` is independently
  installed Claude Code `2.1.215` and must not be the production executable.
- Exact production argv through absolute Node plus the locked `dist/index.js`, the six-name inherited
  environment allowlist, no `CLAUDE_CODE_EXECUTABLE`, first-root cwd/additional directories, and
  `_meta.systemPrompt={type:"preset", preset:"claude_code", append:<Panels role>}` fit the package.
- `session/load` awaits typed history replay before returning. `session/fork` creates a fresh UUID by
  resuming with `forkSession: true`. Native steer is absent, so `supports_steer=False` is correct.
- The proposed human → Automatic Employee → human → restart → compact proof through one durable
  binding is appropriately strong and should remain unchanged after these corrections.

## Finding dispositions and post-amendment verdict

- **P0 reverse capabilities — accepted and resolved.** The contract and plan now freeze
  `filesystem=False`, `terminal=False`, and `permission=True`; require no filesystem/terminal
  advertisement or private terminal extension; keep Bash as visible ordinary typed tool output; and
  remove the terminal stop/owner-waiver language.
- **P1 pre-advertisement validation — accepted and resolved.** The Claude registration now owns one
  initialize-only transient-child preflight under a single 30-second deadline. The FastAPI lifespan
  runs it through exact production argv/environment/cwd before yielding, validates exact protocol,
  identity, image/context/load/fork capabilities, closes the probe, and aborts startup before catalog
  traffic on any failure. Normal employee children remain lazy.
- **P1 compaction state — accepted and resolved.** The strategy now has an exact
  binding-generation/ordinal state machine for start, completion, failure, dedupe, explicit-boundary
  adoption, repeated compactions, exact failure propagation, five-minute capture, and settlement back
  to idle. The three adapter status chunks are normalized to non-transcript controls, so the Panels
  compaction envelope remains the only visible compaction lane.

**Post-amendment verdict: READY.** The three blocking findings are incorporated without changing the
functional human/Automatic Employee continuity proof or adding backend-specific UI/session paths.
