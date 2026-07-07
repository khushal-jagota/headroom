# Spike 09 - Per-ticket agent identity

Factual, opinion-free option map. Sources are relative to
`~/.hermes/hermes-agent/`. No recommendation is made.

Read-only throughout: no files under `~/.hermes/hermes-agent` were modified.

## 0. Short answer

Hermes TUI `session.create` does not have an arbitrary durable `metadata` or
`tag` object. It creates a live UI session id and a separate durable
`stored_session_id` / `session_key`; the latter is what the agent is built with
as `AIAgent.session_id` (`tui_gateway/server.py:4305-4308`,
`tui_gateway/server.py:4413-4419`, `tui_gateway/server.py:3859-3862`).

Existing durable-ish identity carriers are:

- `stored_session_id` / `session_key`: durable transcript id, exposed to tools
  as `HERMES_SESSION_KEY` in TUI turns (`tui_gateway/server.py:4416-4418`,
  `tui_gateway/server.py:1486-1501`, `gateway/session_context.py:124-164`).
- `source`: an arbitrary string accepted by TUI `session.create`, persisted to
  the initial session row, and exposed to tools as `HERMES_SESSION_SOURCE`
  (`tui_gateway/server.py:4320`, `tui_gateway/server.py:4386`,
  `tui_gateway/server.py:1324-1329`, `tui_gateway/server.py:1495-1501`).
- `title`: accepted at birth, but persisted only after the first completed turn
  via `pending_title`; it is not exposed automatically to the agent
  (`tui_gateway/server.py:4309`, `tui_gateway/server.py:4381`,
  `tui_gateway/server.py:6859-6867`, `tui_gateway/server.py:2720-2769`).

There is no first-class arbitrary durable tag column in the SQLite schema
(`hermes_state.py:523-558`) and `SessionDB.create_session` only inserts
`id`, `source`, `user_id`, `model`, `model_config`, `system_prompt`,
`parent_session_id`, `cwd`, and `started_at` (`hermes_state.py:1348-1381`).

## 1. Session birth

### 1a. TUI `session.create` input surface

`session.create` is implemented at `tui_gateway/server.py:4303`. It generates:

- live `session_id` / `sid`: `uuid.uuid4().hex[:8]`
  (`tui_gateway/server.py:4303-4306`);
- durable `stored_session_id` / `session_key`: `_new_session_key()`, formatted as
  `YYYYmmdd_HHMMSS_<uuid6>` (`tui_gateway/server.py:3965-3966`,
  `tui_gateway/server.py:4306`, `tui_gateway/server.py:4416-4418`).

The handler reads these creation params:

