# ACP-10 legacy-absence evidence

Date: 2026-07-21 (Europe/London)

Verdict: **PASS for the current product tree and served build.** The legacy owners, routes,
configuration switches, browser resources, schemas, and event writers are absent. The complete
search has three intentional classes of textual match: negative absence assertions in tests,
current docs saying an old surface does not exist, and migration-only recognition in
`src/planner/core/db.py`. One root historical dogfood command is retained with an explicit
pre-ACP/non-current warning.

## Scope and exit semantics

Included:

- `src`, `tests`, `web/src`, `web/tests`, `config.yaml`, and the currently built `web/dist`;
- live docs under `docs`;
- root architecture/current-context files `AGENTS.md`, `CLAUDE.md`, `CONTEXT.md`, `DESIGN.md`,
  `DOCS.md`, `DOGFOOD.md`, `PRINCIPLES.md`, and `harness-prep.md`; and
- the committed `agent_backends/package.json` and `package-lock.json` for the Gemini check.

Excluded exactly as required: historical `orchestration/**`, `PROGRESS.md`, and `decisions.md`.
Dependency/cache trees (`node_modules`, `__pycache__`, `.venv`, `data`) are not product-source
inputs. `rg` exit `0` means at least one match, exit `1` means no match, and exit `2` would mean a
search error. No command reported exit `2` in the evidence below.

## Deleted paths

Command:

```sh
find \
  src/planner/chat \
  src/planner/minds \
  src/planner/hermes_backend \
  src/planner/core/adapters \
  -type f ! -path '*/__pycache__/*' ! -name '*.pyc' -print 2>/dev/null | sort

for candidate in \
  src/planner/tickets/employee_session_history.py \
  src/planner/files/chat_images.py \
  web/src/components/ChatPanel.svelte \
  web/src/components/ChiefNeutralPane.svelte \
  web/src/lib/neutralPane.ts \
  web/tests/neutral-pane.test.mjs \
  web/tests/ticket-neutral-pane.test.mjs; do
  test ! -e "$candidate" || printf '%s\n' "$candidate"
done
```

Output: no lines. Both commands exited `0`; for `find`, empty output means there is no live file.
Ignored `__pycache__` directories from the old running tree still physically exist, but contain only
`.pyc` files and are outside source and verification inputs. `git ls-files --deleted` lists the old
tracked Python files because this ACP program is still an uncommitted worktree deletion; every one
is missing from the actual tree being audited.

The complete named legacy test-file loop and `test_hermes_backend_*.py` search also emitted no path:
all Chat/activity/clarification/history, Minds/session, raw-frame, pool, neutral-pane, live-chat,
and managed-chat-image suites named by ACP-06 are deleted. The retained files are ACP tests, not
renamed legacy delivery tests.

## Live route and owner searches

The live non-migration scope used below is:

```text
src web/src config.yaml docs AGENTS.md CLAUDE.md CONTEXT.md DESIGN.md DOCS.md DOGFOOD.md
PRINCIPLES.md harness-prep.md
```

### Retired routes and browser targets

Command:

```sh
rg -n -i --no-heading -g '!src/planner/core/db.py' \
  '(/api/chat|/api/messages/chief|/api/relay|/tickets/by-live-session|/tickets/by-employee-session|employee-session-history|/files/chats|chat-file)' \
  src web/src config.yaml docs AGENTS.md CLAUDE.md CONTEXT.md DESIGN.md DOCS.md DOGFOOD.md PRINCIPLES.md harness-prep.md
```

Complete output and status:

```text
docs/chat.md:127:There is no Day conversation, conversation-file route, or Employee-session-history
exit 0
```

Classification: the sole match is a current negative statement. It defines absence; it is not a
route, link, resource, or compatibility path. The exact search of `web/dist` shown below has no
match.

### Retired production owners and imports

Command:

```sh
rg -n --no-heading -g '!src/planner/core/db.py' \
  '(planner\.chat|planner\.minds|planner\.hermes_backend|planner\.core\.adapters|ChatPanel|ChiefNeutralPane|neutralPane|SharedGateway|PoolStepGateway|RawFrameTransport|NeutralDownstreamSession|EmployeeChildRelay|EmployeeChildPool|TranscriptMirrorTee|RelayTeeObserver)' \
  src web/src config.yaml docs AGENTS.md CLAUDE.md CONTEXT.md DESIGN.md DOCS.md DOGFOOD.md PRINCIPLES.md harness-prep.md
```

