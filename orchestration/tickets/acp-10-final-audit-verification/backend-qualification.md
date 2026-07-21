# ACP-10 backend qualification

Date: 2026-07-21 (Europe/London)

Verdict: **PASS. Hermes, Codex, and Claude are qualified functional worker backends.** The current
tree and installed artifacts contain exactly `hermes`, `codex`, and `claude`; Gemini is absent.

This is a current-tree audit, not a package-reputation argument. It uses the installed executables,
committed lock, provider definitions, retained focused outputs, the real-worker record in
`PROGRESS.md` and `computer-use-evidence.md`, and read-only queries against `data/planning.db`. It
does not use the later canonical `./verify` as evidence.

## Exact installed closure

Commands run from the repository root:

```sh
node --version
npm --version
node agent_backends/node_modules/@agentclientprotocol/codex-acp/dist/index.js --version
node agent_backends/node_modules/@agentclientprotocol/claude-agent-acp/dist/index.js --version
agent_backends/node_modules/@anthropic-ai/claude-agent-sdk-darwin-arm64/claude --version
agent_backends/node_modules/@openai/codex-darwin-arm64/vendor/aarch64-apple-darwin/bin/codex --version
```

Output:

```text
v22.22.3
10.9.8
@agentclientprotocol/codex-acp 1.1.4
0.60.0
2.1.215 (Claude Code)
codex-cli 0.144.6
```

The single committed root manifest has SHA-256
`bb3ffd4e302839ae69606c328a54101cbe2e312d40223331eb00d1c41a6155df`; its npm v3 lock has
SHA-256 `95856e238d8bcb23815372424f0d23a735a10f98a8f07226611ffcf7b9e91edf`.
The lock resolves one ACP SDK `1.2.1` and one Zod `4.4.3` for both adapters.

| Installed artifact | Locked registry integrity | Installed file SHA-256 |
| --- | --- | --- |
| `@agentclientprotocol/claude-agent-acp@0.60.0` | `sha512-+ZZCJukpKdEY+/O982UCtgGHOY+MKa/JPpZ34v25ITawRyQyg3cqqOGo3M+9TsA4D+T/NXb+kT3zUB1uQZhY+Q==` | manifest `75208fcbec992fdd6690dbca202279f6eddfefdc090d5c93cc765f62efc08478`; `dist/index.js` `260aac90bf75f197b93640087c1de66441761d43c2784efa035fdcee60b5dacd` |
| Claude Agent SDK `0.3.215`, Darwin arm64 | `sha512-KIOe3N/ypVIdsI7fnJUHT0Djei1RH01TbxK6LI2mgoHKZ6NVDt/Q9kQ1+On3b/l899RhE6C1SMAiQ0iB4sbkjA==` | native Claude Code `90608b5c5ab504e96e77365cea6203d046e291d59b2bb42cf28dcb2ccdf9dd58` |
| `@agentclientprotocol/codex-acp@1.1.4` | `sha512-DzusIpGwlQwMWuHgJhU8FWMsyQvzjenB93IEzQATkdbNulo5Rd9GKOz8+B+/C9iWWxmyXgtgmjzaL+iRFyDryQ==` | manifest `e83ab2cf8ec52f213bc4cbfe3792542e066379dfc092821bb8fd3813741004bd`; `dist/index.js` `7534a0ad3cc4c9affd0b2da5007fa53ea0f1d6fcd71b2c5ef202e2056a976a97` |
| Codex `0.144.6`, Darwin arm64 | `sha512-6zgvh70MzBNSeT17HEhSOrmmGGZGAKzSC7x6JAq+edkJkdPYA9P0I1tG7aJ49GlBkBxuC+MKBH1qm6+2Cghcww==` | native Codex `80a3933d11a9d13ef806aa24f7bb8afc9169cfe4e9b09d6da6a92922cbde9cff` |

The Hermes backend is deliberately external to the npm closure. Production resolves
`~/.hermes/hermes-agent/venv/bin/hermes`; `hermes --version` reports:

```text
Hermes Agent v0.18.2 (2026.7.7.2) · upstream d7b36070 · local 047ba829 (+4 carried commits)
Install directory: /Users/khushaljagota/.hermes/hermes-agent
Install method: git
Python: 3.11.13
OpenAI SDK: 2.24.0
```

The exact checkout HEAD is `047ba829844a557c25ef3e0d4062a862768faab2`; the installed `hermes`
launcher's SHA-256 is `b59720fff7b3be2a369d747a4764ead14e5dfaf3f0630728c738d5026e7d2f28`.
The checkout was only read.

## Registration, catalog, and selector

`src/planner/conversation/backend_catalog.py` contains the sole production registration tuple in
this exact order:

```text
hermes, codex, claude
```

