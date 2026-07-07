# Spike 07 — Gateway configuration surface & session/process model

Factual, opinion-free reference. Every claim cites Hermes source (paths relative to
`~/.hermes/hermes-agent/`) or planner source (paths relative to this repo). No
recommendation is made; the three topologies in §4 are described with their
constraints and consequences only. This spike verifies (does not merely repeat)
`orchestration/runtime-redesign/spikes/01-hermes-linkage.md`; where a fact refines or
adds to spike 01, that is called out inline.

Read-only throughout. Nothing in `~/.hermes/hermes-agent` was modified; no gateway
child was spawned for this spike (all findings are from source reading).

---

## 0 · What the planner does today (the current adapter)

`src/planner/minds/runner.py::run_step` is one send:

1. Build env: copies `os.environ`, then sets `HERMES_PYTHON_SRC_ROOT`
   (`runner.py:70`), `HERMES_HOME` (`runner.py:71`), `HERMES_TUI_SKILLS=<role>`
   (`runner.py:72`), and any per-run `context_env` (`runner.py:73-74`).
2. `GatewayChild(python, env)` spawns `<python> -m tui_gateway.entry`
   (`src/planner/minds/gateway.py:132`, `spawn_popen` at `gateway.py:99-110`) with
   stdin/stdout/stderr pipes, text/line-buffered.
3. `wait_ready` blocks on the `gateway.ready` event (`gateway.py:173-176, 208-216`).
4. One `session.create` (new) or `session.resume` (`runner.py:84-99`), one
   `prompt.submit` (`runner.py:100-104`), drain events to the single
   `message.complete`/`error`/child-death (`runner.py:105-145`).
5. `finally: child.shutdown()` closes stdin and reaps the child every time
   (`runner.py:150-151`, `gateway.py:270-277`).

So today = **one fresh gateway OS process per send, torn down after** (topology (a),
§4). The rest of this spike is what Hermes itself supports, so the owner can weigh
(a) vs (b) vs (c).

---

## 1 · The full configuration surface

Three planes exist. **Process env** (fixed at spawn, read at agent-build/turn time).
**`session.create` / `session.resume` params** (per live session). **Mid-session RPCs**
(mutate a live session or the process). Each knob below is tagged with the plane(s)
that can set it.

### 1a · Where an "agent" is configured — the build path

A session's `AIAgent` is built by `_make_agent` (`tui_gateway/server.py:3718-3869`),
called from `_start_agent_build` (`server.py:963`), which is deferred: `session.create`
schedules it on a 0.05 s timer (`server.py:4404-4411`) and `prompt.submit` forces it on
the first turn (`server.py:6363`). The build reads:

- `ephemeral_system_prompt` = home config `agent.system_prompt`
  (`server.py:3750-3752`) **concatenated with** the preloaded-skill text from
  `HERMES_TUI_SKILLS` (`server.py:3753-3766`); passed at `server.py:3862`.
- `enabled_toolsets` = `_load_enabled_toolsets()` (`server.py:3849`, body
  `2042-…`): `HERMES_TUI_TOOLSETS` if set, else coding-context auto-selection, else
  config.
- model/provider/api creds = per-session `model_override` dict if present, else
  `_resolve_startup_runtime()` (env/config) (`server.py:3767-3821`).
- `max_iterations` = `_cfg_max_turns(cfg, 90)` (`server.py:3825`, body `3399-3407`):
  `HERMES_TUI_MAX_TURNS` env, else config `agent.max_turns`, else 90.
- `reasoning_config`, `service_tier` = per-session override if present, else config
  (`server.py:3839-3848`).
- `checkpoints_enabled` = `HERMES_TUI_CHECKPOINTS` (`server.py:3863`),
  `pass_session_id` = `HERMES_TUI_PASS_SESSION_ID` (`3864`), `skip_context_files` /
  `skip_memory` = `HERMES_IGNORE_RULES` (`3865-3866`).