Output: no lines. Exit: `1`.

### Retired configuration keys

Command:

```sh
rg -n --no-heading \
  '(relay_backend_enabled|PLAN_RELAY_BACKEND_ENABLED|gateway_adapter|PLAN_GATEWAY_ADAPTER|run_startup_recovery_in_test_mode|PLAN_RUN_STARTUP_RECOVERY_IN_TEST_MODE|PLAN_HERMES_BIN|PLAN_HERMES_PROFILE|PLAN_WORKER_SKILL|\bhermes_bin\b|\bhermes_profile\b)' \
  src web/src config.yaml docs AGENTS.md CLAUDE.md CONTEXT.md DESIGN.md DOCS.md DOGFOOD.md PRINCIPLES.md harness-prep.md
```

Complete output and status:

```text
DOGFOOD.md:61:The command below is a historical 2026-07-05 record. `PLAN_HERMES_BIN` belonged to the retired
DOGFOOD.md:70:Then the shaping server was killed and the same DB relaunched with real dispatch through the disclosed shim: `PLAN_DB_PATH=data/dogfood-c5.db PLAN_PORT=8800 PLAN_DISPATCH_ENABLED=1 PLAN_TICK_SECONDS=20 PLAN_MAX_RUNS=1 PLAN_HERMES_BIN=orchestration/dogfood/hermes-wrapped.sh .venv/bin/plan serve` (log: `data/logs/level-c5-server.log`; tracked tail: `orchestration/dogfood-evidence/level-c-server-tail.log`). No test mode; `spawn_adapter: auto` resolved to the real adapter. The shim (`orchestration/dogfood/hermes-wrapped.sh`, sanctioned §18.5 "prompt shape" latitude) execs the real hermes with the unresolvable `--skills planning-worker` flag dropped and the message enriched to "Read <repo>/skills/planning-worker.md, then work planning ticket <id>. Act only through the plan CLI; do not run ./verify, do not modify or delete any repository files." Nothing in `src/` changed; the shim only rewrites the spawn command line.
exit 0
```

`DOGFOOD.md:61-62` continues: “pre-ACP dispatcher and is not a current Panels configuration
key or supported runtime path.” The command is dated historical evidence, not a current setup path.
There is no match in `src`, `web/src`, `config.yaml`, or live `docs`. A separate exact search of
`config.yaml src/planner/core/config.py` for the removed lower-case keys and `PLAN_*` forms exits
`1`. The local variable named `worker_skill` in `conversation/claude_backend.py` is a path to the
current Panels Worker-role skill; it is not the deleted configuration key or environment input.

## Schema and canonical-event vocabulary

Outside the migration owner, the complete search is empty:

```sh
rg -n --no-heading -g '!src/planner/core/db.py' \
  '(chat_messages|chat_turns|chat_turn_activity_entries|agent_chat_sessions|chat_session_created|chat_message_recorded|chat_turn_updated|chat_turn_finished|chat_turn_started|chat_session_key)' \
  src web/src config.yaml docs AGENTS.md CLAUDE.md CONTEXT.md DESIGN.md DOCS.md DOGFOOD.md PRINCIPLES.md harness-prep.md
```

Output: no lines. Exit: `1`.

The unfiltered `src/planner/core/db.py` search is deliberately retained rather than hidden:

```text
268:        if _table_exists(conn, "chat_turns"):
271:                "updated_at, completed_at FROM chat_turns "
303:                "WHERE kind = 'chat_turn_started' ORDER BY id"
321:            "'chat_session_created','chat_message_recorded','chat_turn_started',"
322:            "'chat_turn_updated','chat_turn_finished')"
330:        if "chat_session_key" in _table_columns(conn, "days"):
331:            conn.execute("ALTER TABLE days DROP COLUMN chat_session_key")
332:        conn.execute("DROP TABLE IF EXISTS chat_turn_activity_entries")
333:        conn.execute("DROP TABLE IF EXISTS chat_messages")
334:        conn.execute("DROP TABLE IF EXISTS chat_turns")
335:        conn.execute("DROP TABLE IF EXISTS agent_chat_sessions")
669:        "WHERE substr(entity_id, 1, 2) = 't_' AND kind = 'chat_session_created' ORDER BY id"
722:            if {"employee_session_id", "chat_session_key"} <= columns:
724:                    "ambiguous Ticket schema has employee_session_id and chat_session_key"
847:                    elif "chat_session_key" in columns:
848:                        employee_session_id = row["chat_session_key"]
exit 0
```

