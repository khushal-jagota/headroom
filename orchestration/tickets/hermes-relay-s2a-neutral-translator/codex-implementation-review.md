# S2a implementation-diff review (STEP 4) — Codex review + adjudication

Reviewer: Codex `gpt-5.6-sol`, reasoning effort high, `--sandbox read-only`, stdin closed. Run
against the S2a implementation diff, the amended `D-only-free-hermes-features` contract, the plan,
the S1 sources, `chat/data.py`, and the native `tui_gateway/server.py`. Raw output:
`scratchpad/codex_impl_review_out.txt`.

Codex returned **6 BLOCKING findings** and confirmed 5 axes sound. Every claim re-verified against
source before acting. Plus **1 externally-found defect** the parent folded in from S2b planning
(image-ref resolution — now on the contract). Per `D-codex-loop-cap`, driving these to zero (fixes +
gate re-run) is NOT a new review round.

All 7 are being driven through the implementer in ONE consolidated pass. Adjudication:

## #1 — BLOCKING — cold attach leaks pool RPC responses + bypasses subscribe seam. VALID.
Evidence verified: `_attach` (neutral_downstream_session.py:106) calls `self._relay.subscribe(...)`
directly BEFORE `session.active_list`. For a COLD child (not pre-spawned), the pool's own
`session.create`/`session.resume` response fans out to this now-subscribed downstream
(employee_child_relay.py:392 uncorrelated branch) and the session turns it into an empty-type
`PassthroughEvent` (violates passthrough=unknown-native-only; contract line 49-50/67-69). The
integration harnesses pre-spawn the child, so the pool session RPC completes before subscribe —
masking it. FIX: bootstrap `session.active_list` FIRST (spawns the child + drains its pool session
RPC), THEN subscribe via a synthesized `{relay:"subscribe"}` envelope through
`handle_downstream_message` (plan §0/§3), THEN history. Add a cold-attach test (child NOT pre-spawned)
asserting no `PassthroughEvent` and the subscribe envelope ordering.

## #2 — BLOCKING — overload can't disconnect once send_text is actually blocked. VALID.
Evidence: relay_neutral_route.py checks `overflowed` before dequeuing, but `websocket.send_text` can
block indefinitely; nothing observes overflow set WHILE blocked. Contradicts the disconnect policy
(plan §6). The overload test never waits until `send_text` is entered, so it can overflow before the
writer starts and pass spuriously. FIX: an independent overflow-monitor task/event that fires the
disconnect even while the writer is parked in `send_text`; test with a `send_entered` barrier before
flooding so the flood happens strictly after the writer is blocked.

## #3 — BLOCKING — attach erases native tool-history content. VALID (exact native keys confirmed).
Evidence: native `_history_to_messages` emits tool rows as `{"role":"tool","name":...,"context":...}`
(server.py:4930, confirmed). The session reads `row.get("content", row.get("text",""))` and
`row.get("tool_name")` (neutral_downstream_session.py ~318) — WRONG keys, so real tool rows become
`text=""`, `tool_name=None`. FIX: for a `role=="tool"` row map `context → text` and `name →
tool_name`; keep `content`/assistant handling for non-tool rows. Add an exact native-shaped tool-row
test.

## #4 — BLOCKING — tee can't reconstruct queued prompts; duplicates stale human text. VALID.
Evidence: `_observe` sets `acc.last_user_text = str(text)` overwriting on each `prompt.submit`
(transcript_mirror_tee.py ~128), and `message.start` copies it without consuming. Stock Hermes MERGES
successive mid-turn prompts as `previous + "\n\n" + new` (server.py:5047) — and mid-turn sends are
LEGAL now (D-native-turn-concurrency) — so the tee records only the last prompt. Autonomous
`message.start` (a step with no downstream prompt, server.py:8748) reuses stale human text. FIX: keep
consumable pending-prompt state; merge queued prompts with Hermes's `\n\n` rule; clear on
`message.start`; IGNORE an autonomous `message.start` that has no pending relay prompt (do not
duplicate). Test both.

## #5 — BLOCKING — SQLite connect-open failure escapes the tee isolation boundary. VALID.
Evidence: `_connect_fn` runs BEFORE the worker's `try` (transcript_mirror_tee.py ~167). If open fails,
the worker dies unlogged, queued records never `task_done`, and shutdown can block once the queue
fills. Contradicts plan §5 "Any exception caught and logged". The failure test only makes `execute`
raise, not connect. FIX: move connection acquisition INSIDE the worker's caught path; always
drain/`task_done`; log open failure. Test a raising `connect_fn`.

