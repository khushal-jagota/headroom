# ACP-07 Codex backend definition — focused plan review

> Owner amendment, 2026-07-20: the historical fork/summary blocker below is superseded by
> `orchestration/acp-migration/compaction-primary-source-audit.md`. Codex's same-thread,
> content-free lifecycle is compatible. Only the independently identified typed live/load plan
> mismatch remained. The owner has now accepted that exact stored-plan replay as a tested upstream
> presentation limitation rather than a worker-functionality blocker; no prose parser is allowed.

## Verdict

**READY as a runtime-qualification-gated plan. `@agentclientprotocol/codex-acp` 1.1.4 may be pinned
and tested; production registration still waits for the named real probes.**

No unresolved contract or plan blocker was found. The plan is correct to stop before product-source
edits and selector exposure when the pinned adapter fails qualification.

## High findings

None.

The historical review correctly established three independently inspected facts:

- Its initialization result advertises `loadSession=true` and session resume/list/close/delete, but
  not fork; the ACP request router has no `session/fork` handler.
- Live and loaded compaction become namespaced tool-call updates containing only the compaction flag,
  title, and status. No non-empty inspectable summary crosses ACP.
- Live `turn/plan/updated` becomes an ACP `plan` update, while stored `plan` items replay as an
  `agent_message_chunk` whose text begins `Plan:`.

This is direct evidence from the exact cached tarball
`@agentclientprotocol/codex-acp/-/codex-acp-1.1.4.tgz` (integrity
`sha512-DzusIpGwlQwMWuHgJhU8FWMsyQvzjenB93IEzQATkdbNulo5Rd9GKOz8+B+/C9iWWxmyXgtgmjzaL+iRFyDryQ==`),
not an inference from README copy.

Under the current owner-amended contract, missing fork and summary are compatible with Codex's
same-thread lifecycle, and the stored-plan representation is an accepted, named upstream
presentation limitation. None is now a static dispatch blocker. The real runtime/session/permission
qualification remains mandatory before production registration.

No conformant official alternative is already available locally. The installed `codex-cli 0.144.6`
offers `app-server` and a CLI `fork` command, but no ACP agent command. App-server JSON-RPC is the
provider protocol wrapped by `codex-acp`; wiring it into Panels directly would create a second,
Codex-specific transport and is not an ACP backend definition. The npm cache contains the 1.1.4
Codex ACP tarball but no other Codex ACP adapter.

## Contract coverage checked

- **Identity and process:** package identity is exactly
  `@agentclientprotocol/codex-acp` `1.1.4`; its executable is `dist/index.js`. With `CODEX_PATH`
  absent it resolves the lockfile-installed `@openai/codex/bin/codex.js` and starts `app-server`.
  The proposed absolute `(node, dist/index.js)` argv and lockfile ownership therefore match the
  implementation and avoid ambient Codex.
- **Environment and authentication:** the adapter reads the named `CODEX_*`, `OPENAI_API_KEY`,
  `NO_BROWSER`, `INITIAL_AGENT_MODE`, `DEFAULT_AUTH_REQUEST`, and `APP_SERVER_LOGS` controls.
  `{"methodId":"api-key"}` is the exact default-auth request shape. Authorization is checked before
  `thread/start` or `thread/resume`; failed authentication therefore precedes binding creation.
- **Workspace and services:** `cwd` and ACP additional directories are passed into both thread start
  and resume. Codex runs its own filesystem/terminal tools and maps command, file-change, and broader
  approvals through ACP `session/request_permission`; declaring only permission reverse service is
  accurate.
- **Turn behavior:** the adapter exposes cancellation but no ACP steer method, so
  `supports_steer=False` is honest. Generic Queue and Send Now remain the existing broker path.
- **Sessions and typed replay:** the ACP session ID is the Codex thread ID; load performs
  `thread/resume`, subscribes, reads the same thread, streams history, and only then returns. User,
  assistant, reasoning, tool, diff, and terminal replay are typed; the stored-plan exception above
  is the qualifying failure. A missing thread is not replaced in this load path.
- **One human/automatic worker:** the plan preserves the generic repository, hub, broker, registry,
  and `AcpStepGateway`. Its named test plus real Panels proof require the Ticket's human prompt and
  real `EmployeeStepRunner` prompt to resolve one backend key, binding generation, and ACP session,
  with the worker-context text delivered through the ACP prompt rather than a transcript row.

## Required runtime qualification probe

Do not infer runtime fitness from version or source inspection. Before registration, run the plan's
real-wrapper qualification and retain exact ACP evidence for: same-session content-free compaction
lifecycle, the pinned stored-plan presentation difference, load-callback-before-response order,
permission option order, cancel/replacement behavior, and one real human plus Automatic Employee
continuity proof. Only that evidence can lift the registration gate.

Review was read-only: no network access, package installation, model call, test run, or product-source
edit was used.
