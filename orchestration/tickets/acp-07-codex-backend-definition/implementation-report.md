# ACP-07 Codex backend definition — implementation report

Status: **focused provider gates passed; ready for independent review and serial integration**.

## Outcome

The Codex ACP provider slice is implemented as one thin backend definition behind the generic
employee-backend catalog. It uses the existing official-SDK child, durable binding, permission
broker, turn broker, transcript, and Automatic Employee gateway. It adds no second conversation
path, product field, UI branch, model picker, or provider-specific worker workflow.

The stable catalog symbol is the zero-argument
`build_codex_employee_backend_registration()`. Its runtime builder uses the generic build context's
repository root and data directory, resolves absolute locked artifacts, creates the generic
`SdkAcpEmployeeChildFactory`, exposes the exact executable probe, and deliberately has no eager
startup preflight.

## Exact runtime definition

- Backend key: `codex`.
- Adapter: `@agentclientprotocol/codex-acp` `1.1.4`, release commit
  `921d466e3aaf747885e395e761cd74ad2d39cd96`.
- Shared locked runtime: ACP SDK `1.2.1` and `@openai/codex` `0.144.6`.
- Verified local tools: Node `22.22.3`, npm `10.9.8`, and exact adapter version output
  `@agentclientprotocol/codex-acp 1.1.4`.
- Exact argv: the absolute resolved Node executable followed by this repository's absolute locked
  `agent_backends/node_modules/@agentclientprotocol/codex-acp/dist/index.js`.
- Cwd: the employee's first workspace root. Additional roots remain generic registry behavior.
- Inherited environment: the six SDK defaults plus optional `CODEX_HOME`, `CODEX_API_KEY`, and
  `OPENAI_API_KEY` only. Ambient `CODEX_PATH`, `CODEX_CONFIG`, and `MODEL_PROVIDER` are excluded.
- Overrides: absolute `APP_SERVER_LOGS`, `NO_BROWSER=1`, `INITIAL_AGENT_MODE=agent`, and
  `DEFAULT_AUTH_REQUEST={"methodId":"api-key"}`.
- Expected initialize identity: name `@agentclientprotocol/codex-acp`, version `1.1.4`; the generic
  SDK child owns protocol-v1 and `loadSession=true` enforcement.
- Reverse capabilities: filesystem false, terminal false, permission true.
- Native Steer: unavailable and visibly rejected. Queue and Send Now remain generic broker
  operations.

The executable check runs only the exact locked adapter's `--version` under an empty environment.
It never opens a session, requests authentication, calls a model, invokes `npx`, or consults an
ambient Codex executable.

## Compaction and replay

`CodexAcpTurnStrategy` consumes only the pinned adapter's exact namespaced
`_meta.contextCompaction=true` tool-call start and completed updates. Capture waits for that tagged
terminal update on the same ACP session and returns a content-free lifecycle boundary. It does not
fork or replace the durable binding, parse display prose, inspect Codex files, wait for a summary, or
add a provider timer. The generic in-place seam owns the existing five-minute absolute breaker.

The provider failure hook returns a concrete display-safe `TypeName: message` only for an exact
`/compact` prompt or the exact binding-generation's already-active compaction state. It resets that
state before the generic generation-failure path runs. Ordinary prompt failures return `None`, and
there is no invented tagged `status=failed` update.

Replay remains typed. Durable content-free compaction boundaries are appended from generic
provenance. The pinned upstream limitation is frozen explicitly: a stored `Plan:` replay stays an
`AgentMessageChunk`; Panels does not parse it into a plan.

## Deterministic proof

The focused suite proves exact argv and version, confined environment, cwd, capabilities, missing or
invalid paths, the zero-argument lazy registration, exact adapter executability, unavailable Steer,
exact compaction shape/session matching, same-session completion, cancellation-safe state reset,
exact explicit and active-automatic failure reasons, ordinary/unsafe failure fallback, typed replay,
the stored-plan limitation, and the common conformance harness. The generic in-place compaction seam
also passes alongside the provider tests.

Focused Ruff and strict Mypy pass. The settled Python matrix is 27/27. Exact commands and output are
in `focused-checks.txt`.

## Integration boundary

This lane did not edit the shared package manifest/lock, catalog tuple, composition, broker, runtime
ports, frontend, docs, `PROGRESS.md`, or `decisions.md`; those are owned by their serial integration
lanes. The shared installed manifest already exposes exact Codex `1.1.4` alongside Claude `0.60.0`,
but catalog insertion and the broker's generic prompt-failure consultation must be integrated before
the backend is reachable through Panels.

Authenticated Computer Use dogfood remains required after integration: one disposable Ticket must
prove human chat and one real Automatic Employee step use the same Codex binding/session, followed by
thought replay, a tool/diff/terminal update, permissions, Queue, Send Now, unavailable Steer,
`/compact`, hard refresh, and process restart. No authenticated prompt was sent in this provider
lane. Canonical `./verify` was not run; ACP-10 owns the final settled-tree gate.
