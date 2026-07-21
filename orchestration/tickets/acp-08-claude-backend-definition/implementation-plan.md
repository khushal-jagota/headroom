# ACP-08 Claude Code ACP backend implementation plan

## Gate 0 — settle the executable before source edits

Wait for ACP-06 and ACP-07 integration. Confirm that the production composition has the one ordered
`EmployeeBackendCatalog`, `claude` is not already registered, and a fresh Ticket can store a generic
non-Hermes `employee_backend` without a backend-specific UI branch.

Using a temporary directory only, inspect the exact locked package and run no-prompt stdio probes:

1. freeze `@agentclientprotocol/claude-agent-acp@0.60.0`, its lockfile integrity, Node `>=22`, exact
   initialize identity/version, load capability, and the embedded Agent SDK/Claude Code
   versions;
2. exercise session new/load without a model prompt and capture the exact request/response shape;
3. confirm the fixed
   `ReverseServiceCapabilities(filesystem=False, terminal=False, permission=True)`, standard typed
   Bash output, and absence of the adapter-private terminal metadata capability from Panels'
   initialize request. Missing reverse filesystem/terminal calls are expected, not a dispatch gate.

## Phase 1 — pin and define the backend

Update the one shared `agent_backends/package.json` and generated lockfile with the exact Claude
dependency while preserving ACP-07's exact Codex dependency. If ACP-07 has not created those files
yet, create one combined two-adapter manifest rather than a temporary Claude-only manifest. Add only
`agent_backends/node_modules/` to `.gitignore`. Document `npm ci`; never execute either backend
through `npx`. The accepted lock must resolve one compatible `@agentclientprotocol/sdk@1.2.1` and
one compatible Zod package for both adapters without duplicate or invalid peer edges.

Add `src/planner/conversation/claude_backend.py` with:

- constants for backend key, agent identity/version, Node minimum, adapter entrypoint, inherited
  environment names, and worker-role append text;
- a resolver that accepts the repository root and an already resolved absolute Node path, validates
  the locked adapter file and Node major version, and returns the immutable `AgentBackendDefinition`;
- exact `BackendTurnCapabilities(supports_steer=False, observes_compaction=True)` and
  `ReverseServiceCapabilities(filesystem=False, terminal=False, permission=True)`;
- first-workspace-root cwd resolution; and
- a child-factory decorator that copies every generic new/load request and adds the identical
  Claude `_meta.systemPrompt` append while delegating all lifecycle and ingress behavior to
  `SdkAcpEmployeeChild`.

The append names absolute existing skill files and explains the `skill_view` substitution; it does
not embed their contents. Assert that unrelated caller `_meta` survives and conflicting
`systemPrompt` input fails closed rather than being overwritten.

The decorator also rewrites only exact Claude compaction status chunks into non-transcript
`SessionInfoUpdate` controls carrying the original text. This lets the generic prompt barrier deliver
the controls to the strategy while keeping them out of assistant transcript and Automatic Employee
text. It does not advertise or translate `_meta.terminal_output`.

Add `src/planner/conversation/claude_turn_strategy.py` with one event-loop-confined state record per
exact `(employee_id, acp_session_id, backend_key, binding_generation)`. Each record has
`next_ordinal`, phase, boundary identity, terminal event, and optional exact failure. It must:

- reject `steer` as unsupported;
- on exact `Compacting...` from `idle`, increment the ordinal and return
  `<employee>:<session>:<generation>:<ordinal>`; return the same identity for repeated starts;
- accept only the first exact `\n\nCompacting completed.` or `\n\nCompacting failed<reason>` terminal
  control, make duplicates/conflicts no-ops, and preserve the failed text after removing only the two
  separator newlines;
- let the broker adopt that observation into an already-open explicit `/compact` boundary, wait for
  the terminal event, publish content-free completion or the exact failure on the same session, and
  reset to `idle` so another compaction in the same generation increments the ordinal;
- use the existing 300-second capture budget without adding a 60-second health deadline; and
- suppress the adapter's exact compaction control chunks from assistant transcript and Automatic
  Employee output without inspecting backend-generated context.

Add one initialize-only startup preflight to the materialized Claude registration at ACP-07's
executability seam. During the app lifespan, after composition/catalog materialization but before the
lifespan yields, it:

1. creates one transient child through the exact Claude factory using a synthetic Ticket employee
   rooted at the repository, so argv, cwd, and confined environment are the production ones;
2. under one 30-second absolute deadline calls only ACP initialize and validates protocol 1, exact
   name/version, `promptCapabilities.image`, `promptCapabilities.embeddedContext`, and `loadSession`;
3. closes the child in `finally`, force-closes it if graceful close cannot finish inside the same
   deadline, and propagates a phase-specific startup error.

