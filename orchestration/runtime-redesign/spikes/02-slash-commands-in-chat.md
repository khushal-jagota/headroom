# Spike 02 — Slash-commands + skills in ticket chat

**Goal (owner):** "get slash commands in a nice way without having to hand roll
them, to get skills in." Surface the Hermes gateway's *own* command catalog —
commands **and** skills — inside our ticket chat, driven by the gateway, never
hand-maintained.

**Headline:** The gateway already gives us everything. `commands.catalog` returns
the full registry (135 commands + 62 skill-commands) in one stateless call — no
session needed. The "/" menu is a pure read of that catalog. *Running* a command
is the part with real structure: slash semantics are **entirely client-side** —
`prompt.submit` does **not** interpret a leading `/`. To actually get a skill into
a ticket's mind you call `command.dispatch`, which hands back an invocation
*message*, and you `prompt.submit` that message on **the ticket's own session**.
That is the runtime complement to the kickoff `HERMES_TUI_SKILLS` env from spike 01.

Everything below was re-confirmed live on 2026-07-07 against the installed
hermes-agent, reusing our own tested `GatewayChild` (src/planner/minds/gateway.py).
The catalog call creates **no** session, so nothing was left to clean up.

---

## 0 · Live re-confirmation (throwaway probe, cleaned up)

Spawned one gateway child via `resolve_hermes_python()` + `GatewayChild`, called
`commands.catalog` with no session, closed stdin. Real output:

```
pairs total     : 135
skill_count     : 62
canon aliases   : 87
sub (subcmds)   : 20 commands have subcommands
warning         : ''
categories      : [('Session',24),('Info',14),('Configuration',15),
                   ('Tools & Skills',15),('Exit',1),('TUI',4)]        # = 73
sum(cat pairs)  : 73     (pairs - skills = 135 - 62 = 73)
last 62 pairs   : /workspace-productivity-automation, /writing-plans,
                  /xitter, /xurl, /yuanbao, …   (the skill-commands)
sub sample      : /reasoning: [none,minimal,low,medium,high,xhigh,show,hide,…]
```

Matches spike 01 exactly. **Load-bearing shape fact:** skill-commands are
appended to the flat `pairs` list but are **absent from `categories`** — the
categorized pairs sum to exactly `len(pairs) - skill_count`. So the UI's "Skills"
group is precisely `pairs[-skill_count:]`; no extra call needed. (Source:
server.py:8859-8871 appends skills to `all_pairs` only, after `categories` is
built.)

---

## 1 · Catalog → UI

**Shape of `commands.catalog`** (server.py:8790, stateless — no `session_id`):

| key | contents | UI use |
|---|---|---|
| `pairs` | `[[ "/name", "desc" ], …]` — 73 categorized + 62 skills (last) | flat search index |
| `categories` | `[{name, pairs:[[name,desc],…]}, …]` — 6 groups, **skills excluded** | grouped menu |
| `canon` | `{ "/alias": "/canonical", … }` (87) | resolve alias client-side |
| `sub` | `{ "/cmd": [subcmd,…], … }` (20 cmds) | subcommand hints |
| `skill_count` | `62` | slice `pairs[-62:]` = Skills group |
| `warning` | `""` (non-empty if skill/quick-cmd discovery failed) | log, don't show |

**Expose it as one gateway-wide endpoint, cached.** The catalog is identical for
every session and only changes when skills/commands change on disk — so it does
**not** belong on a per-entity route and must **not** spawn a child per request.

- New adapter method `GatewayAdapter.catalog() -> CommandCatalog` (a frozen
  dataclass: `categories`, `skills`, `canon`, `sub`). `RealGatewayAdapter.catalog`
  spawns a throwaway `GatewayChild` (same env as `send`, no `HERMES_HOME`),
  `wait_ready` → `commands.catalog` → `shutdown`. It synthesizes the `skills`
  list from `pairs[-skill_count:]` and drops the raw `pairs`.
- New route `GET /api/chat/commands` → `service.catalog(...)`, human-only
  (`authctx.reject_agents`, like send).
- **Caching lifecycle:** memoize in `app.state` with a short TTL (≈5 min) — cheap
  insurance against per-keystroke spawns while still picking up a skills change
  within minutes; a `?refresh=1` busts it. Optionally warm it once at boot next to
  the existing `boot_smoke_check` (spawn already happens there). Rationale: ready
  is ~0.1s and the call ~30ms, but a *process* per request is wasteful and the
  data is near-static. The `FakeGatewayAdapter` returns a canned catalog for
  tests — no child in the test path.

**Home-scoping note.** The scan reads the active home's `~/.hermes/skills/`
(server.py:8859 → `scan_skill_commands`). Today we run the owner's default home,
so the menu shows the owner's 62 skills. When the dedicated planner home (spike 01
Decision 6) lands, the same endpoint auto-reflects *that* home's skills — the menu
is correct by construction, never hand-listed.

---

## 2 · The "/" affordance (composer UX)

Keep it minimal and additive to the existing `makeTextInputSource`
(components.js:385) — no new registered chat source, no clutter when unused.

