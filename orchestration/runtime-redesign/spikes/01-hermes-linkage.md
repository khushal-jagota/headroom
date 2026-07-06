# Spike 01 — Hermes linkage (Option 3 + the agent-operation primitive)

Empirical spike: launched the real Hermes TUI gateway as a stdio subprocess from a plain
(non-Hermes) Python process and drove it over JSON-RPC — session lifecycle, skill-at-kickoff
injection, real model round trips, resume across process restarts, interrupt/re-prompt,
slash-command enumeration. All five questions answered with live evidence. **Headline:
Option 3 works end-to-end, and skills-at-kickoff is feasible today via `HERMES_TUI_SKILLS`
env on the gateway child — proven with a live model round trip.**

Everything below was run on 2026-07-06 against the installed hermes-agent
(`~/.hermes/hermes-agent`, gateway `tui_gateway/`, 85 registered RPC methods). All test
sessions were deleted afterwards (verified: resume returns `4007 session not found`).
Probe scripts lived in the session scratchpad; nothing was written outside the v2 repo
and scratch.

---

## 1 · What I tried & what happened

### Launch + transport (probe 1)

Spawned from **system python3** (not Hermes's venv), cwd in a scratch dir:

```python
subprocess.Popen(
    ["~/.hermes/hermes-agent/venv/bin/python", "-m", "tui_gateway.entry"],
    stdin=PIPE, stdout=PIPE, stderr=PIPE, text=True, bufsize=1,
    env={**os.environ, "HERMES_PYTHON_SRC_ROOT": "~/.hermes/hermes-agent"},
)
```

Protocol: newline-delimited JSON-RPC 2.0 on stdin/stdout. Actual transcript (timestamps =
seconds since spawn):

```
[ 0.15s] << event gateway.ready {"skin": ...}
[ 0.15s] >> {"method": "session.create", "params": {"cols": 100, "source": "spike-probe1"}}
[ 0.28s] << {"result": {"session_id": "01c971b1", "stored_session_id": "20260706_162201_0ff398",
             "info": {"model": "gpt-5.5", "lazy": true, "profile_name": "default", ...}}}
[ 0.28s] >> {"method": "bogus.method"}
[ 0.28s] << {"error": {"code": -32601, "message": "unknown method: bogus.method"}}
[ 0.49s] >> {"method": "commands.catalog"}
[ 0.49s] << {"result": {"pairs": [...135 commands...], "skill_count": 62,
             "categories": ["Session","Info","Configuration","Tools & Skills","Exit","TUI"], ...}}
[ 0.82s] >> {"method": "session.close", "params": {"session_id": "01c971b1"}}
[ 0.82s] << {"result": {"closed": true}}
         (stdin closed) → stderr: "[gateway-exit] stdin EOF" → exit rc=0
```

- **Cold start to `gateway.ready`: 0.10–0.15s** (measured across 6 launches).
- `tui_gateway` is importable from the venv python with no `PYTHONPATH`/cwd tricks
  (verified: `venv/bin/python -c "import tui_gateway"` from an unrelated cwd). I still set
  `HERMES_PYTHON_SRC_ROOT` because `entry.py` uses it to guard against cwd shadowing —
  the kanban dispatcher sets it too.
- Clean shutdown = close the child's stdin (`entry.py` main loop is `for raw in sys.stdin`).
  Exit code 0, crash-log breadcrumb written.

### Skill at kickoff + real round trip (probe 2)

Relaunched the gateway with `HERMES_TUI_SKILLS=rollover` in the child env (v1's rollover
skill, chosen because its content is distinctive). Created a session, waited for the
deferred agent build, submitted one prompt forbidding tool use:

```
[ 0.19s] session.create → sid=0b120eb0 key=20260706_162300_3ddbb4
[ 3.34s] << event session.info {model: gpt-5.5, provider: openai-codex,
          tools: [browser, clarify, ..., terminal, ...17 toolsets], system_prompt: ""}
[ 3.34s] >> prompt.submit "Reply with exactly one short line: the name of the preloaded
          skill active in this session, and the planning-dir path it tells you to operate on"
[ 3.34s] << event message.start {}
[ 3.34s] << {"result": {"status": "streaming"}}
[ 3.34s] >> prompt.submit "second prompt"          ← while the first is running
[ 3.34s] << {"error": {"code": 4009, "message": "session busy"}}
[ 3.6–11.7s] << message.delta ×16, thinking.delta, reasoning.available
[11.72s] << event message.complete {
           "text": "rollover — ~/.hermes/planning/sprints/current/daily/YYYY-MM-DD/",
           "status": "complete",
           "usage": {"input": 32068, "output": 134, "cost_status": "included", "cost_usd": 0.0}}
```

The model named the preloaded skill **and quoted the v1 rollover skill's planning-dir
path, with zero tool calls** — the skill's guidance was demonstrably in its system prompt
from kickoff. Note `session.info.system_prompt` is empty: the skill text rides in the
agent's *ephemeral* system prompt (appended at API-call time, not in the cached base), so
the injection is real but invisible in that field.

### Durable resume across a gateway restart (probe 3)

Killed the gateway process entirely. Launched a **fresh** child (same skill env), resumed
by the stored key:

```
[ 0.99s] session.resume {"session_id": "20260706_162300_3ddbb4"} →
         {"session_id": "1d052848", "resumed": "20260706_162300_3ddbb4",
          "message_count": 2, "messages": [ ...both prior turns, verbatim... ]}
[ 3.62s] (prompt: "quote your previous answer; is the preloaded skill still active?")
         message.complete: "\"rollover — ~/.hermes/planning/sprints/current/daily/YYYY-MM-DD/\"
                            \nyes — rollover"
         usage: cache_read=31744   ← provider prompt-cache HIT across process restart
```

Resume including agent rebuild: **0.9s**. Conversational continuity and skill
re-injection both proven. Also: `session.resume` follows the compression-continuation
chain to the live tip (server.py:4612) — resuming a rotated-out key lands on the
descendant session that holds the post-compression turns.

Negative checks, same probe:

```
session.resume {"session_id": "bogus_key_xyz"}  → {"code": 4007, "message": "session not found"}
gateway launched with HERMES_TUI_SKILLS=no-such-skill-zzz, session.create →
  [0.60s] << event error {"message": "agent init failed: Unknown skill(s): no-such-skill-zzz"}
```

A bad skill name fails **loudly and detectably** at agent build, before any model call.

### Interrupt / re-prompt / concurrency / slash.exec (probe 5)

```
prompt.submit (long list task) → message.start, deltas flowing
session.create (second session, same process, mid-run) → OK — two live sessions coexist
session.interrupt → {"result": {"status": "interrupted"}}
  << event message.complete {"text": "Operation interrupted: ...", "status": "interrupted"}
prompt.submit "reply: recovered" on the SAME session →
  << message.complete {"text": "recovered", "status": "complete"}   ← re-promptable
slash.exec {"command": "/status"} → {"result": {"output": "Hermes CLI Status\n\nSession ID: ..."}}
session.active_list → both sessions listed with status/keys
session.close ×2; session.delete ×2 → {"deleted": ...} / 4007 for the row-less one
```

### Isolation home (zero-cost probe)

Launched the gateway with `HERMES_HOME=<scratch>/plannerhome`. The gateway scaffolded a
complete fresh home there (own `state.db`, `skills/`, `logs/`, …), fell back to default
model config, and the agent build failed **loudly** with a structured event:

```
<< event error {"message": "agent init failed: No inference provider configured. ..."}
```

So a dedicated home fully isolates planner sessions/config from the owner's `~/.hermes`,
at the cost of provisioning model config + credentials into it (exactly the kanban
profile pattern). Scratch home deleted after.

### Session-row hygiene (probes 1/3/4)

- `session.create` does **not** write a DB row; the row appears lazily on first prompt
  (server.py:4393 comment + verified: never-prompted keys return 4007 on delete/resume).
- `session.delete <stored key>` removes the row — but returns
  `4023 cannot delete an active session` if a live handle exists in some process; close
  first (or let the child exit), then delete.
- All spike sessions verified gone.

---

## 2 · Findings per question

### Q1 — Transport: YES, works exactly as hoped

- `venv/bin/python -m tui_gateway.entry` is a stdio JSON-RPC server: one request or
  response/event per line; first frame is the `gateway.ready` event. Parent needs no
  Hermes imports, no venv coupling — proven from system python3.
- Full round trip proven: create → prompt → streamed deltas → complete → close → **resume
  the same durable key in a brand-new process** → continuity → delete.
- Session identity: `session.create` returns a live per-process handle (`session_id`,
  8-hex) plus the durable `stored_session_id` (`YYYYMMDD_HHMMSS_xxxxxx`), persisted in
  the active home's `state.db`. All later RPCs take the live handle; `session.resume`
  takes the durable key (or a title) and returns a new live handle + full history.
- Method surface (all 85, enumerated from `@method("...")` in `tui_gateway/server.py` and
  spot-called): `session.create/resume/list/close/delete/status/history/title/usage/
  interrupt/steer/branch/compress/undo/save/activate/active_list/most_recent/cwd.set`,
  `prompt.submit`, `prompt.background`, `approval.respond`, `clarify.respond`,
  `sudo.respond`, `secret.respond`, `commands.catalog`, `command.resolve`,
  `command.dispatch`, `complete.slash`, `complete.path`, `slash.exec`, `cli.exec`,
  `shell.exec`, `skills.manage`, `skills.reload`, `tools.list/show/configure`,
  `toolsets.list`, `config.get/set/show`, `model.options/save_key/disconnect`,
  `session.info` (event), plus voice/browser/billing/rollback/cron/plugins groups we
  don't need. Slow handlers (`session.resume`, `slash.exec`, `cli.exec`, `shell.exec`,
  `session.compress/branch`, `skills.manage`, …) run on an internal 4-thread pool, so
  `session.interrupt`/`approval.respond` stay responsive mid-run.