`src/planner/worker_types/configuration.py` constructs the Worker registry from that same catalog
object. Every shipped Worker type declares `default_employee_backend="hermes"`. The
`GET /api/worker-types` response exposes the ordered catalog, and
`web/src/routes/TicketRoute.svelte` builds the restrained **Worker** pill from that response. The
pill is editable only during pristine Kickoff with no Employee session; the first demand or Kickoff
advance freezes the stored Ticket choice. The provider activation review is `READY`, and
`test_production_employee_backend_catalog_is_hermes_codex_claude` plus the manifest endpoint tests
freeze the exact keys and order.

## `hermes`

Status: **QUALIFIED**.

- Executable definition: absolute installed `hermes`, argv `("<absolute hermes>", "acp")`.
- Initialize identity enforced by Panels: name `hermes-agent`, version `0.18.2`, protocol version 1,
  and ACP load-session support through the common SDK child.
- Panels-declared capabilities: `supportsSteer=true`, `observes_compaction=true`,
  `filesystem=false`, `terminal=false`, `permission=true`.
- Provider behavior actually exercised: new/load/fork, native Steer, images, commands, typed
  thought/tool/terminal/diff/usage, permission, cancellation, child recovery, and Hermes's durable
  fork/rebind compaction path. No private Hermes context is exposed.
- Registration/selector: first catalog key and default for Chief and every shipped Worker type;
  selectable through the same Ticket selector as the other providers.
- Focused evidence: ACP-04 retained 164 passing Python tests, five passing ACP web suites, clean
  Ruff/Mypy, and the real official-SDK browser/worker session proof. ACP-05 then exercised the real
  production-served Hermes process on `127.0.0.1:8767`, including `TICKET CHAT READY.`, naturally
  discovered Automatic Employee Ticket `t_b7sdhtzn`, typed activity, proposal settlement, reload,
  restart, permission, requested cancellation, and content-free compaction.

ACP-10 closed the exact combined experiential gate on fresh Ticket `t_x2f5up6e`. Opening pristine
Kickoff created no binding. The first actual Safari prompt created Hermes session
`f5b57fd7-7873-4285-9f6c-0e6d0ee219d4`, generation 1, and returned `HERMES HUMAN OK`. After ordinary
Kickoff approval and adding the Ticket to today, natural discovery—without a manual worker command—
completed run `run_1wcjzguy` on that exact session, rendered typed worker tool activity, and produced
the normal Success proposal. Human chat afterward returned `HERMES AFTER AUTO OK`.

Read-only database corroboration:

```text
Ticket       backend  ACP session                           gen  Employee run  run session  status
t_x2f5up6e   hermes   f5b57fd7-7873-4285-9f6c-0e6d0ee219d4  1    run_1wcjzguy same         complete
```

The Ticket mirror, durable binding, and completed run session are byte-identical.

## `codex`

Status: **QUALIFIED**.

- Exact adapter: `@agentclientprotocol/codex-acp@1.1.4`, running through absolute Node plus the
  committed `dist/index.js`; no ambient `codex` command or runtime `npx` path is used. The lock owns
  Codex `0.144.6` and its matching native package.
- Adapter-advertised initialization: protocol 1; exact name/version; `loadSession=true`; image and
  embedded-context prompt capabilities; resume/list/close/delete/additional-directory session
  capabilities; explicit API-key auth method. Unauthenticated new/load/list fails before binding
  with JSON-RPC `-32000 Authentication required`.
- Panels-declared capabilities: `supportsSteer=false`, `observes_compaction=true`,
  `filesystem=false`, `terminal=false`, `permission=true`. Codex is lazy at startup; its availability
  check is only the exact locked adapter's `--version` command with empty environment.
- Compaction/replay: exact `_meta.contextCompaction=true` lifecycle on the unchanged session,
  terminal content-free completion, and exact explicit-prompt failure when available. Stored plans
  replay as ordinary `Plan:` agent prose while live plans are typed; this pinned upstream
  presentation difference is accepted and no parser or private-state shim exists.
- Conformance/focused evidence: no-model runtime prequalification initialized the locked adapter;
  the provider/generic set passed 27 tests; the settled current-tree integration gate passed 193
  tests; and the single combined review returned `READY` with no P0/P1 finding.
- Real worker evidence: Ticket `t_nkq3108b` selected Codex through pristine Kickoff, returned
  `CODEX HUMAN OK`, naturally ran Automatic Employee step `run_3k18evvz`, returned
  `CODEX AFTER AUTO OK`, compacted/reloaded on the same session, and after a full server restart
  returned `CODEX RESTART OK` through Safari. Typed thought/tool state and real permission choices
  were visible; Steer was honestly unavailable while Queue and Send Now remained available.

