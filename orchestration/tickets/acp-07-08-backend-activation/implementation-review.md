# ACP-07/08 backend activation — focused implementation review

## Verdict

**READY.** No unresolved P0/P1 contract violation was found in the settled Codex, Claude, generic
in-place compaction, prompt-failure, catalog, composition, or production-lifespan implementation.

## Findings

None.

## Focused evidence

- The single locked `agent_backends` manifest owns exactly Codex ACP `1.1.4` and Claude ACP `0.60.0`.
  Its generated lock resolves ACP SDK `1.2.1`, one Zod `4.4.3`, Codex `0.144.6`, Claude Agent SDK
  `0.3.215`, and the matching platform packages. Both definitions use absolute Node-plus-locked-
  entrypoint argv and their frozen identity, environment, cwd, Steer, and false/false/true reverse-
  service contracts. Generic initialization enforces protocol 1, exact name/version, and
  `loadSession=true`; Claude's preflight additionally enforces image and embedded-context support.
- Codex is materially lazy: registration/materialization creates no child, session, prompt, or model
  work, and its executable predicate invokes only the locked adapter's exact `--version` path with an
  empty environment. The confined worker environment omits ambient Codex path/config/provider and
  browser/auth-mode controls, preserves only the frozen optional auth inputs, and leaves `CODEX_PATH`
  absent so the adapter resolves its lock-owned Codex. Pinned adapter source performs authorization
  before `session/new` or `session/load`, so an auth failure precedes binding CAS; its load path replays
  before returning and a missing stored thread fails rather than creating a replacement.
- The Codex strategy recognizes only the pinned adapter's exact namespaced
  `_meta.contextCompaction=true` start/completed tool shapes on the exact session. It never accepts an
  invented failed tag, parses prose, reads private state, or changes the binding. Its replay path keeps
  the pinned stored `Plan:` assistant chunk typed as ordinary ACP text instead of reconstructing a
  plan. The bounded broker correction uses exact `TypeName: message` only for an explicit `/compact`
  or the same binding-generation's active compaction; ordinary prompt failures and invalid/raising
  hooks retain `Employee connection failed` and use the existing one-shot generation-failure path.
- Claude's decorator applies the same caller-preserving `claude_code` preset plus Panels worker-role
  append to new, load, and captured-load requests, rejects pre-existing `systemPrompt`, and converts
  only the three pinned compaction control shapes into non-transcript metadata. The strategy keys
  state by employee/session/backend/binding generation, retains ordinals, deduplicates starts, makes
  the first terminal control authoritative, preserves the exact adapter failure after only the two
  separator newlines, and resets on completion or cancellation. No generated compacted context is
  extracted or rendered.
- Claude alone owns one initialize-only startup preflight. The transient decorated child uses the
  production confined environment and repository-root cwd, performs no new/load/prompt operation,
  remains non-executable until validation and close succeed, and settles spawn/initialize
  cancellation plus graceful-close/force-close ownership under the one absolute deadline. Production
  startup audits storage, builds the one composition, awaits ordered preflights, and only then
  publishes `app.state.conversation` and starts Employee loops. Failure closes admission, shuts down
  the composition with the existing deadline, leaves conversation unavailable, starts no loop, and
  re-raises.
- The sole production registration tuple is exactly `hermes, codex, claude`; the Worker registry and
  composition consume the same catalog object, while Chief and all shipped Worker defaults remain
  Hermes. No provider-name branch exists in Ticket chat, binding resolution, the permission broker,
  `AcpStepGateway`, or `EmployeeStepRunner`. The settled non-Hermes production-composition proof sends
  one human prompt and one real Automatic Employee prompt through the same stored backend and ACP
  session, matching the Ticket mirror.
- The generation-bound in-place seam reacquires the exact runtime lease and accepts only terminal
  `ContextCompaction` results under the existing 300-second deadline. Codex and Claude return through
  that branch before every Hermes normalize/prepare/fork/private-load/CAS/commit/abort path, so their
  durable session ID and binding generation stay unchanged. Provider `TimeoutError` remains concrete;
  only Panels' timer becomes the explicit Panels deadline error.

## Review boundary and remaining gates

I read the frozen provider/activation contracts, plans, reports, focused evidence, the generic
selector contract/review, and the relevant current source, tests, lockfile, and pinned adapter source.
Per the review brief, I did not run models, network access, a live server, tests, or `./verify`, and I
did not edit product, test, or documentation source.

Authenticated Codex and Claude Ticket/Automatic-Employee dogfood and ACP-10's one canonical
`./verify` remain required completion gates. They are intentionally pending evidence, not an
implementation-review violation.