- Events observed live: `gateway.ready`, `session.info`, `message.start`,
  `message.delta`, `thinking.delta`, `reasoning.available`, `message.complete`
  (status `complete` | `interrupted`; `error` also possible per source), `error`,
  `status.update`. Source also defines `tool.start/complete/generating`,
  `approval.request`, `background.complete`.
- Timings: **ready 0.10–0.15s; agent build ~3.3s fresh / ~0.9s resume; tiny prompt round
  trip 2.6–8.4s** (gpt-5.5, high reasoning). Costs rode the owner's subscription
  (`cost_status: "included"`); ~32k input tokens per step (full toolset + skill), with
  provider prompt-caching working across process restarts.
- Errors are clean JSON-RPC: `-32601` unknown method, `4007` not found, `4009` busy,
  `4023` delete-while-active, `4090` session-slot limit (only if
  `max_concurrent_sessions` is configured; it's `null` here).

### Q2 — Skills at kickoff: YES — feasible today, per gateway *process*

- Mechanism: set `HERMES_TUI_SKILLS=<name>[,<name>…]` in the child's env. Every agent
  build in that process (create *and* resume) calls
  `build_preloaded_skills_prompt(...)` (agent/skill_commands.py:564), which loads each
  skill's SKILL.md, wraps it in an activation note — *"Treat its instructions as active
  guidance for the duration of this session"* — and appends it to the agent's ephemeral
  **system prompt** (server.py:3753–3766). This is not a model-chosen load; it's forced
  guidance, in force from turn 1. **Proven live**: the model named the skill and quoted
  its content with tools forbidden.
- Granularity: **per process**, not per `session.create` call (the env is read at agent
  build inside that process). There is no per-session skills param on `session.create`.
  So role separation = one gateway child per role (or per run) with different env — which
  is precisely how "the kanban flips a plain hermes chat into worker-mode": the kanban
  worker is `hermes -p <profile> chat -q "…"` with role env vars, one process per run.
- Because the skill rides the *ephemeral* prompt, it is **not persisted with the
  session** — whoever resumes the session must supply the env again (proven: resume in a
  skill-env process re-injected it). Our runtime must own "which role skill belongs to
  this session" and set the env on every child it spawns for it.
- Unknown skill name → structured `error` event at build ("agent init failed: Unknown
  skill(s): …"), before any model call.
- Relevant kanban history (docs/kanban-guide/03…/prompt-injection.md): the kanban
  *abandoned* its force-loaded `kanban-worker` skill and folded the mandatory protocol
  into an always-injected system-prompt block (`KANBAN_GUIDANCE`, gated on
  `HERMES_KANBAN_TASK`), because "a rule the model can fail to load is not a rule."
  The preload mechanism we're using here is the same *shape* as their fix — skill text
  forced into the system prompt — so we get the unskippable property without patching
  hermes-agent. Their per-task `--skills` flag (same underlying mechanism) remains
  supported for opt-in extras.

### Q3 — System prompt at kickoff: YES, but the same per-process/per-home granularity

- `agent.system_prompt` in the active home's `config.yaml` is read at agent build and
  passed (concatenated with any preloaded-skill text) as the agent's ephemeral system
  prompt (server.py:3750–3766). Per-agent = per-`HERMES_HOME` (or per gateway process via
  which home you point it at). No per-session RPC param exists.
- Practical consequence: role framing belongs **in the role skill** (one lever, proven);
  the config `system_prompt` is a home-wide baseline. Per-*ticket* dynamic context (ids,
  paths) belongs in the next-step prompt text and/or per-run env — kanban does exactly
  this (`HERMES_KANBAN_TASK` env + a minimal spawn prompt + tools that return context).
- Also available: a `personality` overlay (`config.set` / `slash.exec /personality`)
  mutates the live agent's ephemeral prompt but injects a visible pivot marker into
  history — not suitable for role framing.

### Q4 — Slash-command enumeration: YES

- `commands.catalog` (live-tested) returns the full registry: 135 commands as
  `[name, description]` pairs, grouped `categories`, a `canon` alias→canonical map,
  `sub` (per-command subcommand lists), plus 62 skill-commands scanned from the skills
  library, plus user `quick_commands`. Everything a UI needs to render a slash menu.
- `command.resolve` canonicalizes a name; `slash.exec {session_id, command}` executes
  (live-tested with `/status`; heavier commands run through a persistent per-session
  `slash_worker.py` subprocess the gateway manages itself). `complete.slash` exists;
  returned `items: []` for a valid prefix in my test — didn't chase it; the catalog is
  the enumeration mechanism, completion is cosmetic.

### Q5 — Run boundary & the approval cycle: YES — code observes start and end structurally

- **Start**: `prompt.submit` returns `{"status": "streaming"}` immediately and
  `message.start` is emitted. **End**: exactly one `message.complete` per run with
  `status ∈ {complete, interrupted, error}` plus the full final text and usage. Observed
  live for complete and interrupted; error path verified in source
  (server.py:6770–6805) and via the `error` event for build failures. The doc's claim
  holds: the transport gives structured start/finish/error.
- **One in-flight run per session** enforced *within a gateway process* (`4009 session
  busy` — proven). It does NOT span processes: two children could resume the same stored
  key concurrently. The already-decided per-session serialized queue in our server is
  therefore load-bearing correctness, not just ergonomics.
- **Re-promptability**: after `message.complete` (including after interrupt), the same
  session takes the next `prompt.submit` cleanly — proven. This is the entire mechanical
  basis for "every run ends awaiting approval; approval later re-prompts": the approval
  layer lives wholly in our code between two prompts; nothing approval-shaped ever
  reaches the model. `session.steer` additionally injects mid-run text without breaking
  the turn (source-verified).
- **`approval.request`/`approval.respond`** exist but are a different layer: in-run
  tool-permission gates (dangerous shell commands etc.), emitted only when Hermes
  approvals are on. This machine runs `approvals.mode: off` (sessions report
  `yolo: true`), so they never fire. Our planner gate does not need them; if we ever
  want in-run tool gating for planner minds, it's a config switch in the planner home
  plus handling that event.
- Bonus boundary signal: with a child-per-run topology (below), the child's process exit
  is a second, OS-level end-of-run signal — belt and braces for System B.

---

## 3 · Decisions (proposed)

1. **Adopt Option 3 as specified — it is proven.** Stdio child on Hermes's own
   interpreter; JSON-RPC; structured events; no venv coupling; resume across restarts
   works. The in-process mount (old viewer: `from tui_gateway import server` inside its
   own process) and the WS daemon are both unnecessary for v2; WS remains a pure
   transport swap later (`tui_gateway/ws.py` drives the same `dispatch`).
2. **Role skills at kickoff via `HERMES_TUI_SKILLS` on the child env — adopt.** It's the
   same forced-system-prompt shape the kanban converged on, it's loud on failure, and
   it's re-applied on every resume. Accept the consequence: role → process, so the
   runtime spawns gateway children with role-specific env rather than multiplexing all
   roles through one child.
3. **Child-per-run topology (kanban-shaped), not one long-lived shared gateway.**
   System B spawns a child for a step: spawn → ready (~0.1s) → `session.resume` (or
   create) → `prompt.submit` → drain events → `message.complete` → close stdin → child
   exits. Rationale: per-run env gives us per-ticket context injection (the kanban
   pattern: `HERMES_KANBAN_TASK` et al.), role skills per run, no long-lived child to
   supervise, process-exit as a backup end signal, and prompt-cache survives across
   processes anyway (proven). ~1s overhead per step is noise against multi-minute steps;
   chat gets the same path (a chat message is just a producer into the session's queue).
   If chat latency ever matters, a kept-warm child per active session is a pure
   optimization on the same primitive.
4. **Role framing lives in the role skill; per-ticket specifics live in the next-step
   prompt (+ per-run env).** Don't build a per-session system-prompt mechanism that
   Hermes doesn't offer; don't use the personality overlay.
5. **Approval machinery stays entirely in our code** — the gateway's approval events are
   not our gate and stay off. The gate = System B observing `message.complete`, running
   the proposal-present check, stamping status, and later re-prompting. Nothing new
   needed from the gateway; this is exactly the decided design and the transport
   supports it.
6. **Dedicated `HERMES_HOME` for planner minds — recommended, needs owner sign-off.**
   Proven that a separate home fully isolates sessions/config/skills (and that it needs
   model config + credentials provisioned into it, or the build fails loudly). Keeps
   planner sessions out of the owner's real session list, lets us pin
   model/approvals/system_prompt/skills for planner agents without touching the personal
   config, and gives our v2 role skills a home we own. Fallback: default home + v2 skills
   installed under `~/.hermes/skills/` — works (probe 2 used the default home) but
   mingles state.

---

## 4 · The plan — the agent-operation primitive

One module in v2 (say `src/planner/minds/gateway.py` + `runner.py`), no Hermes imports:

**a) Process layer (`GatewayChild`).** Spawn
`<hermes>/venv/bin/python -m tui_gateway.entry` with pipes,
`HERMES_PYTHON_SRC_ROOT=<hermes root>`, cwd = a per-run workspace dir, plus the env below.
Reader thread turns stdout lines into frames (responses keyed by `id`; `method:"event"`
frames onto a per-run event stream); stderr tailed into our run log. Wait for
`gateway.ready` (timeout ~10s). Shutdown = close stdin, `wait()` with a short grace,
then kill. Discovery of the hermes root/interpreter: config value with the
`~/.hermes/hermes-agent/venv/bin/python` default, validated at boot by a cheap
launch-and-ready smoke check.

**b) Env assembly (the role parameterization).**
- `HERMES_TUI_SKILLS=planner-worker | planner-rollover | planner-main` — the role skill
  (v2-namespaced skills we author, installed in the active home's skills dir).
- `HERMES_HOME=<planner home>` (per Decision 6).
- Per-ticket context env for the CLI the skill tells the mind to use (e.g.
  `PLANNER_TICKET_ID`, `PLANNER_API_URL`) — mirroring kanban's `HERMES_KANBAN_*` pins.
- Optionally `HERMES_TUI_TOOLSETS=<subset>` to slim the toolset (env lever confirmed in
  source, server.py:2042; not live-tested).

**c) Run primitive (what System B calls).**
`run_step(session_key | None, role, prompt_text, on_event) → RunResult`:
1. spawn child with role env; await `gateway.ready`;
2. `session.create` (first step — persist returned `stored_session_id` on the
   ticket/global row) or `session.resume {session_id: stored_key}`;
3. `prompt.submit`; stream events (deltas → optional live chat UI; `tool.*` → run log);
4. on `message.complete`: capture `{text, status, usage}`; on `error` event or child
   death: `RunResult(errored)`;
5. close stdin; reap child.
Status mapping is System B's, exactly as decided: launch → `agent_working`; complete →
proposal-present check → `awaiting_approval` or `errored`; interrupted/error → `errored`
(or per policy). Kickoff is just step 0 through the same call.

**d) Serialization.** The per-session queue (already DECIDED) sits in front of
`run_step`: one in-flight run per `session_key`, chat messages and next-step prompts are
both producers. This is mandatory — the gateway's busy guard does not span our
child-per-run processes.