| Param | Birth behavior | Durable behavior |
| --- | --- | --- |
| `cols` | Read with default `80` and stored on the live session (`tui_gateway/server.py:4307`, `tui_gateway/server.py:4367`). | Not persisted in `sessions`. |
| `messages` | Coerced by `_coerce_seed_history`; only list items with role `user`, `assistant`, or `system` and non-empty string `content`/`text` survive (`tui_gateway/server.py:4198-4218`, `tui_gateway/server.py:4308`). | Stored in live `session["history"]` and returned to the client (`tui_gateway/server.py:4371`, `tui_gateway/server.py:4418-4419`). Not written by `session.create`; see seed caveat below. |
| `title` | Stored as live `pending_title` (`tui_gateway/server.py:4309`, `tui_gateway/server.py:4381`). | Applied to SQLite after the first completed turn (`tui_gateway/server.py:6859-6867`) through `set_session_title` (`hermes_state.py:1886-1933`). |
| `cwd` | Resolved and stored as live `cwd`; `explicit_cwd` records whether the client chose a real directory (`tui_gateway/server.py:4310-4319`, `tui_gateway/server.py:4370`, `tui_gateway/server.py:4375`). | Persisted as `sessions.cwd` only when explicit (`tui_gateway/server.py:1248-1253`, `tui_gateway/server.py:1324-1330`). |
| `source` | Arbitrary string, default `"tui"`, stored on the live session (`tui_gateway/server.py:4320`, `tui_gateway/server.py:4386`). | Persisted as `sessions.source` on the first real DB row (`tui_gateway/server.py:1219-1224`, `tui_gateway/server.py:1324-1327`). |
| `profile` | Resolves profile home and stores it on the live session (`tui_gateway/server.py:4323-4328`, `tui_gateway/server.py:4382`). | Causes row creation in that profile's `state.db` instead of the launch profile DB (`tui_gateway/server.py:1258-1273`). |
| `model` | Creates a per-session `model_override` if non-empty (`tui_gateway/server.py:4330-4340`, `tui_gateway/server.py:4378`). | Persisted into row `model` and `model_config` on first activity (`tui_gateway/server.py:1276-1289`, `tui_gateway/server.py:1324-1329`). |
| `provider` | Stored inside `model_override` only when `model` is provided (`tui_gateway/server.py:4335-4340`). | Persisted into `model_config.provider` if present (`tui_gateway/server.py:1290-1297`, `tui_gateway/server.py:1324-1329`). |
| `reasoning_effort` | Parsed into `create_reasoning_override` (`tui_gateway/server.py:4341-4348`, `tui_gateway/server.py:4379`). | Persisted as `model_config.reasoning_config` (`tui_gateway/server.py:1319-1320`). |
| `fast` | Sets `create_service_tier_override = "priority"` when truthy (`tui_gateway/server.py:4349-4351`, `tui_gateway/server.py:4380`). | Persisted as `model_config.service_tier` (`tui_gateway/server.py:1321-1322`). |
| `close_on_disconnect` | Stored as live boolean (`tui_gateway/server.py:4365`). | Not persisted in `sessions`. |

The handler has no `metadata` read or validation path; it only reads the params
above before constructing `_sessions[sid]` (`tui_gateway/server.py:4303-4391`).
Unknown params therefore have no persistence path in this handler.

### 1b. What actually reaches SQLite

TUI intentionally does not create a SQLite row during `session.create`; the row
is lazy-created after the first prompt so abandoned drafts do not create empty
sessions (`tui_gateway/server.py:4393-4398`, `tui_gateway/server.py:6361-6363`).

The lazy row creation persists the durable `session_key` as `sessions.id`, plus
`source`, `model`, `model_config`, and optional `cwd` (`tui_gateway/server.py:1240-1246`,
`tui_gateway/server.py:1324-1330`). That lands in the fixed `sessions` table,
which has no `metadata` or tag column (`hermes_state.py:523-558`).

`SessionDB.create_session` accepts `session_id`, `source`, and only the optional
kwargs supported by `_insert_session_row`: `model`, `model_config`,
`system_prompt`, `user_id`, `parent_session_id`, and `cwd`
(`hermes_state.py:1348-1381`). Session title is a separate update path
(`hermes_state.py:1886-1933`).

### 1c. Seed messages are accepted, but are not a clean durable tag

Seed `messages` become live `session["history"]` and are passed as
`conversation_history` to the first agent turn (`tui_gateway/server.py:4371`,
`tui_gateway/server.py:6583-6586`, `tui_gateway/server.py:6720-6729`).

The agent initializes `messages = list(conversation_history)` and appends the
new user message (`agent/turn_context.py:220-264`). The SQLite flush skips
objects that came from `conversation_history` because it assumes they were
already stored history (`run_agent.py:1581-1588`, `run_agent.py:1617-1630`).
So birth seed messages are a live-context seed, not a reliable SQLite session
tag created by `session.create`.

### 1d. HTTP API side note

The HTTP API route `POST /api/sessions` is a different surface. It accepts
`id`/`session_id`, optional `model`, optional string `system_prompt`, and
optional `title`; it fixes `source` to `"api_server"` and does not accept a
metadata object (`gateway/platforms/api_server.py:1469-1504`). Its response
hides full `system_prompt` and `model_config`, exposing only booleans
(`gateway/platforms/api_server.py:1386-1400`).

## 2. What the agent can know about itself

### 2a. System prompt