The preflight callbacks reject any unexpected update/permission request, and no new/load/prompt
method is called. On failure app startup aborts before any manifest route can expose the immutable
catalog; on success the transient child is gone and normal employee creation remains lazy. The cheap
runtime executability status thereafter reads the successful preflight result rather than spawning
another probe.

## Phase 2 — prove the adapter in isolation

Add `tests/unit/test_claude_acp_backend.py` and extend the shared conformance subject only through its
existing backend-definition seam. Cover:

1. locked absolute argv, no global `claude`/`npx`, environment allowlist and generic identity
   injection, cwd/additional roots, exact initialize identity, and startup failures;
2. the same system-prompt metadata on new/load, preserved generic fields, and no second session
   store;
3. real adapter new/load continuity, typed thought/text/tool/plan updates, cancellation, real
   permission options and settlement, fixed false/false/true reverse capabilities, no advertised
   private terminal extension, and visible Bash output as an ordinary typed tool call;
4. unsupported Steer with generic Queue and Send Now unchanged;
5. exact start/completed/failed control normalization, repeated starts and terminal controls,
   conflicting terminal first-wins, two distinct compactions in one binding generation, explicit
   boundary adoption, status exclusion from transcript/worker text, exact adapter failure, process
   death, same-session continuity, and the full five-minute-budget boundary
   using a fake clock rather than waiting five minutes;
6. a real app-lifespan preflight orders initialize/validation/close before route exposure, performs no
   session or prompt operation, remains one-shot while ordinary children stay lazy, and aborts within
   the shared 30-second deadline for spawn, capability, protocol, exit, and hang failures.

Separate three evidence classes in test/report names: scripted common-contract proof, exact-package
no-model protocol proof, and paid real-Claude prompt proof. A scripted reverse call cannot satisfy the
real adapter capability claim.

## Phase 3 — register once and prove shared Ticket ownership

After Phases 1–2 are green, add the Claude definition/factory/preflight as one entry in ACP-07's
ordered production registration tuple, after Hermes and Codex. Do not add a second allowlist. Each app
lifespan materializes the immutable catalog, completes every registration's bounded startup preflight,
and only then yields to serve the manifest. A failed Claude preflight aborts startup; it never removes
the key after exposure or falls back to Hermes.

Extend the production-composition e2e seam with a fresh Ticket selected as `claude` and a paid,
opt-in real-backend test. Record the binding after each action:

1. human Chat stores nonce A;
2. an eligible Automatic Employee step asks the same worker to read the Ticket through
   `panels worker my-ticket`, recall nonce A, and file the normal proposal;
3. human Chat asks for the proposal and nonce A;
4. restart the Panels composition, load the same durable session, and recall both facts;
5. run `/compact`, allow up to five minutes while preserving visible compacting state, verify the
   content-free completion on the same binding/session, then recall both facts.

The session ID must remain identical through all five steps. The
transcript and automatic result must come through the existing typed hub and step settlement; no DB
chat insertion counts as worker delivery.

## Phase 4 — real Panels Computer Use proof and handoff

Start the actual Panels server with the project-local adapter available. Through Computer Use:

- create one disposable Ticket in the ordinary UI, leave its Worker type unchanged, select `claude`
  in Kickoff, and start it;
- send the nonce prompt in Ticket Chat and visibly inspect streamed Claude text, thought/tool state,
  permission handling, and the absence of Steer;
- place the Ticket in the normal eligible Day/Stage state and let the automatic discovery/runner
  produce its proposal; inspect that proposal and continue Chat;
- reload and restart once to prove load continuity, then compact and wait for completion without
  treating the first 60 seconds as a failure.

Capture the UI actions, typed transcript outcome, exact backend key, binding IDs/generations, and
adapter/Node versions in `live-dogfood-evidence.md`. Delete only the disposable Ticket/session through
ordinary product cleanup after evidence is complete; do not clear unrelated sessions.

Update the employee-runtime and installation docs with the project-local dependency, authentication
expectation, selection/freeze behavior, unsupported Steer, five-minute compaction observation, and
failure behavior. Add an implementation report with exact focused commands and full outputs. Use one
independent review round against this contract, then hand the integrated tree to ACP-10; do not run
canonical `./verify` in ACP-08.

## Planned source files after the gates

- `.gitignore`
- `agent_backends/package.json`
- `agent_backends/package-lock.json`
- `src/planner/conversation/claude_backend.py`
- `src/planner/conversation/claude_turn_strategy.py`
- `src/planner/conversation/__init__.py`
- ACP-07's production catalog/composition registration file
- `tests/unit/test_claude_acp_backend.py`
- the existing ACP conformance/composition/e2e test files needed by the named proofs
- `docs/employee-runtime.md` and the existing installation/configuration page
- this ticket's evidence, report, and review files

If an existing generic seam cannot express the frozen behavior, stop and cut a narrowly scoped
generic follow-up. Do not broaden this backend ticket into changes to UI, Ticket schema, session
ownership, broker semantics, or the shared wire protocol.
