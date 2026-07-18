# S2b Chief pane — Codex plan review (round 1)

Reviewer: Codex `gpt-5.6-sol`, reasoning effort high, read-only sandbox. Target:
`implementation-plan.md` against the binding contract, the shipped S2a surface, and the
codebase. Below: each finding, my verification, and disposition (ACCEPT = fix in the plan
revision; ESCALATE = needs main's ruling; REFUTE = not a defect).

## Blocking findings (verified against code)

**F1 — Command UI exceeds the skills-only contract; `ChatComposer` reuse is incompatible.**
ACCEPT. Contract line 52-54 authorizes "the picker rendered from the catalog result ...
skills inserting their trigger text into the composer" — it does NOT make commands runnable.
The plan §4.4's "picking a command inserts/sends its trigger" exceeds that. Also verified:
`ChatComposer.svelte` auto-sends skills/commands (`choose`→`send`, line 268-276), has a
disabling `busy`/`submitDisabled`/`disabled` state (line 375, 422) that violates
`D-native-turn-concurrency` (composer never disabled). Fix: the neutral pane needs a
DEDICATED composer (not `ChatComposer` reuse); render SKILLS only in the picker; a skill
pick INSERTS its trigger into the draft (no auto-send); never a busy/disabled state.

**F2 — The flag-on ownership guard misses several crossover paths.** ACCEPT (partial
ESCALATE via Collision #1). Verified: `EntityRoutingGateway._gateway_for` falls through to
the worker (default) gateway (`shared_gateway.py:77`). The plan §1.4 guards only the two
send endpoints, but Chief turns are ALSO touched by startup recovery
(`_recover_running_human_chat_turns`, server.py:139, runs at line 231 BEFORE relay
composition and includes Chief entities), and by continue/pause/clarification paths in
`chat/service.py` that capture the gateway independently. Fix: ONE central flag-on Chief
guard in `chat/service.py` covering every gateway-touching lifecycle op, plus explicitly
skip/settle any stale running-Chief startup recovery before gateway capture. Tests for each
bypass + a stale-running-Chief boot. (This widens the single `chat/` guard the plan
proposed — still "minimal" in spirit but broader than one endpoint check; belongs to
Collision #1's ruling. See escalation.)

**F3 — The startup-assertion test can pass without `_lifespan` actually invoking it.**
ACCEPT. Plan §7.3 only evaluates three booleans + a hand-built bad triple; it never proves
`_lifespan` calls the assertion. Fix: extract `_assert_single_chief_owner(...)`, call THAT
exact helper from `_lifespan`, and test both the helper and a lifespan boot with an
inconsistent composition. (Collision #2's "assert on composition inputs" stance stands — no
`minds/` change needed — but the assertion must be genuinely wired into boot.)

**F4 — New-conversation creates a SECOND live session; the pane reopens OLD history.**
ACCEPT (correctness bug). Verified: S2a's `_sole_session_id` takes `sessions[0]`
(neutral_downstream_session.py:295), and Hermes `active_list` preserves creation/insertion
order (hermes server.py:6084). The plan §3.2 does `session.create` WITHOUT closing the old
session, then re-bootstraps via `active_list` — which returns `[old, new]`, so the pane
bootstraps the OLD session and reopens old history. Fix: the pool's rebind must make the NEW
session the one the pane bootstraps — either `session.close` the old live session before
`session.create`, or have the pool RETURN the new live+stored ids and have the session use
the returned live id for `session.history` directly (not an ambiguous `active_list`).
Test: exactly one live session remains after rebind; the pane gets EMPTY history.

**F5 — Fresh Chief bindings are never persisted → restart forks the session.** ACCEPT.
Verified: nothing under `hermes_backend/` writes `agent_chat_sessions.chat_session_key`; the
initial pool `session.create` (employee_child_pool.py:253) and any `/new` rebind are
memory-only; the tee writes transcript rows only. So after first use or `/new`, a restart
adopts the STALE key or creates yet another session. Fix: a composition-owned persistence
callback that upserts the Chief's stored-session binding into `agent_chat_sessions` after the
first create AND every rebind, before success is reported. Test: recreate-pool/restart
resumes the LATEST key. (This is additive persistence, but note it writes a `chat/`-owned
table — see escalation: is this a `chat/`-authorized change or does it need main's ruling on
where the write lives? The read already lives in composition; the write is symmetric.)

**F6 — The new-conversation branch blocks the asyncio loop.** ACCEPT. Verified:
`_transport_request` blocks on a `threading.Event` up to the request timeout
(employee_child_pool.py:315-354); the plan §3.3 calls synchronous `rebind_fresh_session`
directly from `handle_neutral_request`, which runs in the WS receive task
(relay_neutral_route.py:89-94). That would block the event loop. Fix: run the rebind through
the pool's executor (`run_in_executor`), await it. Test: a held rebind RPC keeps the
writer/event loop responsive.

**F7 — Image reference is an HTTP managed reference, NOT a child-openable path (Collision #3
is a BLOCKER, not a note).** ESCALATE — this is an S2a translator gap. Verified: the upload
endpoint returns `/files/chats/<entity>/<path>` (files/api.py:112-116); the legacy path
resolves that to an absolute filesystem `Path` via `_resolve_turn_image`
(chat/service.py:580+) BEFORE the gateway; S2a's translator forwards `image_ref` verbatim as
`image.attach {path}` (hermes_frame_translation.py:223). So images cannot work through S2a as
shipped. This is inside S2a's contract ("images: the existing upload flow produces
references carried on send"). S2b must NOT silently patch it in `chat/` or the translator.
Options for main: (a) fix S2a's translator to resolve the managed reference to a filesystem
path before `image.attach` (an S2a change or follow-up ticket); (b) add server-side
managed-reference resolution in the neutral route before translation (widens S2b);
(c) descope the images e2e for S2b until S2a resolves it (honest, but the contract names
images as a re-anchored scenario). Recommend (a) as a small S2a fix. The scripted child must
VALIDATE the resolved path — an ACK-anything fake would let the images scenario pass falsely.

**F8 — Live sends never render the user's own message.** ACCEPT. Verified: S2a emits no
user-message event; its frame→event mappings are assistant-lifecycle only
(hermes_frame_translation.py). The plan §4.2 models history + assistant in-flight state but
never appends the outgoing human line, while §7.1 asserts the user line renders. Fix: the
client appends an OPTIMISTIC human entry (with image presentation) on send; the authoritative
`HistorySnapshotEvent` on the next attach/reattach replaces it. Unit-test both.

## Fold-law / pane completeness

**F9 — The fold law and unit coverage are incomplete.** ACCEPT. Plan §4.2/§4.5 omit reducer
behavior + assertions for `agent_question` and `tool_approval_request`, and `history_snapshot`
replacing only the settled transcript leaves stale text/thinking/tools/questions/approvals
after recovery. Fix: an EXHAUSTIVE 13-kind reducer; `history_snapshot` clears ALL ephemeral
turn state; completion/failure/reset and answering/responding clear their pending state; test
an idle `turn_failed` too (compact's 4009 arrives with no open turn).

**F10 — The picker never requests its catalog.** ACCEPT. S2a does NO attach-time catalog
fetch (neutral_downstream_session.py:104 comment). The plan renders `catalog_result` but
attach sends only `attach_to_employee`. Fix: request the catalog on picker OPEN (`listCatalog`);
unit + e2e assert that exact request and the resulting skill list.

**F11 — Capability defaults to the WRONG pane before meta resolves.** ACCEPT. `/api/meta` is
fetched in `onMount` (App.svelte:93), AFTER child routes render; a `false` default transiently
mounts legacy `ChatPanel` on a flag-on boot, and a meta failure leaves it there. Fix: tri-state
`unknown | enabled | disabled`; render NEITHER chat path while `unknown`; expose neutral
readiness only after initial history; flag-on Playwright waits for that marker.

## Scripted-child / e2e realism

**F12 — The scripted child must be a STATEFUL session model, not static RPC replies.** ACCEPT.
history/refresh/new-conversation/reset need stored+live ids and per-session messages that
survive respawn; the fixture must seed a known Chief key (the harness DB starts empty unless
`seed_db` is used, conftest.py:68-96). Fix: seed the Chief key via `seed_db`; the fake keeps a
shared session store across children; history updates on completed prompts; `create` yields
empty, `active_list` is accurate.

**F13 — The scripted timing cannot prove interrupt / mid-turn send / thinking / incremental
render.** ACCEPT. `RawFrameChildTransport` has ONE serial writer calling `ChildProcess.send`
(raw_frame_transport.py:157); blocking there deadlocks `clarify.respond`, and synchronous
full-stream completion leaves no running turn to interrupt/steer, and a "partial prefix"
assertion can pass AFTER the full string arrives. Fix: a NON-blocking fake state machine with
held turns released on cue (clarify completes later; interrupt yields an interrupted
completion; a reset cue); prove intermediate text/thinking with a pre-installed
`MutationObserver` or an explicit release gate.

**F14 — The compact e2e expects an event S2a never emits.** ACCEPT. S2a consumes a successful
compact ACK WITHOUT emitting anything (neutral_downstream_session.py:242-247). Fix: assert the
OUTGOING compact request + the ABSENCE of a failure event, not an invented confirmation. The
4009-surfaces-as-failure variant stays (that DOES emit `TurnFailed`).

## Discipline / scope

**F15 — RED-first ordering + allowlist nits.** ACCEPT. UI is built (step 6) before its
Playwright tests (step 7) — move mount/send capability tests ahead of UI. The adoption test's
in-memory DB is invisible to composition's separate connection (db.py:204) — use a migrated
temp FILE db. The allowlist's `chat/api.py OR chat/service.py` must be resolved to ONE —
choose `service.py` (F2 puts the central guard there).

**F16 — Two additions exceed the contract.** ACCEPT (both). (a) The extra
`relay_test_scripted_child` config/env flag is unnecessary — `test_mode && relay_backend_enabled`
already uniquely selects fake test composition (no production path composes the relay in test
mode). Drop the flag; gate the scripted spawn on `test_mode`. (b) The defensive wrong-employee
event filter in the client duplicates the relay's per-employee routing invariant and can MASK
a server defect. Remove it. (Both are "everything earns its existence" violations.)

## Confirmed sound by the reviewer
Adoption by seeding `_stored_session_id_by_employee` correctly selects the resume branch;
pool-direct lifecycle RPCs preserve the denylist; optional `pool_provider` injection is
additive; the no-resource reactivity boundary holds (completeness test stays green); the
request wire names match S2a; flag-off regression coverage and the no-skip rule are sound.

## Verdict
Not yet sound to implement. Material fixes required. Three items implicate cross-ticket /
contract-interpretation boundaries and need main's ruling before the plan revision:
- **F7 (image reference)** — an S2a translator gap; S2b cannot fix it in scope.
- **F2 + F5 (chat/ guard breadth + Chief-binding persistence write)** — how far the
  contract-authorized `chat/` change extends (one endpoint vs. a central guard across all
  Chief lifecycle ops; and whether persisting the pool's Chief binding back to
  `agent_chat_sessions` is in-scope for S2b).
All other findings (F1, F3, F4, F6, F8–F16) are plan-internal and will be fixed in a single
revision, then re-confirmed with one Codex round (D-codex-loop-cap).

---

# Round 2 (confirming) — Codex re-review + orchestrator resolution

D-codex-loop-cap reached (2 rounds). Codex confirmed the two hard checks pass (`session.close`
is valid + the pool-transport bypass of the denylist is real; `pool.init_executor` exists and is
appropriate) and confirmed F7 is now resolved in S2a (my round-1 finding was correct and DROVE that S2a fix — not a false alarm). It raised residual under-specification on 7 items,
which I verified against code and resolved in the plan (no third Codex round; these are precise,
bounded specification fixes, not open design questions):

- **F7 — CORRECT when raised; RESOLVED in S2a (not a self-retraction).** The round-1 finding was
  accurate: at the time it was raised, S2a's translator DID forward image refs verbatim as
  `image.attach {path}`. It drove main's directive into S2a's still-open pipeline (main amended
  S2a's contract + a RED test: server-side resolution of managed refs to absolute paths before
  `image.attach`; unresolvable → neutral error). The resolution seam now visible in
  `NeutralDownstreamSession.handle_neutral_request` (`neutral_downstream_session.py:89-92`, comment
  cites "defect #7") IS that fix landing — the finding caused the fix, it did not describe a
  pre-existing state I misread. So S2b plans against S2a's RESOLVED-refs behavior; the images e2e
  stays in scope. NOT an S2b-side change, NOT a standing escalation. (Two scope escalations remained
  after this — Collisions #1 and #4 — both since ruled in-scope.)
- **F4 (old live id) — FIXED.** `EmployeeChildRecord` carries only `stored_session_id`, not the
  live id (verified `employee_child_pool.py:52-59`, `:300`). The pool cannot source the old live
  id. Resolved: the SESSION passes its already-bootstrapped `self._session_id` into
  `rebind_fresh_session(employee_entity_id, old_live_session_id)`. Added per-employee rebind
  serialization (concurrent `/new` must not create two live sessions).
- **F4 (new) fan-out of pool RPC responses — FLAGGED for the implementer.** Pool-issued
  close/create responses have an int id but no downstream `PendingForward`, so
  `deliver_child_frame` treats them as uncorrelated and fans them to subscribers
  (`employee_child_relay.py:367-411`). Same path S1's spawn-time RPCs take, but newly reachable at
  runtime during a `/new` while panes are attached. Plan now REQUIRES the implementer to confirm
  pool RPC ids are excluded from fan-out (or dropped by the translator) + a test that a second pane
  gets no stray passthrough row; if exclusion needs an S1 relay change, that is a collision to
  raise (not silently patch).
- **F2 (config injection) — FIXED.** `ChatTurnLifecycle.__init__` takes no config
  (`service.py:163-171`, verified). Resolved: inject a `chief_pool_owned: Callable[[], bool]`
  predicate, wired by `create_app` to `lambda: config.relay_backend_enabled`. Recovery uses a
  distinct settle-not-raise branch (a raising guard cannot settle-and-return).
- **F5 (upsert/timestamps/event) — FIXED.** The row is created lazily (`INSERT OR IGNORE`,
  `chat/service.py:92`); a raw `UPDATE` misses the absent row, `updated_at`, and the
  `chat_session_created` event (`chat/data.py:960,966`). Resolved: a NEW call-only
  `record_agent_session_key(conn, entity_id, key, now)` in `chat/data.py` keeps the timestamp/event
  semantics inside the chat-owned writer; the pool-persistence closure calls it. (Adding this
  call-only helper folds into Collision #4's escalation.)
- **F9 (clear pending on completion/failure/reset) — FIXED.** Reducer entries 7/8/11 now clear
  pending question + pending approval on `turn_completed`, `turn_failed`, and `child_reset`.
- **F11 (meta failure → wrong pane) — FIXED.** Meta failure now resolves to `error` (neither path +
  retry), NOT `disabled`; only a successful `relay_chief_enabled === false` yields legacy.
- **F13 (release-gate mechanism) — SPECIFIED.** The e2e server is a separate subprocess, so the
  release cue is stdin-driven (the pane's own subsequent requests) plus a fixed inter-token pacing
  constant in the scripted child's own reader (not `time.sleep` in the transport writer);
  Playwright proves intermediate text via a pre-installed `MutationObserver`.
- **F14 (absence barrier) — FIXED.** The compact test now drives a following observable action
  (list_catalog/send) and asserts no failure line in between — checking absence AFTER the ACK is
  processed.

## Remaining escalations to main (now TWO, down from three)
- **Collision #1 (F2 guard breadth)** — the central guard spans six Chief lifecycle ops; is that
  breadth within the contract's "minimal chat/ change"? Recommend yes.
- **Collision #4 (F5 persistence write)** — persisting the pool's fresh Chief binding writes the
  `agent_chat_sessions` table (now via a new call-only `chat/data.py` helper). In-scope for S2b, or
  scoped separately? Recommend in-scope (the necessary other half of adoption).
- **F7 is NO LONGER escalated** (retracted — S2a already resolves it).

## Verdict
All 16 round-1 findings resolved; all 7 round-2 residuals resolved or precisely flagged for the
implementer. The plan is sound to implement against once S2a lands green and main rules on the two
remaining scope escalations (Collisions #1 and #4). Codex loop cap reached.

---

# Main's rulings (2026-07-18) — all three resolved

Main ruled all three escalations on the orchestrator's recommendations:

1. **IMAGE REFS (F7)** → fixed in S2a NOW (its pipeline is still open; main amended S2a's contract
   and directed a RED test: server-side resolution of managed refs to absolute paths before
   image.attach; unresolvable → neutral error). S2b plans against RESOLVED-REFS behavior; the images
   e2e stays in scope. Plan §10 Collision #3 + §6.2 + §7.1 updated to the resolved posture.

2. **CENTRAL CHIEF GUARD (Collision #1)** → IN-SCOPE. "Minimally" = no broader than required, not
   smaller than correct. One central flag-on guard in chat/service.py across every gateway-touching
   Chief lifecycle op + skip-and-settle of stale running-Chief startup recovery is the minimal
   CORRECT closure. Named for exactly what it is (pool-ownership crossover guard). Plan §1.4 + §10
   + §9 updated to RULED.

3. **CHIEF BINDING PERSISTENCE (Collision #4)** → IN-SCOPE (necessary other half of adoption).
   Composition-owned callback; write through an existing chat/ data write function call-only if one
   fits, else the narrowest addition. VERIFIED no existing writer fits (bind_human_turn_session
   requires a running turn; attach_session_key writes the turn column), so the plan adds the
   narrowest call-only record_agent_session_key helper in chat/data.py. Plan §2.5 + §10 + §9
   updated to RULED; chat/data.py added to the allowlist.

All "pending ruling" / "escalated" markers cleared throughout the plan. No open questions remain.

## Pipeline state
- STEP 1 (plan) ✅ · STEP 2 (Codex plan review, 2 rounds, cap reached) ✅ · rulings reconciled ✅
- HOLDING on the implementation gate: S2b implementation MUST NOT begin until main confirms S2a is
  integrated + verify green. (Task #11 shows S2a completed, but I await main's explicit go-signal.)
