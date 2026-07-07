# Spike 03 — Slash display/config commands execute in ticket chat

**Goal (owner):** the ticket chat's `/` menu already loads **skills** into the
employee. Now **display / config / session-management commands** — `/status`,
`/model`, `/config`, and especially **`/compress`** — must *execute* too, not sit
inert. They were a first-slice scope cut (spike 02 §3 "Scope call"); the owner now
wants them built.

**Headline:** the backend already does the whole job. `run_command` +
`_interpret` in `core/adapters/real.py` call `slash.exec` first, handle its
`{output}` as a `kind:"system"` display line, fall through to `command.dispatch`
on rpc 4018, and the fake scripts the exact same shapes. The chat service, the
routes, the contracts, `ctx.runCommand`, and the `chat-sys` transcript row are all
in place and unit-tested (`test_command_display_path_is_system_kind` already
asserts `/status → kind:"system"`). **The entire gap is one frontend classifier
decision:** a non-skill `/cmd` is never handed to `ctx.runCommand` — the menu
*inserts* it and a typed send posts it as **plain chat** (an inert leading-slash
string the model just reads). Fix that one routing fork and display commands light
up. `/compress` needs one extra thought (a small backend feedback tweak + a note on
key rotation), covered in §3.

All gateway facts below were read live against the installed
`~/.hermes/hermes-agent/tui_gateway/` (read-only), 2026-07-07.

---

## 1 · What already works vs. what's blocked (full trace)

### Works end-to-end today (backend + transport + render — all built)

| Layer | Location | State |
|---|---|---|
| Route `POST /api/chat/{id}/command` | `chat/api.py:61-79` (human-only) | ✅ |
| Service `run_command` (+ key persist) | `chat/service.py:143-167` | ✅ |
| Adapter `run_command`: resume→`slash.exec`→(4018)`command.dispatch` | `core/adapters/real.py:197-238` | ✅ |
| `_interpret`: skill/send→`prompt.submit`→`"assistant"`; exec/plugin→`"system"`; alias→one hop | `core/adapters/real.py:240-266` | ✅ |
| Fake scripts skill→assistant, **everything else→`kind:"system"`** | `core/adapters/fakes.py` (`EchoGatewayAdapter.run_command`) | ✅ |
| `ctx.runCommand`: optimistic echo, POST, render `res.kind==="system" ? system : planner` | `assets/components.js:912-951` | ✅ |
| System transcript row (`chat-sys`, bare monospace) | `assets/components.js:844-848` | ✅ |
| `/` menu renders **all** categories + Skills | `assets/components.js:549-552` | ✅ |
| Unit: `/status → kind:"system"` | `tests/unit/test_chat_commands.py:test_command_display_path_is_system_kind` | ✅ |

So: if the frontend *called* `ctx.runCommand("/status")`, the backend already runs
it, returns `{reply_text:"…", kind:"system"}`, and the panel already draws it as a
system line. Nothing on the backend or transport is missing for display commands.

### Blocked: the frontend classifier never routes non-skill `/cmd` to `runCommand`

The composer (`makeTextInputSource`, `assets/components.js:418`) has **two** places
where a `/cmd` decides its fate, and **both** send only *skills* to `runCommand`:

**Drop point A — menu pick (`selectItem`, `assets/components.js:500-513`):**
```
if (rec.skill && canRun()) { … runSlash(rec.name); }   // skills execute
else { textarea.value = rec.name + " "; … }             // non-skill: INSERT only
```
Picking `/status` / `/model` / `/compress` from the menu just drops `"/status "`
into the textarea. It never runs.

**Drop point B — typed send (`skillCommandFor` → `routeSend`, `assets/components.js:584-618`):**
```
function skillCommandFor(text){ … if (!isSkill(name)) { return null; } … }  // only skills qualify
function routeSend(text){
  var skillCmd = skillCommandFor(text);
  if (skillCmd !== null) { ctx.runCommand(skillCmd); }   // skills → command
  else { ctx.submit(text); }                             // EVERYTHING ELSE → plain chat
}
```
Typing `/status` and pressing Enter falls to `ctx.submit("/status")` — a normal
chat send. The gateway's `prompt.submit` does **not** interpret a leading `/`
(spike 02 §3), so the model just sees the literal text `/status`. **This is the
"sent as plain chat / inert" the owner is seeing.**

`isSkill` (`assets/components.js:469`) is the sole gate; it returns true only for
names in `catalog.skills`. Categorized commands (Session/Info/Configuration/TUI/
Exit) are all treated as "not runnable → insert or plain-send."