Read-only database corroboration:

```text
Ticket       backend  ACP session                           gen  Employee run  run session  status
t_nkq3108b   codex    019f81a8-7041-7bb3-b7da-4445912fc3d0  1    run_3k18evvz same         complete
```

The Ticket mirror, durable binding, and completed run session are byte-identical.

## `claude`

Status: **QUALIFIED**.

- Exact adapter: `@agentclientprotocol/claude-agent-acp@0.60.0`, running through absolute Node plus
  the committed `dist/index.js`; its Agent SDK is `0.3.215` and its embedded native Claude Code is
  `2.1.215`.
- Adapter-advertised initialization: protocol 1; exact name/version; `loadSession=true`; image and
  embedded-context prompt capabilities; additional-directories/close/delete/fork/list/resume session
  capabilities. A new session with no prompt exists only in-process; a fresh process correctly
  returns `-32002 Resource not found`, which is why startup qualification is initialize-only.
- Panels-declared capabilities: `supportsSteer=false`, `observes_compaction=true`,
  `filesystem=false`, `terminal=false`, `permission=true`. Claude additionally declares that a
  requested cancellation requires a fresh child because the pinned adapter can continue publishing
  provider-side background work after returning a terminal cancellation response.
- Startup: Claude alone supplies the production startup preflight. It spawns the exact decorated
  child, initializes and validates it, closes/force-closes under one absolute deadline, and performs
  no new/load/prompt/model work before admission. Cancellation and failure leave the application
  unadmitted.
- Compaction/replay: exact provider controls become content-free started/completed/failed lifecycle
  on the unchanged session. Real dogfood found that pinned 0.60.0's `session/load` translated a
  private `isCompactSummary` row into an ordinary user chunk. The provider-local classifier now
  replaces only that exact synthetic template with `ContextCompaction`; ordinary user replay stays
  untouched and no summary/path/instructions are exposed.
- Conformance/focused evidence: no-model runtime qualification initialized the exact adapter and
  proved same-process new/load behavior; the provider/generic set passed 33 tests before activation;
  the post-dogfood complete strategy suite passed 25/25 with Ruff and strict Mypy; the settled
  193-test integration gate and combined independent review are green/`READY`.
- Real worker evidence: Ticket `t_1xbdpkq0` selected Claude through pristine Kickoff, returned
  `CLAUDE HUMAN OK`, naturally ran Automatic Employee step `run_jsmxnrt8`, showed typed internal
  Bash/tool activity and the normal Success proposal, returned `CLAUDE AFTER AUTO OK`, then
  compacted/reloaded without private context. After a full server restart Safari returned
  `CLAUDE RESTART OK`. Queue auto-started once in FIFO order while Steer remained honestly disabled.
  The first Send Now probe exposed a late old-turn completion; after the capability correction, the
  live retest retired child PID `2477`, loaded the unchanged generation-1 binding into PID `7324`,
  returned the exact successor once, and showed no forbidden old completion beyond the prior interval.
  Ordinary Bash correctly remained unprompted under the local Claude `auto` policy. Claude's native
  `ExitPlanMode` path separately produced the real `Ready to code?` ACP permission card with five
  provider choices; selecting `Yes, and use "auto" mode` settled once and completed the read-only
  probe.

Read-only database corroboration:

```text
Ticket       backend  ACP session                           gen  Employee run  run session  status
t_1xbdpkq0   claude   e2562875-0b3f-482e-8f75-52e0d0e46731  1    run_jsmxnrt8 same         complete
```

The Ticket mirror, durable binding, and completed run session are byte-identical.

## Gemini

Status: **ABSENT / NOT APPLICABLE** by owner decision.

The complete implementation/test/package/build search below returned no match:

```sh
rg -n -i '\bgemini\b' \
  src tests web/src web/tests web/dist config.yaml \
  agent_backends/package.json agent_backends/package-lock.json
```

Exit status: `1`, meaning no match. Current live docs mention Gemini only to say it is not
registered. There is no Gemini package, provider module, catalog entry, selector value, conformance
suite, or tests-as-delivery.

## Final qualification disposition

| Backend | Exact install | Definition/conformance | Registration/selector | Real functional worker | Verdict |
| --- | --- | --- | --- | --- | --- |
| `hermes` | proved | proved | proved | same-session human -> automatic -> human | **proved** |
| `codex` | proved | proved | proved | same-session human -> automatic -> human -> restart | **proved** |
| `claude` | proved | proved | proved | same-session human -> automatic -> human -> compact/reload -> restart | **proved** |
| `gemini` | absent | not applicable | absent | not applicable | **not applicable** |

All required backends are qualified. The later single canonical `./verify` remains a separate
ACP-10 gate.