The env values above are read **at each build** via `os.environ.get(...)`, so a fresh
`session.create`/`session.resume` re-reads them — but a process's `os.environ` is fixed
at spawn (the parent cannot mutate a running child's env). `reload.env`
(`server.py:8728-8746`) re-reads `~/.hermes/.env` into the process at runtime; ordinary
`HERMES_*` set by the spawner are not otherwise refreshed.

### 1b · Process-spawn env vars (the ones that configure an agent/session)

Found by grepping `os.environ`/`getenv` across `tui_gateway/`, `agent/`, `run_agent.py`,
`hermes_cli/`. `HERMES_HOME` alone has 378 references. The knobs that shape an
agent/session:

| Env var | What it sets | Plane / where read |
|---|---|---|
| `HERMES_HOME` | Active home: `state.db`, `skills/`, `logs/`, `config.yaml` — the whole config + session store | process; resolver `hermes_constants.py:54` (`get_hermes_home`) |
| `HERMES_PYTHON_SRC_ROOT` | `sys.path` guard so installed pkgs win over CWD | process; `tui_gateway/entry.py:7-12` |
| `HERMES_TUI_SKILLS` | Comma/newline list of skills force-loaded into the ephemeral system prompt from build 1 (role framing) | process; `server.py:3410-3419` → `3753-3766` |
| `HERMES_TUI_TOOLSETS` | Enabled toolset subset (else coding auto-select) | process; `server.py:2042-2047` |
| `HERMES_TUI_MAX_TURNS` | Max agent iterations per turn | process; `server.py:3399-3407` |
| `HERMES_MODEL` / `HERMES_INFERENCE_MODEL` | Startup model (provision-time seed) | process; `server.py:1583-1595`, `1629-1632` |
| `HERMES_TUI_PROVIDER` / `HERMES_INFERENCE_PROVIDER` | Provider | process; `server.py:1625`, `1646` |
| `HERMES_YOLO_MODE` | Bypass tool-approval gates | process (+ mutable via config.set); `server.py:8068-8074`, `hermes_cli/oneshot.py:171` |
| `HERMES_IGNORE_RULES` | Skip context files + memory | process; `server.py:3865-3866` |
| `HERMES_TUI_CHECKPOINTS` | Enable file checkpoints | process; `server.py:3863` |
| `HERMES_PROFILE` | Named profile (its own home/state.db) | process; used across `hermes_cli/` |
| `HERMES_EPHEMERAL_SYSTEM_PROMPT` | Ephemeral system prompt — **classic-CLI only** (`cli.py:3589`); the `tui_gateway` build path does **not** read it (it builds `ephemeral_system_prompt` from config + skills, `server.py:3862`). Setting it on a gateway child has no effect. | (not the gateway plane) |
| `HERMES_KANBAN_TASK`, `HERMES_KANBAN_WORKSPACE`, `HERMES_KANBAN_BOARD`, `HERMES_KANBAN_BRANCH`, `HERMES_KANBAN_DB`, … | Per-task context read by tools/skills/prompt; drives a `KANBAN_GUIDANCE` system-prompt block when the kanban tool is present | process; `run_agent.py:2801`, `agent/skill_utils.py:239`, `agent/turn_finalizer.py:85`; block at `agent/prompt_builder.py:182-186`, injected `agent/system_prompt.py:197-204` |

Gateway-mechanics env (not agent-shaping, but relevant to lifecycle):
`HERMES_TUI_RPC_POOL_WORKERS` (`server.py:192`, default 4),
`HERMES_TUI_SLASH_TIMEOUT_S` (`server.py:141`, default 45),
`HERMES_TUI_SESSION_TTL_S` (`server.py:641`, default 6 h — idle-session eviction),
`HERMES_TUI_WS_ORPHAN_REAP_GRACE_S` (`server.py:157`, default 20),
`HERMES_TUI_GATEWAY_SHUTDOWN_GRACE_S` (`entry.py:63`, default 1.0),
`HERMES_TUI_SIDECAR_URL` (`entry.py:39`, mirror events to a dashboard WS).

The full `HERMES_TUI_*` namespace (43 vars) and the full `HERMES_*` frequency table were
enumerated during this spike; the table above is the subset that configures an
agent/session or the gateway lifecycle. The rest are transport/UI/credentials/billing/
voice/desktop knobs (e.g. `HERMES_TUI_THEME`, `HERMES_TUI_FPS`, `HERMES_OAUTH_FILE`,
`HERMES_DASHBOARD_*`).

### 1c · `session.create` params (`server.py:4303-4351`)

| Param | Effect | Notes |
|---|---|---|
| `cols` | Terminal width for render | `4307` |
| `messages` | Seed history | `4308` |
| `title` | Pending session title | `4309` |
| `cwd` | Session working dir (only an existing dir is persisted) | `4314-4319` |
| `source` | Session source label (e.g. `"planner"`) | `4320` |
| `profile` | App-global profile → that profile's home/state.db | `4327-4328` |
| `model` | **Per-session model override**, built into the agent | `4335-4340` → `3770-3811` |
| `provider` | Per-session provider (with `model`) | `4337` |
| `reasoning_effort` | Per-session reasoning override | `4342-4348` |
| `fast` | Per-session priority service tier | `4351` |
| `close_on_disconnect` | Close when transport drops | `4365` |

**Verify-note vs spike 01:** spike 01 §Q3 states "No per-session RPC param exists" for
model/system-prompt. That holds for **skills, toolsets, and system prompt** (no
`session.create` param — they are process/home plane only). It does **not** hold for
**model / provider / reasoning_effort / fast**, which *are* per-session `session.create`
params applied as overrides in `_make_agent` (`server.py:4335-4351`, `3767-3848`). Model
is additionally changeable mid-session (below).

### 1d · `session.resume` params (`server.py:4555-4567, 4585, 4655, 4678`)

`session_id` (durable key **or** title, `4557-4584`), `cols` (`4561`), `profile`
(`4566`), `lazy` (`4585` — watch-window attach, no agent build), `close_on_disconnect`
(`4692`), `source` (`4678`). Resume restores a per-session `model_override` from the
stored DB row (`server.py:4836-4839`). No skills/toolsets/system-prompt param — those
come from the resuming **process's** env/home at rebuild (spike 01 §Q2: the role skill
must be re-supplied via env on every child that resumes).