**e) Chat.** Per-ticket chat = enqueue the human's text as a `run_step` on the ticket's
session. Global chat = a singleton session with `role=planner-main`. Same primitive,
different session row. Streaming deltas can be relayed to the UI from `on_event`.

**f) Slash menu.** On demand (or cached at boot): spawn/borrow a child,
`commands.catalog`, cache `{pairs, categories, canon, sub}` for the UI. Invocation via
`slash.exec {session_id, command}` on the session's child during a run window, through
the same queue.

**g) Interrupt/steer (operator controls).** While a run is live its child is known:
`session.interrupt` (proven: yields `message.complete{status:interrupted}`, session
stays usable) and `session.steer` map directly to stop/nudge buttons if wanted.

**h) Hygiene + supervision.** Sessions never prompted leave no rows. Deleting a ticket
can `session.delete` its stored key (must not be live). A boot-time smoke check
(launch → ready → `commands.catalog` → exit) pins the private-surface risk: if a Hermes
update moves anything we depend on, v2 says so at startup, loudly, instead of failing
mid-run.

**Follow-up tickets that fall out:** (1) `GatewayChild` + run primitive + queue with a
fake-gateway test double (feed it canned frames) and one real-gateway smoke test;
(2) the three role skills (worker first — port the intent from v1's productivity skills
and the kanban worker-protocol lessons); (3) planner-home provisioning script (config +
creds + skills sync) if Decision 6 is accepted; (4) System B wiring onto `RunResult`.