- **Trigger:** the menu appears only while the composer text starts with `/`.
  Otherwise the composer is byte-for-byte today's behavior.
- **Data:** fetch `/api/chat/commands` **once** on first `/` (or panel open),
  cache on the `Planner` namespace. One fetch per page load, not per keystroke.
- **Render:** a popover anchored above the textarea. Sections in catalog order
  (Session, Info, Configuration, Tools & Skills, Exit, TUI) then a **Skills**
  section (the `skills` list). Each row = `/name` + dimmed description. As the
  user types past the `/`, filter by prefix/substring over name+desc across all
  sections (skills included); resolve typed aliases via `canon`.
- **Keys:** ↑/↓ move, Enter selects, Esc dismisses. Mouse click selects.
- **On select:** insert the canonical `/name ` into the composer (trailing space)
  and keep focus, so the user can add args (or a subcommand from `sub`) and press
  Send — one code path, no surprise auto-execution. For a **skill** with no args,
  selecting can Send immediately (skills rarely take args); that is the headline
  one-click "load this skill" gesture. (Owner-taste call; default to insert-only
  and let Enter-on-empty-arg skills send.)
- The menu is a *view* of the catalog; it encodes zero command knowledge itself.

---

## 3 · Running a command (the real structure)

**Key finding — slash routing is 100% client-side.** `prompt.submit`
(server.py:6318) submits text verbatim to the model; a leading `/` is not special.
So sending `/writing-plans` as a normal chat message is inert text — it does **not**
trigger skill loading. The gateway exposes two execution methods, and the TUI (and
therefore we) is the router:

1. **`slash.exec {session_id, command}`** (server.py:10061) — display/info & config
   commands (`/status`, `/model`, `/tools`, `/config …`). Runs in a per-session
   `slash_worker.py` subprocess (a full `HermesCLI` resumed on the session key),
   captures printed output, returns `{output}`. It **refuses skill-commands and
   pending-input commands**: for a skill key it returns error `4018 "skill command:
   use command.dispatch for /x"` (server.py:10102-10111); for `retry/queue/q/steer/
   plan/goal/undo` it internally forwards to `command.dispatch`.

2. **`command.dispatch {session_id, name, arg}`** (server.py:8965) — skills, quick-
   commands, plugins, and the queue/retry/steer/goal/undo family. Returns a **typed
   payload**, and the type dictates what the client does next:

   | `type` | payload | client action |
   |---|---|---|
   | `skill` | `{message, name}` | **`prompt.submit(message)`** → drain to `message.complete` |
   | `send` | `{message, notice?}` | show `notice` as a sys line, **`prompt.submit(message)`** |
   | `exec` | `{output}` | display only — no model turn |
   | `plugin` | `{output}` | display only |
   | `alias` | `{target}` | resolve → re-dispatch the target (one hop) |

**Yes, the same session as the chat — the ticket's mind.** Every path takes
`session_id` and must run on the entity's `chat_session_key`. A skill loaded via a
ticket's chat must land in *that ticket's* conversation, not a fresh mind.

**Recommended flow through our stack** (mirrors the TUI faithfully, no guessing):

`POST /api/chat/{entity_id}/command` `{command:"/name args"}`
→ `service.run_command` → `RealGatewayAdapter.run_command(session_key, entity_id, command)`:

1. spawn child, `session.resume` (or `session.create` first time — persist
   `chat_session_key` + log `chat_session_created`, identical to `send`);
2. `slash.exec {session_id, command}`;
3. on RPC error `4018` (skill/pending-input) → `command.dispatch {session_id, name,
   arg}`, then interpret by `type` per the table (skill/send → `prompt.submit` the
   `message`, drain to the single `message.complete`, return the model reply);
4. otherwise `slash.exec` succeeded → return its `output` as a display line.

Return `{reply_text, session_key, kind}` where `kind ∈ {assistant, system}`
distinguishes a model turn (skill/send) from display output (exec/plugin/status).
This flows through the existing child-per-run adapter shape (spawn → resume →
submit → drain → close) — the same primitive `send` already uses.

**Scope call for the first slice:** skills are the owner's headline. Wire the
`command.dispatch → prompt.submit` (skill/send) path first; `slash.exec` display
commands (status/model/config) are hermes-session introspection of low value to
planner chat and stand up the heavier `slash_worker` — defer them. Because the
catalog cleanly separates skills (`pairs[-skill_count:]`) from categorized
commands, the composer can classify client-side and only enable the skills path in
slice 1, treating other picks as insert-into-composer.

---

## 4 · Skills

- **Where they come from:** `scan_skill_commands()` (agent/skill_commands.py:348)
  walks the active home's `~/.hermes/skills/` (+ `skills.external_dirs`), reads each
  `SKILL.md` frontmatter, and maps `name → /command`. The catalog appends these 62
  to `pairs` (last 62) — that is the entire "skills in the menu" mechanism, and it
  auto-tracks skills added/removed on disk (`skills.reload` rescans).