### 1e · `prompt.submit` / `prompt.background` / `command.dispatch` / `slash.exec` params

- **`prompt.submit`** (`server.py:6318-6384`): `session_id`, `text`,
  `truncate_before_user_ordinal` (optional history rewind). Returns
  `{"status":"streaming"}` immediately; the turn streams as events.
- **`prompt.background`** (`server.py:7621-7664`): `session_id`, `text`. Spawns a
  one-off `AIAgent` on a daemon thread (`7631-7663`), emits `background.complete`.
- **`command.dispatch`** (`server.py:8965-9034`): `name`, `arg`, `session_id`. Resolves
  quick-commands / plugin / skill commands; a skill command returns a `message` the
  client then feeds back as a `prompt.submit`.
- **`slash.exec`** (`server.py:10061-10140`): `session_id`, `command`. Runs on a
  **per-session `_SlashWorker` subprocess** (`server.py:231-260`, attach `3924-3928`,
  use `10134-10140`); pending-input/skill/plugin commands are routed elsewhere
  (`10080-10132`). `slash.exec` is a long handler (runs on the pool, §2).

### 1f · Mid-session changes

- **`config.set`** (`server.py:7841-8357`): switch model (`7862` — guarded 4009 if
  busy), toggle `HERMES_YOLO_MODE` (`8068-8074`), reasoning, `personality`, busy-input
  mode, etc. `_sync_agent_model_with_config` re-syncs the live agent's model each turn.