**Conclusion:** the feature is ~90% built. The remaining work is a frontend-only
classifier change (plus a tiny backend feedback tweak for `/compress`, §3). No new
route, no new contract, no new adapter method.

---

## 2 · The precise change — make display commands execute + render as a system line

Route **every known non-skill catalog command** through `ctx.runCommand`, exactly
like skills, with three refinements. All edits are inside `makeTextInputSource`
(`assets/components.js`); `ctx.runCommand` and the transcript render need **no**
change (they already branch on `kind`).

**(a) One classifier helper.** Add `commandKind(name)` beside `isSkill`
(`assets/components.js:469`). Given a canonical `/name`, return one of:
- `"skill"` — name ∈ `catalog.skills`
- `"exit"` — name ∈ the **Exit** category (`cat.name === "Exit"`) — *not runnable*
- `"sub"` — name ∈ `catalog.sub` (takes an enumerated subcommand, e.g. `/model`, `/reasoning`)
- `"command"` — name ∈ any other category's pairs — *runnable display/config command*
- `null` — unknown (not in the catalog at all)

**(b) Menu pick (`selectItem`, `assets/components.js:500`).** Replace the
skill-only branch with:
- `skill` or `command` → `runSlash(rec.name)` (execute immediately — the owner's
  one-click gesture, matching how skills already fire; `/status`, `/config`,
  `/compress` run on pick).
- `sub` → keep today's **insert** `"/name "` so the user can add the subcommand,
  then Send executes it (`/model ` → type `sonnet` → Enter).
- `exit` → not shown at all (see (d)); never reached here.

**(c) Typed send (`routeSend` + `skillCommandFor`, `assets/components.js:584-618`).**
Generalize `skillCommandFor` into `commandFor(text)`: resolve the leading token via
`catalog.canon`, and if `commandKind` of the canonical name is `"skill"`,
`"command"`, or `"sub"`, return the canonical name **with the typed args preserved**
(the existing name-splice at `:592` already does this). Then:
```
var cmd = commandFor(text);
if (cmd !== null) { ctx.runCommand(cmd); } else { ctx.submit(text); }
```
`exit` and `null` (unknown) fall to `ctx.submit` — plain text, which is inert and
harmless (the model ignores a stray `/quit`; a typo like `/hlep` is not a command
and correctly stays chat rather than erroring in the worker).

**(d) Hide the Exit category from the menu (`renderMenu`, `assets/components.js:549`).**
Skip `addSection` when `cat.name === "Exit"`. A web chat can't meaningfully "quit"
the mind, and running `/quit`/`/exit` in the slash worker is a no-op-or-worse; it
stays out of reach. (One string compare; catalog category names are stable —
spike 02 §0.)

**Net diff:** one new helper, two rewritten call sites, one `renderMenu` guard.
No backend, contract, route, or `ctx` change for the core path.

**Rendering (already done, no work):** a display command returns `kind:"system"`,
`ctx.runCommand` pushes `{who:"system"}`, and `paint()` draws a `chat-sys`
monospace line (`assets/components.js:844-848`). A subcommand/config that triggers a
model turn (e.g. a `command.dispatch` `send` type) returns `kind:"assistant"` and
draws a normal reply bubble. The `kind` split already routes both correctly.

---

## 3 · `/compress` specifically

`/compress` is a real **Session** command → it appears in the menu and, after §2,
runs on pick. Its gateway path is *not* `command.dispatch`; it goes through
`slash.exec` with a live-agent side-effect, and it **rotates the session key**.

**Gateway path (read live):** our `run_command` calls
`slash.exec {session_id, command:"/compress"}` (`real.py:215`). In the gateway
(`server.py` `@method("slash.exec")` at :10061):
- `/compress` is **not** in `_PENDING_INPUT_COMMANDS` (`server.py:8775` —
  retry/queue/q/steer/plan/goal/undo), **not** in `_WORKER_BLOCKED_COMMANDS`
  (`:8787` — snapshot/snap), **not** a skill, **not** a plugin. So it runs in the
  per-session `slash_worker` and then `_mirror_slash_side_effects` (`:9967`)
  special-cases `name == "compress"` (`:9999`): it compresses the **live** agent
  history (`_compress_session_history`, `:2463`) and calls
  `_sync_session_key_after_compress` (`:2516`), which **rotates
  `session["session_key"]` to the continuation session id** and restarts the
  slash worker.