- **What selecting one does:** `command.dispatch` → `build_skill_invocation_message`
  (agent/skill_commands.py:517) loads the **full SKILL.md body** and wraps it in an
  activation note — *"[IMPORTANT: The user has invoked the "X" skill … follow its
  instructions. The full skill content is loaded below.]"* — and returns it as the
  `message`. The client `prompt.submit`s that message, so the skill's content enters
  the ticket's conversation as a **user turn** and the mind acts on it that turn.
- **Runtime skill-load vs kickoff skill (ties to spike 01):** two delivery routes
  for the *same* skill files —
  - **Kickoff (`HERMES_TUI_SKILLS`)**: forced into the agent's *ephemeral system
    prompt* at build; unskippable, session-wide, re-applied on every resume,
    invisible in `session.info.system_prompt`. This is how a **role** skill is
    pinned to a mind (spike 01 Decision 2).
    A skill-command is the **opt-in, mid-session, in-conversation** complement:
    the user pulls an *extra* skill into a live ticket mind as a one-time user
    message. Kickoff = the role the mind always is; slash-skill = a tool the human
    hands it now. They compose cleanly and don't conflict.
- Unknown/again is safe: `get_skill_commands()` rescans on platform change; a
  missing skill just isn't in the catalog.

---

## 5 · Minimal backend + frontend changes

**Backend**
- `chat/contracts.py`: add `CommandCatalog` (categories, skills, canon, sub) and a
  `CommandRunResult(reply_text, session_key, kind)` dataclass.
- `core/adapters/base.py`: extend `GatewayAdapter` with `catalog()` and
  `run_command(session_key, entity_id, command)`.
- `core/adapters/real.py`: implement both on `RealGatewayAdapter`, reusing the
  existing `GatewayChild` spawn/env. `catalog()` is stateless (no session);
  `run_command` reuses the resume/create + drain shape from `send`.
- `core/adapters/fake.py`: canned catalog + scriptable `run_command` (no child) so
  `./verify` never spawns a gateway.
- `chat/service.py`: `catalog(gateway)` (pass-through) and `run_command(conn,
  gateway, entity_id, command, now)` — same first-reply key-persist + one
  `chat_session_created` event as `send`.
- `chat/api.py`: `GET /api/chat/commands` and `POST /api/chat/{entity_id}/command`,
  both `authctx.reject_agents`.

**Frontend**
- `components.js`: enhance `makeTextInputSource` — the "/" popover (fetch-once
  catalog, grouped menu with Skills, filter, keyboard nav, select→insert). New
  `ctx.runCommand(command)` alongside `ctx.submit`; `chatPanel` wires it to
  `POST /api/chat/{id}/command` and appends the result to `chatTranscripts`
  (assistant vs system line by `kind`).
- CSS: one popover block + a section-header + skill-row style. No new screen.

**Respected invariants:** one mind per ticket (same `chat_session_key`
everywhere); "model does next step only" (a slash-skill is just the next-step
prompt; nothing command-shaped bypasses the mind); chat stays human-only; the
event feed remains the invalidation signal (no client state store — the menu is a
transient view of a fetched catalog).

**Known pre-existing risk (not introduced here):** the chat adapter spawns a child
per request and does not sit behind the per-session queue (minds/queue.py). Two
concurrent human sends/commands on one ticket would resume the same key in two
processes — the cross-process concurrency the gateway's in-process busy-guard does
**not** cover (spike 01 Q5). Fixing chat to go through the per-session queue is the
right follow-up; it is orthogonal to this feature.

---

## 6 · Smallest first slice to ship

1. **Catalog read-through.** `adapter.catalog()` + `GET /api/chat/commands`
   (cached, human-only) + `FakeGatewayAdapter.catalog`. Pure read; ships alone.
2. **The "/" menu.** Composer popover driven by that endpoint — grouped, Skills
   included, filter + keyboard nav, select inserts `/name ` into the composer.
   Immediately delivers "slash commands, driven by the gateway, not hand-rolled,"
   with zero execution risk.
3. **Skill execution.** `POST /api/chat/{id}/command` → `command.dispatch` →
   `prompt.submit(message)` on the ticket session, for the **Skills** group only.
   This is the owner's headline ("get skills in") and reuses the proven send path.

`slash.exec` display commands, one-click skill Send, inline `complete.slash`
completion, and moving chat behind the per-session queue are all follow-ups.

---

## 7 · Open sub-questions

1. **First-reply latency.** Loading a full SKILL.md as a user turn then a real
   model round trip is a multi-second turn (spike 01: 2.6–8.4s) — fine, but the
   composer should show a pending state (chat already does per `send`).
2. **`command.dispatch` fall-through** for a genuinely unknown name — the
   recommended flow never relies on it (catalog-driven classification +
   `slash.exec`-first), so this is only relevant if we later route arbitrary typed
   commands. Worth one probe if slice-3 scope widens.
3. **Alias re-dispatch depth.** Cap at one hop (`type:"alias"` → resolve `target`
   once); the catalog `canon` map already resolves most aliases client-side before
   we ever call the gateway.
4. **Home switch.** When the dedicated planner home lands, confirm the catalog TTL
   picks up the new skills set (it will — the scan is home-scoped) and drop the
   owner's-home skills from the menu.