---

## 5 · Open sub-questions

1. **Planner home provisioning (Decision 6).** What exactly must be copied/linked into a
   dedicated `HERMES_HOME` for a working build (model config, `.env`/auth, skills)?
   Settled by: a 30-minute provisioning probe — scaffold the home, copy the model block +
   creds, prove one round trip. (Blocked only on the owner accepting Decision 6.)
2. **Toolset slimming.** Does `HERMES_TUI_TOOLSETS` cleanly cut the 32k-token toolset
   baseline, and what's the minimal set a worker needs (terminal for our CLI + file)?
   Settled by: one live probe with the env set, checking `session.info.tools` + usage.
3. **`message.complete` uniqueness under edge cases.** Auto-compression mid-run rotates
   the session key (`_sync_session_key_after_compress`); goal-mode can chain turns. We
   don't use goal mode, and resume follows the rotation chain (source-verified), but the
   runner should treat "first `message.complete` after our submit" as the boundary and
   re-read the stored key after each run. Settled by: a long-session integration test
   once the runner exists.
4. **`complete.slash` returned empty** for a valid prefix — harmless (catalog covers
   enumeration), but worth one look if we want inline completion in chat.
5. **Version pinning.** The gateway surface is an internal API (68 methods in the old
   planner's notes, 85 now; core methods unchanged). The boot smoke check covers
   detection; do we also want to pin the hermes-agent version v2 was validated against
   and warn on drift? Settled by: owner preference.