TUI `_make_agent` builds the agent system prompt from config `agent.system_prompt`
plus preloaded skills; there is no per-session identity param in `session.create`
that is appended to the prompt (`tui_gateway/server.py:3750-3766`,
`tui_gateway/server.py:3862`). Hermes can include the agent's session id in the
system prompt only when `pass_session_id` is true (`agent/system_prompt.py:454-456`);
TUI wires that from process env `HERMES_TUI_PASS_SESSION_ID`, not from a
per-session field (`tui_gateway/server.py:3863-3864`).

The generic gateway session-context prompt can render source/user/channel
information, but not `session_id` or `session_key`: the context object has those
fields (`gateway/session.py:190-223`), while the prompt builder renders source,
user, connected platforms, and delivery options (`gateway/session.py:262-469`).
That prompt builder is not the TUI `session.create` identity channel.

### 2b. In-process tools

Hermes replaced process-global `os.environ` session state with task-local
ContextVars because concurrent gateway messages can otherwise overwrite each
other (`gateway/session_context.py:1-37`). The supported read API is
`get_session_env`, which reads ContextVar first and falls back to `os.environ`
only if the ContextVar was never set (`gateway/session_context.py:212-235`).

TUI turn setup calls `_set_session_context(session["session_key"])`
(`tui_gateway/server.py:6605-6607`). That helper binds `session_key`, `source`,
and `cwd`, but not `session_id` (`tui_gateway/server.py:1486-1501`). Therefore a
TUI in-process tool can reliably learn:

- `HERMES_SESSION_KEY`: the current durable session key
  (`tui_gateway/server.py:1486-1501`, `gateway/session_context.py:92-105`,
  `gateway/session_context.py:124-164`);
- `HERMES_SESSION_SOURCE`: the live session's `source`
  (`tui_gateway/server.py:1495-1501`, `gateway/session_context.py:92-105`);
- not title, model_config, arbitrary metadata, or a ticket id unless the owner
  encoded it into `source` or provided a custom lookup keyed by session key.

`AIAgent.__init__` also calls `set_current_session_id(agent.session_id)`, which
writes `HERMES_SESSION_ID` to both ContextVar and `os.environ`
(`agent/agent_init.py:1034-1054`, `gateway/session_context.py:109-122`).
In TUI turns, however, `_set_session_context` calls `set_session_vars` without
`session_id`, and `set_session_vars` explicitly sets `_SESSION_ID` to the
default empty string when none is passed (`tui_gateway/server.py:1486-1501`,
`gateway/session_context.py:124-164`). For shared-child identity, the safe TUI
runtime handle is `HERMES_SESSION_KEY`, not process-level env.

### 2c. Shell tools

The local terminal environment bridges non-empty ContextVar session values into
subprocess env because ContextVars do not propagate to child processes
(`tools/environments/local.py:475-482`). Since TUI binds a non-empty
`HERMES_SESSION_KEY`, shell commands can read `HERMES_SESSION_KEY` in normal TUI
turns (`tui_gateway/server.py:1486-1501`, `tools/environments/local.py:475-482`).

No built-in shell env var carries the session title, arbitrary metadata, or
ticket id unless that value is encoded into `source`/`session_key` or resolved by
a custom tool/API lookup.

### 2d. Lookup from inside the agent

Hermes exposes `SessionDB.get_session(session_id)` to read a fixed session row
by id (`hermes_state.py:1766-1773`). A custom in-process tool could read
`HERMES_SESSION_KEY` via `get_session_env` and then use Hermes DB access or the
planner's own API to resolve `session_key -> ticket`. Hermes does not ship a
current-session metadata tool or a current-ticket concept in the TUI gateway
source above.

## 3. How kanban scopes per-job task identity

Kanban uses a different process model. The dispatcher spawns one Hermes process
per task as:

`hermes -p <assignee> ... chat -q "work kanban task <task.id>"`
(`hermes_cli/kanban_db.py:7286-7313`, `hermes_cli/kanban_db.py:7395-7421`).

The dispatcher stamps the worker process env with the task identity and run
identity:

- `HERMES_KANBAN_TASK = task.id` (`hermes_cli/kanban_db.py:7333-7336`);
- `HERMES_KANBAN_WORKSPACE`, optional branch, run id, and claim lock
  (`hermes_cli/kanban_db.py:7335-7356`);
- board/DB/workspace root pins so the worker sees the same board the dispatcher
  used (`hermes_cli/kanban_db.py:7376-7388`);
- those env vars are passed to the child `Popen` call (`hermes_cli/kanban_db.py:7435-7444`).

The kanban tools are registered only for dispatcher workers or configured
orchestrator profiles. `_check_kanban_mode` returns true when
`HERMES_KANBAN_TASK` is set, while orchestrator-only tools are hidden from
task workers (`tools/kanban_tools.py:64-92`, `tools/kanban_tools.py:1474-1507`).

Task-scoped tools default `task_id` from `HERMES_KANBAN_TASK`
(`tools/kanban_tools.py:99-104`). Destructive lifecycle tools enforce that a
worker can mutate only its own task (`tools/kanban_tools.py:134-163`):
`kanban_complete`, `kanban_block`, and `kanban_heartbeat` all resolve the task
id from the env default and then call the ownership guard
(`tools/kanban_tools.py:478-485`, `tools/kanban_tools.py:611-619`,
`tools/kanban_tools.py:650-666`).

The worker's first task-detail tool is `kanban_show`: it defaults to the env
task id, returns the task row, comments, events, runs, parents/children, and a
preformatted `worker_context` from `build_worker_context`
(`tools/kanban_tools.py:341-406`). That context builder starts with
`# Kanban task {task.id}: {task.title}` and includes assignee, status, tenant,
workspace, body, attachments, prior attempts, parent handoffs, role history, and
comments (`hermes_cli/kanban_db.py:7522-7665`).

This pattern depends on one OS process per job because the identity is process
env (`HERMES_KANBAN_TASK`). It does not directly transfer to one shared TUI
gateway child with many concurrent ticket sessions.

## 4. Durability across compaction

Conversation content is weaker than session-row state as an identity carrier.
Hermes compression is explicitly lossy: the default compressor summarizes middle
turns while protecting head and tail (`agent/context_compressor.py:612-621`).
The initial head protection decays after the first compression so early turns do
not stay immortal (`agent/context_compressor.py:2006-2021`). The summary prompt
asks for task/progress/state/decisions/files/critical context, but not arbitrary
identity tags unless the summarizer decides they matter
(`agent/context_compressor.py:1526-1600`). When summary generation fails under
historical behavior, the compressor may insert a deterministic fallback and drop
the middle window (`agent/context_compressor.py:817-821`,
`agent/context_compressor.py:1224-1416`).

In in-place compaction, Hermes keeps the same session id and rewrites the live
message set: archived pre-compaction turns remain on disk and searchable, but
live-context reloads only active compacted rows (`agent/conversation_compression.py:527-546`,
`hermes_state.py:2709-2759`, `hermes_state.py:3498-3541`).

In legacy rotation compaction, Hermes ends the old session, creates a child with
`parent_session_id`, and rotates `agent.session_id` (`agent/conversation_compression.py:557-602`).
TUI then re-anchors `session["session_key"]` to the new child id
(`tui_gateway/server.py:2518-2582`). Title is copied forward with
auto-numbering (`agent/conversation_compression.py:651-656`). The child
session's `source` is created from `agent.platform` (for TUI, `"tui"`), not from
the original custom `source` string (`agent/conversation_compression.py:596-599`).

So:

- A birth seed in conversation content can be summarized away from live context,
  even if archived rows remain searchable (`hermes_state.py:2719-2729`,
  `hermes_state.py:3498-3502`).
- Session-row fields are not summarized, but legacy compression can rotate to a
  child row; title is propagated with numbering, model_config is copied, and
  custom TUI `source` is not copied into the child row
  (`agent/conversation_compression.py:596-602`,
  `agent/conversation_compression.py:651-656`).
- There is no arbitrary session metadata field that would be "more durable" than
  content because the schema lacks one (`hermes_state.py:523-558`).

## 5. Option set

No ranking is implied.