## #6 — BLOCKING — production tee registration untested (could be removed silently). VALID.
Evidence: registration happens only when `db_path` AND `now` are supplied (composition.py:42), but the
composition harness supplies neither, and no test asserts the tee is stored/started/registered as a
relay observer. FIX: a composition test that composes ENABLED with a temp DB + clock, asserts the
exact registered observer (present in relay observers, started), and shuts it down.

## #7 (parent-folded, from S2b planning; now on contract lines 75-79) — BLOCKING — image refs forwarded verbatim. VALID.
Evidence: send-message maps each `image_ref` to `image.attach {path: ref}` verbatim
(hermes_frame_translation.py ~226). But refs are Panels web-relative (`/files/chats/<entity>/...`,
files/api.py) that a child cannot open; the legacy path resolves them to absolute filesystem paths
pre-gateway (`_resolve_turn_image` → `resolve_chat_file` → `.absolute_path`, chat/service.py:580).
Contract now REQUIRES: server-side resolution of each managed ref to a child-openable absolute path
before `image.attach`; an unresolvable ref → neutral request error, NOTHING reaches the child. FIX:
resolve each ref in the neutral session via the PUBLIC framework-free seam
`planner.files.logic.paths.resolve_chat_file(db_path, entity_id, relative_path)` (files/logic, NOT
chat/ — call-only respected; mirror `_resolve_turn_image`'s parse/canonicalize), using
`employee_entity_id` as the chat entity and a `db_path` threaded through composition. On any resolution
failure emit `TurnFailedEvent(reason=agent_error, detail=...)` and emit NO native frame (not even the
`prompt.submit`). RED test: managed ref → resolved ABSOLUTE path in the `image.attach` frame;
unresolvable ref → neutral error, zero child frames.

## Sound axes (Codex #7-11, confirmed)
Vocabulary is exactly the amended 7 requests + 13 events; no model/command machinery remains; cut/
unknown requests produce exact neutral failures with zero child frames; no busy gate; 4009 only as a
native-response mapping; catalog payload-as-is; negative-id correlation consumed once; no S1 file
modified; `session.active_list` uses `"id"`; history via `session.history` never `session.resume`;
`neutral_vocabulary` imports only stdlib (AST-tested); additive wiring; skip-scan clean.

## Loop discipline
This is the single STEP 4 review round (`D-codex-loop-cap`). The 7 fixes are driven to zero through
the implementer; gates re-run by the orchestrator. NO second Codex round — the findings were
re-verified directly against source, and a pure fix-to-green does not warrant another review round.

## Outcome — all 7 driven to zero (orchestrator-verified)

The implementer fixed all 7 RED-first. The orchestrator re-ran the gates independently (not on the
implementer's word) and spot-checked the two load-bearing fixes at source:
- pytest (8 S2a + 7 S1 hermes_backend test files): **93 passed** (was 87; +6 new edge-case tests).
- `ruff check .` (repo-wide): **All checks passed**.
- `mypy --strict` (6 S2a source files): **no issues**.
- Skip-scan: **no `pytest.skip`/`skipif`/`mark.skip`** anywhere.
- **S1 files UNCHANGED** vs HEAD (employee_child_relay/relay_tee/relay_route/raw_frame_transport/
  employee_child_pool — zero diff); **`chat/` UNCHANGED** (call-only respected).

Source spot-checks:
- #1: `_attach` now does `session.active_list` FIRST (drains the pool's correlated session RPC
  before this conn is a fan-out subscriber), THEN subscribes via a synthesized `{relay:"subscribe"}`
  envelope through `handle_downstream_message`, THEN `session.history`. The pool `session.create`
  response can no longer leak as a passthrough. Verified.
- #7: image resolution imports the PUBLIC `planner.files.logic.paths.resolve_chat_file` (never
  chat/'s private `_resolve_turn_image`); on the FIRST unresolvable ref it emits `TurnFailedEvent`
  and `return`s BEFORE `neutral_request_to_native_frames` — so NO native frame (not `image.attach`,
  not `prompt.submit`) reaches the child. Verified at neutral_downstream_session.py:95-131.

Three implementer judgment calls, all sound: (a) #2's overload test uses faithful integration (real
attach + a post-attach block flag so `send_text` genuinely parks) rather than poking internals; (b)
`db_path` is a REQUIRED session/route param (matches the production path where `config.db_path` is
always in scope; avoids a silent-None branch) — cost: test construction sites pass a dummy db_path;
(c) image resolution is async because the failure path emits a neutral event while the pure
translator stays framework-free. STEP 4 complete; ticket ready for parent integration + canonical
`./verify`.