- **`session.steer`** (`server.py:6281`): inject text mid-run without ending the turn.
- **`session.interrupt`** (`server.py:6035`): end the current turn (yields
  `message.complete{status:interrupted}`; session stays usable — spike 01 §probe 5).
- **`reload.env`** (`server.py:8728-8746`): re-read `~/.hermes/.env` into the process.

### 1g · Fixed once the agent is built (for a given turn)

Within one built `AIAgent`, the toolset snapshot, the preloaded-skill text, and the
ephemeral system prompt are fixed (MCP tools are snapshotted once at build,
`server.py:3731-3748`, and only refreshed by `/reload-mcp`). A **new** `session.create`
or `session.resume` re-runs `_make_agent` and re-reads the process env — so changing a
role skill / toolset requires a new build, which in practice means a process whose env
carries the new value (env is fixed per process at spawn).

---

## 2 · Session + concurrency model

### 2a · One process, many sessions — yes

`_sessions: dict[str, dict]` (`server.py:125`) is an **in-process** table keyed by the
live 8-hex `session_id`, guarded by the RLock `_sessions_lock` (`server.py:134`). Each
`session.create` (`server.py:4359-4392`) and each non-lazy `session.resume`
(`server.py:4685-4720, 4816-4851`) inserts an entry. `session.active_list`
(`server.py:5016`) lists them. Spike 01 (probe 5) observed two live sessions coexisting
in one child. **Session state is split:** the live handle + the built `AIAgent` object +
the `_SlashWorker` live in the process `_sessions` dict; the durable transcript lives in
the home's `state.db` (`session.create` writes no row — `server.py:4393-4398` — the row
appears lazily on first prompt via `_ensure_session_db_row`, `server.py:6362`; `AIAgent`
also INSERT-OR-IGNOREs per turn).

### 2b · Concurrent runs in one process — yes, unbounded by design

The dispatcher (`tui_gateway/entry.py:324-344`) reads stdin **serially** and calls
`dispatch(req)` (`server.py:914-952`). `dispatch` runs most methods **inline** on that
single reader thread and routes only `_LONG_HANDLERS` to a thread pool
(`server.py:934-948`). `_LONG_HANDLERS` = `{billing.step_up, browser.manage, cli.exec,
llm.oneshot, plugins.manage, session.branch, session.compress, session.resume,
shell.exec, skills.manage, slash.exec}` (`server.py:175-189`); the pool is a
`ThreadPoolExecutor(max_workers=HERMES_TUI_RPC_POOL_WORKERS or 4)`
(`server.py:191-200`).

`prompt.submit` is **not** a long handler → it runs inline, sets the session's `running`
flag, spawns a `run_after_agent_ready` **daemon thread**, and returns immediately
(`server.py:6357-6384`). The actual model turn (`_run_prompt_submit`, `server.py:6583`)
takes only that session's `history_lock` and launches the model loop on a **fresh
`threading.Thread(target=run, daemon=True)`** (`server.py:~7009`) — **not** the bounded
pool. There is **no global run lock** (`_prompt_lock`, `server.py:135`, is used only for
inflight/notification bookkeeping at `1530-1554, 7785`). Consequence: two different
sessions in one process can each have a turn running concurrently, each on its own
daemon thread; concurrency across sessions is bounded only by CPU/provider, not by any
gateway lock.

### 2c · The busy guard (`4009`) — per-session, in-process memory

`prompt.submit` checks, under that session's `history_lock`:

```
if session.get("running"):
    return _err(rid, 4009, "session busy")      # server.py:6331-6332
```

`running` is a key on the in-memory `_sessions[sid]` dict (`False` at create,
`server.py:4383`; set `True` at `6357`; reset on completion/error). It is **not** in
`state.db`. The same `4009` guard protects `session.cwd.set` (`4875`), handoff
(`5299-5300`), `session.undo`/`session.compress` (`5785, 5808`), model switch
(`7862-7863`), `/retry` (`9051`), rollback (`10488-10489`) — all on the same in-process
flag.