> **DECISION (2026-07-08): Option A — the CLI tool lookup.** The agent reads `HERMES_SESSION_KEY`
> (per-turn) and a `panels` command resolves key→ticket through planner state, returning the full
> ticket; the `panels-worker` skill prompt says "if you don't know who you are, use this part of the
> CLI." Birth-injection options (B `source`, C title, D seed message, F system_prompt, G Hermes
> metadata) were rejected — the seed fades on compaction, `source`/title overload Hermes fields, and a
> clean durable system-prompt/metadata slot would require extending Hermes. The tool is durable,
> always-correct, and available today. This is also what confirmed staying on the one shared gateway
> child (identity is cheap in the shared model). See decisions.md 2026-07-08.

### Option A - Planner mapping keyed by `stored_session_id` / `HERMES_SESSION_KEY`

At `session.create`, store `stored_session_id -> ticket_id` in planner-owned
state. Inside the agent, a custom tool reads `HERMES_SESSION_KEY` and resolves it
through planner state/API.

Hermes support today:

- TUI returns `stored_session_id` and stores it as `session["session_key"]`
  (`tui_gateway/server.py:4416-4418`, `tui_gateway/server.py:4384`).
- TUI binds `HERMES_SESSION_KEY` per turn through ContextVars
  (`tui_gateway/server.py:1486-1501`, `gateway/session_context.py:124-164`).
- Shell subprocesses can receive non-empty ContextVar values
  (`tools/environments/local.py:475-482`).

Compaction requirement:

- If legacy compression rotates the session id, TUI changes `session_key` to the
  child id (`tui_gateway/server.py:2518-2582`). The planner lookup must either
  update the mapping after turns, or resolve child -> parent through
  `parent_session_id` (`hermes_state.py:523-558`,
  `agent/conversation_compression.py:596-602`).

Status: supported today with planner-owned mapping and a custom resolver tool.

### Option B - Encode ticket id in TUI `source`

Pass `source: "planner:<ticket-id>"` to `session.create`. The agent/tool can
read `HERMES_SESSION_SOURCE`; the initial SQLite row also stores it.

Hermes support today:

- `session.create` accepts `source` as a string and stores it live
  (`tui_gateway/server.py:4320`, `tui_gateway/server.py:4386`).
- First DB row persists that source (`tui_gateway/server.py:1219-1224`,
  `tui_gateway/server.py:1324-1327`).
- TUI binds source into ContextVars (`tui_gateway/server.py:1495-1501`,
  `gateway/session_context.py:124-164`).

Compaction requirement:

- In the live shared child, `session["source"]` remains on the session dict
  because compression sync only changes `session_key` (`tui_gateway/server.py:2518-2582`).
- In legacy compression child rows, DB `source` becomes `agent.platform`
  (`"tui"`), not the original source tag (`agent/conversation_compression.py:596-599`).
- After process restart/resume, preserving this option requires the planner to
  pass the source again on resume or recover it from planner mapping.

Status: supported today as an overloaded scalar tag, not as generic metadata.

### Option C - Encode ticket id in the title

Pass `title` at `session.create`, then have a custom tool resolve
`HERMES_SESSION_KEY -> SessionDB.get_session(...).title`.

Hermes support today:

- `session.create` accepts `title` into `pending_title`
  (`tui_gateway/server.py:4309`, `tui_gateway/server.py:4381`).
- Title is persisted after the first completed turn
  (`tui_gateway/server.py:6859-6867`, `hermes_state.py:1886-1933`).
- `get_session` and `get_session_title` can read it
  (`hermes_state.py:1766-1773`, `hermes_state.py:1935-1942`).

Limits:

- Title is not available automatically in system prompt/tools/env
  (`tui_gateway/server.py:2720-2769`, `gateway/session_context.py:92-105`).
- Title is user-visible, unique-validated, and sanitized by the title setter
  (`hermes_state.py:1886-1933`).
- Legacy compression propagates title with auto-numbering, so an exact ticket-id
  title may become a numbered continuation title (`agent/conversation_compression.py:651-656`).

Status: supported today as a visible overloaded field plus lookup.

