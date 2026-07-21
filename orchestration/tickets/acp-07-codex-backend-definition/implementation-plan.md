# ACP-07 Codex backend definition — implementation plan

## Status and ordering

Plan complete; source dispatch waits only for ACP-06 and the generic ACP-07 selector/catalog slice.
Rebase this file list onto those settled names. The exact inspected `1.1.4` pin is the activation
candidate; its known stored-plan presentation difference is accepted and must be frozen in tests.

## 1. Freeze and qualify the runtime dependency

Add a small production runtime manifest under `agent_backends/`:

- `agent_backends/package.json`
- `agent_backends/package-lock.json`
- `.gitignore` for only `agent_backends/node_modules/`

Pin `@agentclientprotocol/codex-acp` exactly and use the lockfile to freeze its Codex binary package.
Install with `npm ci`; never invoke `npx`. Record the adapter version, source commit, lockfile Codex
version, Node version, and `--version` output in the implementation report.

Before product edits, run one no-model qualification probe against the installed entrypoint:

- initialize identity/version/protocol and capabilities;
- inspect/fixture-test live and loaded thought, plan, tool/diff/terminal, commands, and permissions;
- prove `session/load` finishes after typed replay;
- prove exact namespaced compaction start/completion and error observation on the same session;
- prove cancel permits the generic requested-cancel replacement/load path.

If a mandatory process/session/permission/cancellation probe fails, stop before registration and
retain the concrete output. Do not compensate with transcript parsing, Codex session-file reads, a
timer, or a test-only waiver. For `1.1.4`, missing fork and summary are compatible with the
lifecycle-only contract. The live typed plan/stored prose-plan difference is asserted exactly as a
known upstream presentation limitation rather than waived or repaired.

## 2. Implement the thin definition after qualification

Create:

- `src/planner/conversation/codex_backend.py`
- `src/planner/conversation/codex_turn_strategy.py`

`codex_backend.py` resolves and validates the absolute Node and adapter entrypoint, builds the exact
argv/environment/cwd/capabilities from the contract, checks `--version`, and returns the catalog
registration with `SdkAcpEmployeeChildFactory`. Its executable probe checks the same pinned artifacts;
it does not call a model or use the ambient Codex binary.

`codex_turn_strategy.py` declares Steer unavailable and normalizes only the qualified adapter's stable
namespaced compaction start/completion/error signals into the content-free lifecycle. Keep the generic broker, child, binding,
permission, reverse-service, transcript, and `AcpStepGateway` source unchanged. If qualification needs
a core API change, return to contract review instead of adding an implicit optional method.

Expose the two public backend symbols from `src/planner/conversation/__init__.py` only if existing
package conventions require it.

## 3. Register once through the generic seam

Edit only the settled generic registration file
`src/planner/conversation/backend_catalog.py` to add the Codex builder after Hermes. Supply the locked
runtime paths and log directory from the settled conversation backend configuration/composition file;
do not create a second catalog or environment-dependent frontend list.

No Ticket, Worker-type, selector, browser transcript, broker, discovery, runner, or step-gateway
behavior changes in this slice. The generic manifest immediately becomes `['hermes', 'codex']`.

## 4. Focused tests

Add:

- `tests/unit/test_codex_backend.py`
- `tests/unit/test_codex_turn_strategy.py`
- `tests/unit/test_codex_backend_conformance.py`

Extend only:

- `tests/unit/test_acp_conversation_composition.py`
- `tests/e2e/test_acp_conversation.py` for the real two-entry catalog path

Use `tests/support/acp_conformance.py` as a read-only assertion harness from the new Codex
conformance test. Mandatory worker/session probes stay common. The exact stored-plan replay case is a
named backend qualification assertion and does not weaken other backends' typed replay requirements.

Named acceptance cases:

- `test_codex_definition_is_locked_confined_and_permission_only`
- `test_codex_definition_rejects_missing_or_wrong_adapter_version`
- `test_codex_strategy_declares_steer_unavailable_without_fallback`
- `test_codex_live_and_loaded_updates_pass_common_conformance`
- `test_codex_stored_plan_replay_matches_pinned_upstream_limitation`
- `test_codex_explicit_and_automatic_compaction_have_exact_lifecycle`
- `test_codex_permission_cancel_death_disconnect_and_timeout_settle_once`
- `test_codex_ticket_human_and_automatic_step_share_durable_session`

The real wrapper qualification may be an explicit environment-gated integration check, but the
ordinary suite must still exercise the definition and strategy deterministically from captured exact
ACP fixtures. It may not fake away a capability the production pin lacks.

Run focused Ruff and strict Mypy on the two definition files and touched catalog/composition files,
then the named Python tests and existing ACP browser tests. Run one independent plan-aware diff review
and one correction round only if it finds a concrete defect. Do not run `./verify`.

## 5. Real Panels proof

After focused checks, restart real Panels on `127.0.0.1:8767` and use actual Computer Use:

1. Create a disposable pristine Ticket, select Codex in Kickoff, confirm no binding exists before
   demand, advance Kickoff, and confirm the selector freezes.
2. Send a human prompt; record its Codex backend key/session ID and visible typed result.
3. Make that Ticket eligible and let the real discovery/`EmployeeStepRunner` path perform one
   Automatic Employee step. Confirm the same binding/session, actual worker prompt delivery, and
   normal product settlement.
4. Exercise thought refresh, tool/diff/terminal, permission allow and reject, Queue, Send Now,
   unavailable Steer, content-free `/compact` start/completion, hard refresh, and server restart.

Record screenshots/observations and database/session evidence under this ticket. A definition-only
or scripted-only result is not completion.

## 6. Documentation and evidence

Update the settled live docs for worker types and employee runtime, plus:

- `orchestration/tickets/acp-07-codex-backend-definition/implementation-report.md`
- `orchestration/tickets/acp-07-codex-backend-definition/focused-checks.txt`
- `orchestration/tickets/acp-07-codex-backend-definition/implementation-review.md`
- `PROGRESS.md`
- `decisions.md`

Document the exact installed pin, authentication prerequisite, `agent` sandbox/approval mode,
unavailable Steer, shared Ticket session, and failure behavior. Do not add a Codex settings page or
backend-specific UI explanation.