**Cross-process scope — decisive:** because `running` lives in one process's `_sessions`
dict, a **second OS process** that resumes the same stored key builds its **own**
`_sessions` entry with its **own** `running` flag. The `4009` guard therefore does **not
span processes** — two children resuming the same durable key can both run turns against
it concurrently, interleaving writes to the same `state.db` conversation. This confirms
spike 01 §Q5 ("It does NOT span processes"). The planner's per-session serialized queue
in front of `run_step` is the only thing preventing this today; it is load-bearing
correctness, not ergonomics.

The **only** cross-process guard is `try_acquire_active_session`
(`hermes_cli/active_sessions.py:234-295`), a **file-registry count cap** on
`max_concurrent_sessions`, claimed at `session.create` (`server.py:4355-4357` → `4090`
when over cap). It is a **no-op lease when the cap is unset** (`active_sessions.py:246-254`;
spike 01 confirms the cap is `null` here). Even when set, it caps the **total count** of
active sessions and appends one entry per acquisition (`active_sessions.py:256-289`) — it
does **not** enforce per-session mutual exclusion (nothing rejects a second entry with the
same `session_id`). So it is not a per-session busy guard across processes under any
configuration.

### 2d · Session persistence across a child's exit — yes

A session's transcript survives its creating child because it is in the home's
`state.db`. A later, different child resumes it via `session.resume`
(`server.py:4555`): `db.get_session` / `get_session_by_title` (`4580-4584`), then
`db.resolve_resume_session_id` follows the **compression-continuation chain to the live
tip** (`server.py:4612-4619`) so a resume on a rotated-out parent id binds to the
descendant holding post-compression turns (spike 01 §probe 3, resume across a full
process restart, proven — including a provider prompt-cache hit). Auto-compression ends a
session and forks a continuation child key (`_sync_session_key_after_compress`, spike 01
§open-q 3); resume traverses that chain. Unknown key → `4007 session not found`
(`server.py:4599`). If a session is currently live **in this process**, resume fast-paths
to the existing handle instead of rebuilding (`server.py:4642-4646`).

Independently of process exit, **idle sessions are evicted from `_sessions`** after
`HERMES_TUI_SESSION_TTL_S` (default 6 h) by `_reap_idle_sessions`
(`server.py:641-686`) — the transcript stays in `state.db`; only the in-process handle is
dropped, and the process keeps running.

---

## 3 · Process lifecycle facts

- **Spawn → `gateway.ready`:** `main()` optionally starts a backgrounded MCP-discovery
  daemon thread (`entry.py:286-314`, only if `mcp_servers` configured), then emits the
  `gateway.ready` event (`entry.py:316-322`) **before** entering the stdin loop
  (`entry.py:324`). Discovery is backgrounded so a dead MCP server can't stall ready.
  Spike 01 measured ready at **0.10–0.15 s** (spike 01:50).
- **Agent build is separate and deferred:** `session.create` returns a lightweight
  handle and schedules `_make_agent` on a 0.05 s timer (`server.py:4404-4411`);
  otherwise the first `prompt.submit` forces the build (`server.py:6363`). Spike 01
  measured build at **~3.3 s fresh / ~0.9 s on resume** (spike 01:186, 102), with
  provider prompt-cache carrying across process restarts.
- **What a live child holds:** the `_sessions` dict of live handles + built `AIAgent`
  objects; a per-session `_SlashWorker` **subprocess** each (`server.py:231-260,
  3924-3928`); the 4-thread RPC pool (`server.py:197`); the MCP-discovery thread; an open
  `state.db` handle (`_get_db`); per-turn daemon threads; per-session notification pollers
  (`server.py:3959`).