### Option D - Birth seed message: "you are the employee for ticket X"

Pass a seed `messages` item at `session.create` and rely on conversation context.

Hermes support today:

- TUI accepts seed messages with roles `user`, `assistant`, or `system`
  (`tui_gateway/server.py:4198-4218`, `tui_gateway/server.py:4308`).
- The seed is live `session["history"]` and is supplied as conversation history
  on the first turn (`tui_gateway/server.py:4371`, `tui_gateway/server.py:6583-6586`,
  `tui_gateway/server.py:6720-6729`).

Limits:

- `session.create` does not persist a SQLite row immediately
  (`tui_gateway/server.py:4393-4398`).
- The SQLite flush skips `conversation_history` objects as presumed already
  stored history (`run_agent.py:1617-1630`), so seed messages are not a reliable
  durable DB tag.
- Compression can summarize/drop early conversation content from live context
  (`agent/context_compressor.py:612-621`, `agent/context_compressor.py:2006-2021`).

Status: supported today as live prompt context, not reliable durable identity.

### Option E - Per-turn instructions/system message

Send identity every turn instead of only at birth.

Hermes support today:

- TUI prompt submission takes only `text` for the user turn; no per-turn system
  identity param exists on `prompt.submit` (`tui_gateway/server.py:6318-6384`).
- The HTTP API chat route accepts per-turn `system_message` or `instructions`,
  but that is the HTTP route, not TUI `prompt.submit`
  (`gateway/platforms/api_server.py:1622-1650`).

Status: not a TUI `session.create` birth-once solution. Possible only by changing
planner turn text, using HTTP routes, or adding a TUI-side feature.

### Option F - Store identity in `system_prompt` / `model_config`

Use existing session columns as hidden storage.

Hermes support today:

- `sessions.system_prompt` and `sessions.model_config` exist
  (`hermes_state.py:523-530`).
- `SessionDB.create_session` can insert both (`hermes_state.py:1348-1381`).
- TUI `session.create` does not accept arbitrary `system_prompt` or arbitrary
  `model_config`; it builds system prompt from config/skills and derives
  model_config from model/provider/reasoning/fast (`tui_gateway/server.py:3750-3766`,
  `tui_gateway/server.py:1276-1329`).
- HTTP `/api/sessions` accepts `system_prompt`, but the HTTP session response
  hides the value and TUI does not use that route (`gateway/platforms/api_server.py:1491-1495`,
  `gateway/platforms/api_server.py:1386-1400`).

Status: not supported as a TUI `session.create` arbitrary identity channel
without direct DB writes or Hermes changes.

### Option G - Add first-class Hermes session metadata

Add a `metadata` JSON column or side table, accept `metadata` in
`session.create`, expose it through a safe current-session API/tool, and define
compression-copy behavior.

Hermes support today:

- Current `sessions` schema has no metadata column (`hermes_state.py:523-558`).
- TUI `session.create` does not read `metadata` (`tui_gateway/server.py:4303-4391`).
- HTTP `/api/sessions` does not persist unknown body fields and only creates
  `source="api_server"`, `model`, `system_prompt`, and optional title
  (`gateway/platforms/api_server.py:1469-1504`).

Status: requires Hermes changes.

### Option H - Use direct DB/source/title plus planner fallback lookup

Combine a Hermes-visible scalar (`source` or title) with a planner mapping keyed
by `stored_session_id`, and have the custom resolver tool prefer direct context
but fall back to planner/lineage lookup.

Hermes support today:

- Direct scalar: `source` is available through `HERMES_SESSION_SOURCE`
  (`tui_gateway/server.py:4320`, `tui_gateway/server.py:1495-1501`).
- Durable key: `HERMES_SESSION_KEY` is available to tools/shell
  (`tui_gateway/server.py:1486-1501`, `tools/environments/local.py:475-482`).
- Lineage: compression child rows carry `parent_session_id`
  (`agent/conversation_compression.py:596-602`, `hermes_state.py:523-558`).

Status: supported today with planner-owned resolution logic; no Hermes metadata
change required.
