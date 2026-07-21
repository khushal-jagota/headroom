# Provider implementation review — MR-02 / MR-03 / MR-04

Verdict: **READY**

Scope: one bounded independent review of the settled Hermes, Codex, and Claude
provider implementation plus the five live documentation files against
`contract.md` and the three provider plans. Findings were limited to concrete P0/P1
violations.

## Findings

No P0 or P1 violations found.

## Evidence checked

- Hermes discovery runs the exact resolved Hermes interpreter and source root with the
  production planner `HERMES_HOME`, imports `load_config_readonly`, and calls the pinned
  current-provider catalog helper. The live read-only probe resolved provider
  `openai-codex`, native model `gpt-5.6-sol`, and seven models. Probe stderr and malformed
  payloads never enter product errors, so credential-bearing diagnostics are not exposed.
- The Hermes launch adapter rejects reasoning before any RPC and sends an explicit model
  only through the separate legacy capability. `SdkAcpEmployeeChild` emits exactly
  `session/set_model` with `sessionId` and `modelId`; the role-skill decorator forwards it
  unchanged. The real SDK scripted-peer proof checks the camel-case wire and
  `new_session -> set_model -> prompt` order.
- The Hermes checkout was inspected read-only only: no git command or write was performed
  there. The reviewed Panels change adds a subprocess probe and adapter in this repository;
  it contains no Hermes source-writing path. Before/after source-hash dogfood remains the
  final integration evidence explicitly deferred by the MR-02 report.
- Codex constructs one pinned definition and SDK factory, returns that factory for durable
  work, and gives the same definition/factory to the generic stable ACP configuration
  adapter. Claude does the same with its compaction-normalizing durable factory; its child
  forwards `set_config_option` and `close_session` without bypassing the decorator.
- The shared adapter discovers and applies only semantic `model` and `thought_level`
  selects. It validates exact advertised values and exact returned current values,
  refreshes options after model selection, applies model before reasoning, and never
  aliases, clamps, or substitutes an explicit choice.
- Every Codex/Claude discovery path closes the temporary ACP session and child in `finally`;
  close failures remain visible and uncached. Hermes discovery creates no ACP session.
- The registry invokes provider configuration only in the no-binding first-session branch.
  Bound load, requested-cancel replacement, compaction, winner adoption, and New
  Conversation paths load/create without reapplying historical launch fields. Bound
  employee resolution clears both launch inputs, and the provider-focused tests explicitly
  cover the named paths.
- The frontend contains no provider model or reasoning inventory; it renders the product
  catalog response. `docs/README.md`, `docs/chat.md`, `docs/employee-runtime.md`,
  `docs/tickets-and-gates.md`, and `docs/worker-types.md` consistently describe the actual
  Kickoff-only controls, Hermes model-only behavior, semantic Codex/Claude discovery,
  launch-only application, and historical post-binding values.
- The orchestrator's settled cross-provider gate is green: Ruff, strict Mypy, 343 focused
  Python tests, the full frontend test script, and zero Svelte diagnostics.

Real first-prompt Safari dogfood and the final repository-wide `./verify` are correctly
reserved for final integration and are not unresolved implementation-review findings.