- **Child dies mid-run:** all in-process state is lost — live handles, the in-flight
  turn's daemon thread, and any tail not yet committed. `state.db` retains the turns
  `AIAgent` committed per turn; the signal handler flushes sessions within the shutdown
  grace window (`entry.py:139-155`, `_shutdown_sessions`). A later child resumes from
  `state.db` (§2d). Reader threads on the planner side surface child death as an errored
  `RunResult` (`runner.py:106-114`, `gateway.py:189-198`).
- **Keep-warm / reuse:** nothing in Hermes forces per-send teardown. One child can serve
  unlimited `session.create`/`session.resume` + `prompt.submit` calls across many
  sessions; Hermes's own TUI keeps a single gateway child for an entire interactive
  session (many turns). Clean shutdown = close the child's stdin (`entry.py:344` exits on
  EOF); the planner does this every send (`gateway.py:270-277`). Idle **sessions** inside
  a warm child are reaped after the TTL (§2d); the **process** persists until stdin
  closes or it is killed.

---

## 4 · The three topologies — factual constraints only

The load-bearing split from §1: **model / provider / reasoning / fast** are per-session
(`session.create` params). **Role skill (`HERMES_TUI_SKILLS`), toolset
(`HERMES_TUI_TOOLSETS`), home (`HERMES_HOME`), system-prompt baseline, max-turns, and
task-context env (`HERMES_KANBAN_*`-style)** are per-**process** (fixed at spawn, read
from the one process env at each build). This split is what differs the topologies.

### (a) Child-per-send — current

**Means:** every send spawns a fresh `tui_gateway.entry` process with that send's role
env, does one create-or-resume + one `prompt.submit`, drains to `message.complete`, then
closes stdin and reaps (matches §0, `runner.py`).

- **Concurrency:** one process = one session = one run; cross-send concurrency is many
  independent processes.
- **Busy-guard scope:** moot — one session per process; the planner's own per-session
  queue prevents two live children on the same durable key (required, §2c).
- **Per-send latency:** spawn → ready (~0.1 s) **plus** agent build (~3.3 s fresh /
  ~0.9 s resume) on every send; provider prompt-cache survives across processes (spike
  01:99).
- **Lifecycle:** trivial — spawn/teardown per send; no long-lived child to supervise;
  the child's process exit is a second, OS-level end-of-run signal.
- **Crash blast-radius:** one send; `state.db` holds committed turns; the next send
  resumes.
- **Role skill + task context:** both delivered per send via the child's env
  (`HERMES_TUI_SKILLS` + `HERMES_KANBAN_*`-style vars) — arbitrary per send because the
  process is fresh each time.

### (b) One warm child per ticket

**Means:** one long-lived `tui_gateway.entry` process per active ticket, its env fixed at
spawn to that ticket's role skill + task-context; it serves many `prompt.submit` on that
ticket's session (and its chat) without re-spawning or rebuilding.

- **Concurrency:** one live session per child (that ticket); concurrency across tickets =
  one process each. (A single child *can* hold more sessions, §2a, but per-ticket
  topology uses one.)
- **Busy-guard scope:** the in-process `4009` guard now **meaningfully serializes** that
  ticket's session within its own process — next-step prompts and chat messages to the
  same session are naturally serialized by `running`; no second process touches that key,
  so the guard is authoritative for that ticket.
- **Per-send latency:** after warm-up, **no spawn and no rebuild** — the built `AIAgent`
  persists in `_sessions`; a send is just `prompt.submit` + streamed turn.
- **Lifecycle:** supervise one child per active ticket; handle idle-session eviction
  (`HERMES_TUI_SESSION_TTL_S`, §2d — a warm-but-idle ticket's session can be reaped from
  the dict, after which the next send rebuilds within the same still-running child) and
  child crashes.
- **Crash blast-radius:** that ticket's in-flight turn; `state.db` retains committed
  turns; resume (same or new child) rebuilds.