Classification:

- Lines 268-335 are `_migrate_to_v25`, entered only for an incoming schema below 25. It copies the
  permitted worker correctness rows, deletes old events/bindings, removes the Day column, drops all
  four legacy tables, and durably marks v25 in one transaction. This is the terminal one-way
  deleter, not a live owner.
- Line 669 is the specifically sealed `_rewrite_ticket_employee_session_events` recognizer. It
  accepts historical Ticket `chat_session_created` only while rebuilding an older Ticket schema and
  rewrites it to `employee_session_changed`; there is no live enum member or writer.
- Lines 722/847 are the same older-Ticket-schema parser recognizing `chat_session_key` as historical
  input and rejecting ambiguous dual ownership. They cannot create a current Chat column.

The historical vocabulary is confined to this migration module. No live product module, frontend,
config, doc alternative, or event writer contains it.

## Tests retain only negative guards

Command:

```sh
rg -n --no-heading \
  '(/api/chat|/api/messages/chief|/api/relay|/tickets/by-live-session|/tickets/by-employee-session|employee-session-history|/files/chats|planner\.chat|planner\.minds|planner\.hermes_backend|planner\.core\.adapters|ChatPanel|ChiefNeutralPane|neutralPane|SharedGateway|PoolStepGateway|PLAN_GATEWAY_ADAPTER|PLAN_RELAY_BACKEND_ENABLED|PLAN_RUN_STARTUP_RECOVERY_IN_TEST_MODE)' \
  tests web/tests
```

Complete matches:

```text
web/tests/acp-contracts.test.mjs:94:  /\/api\/chat\/commands|_acp\/skills\/list|resourceCatalogue|neutralPane|JSON-RPC|acp-ui|\bReact\b/,
web/tests/acp-production-mount.test.mjs:24:assert.doesNotMatch(wrapperSource, /<style>|ChatPanel|ChiefNeutralPane|relayChief|\/api\/chat|\/api\/relay/);
web/tests/acp-production-mount.test.mjs:45:    /ChatPanel|ChiefNeutralPane|relayChief|retryRelayChiefMeta|chatGatewayStatus|\/api\/chat|\/api\/relay/,
tests/unit/test_chat_ingress_contract.py:35:        "/api/chat",
tests/unit/test_chat_ingress_contract.py:36:        "/api/messages/chief",
tests/unit/test_chat_ingress_contract.py:37:        "/api/relay",
tests/unit/test_chat_ingress_contract.py:38:        "/api/tickets/by-live-session",
tests/unit/test_chat_ingress_contract.py:39:        "/api/tickets/by-employee-session",
tests/unit/test_chat_ingress_contract.py:40:        "/api/tickets/{ticket_id}/employee-session-history",
tests/unit/test_chat_ingress_contract.py:41:        "/files/chats",
tests/unit/test_chat_ingress_contract.py:106:        "planner.chat",
tests/unit/test_chat_ingress_contract.py:107:        "planner.minds",
tests/unit/test_chat_ingress_contract.py:108:        "planner.hermes_backend",
tests/unit/test_chat_ingress_contract.py:109:        "planner.core.adapters",
tests/unit/test_chat_ingress_contract.py:110:        "SharedGateway",
tests/unit/test_chat_ingress_contract.py:111:        "PoolStepGateway",
tests/unit/test_chat_ingress_contract.py:112:        "PLAN_GATEWAY_ADAPTER",
tests/unit/test_chat_ingress_contract.py:113:        "PLAN_RELAY_BACKEND_ENABLED",
tests/unit/test_chat_ingress_contract.py:114:        "PLAN_RUN_STARTUP_RECOVERY_IN_TEST_MODE",
tests/unit/test_chat_ingress_contract.py:135:        "/api/chat",
tests/unit/test_chat_ingress_contract.py:136:        "/api/messages/chief",
tests/unit/test_chat_ingress_contract.py:137:        "/api/relay",
tests/unit/test_chat_ingress_contract.py:138:        "/files/chats",
tests/unit/test_chat_ingress_contract.py:139:        "ChatPanel",
tests/unit/test_chat_ingress_contract.py:140:        "ChiefNeutralPane",
tests/unit/test_conversation_turn_broker.py:2954:        assert "planner.chat" not in source, source_path
exit 0
```