- `slash.exec` returns `{output: <worker output>, warning: <before/after summary>}`
  (`server.py:10146-10151`). The meaningful "compressed N→M messages / ~X→~Y
  tokens" feedback is in **`warning`**, not `output`.

**Backend tweak (small, needed for good feedback).** Our non-typed `slash.exec`
branch uses `result.get("output")` and **ignores `warning`**
(`real.py:227-232`). For `/compress` (and `/model x`, whose confirmation is also a
`warning`) the display line would be empty/"(no output)". Fix: in `run_command`,
when the result has no `type`, combine output + warning:
```
output  = str(result.get("output") or "")
warning = str(result.get("warning") or "")
reply   = (output + "\n" + warning).strip() if warning else output
```
Return that as `kind:"system"`. One-liner; the fake gets a scripted `warning` for a
`/compress`-style command to test it.

**Session-key rotation — the `_persist_key` interaction (owner's call-out #3).**
`/compress` rotates the gateway's session key, but `slash.exec` **does not return
the new key** — so our `run_command` returns the pre-call `stored` key
(`real.py:212, 231`), and the rotation is *not* persisted on the compress call
itself. This is **already handled** by the existing machinery, and needs **no new
key code**, because it **self-heals on the next message**:

1. `/compress` runs in a per-request child; the continuation session is written to
   SessionDB and the parent→child chain is recorded. Child dies; the in-memory
   rotation is gone but the DB chain persists.
2. The **next** chat message spawns a fresh child and resumes the *stored* (old)
   key. The gateway's `session.resume` follows the compression-continuation chain:
   `resolve_resume_session_id(target)` (`server.py:4601-4619`) resolves the old id
   to the **live tip** and returns it as `resumed` (`:4633`, `target` already
   reassigned to the tip).
3. `_resume_or_create` (`real.py:115`) therefore returns a `stored` that **differs**
   from the key we passed in → `service.run_command` sees `result.session_key !=
   stored_key` → `_persist_key`'s **re-mint branch** (`service.py:102-111`, built
   for exactly "the adapter created a fresh session because the stored key was stale
   … or rotated") updates the row and logs one `chat_session_created`. Self-healed.

So the answer to "does `_persist_key` handle the key change?" is **yes — the
re-mint branch already does**, one message later. The stored key is stale only in
the window between `/compress` and the next message, and `session.resume` tolerates
that (it follows the chain). **Recommendation: do nothing extra here.** Persisting
the rotated key *immediately* would require the gateway to surface it from
`slash.exec` (it doesn't) or an extra `session.info`/re-resume round-trip — not
worth it for a one-message delay that is invisible to the user.

**Real-gateway-only caveat (flag, don't test hermetically):** the self-heal relies
on the continuation being flushed to SessionDB before the per-request child exits —
core hermes auto-compression behavior (the TUI's own "came back and the reply is
there" fix, `server.py:4601-4607`). We cannot exercise a real `/compress` in
`./verify` (no real gateway — project invariant). It is asserted at the **service**
layer with a fake that returns a rotated key (§5), and verified against the real
gateway only in a one-off manual smoke, noted in decisions.md.

---

## 4 · Risks

1. **Heavier `slash_worker` + latency.** A non-skill display command spawns a
   gateway child *and*, on first use per session, a `slash_worker` subprocess (a
   full `HermesCLI` resumed on the key) inside that child. Because our chat adapter
   is **child-per-request** (`real.py:209`), the worker is cold-started on every
   command — no reuse. Budget: `wait_ready` ~0.1s + worker start + command. It is
   noticeably heavier than a plain `send`. Mitigation: the composer already shows a
   pending state (`chatPending` → thinking dots), so it reads as "working," not
   "hung." Acceptable for a human-initiated, occasional action. *(Note: the catalog
   memoization warns explicitly against a child-per-keystroke; this is
   per-*command*, not per-keystroke, so it's fine.)*

2. **Chat is not behind the per-session queue (pre-existing — flag, do not solve
   here).** The chat adapter spawns a child per request and does **not** sit behind
   `minds/queue.py`; two concurrent human sends/commands on one ticket resume the
   same key in two processes — the cross-process concurrency the gateway's
   in-process busy-guard does not cover (spike 01 Q5 / spike 02 §5). This spike does
   not change that surface; it only adds more human-initiated calls through the same
   unqueued path. **A separate agent is on moving chat behind the queue** — leave it
   to them; note the dependency in decisions.md so the two land coherently.

3. **Session-rotating commands beyond `/compress`.** `/clear`, `/new`, and friends
   also rotate/reset the session; all ride the same self-heal (§3). No special
   casing — the re-mint branch is generic. Worth a line in DOCS.md so the behavior
   ("chat key updates one message after a compress/clear") is documented, not
   surprising.

4. **Exit / destructive commands.** Hidden from the menu and not routed to
   `runCommand` (§2d); a typed `/quit` degrades to inert plain text. This is the one
   place we deliberately *don't* mirror the gateway catalog 1:1.

---

## 5 · Slice plan (smallest shippable first) + hermetic tests

**Slice 1 — the core (frontend-only, no backend change).** The `commandKind`
classifier + the two rewired call sites + hide-Exit (§2 a–d). Delivers `/status`,
`/model`, `/config`, `/compress`, and session-management commands executing on both
menu-pick and typed-send, rendered as system lines. Ships alone; zero backend risk.

- *Unit:* already green — `test_command_display_path_is_system_kind`
  (`/status → system`). No new backend behavior to cover in slice 1.
- *e2e (Playwright, fake gateway — no real gateway, ever):* add
  `test_slash_menu_runs_display_command`, mirroring `test_slash_menu_runs_skill`
  (`tests/e2e/test_flows_a.py:288`). Warm up a send (mints the key / flush), type
  `/status`, and (i) **pick** it from the menu, then (ii) in a second assertion,
  **type-and-Send** `/status`. Assert a `[data-chat-msg="system"]` row appears
  containing the fake's `exec: /status`. The fake already returns `kind:"system"`
  for every non-skill command (`fakes.py`), so **no fake change** is needed for the
  core. Add a small assertion that the **Exit** category rows are absent from the
  menu — this needs one line added to `CANNED_CATALOG`: an `Exit` category, e.g.
  `CommandCategory("Exit", (("/quit","Exit the session"),))`. (The full-shape test
  `test_commands_endpoint_returns_catalog_shape` compares against `CANNED_CATALOG`
  itself, so it stays green; the skills/`cat_names` assertions are unaffected.)

**Slice 2 — `/compress` feedback fidelity (backend one-liner).** Surface
`slash.exec` `warning` alongside `output` in `run_command` (§3). 

- *Unit:* extend `EchoGatewayAdapter.run_command` to return a `warning` for a
  scripted command (e.g. treat `/compress` as producing
  `output="(no output)", warning="compressed 40 → 8 messages"`), and assert the
  service returns the combined text as `kind:"system"`. Pure fake; no gateway.
- *Unit (rotation self-heal):* add a service-layer test modeled on
  `test_command_lost_race_adopts_winner_key_no_event`
  (`tests/unit/test_chat_commands.py`): a fake whose `run_command` returns a
  **different** `session_key` than the stored one (simulating the next-message
  chain resolution after a compress) → assert `_persist_key`'s re-mint branch
  updates the row **and** logs exactly one `chat_session_created`. This is the
  hermetic proof of the owner's #3 concern, with no real gateway.

**Slice 3 — (optional, likely unneeded) immediate rotated-key persist.** Only if
the one-message-delayed self-heal ever proves user-visible. Would require an extra
`session.info` read after a known session-rotating command. Documented follow-up;
do not build speculatively.

**Out of scope / dependencies:** moving chat behind the per-session queue (risk 2)
is a separate agent's ticket; `/compress` against a real gateway is a manual smoke,
not part of `./verify`.

---

## 6 · Files touched (for ticketing)

- `assets/components.js` — `makeTextInputSource`: add `commandKind`; rewrite
  `selectItem` (`:500`), `skillCommandFor`→`commandFor` + `routeSend`
  (`:584-618`); guard `renderMenu` to hide Exit (`:549`). *(Slice 1)*
- `src/planner/core/adapters/real.py` — `run_command`: combine `output` + `warning`
  in the non-typed branch (`:227-232`). *(Slice 2)*
- `src/planner/core/adapters/fakes.py` — add an `Exit` category to `CANNED_CATALOG`;
  script a `warning` for a `/compress`-style command in
  `EchoGatewayAdapter.run_command`. *(Slices 1–2)*
- `tests/e2e/test_flows_a.py` — `test_slash_menu_runs_display_command`. *(Slice 1)*
- `tests/unit/test_chat_commands.py` — warning-surfacing test + rotation self-heal
  test. *(Slice 2)*
- `DOCS.md` / `decisions.md` — document the display-command execution, the
  one-message-delayed key update after compress, and the queue dependency.