- **Role skill + task context:** fixed for the child's lifetime — the role skill and
  `HERMES_KANBAN_*`-style context are read from the child's process env, which cannot
  change after spawn (`reload.env` only re-reads `~/.hermes/.env`, `server.py:8728`).
  Changing a ticket's role skill requires respawning its child. Per-send **model /
  provider / reasoning** can still vary per `session.create`, but a warm child reuses one
  built session, so model changes go through `config.set` mid-session (`server.py:7862`).

### (c) One persistent shared child for all

**Means:** a single `tui_gateway.entry` process for every ticket and chat; each ticket/
chat is a distinct session (distinct `_sessions` entry) inside it.

- **Concurrency:** one process holds **many** sessions (§2a) and runs them **concurrently**
  on per-turn daemon threads (§2b) — supported without a global lock.
- **Busy-guard scope:** the `4009` guard is per-session within that one process, so each
  session is correctly serialized and distinct sessions run in parallel; because it is one
  process, the guard is authoritative (no cross-process duplication for a given key as
  long as the planner routes each key to this one child).
- **Per-send latency:** no spawn; first build **per session** (~3.3 s), none thereafter.
- **Lifecycle:** supervise one process; idle sessions reaped after the TTL (§2d);
  `HERMES_TUI_RPC_POOL_WORKERS` (default 4) bounds only the long-handler pool, not model
  turns.
- **Crash blast-radius:** **all** sessions/tickets at once — every in-flight turn dies
  with the process; `state.db` retains committed turns; every session must be resumed
  (in a new process).
- **Role skill + task context — the binding constraint:** `HERMES_TUI_SKILLS`,
  `HERMES_TUI_TOOLSETS`, `HERMES_HOME`, the system-prompt baseline, and
  `HERMES_KANBAN_*`-style task env are **process-global**; every session built in the one
  shared child reads the **same** values (there is no per-session param for skills/
  toolsets/home/task-env — §1c/§1d). So per-ticket **role skills** or per-ticket
  **task-context env** cannot differ between sessions in a single shared child. Per-ticket
  **model / provider / reasoning** *can* differ (per-session `session.create` params).
  Delivering differing per-ticket role/context under (c) would have to ride the
  `prompt.submit` text and/or tools that read a per-turn source, rather than per-session
  env — Hermes exposes no per-session env/skills/toolset/system-prompt override for a
  shared process.

---

## 5 · Source index (primary citations)

Hermes (`~/.hermes/hermes-agent/`):
`tui_gateway/entry.py:7-12, 39, 63, 129-155, 286-344`;
`tui_gateway/server.py:125, 134-135, 175-200, 641-686, 875-952, 963, 1583-1649,
2042-2047, 3399-3419, 3718-3869, 4303-4411, 4555-4720, 4816-4851, 4875, 5016, 6318-6384,
6583, 7009, 7621-7664, 7841-8074, 8728-8746, 8965-9034, 10061-10140`;
`hermes_cli/active_sessions.py:234-320`; `hermes_cli/oneshot.py:171`;
`agent/prompt_builder.py:182-186`; `agent/system_prompt.py:197-204`;
`agent/skill_utils.py:239`; `agent/turn_finalizer.py:85`; `run_agent.py:2801`;
`cli.py:3589`; `hermes_constants.py:54`.

Planner (this repo): `src/planner/minds/gateway.py:99-110, 132, 173-216, 270-277`;
`src/planner/minds/runner.py:54-151`; `src/planner/minds/config.py:16-71`.

Prior spike verified: `orchestration/runtime-redesign/spikes/01-hermes-linkage.md`
(§Q1–Q5, probes 1/3/5). Refinements added here: per-session model/provider/reasoning/fast
params on `session.create` (§1c); the cross-process count-cap lease vs. the in-process
per-session busy guard (§2c); the per-process vs per-session config split as the binding
constraint on topology (c) (§4).