Every match is inside `assert not`, `assert.doesNotMatch`, or a forbidden-token fixture. These tests
make legacy reintroduction fail; they are not compatibility delivery.

## Served build

Command:

```sh
rg -n --no-heading \
  '(/api/chat|/api/messages/chief|/api/relay|/tickets/by-live-session|/tickets/by-employee-session|employee-session-history|/files/chats|chat-file|ChatPanel|ChiefNeutralPane|neutralPane)' \
  web/dist
```

Output: no lines. Exit: `1`.

`web/dist/index.html` points at the currently generated Vite assets, so the app FastAPI serves does
not retain an old route/component chunk.

## Gemini absence

Command:

```sh
rg -n -i --no-heading '\bgemini\b' \
  src tests web/src web/tests web/dist config.yaml \
  agent_backends/package.json agent_backends/package-lock.json
```

Output: no lines. Exit: `1`.

Current docs contain four matches only to state that Gemini is not registered. There is no Gemini
implementation, package, registration, selector value, generated asset, or tests-as-delivery.

## Retained ACP owners and live database

Source-presence command:

```sh
rg -n \
  '@app\.websocket\("/api/conversation"|/worker-self|^class ConversationComposition|^class SqliteConversationBindingRepository|^class AcpStepGateway|^class SqliteEmployeeStepRepository' \
  src/planner
```

Output:

```text
src/planner/conversation/composition.py:84:class ConversationComposition:
src/planner/conversation/sqlite_binding_repository.py:30:class SqliteConversationBindingRepository:
src/planner/core/server.py:231:    @app.websocket("/api/conversation")
src/planner/tickets/api.py:535:@router.get("/tickets/{ticket_id}/worker-self")
src/planner/runtime/acp_step_gateway.py:53:class AcpStepGateway:
src/planner/runtime/employee_step_repository.py:32:class SqliteEmployeeStepRepository:
```

`ConversationComposition` constructs the one registry, hub, turn broker, permission broker,
binding repository, and `AcpStepGateway`; the owner-token absence search proves there is no second
legacy composition. `sdk_child.py` sets `PLAN_TICKET_ID` for Ticket employees, and worker-self uses
that exact Ticket route while rejecting duplicate session ownership. `AcpStepGateway` prepares
pending worker context into the real ACP prompt and acknowledges its exact receipts only after ACP
admission. `employee_step_runs` remains correctness state, not a transcript.

Read-only live query:

```sh
sqlite3 'file:data/planning.db?mode=ro' <<'SQL'
PRAGMA user_version;
SELECT name, type FROM sqlite_master
 WHERE type IN ('table','index')
 AND name IN ('chat_messages','chat_turns','chat_turn_activity_entries',
              'agent_chat_sessions','employee_step_runs',
              'idx_employee_step_runs_one_running','conversation_session_bindings')
 ORDER BY type, name;
PRAGMA table_info(employee_step_runs);
SELECT name FROM pragma_table_info('days') WHERE name='chat_session_key';
PRAGMA foreign_key_check;
SQL
```

Result:

```text
user_version = 26

idx_employee_step_runs_one_running  index
conversation_session_bindings       table
employee_step_runs                  table

employee_step_runs columns:
employee_step_id | ticket_id | status | employee_session_id | error |
started_at | updated_at | completed_at

days.chat_session_key: no row
foreign_key_check: no row
```

No legacy table is present. The Employee table has exactly the eight contracted columns and its one
partial running index. The binding table remains the sole durable ACP session owner; Tickets retain
only their exact session mirror. Current schema-v26 Codex and Claude dogfood rows further prove each
completed Employee run session equals its durable binding and Ticket mirror.

## Final disposition

The one-way deletion is complete in product source, tests-as-delivery, configuration, live docs,
browser source, generated build, and current SQLite state. The retained occurrences are fully
classified and earned: negative guards, explicit historical documentation, and sealed migration
input/deletion logic. No legacy alternative is runnable or selectable.
