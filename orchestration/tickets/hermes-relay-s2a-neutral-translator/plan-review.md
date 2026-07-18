# S2a plan review (STEP 2) — Codex plan review + adjudication

Reviewer: Codex `gpt-5.6-sol`, reasoning effort high, `--sandbox read-only`, stdin closed. Run
against the implementation plan, the amended contract, the landed S1 sources, `chat/data.py`,
PRINCIPLES/CLAUDE.md, and the native `tui_gateway/server.py` reference. Raw output:
`scratchpad/codex_plan_review_out.txt` (also reproduced verbatim per finding below).

Codex returned 9 findings (7 BLOCKING, 2 NON-BLOCKING). Every claim was independently re-verified
against source before acting. Two findings (#1, #6) touch the CONTRACT itself and are escalated to
the orchestrator; the other seven are fixed in the plan. Codex's "sound axes" line confirmed the S1
correlation seam, denylist enforcement, tee bidirectionality, `record_message` signature, the
amended rulings, the allowlist, fake-only policy, and the no-skip rule.

Two of the fixes I had already applied to the plan BEFORE this review landed (I found them by
independent source verification while Codex ran): the history-on-attach `session.resume`→
`session.history` correction (= Codex #3's fix) and the RPC-response discrimination mechanism.

---

## #1 — BLOCKING — `/model` cannot execute through `command.dispatch`. **ESCALATED (contract).**

Codex: catalog membership does not imply dispatch support. `command.dispatch` handles
quick/plugin/skill + a few explicit builtins, then returns `4018 not a quick/plugin/skill command`
(`server.py:11799,12267`); its only model-switch branch is `_mirror_slash_side_effects`, reached
via the DENYLISTED `slash.exec` (`server.py:12955,13136`).

VERIFIED TRUE. `command.dispatch` (server.py:11799-12268) rejects `/model` with 4018 — it is a
`COMMAND_REGISTRY` entry but NOT a `quick_command`, and the switch machinery (`_apply_model_switch`,
server.py:2815) is only reached from `slash.exec` (denylisted). So the amended contract's "the
catalog-vetted `/model` switch through the mediated command surface" (contract.md:42) rests on a
seam that does not perform the switch.

The real stock seam I found: `config.set {key:"model", value:<name>, session_id:<sid>}`
(server.py:10211) — session-scoped (calls `_apply_model_switch`, line ~10247), NOT on the S1
denylist (verified: 0 matches), and it rejects mid-turn with `4013 session busy` (native
turn-concurrency, consistent with D-native-turn-concurrency — Panels invents nothing). But
`config.set` is NOT a "catalog-vetted command through the mediated surface"; it is a distinct
generic RPC. So resolving this changes the request→RPC mapping AND the contract's stated model-switch
mechanism. **Escalated to the orchestrator** (the parent explicitly ruled the mediated-`/model`
path; only the owner can re-point it). Options in the escalation message.

## #2 — BLOCKING — attach has no reliable live `session_id` bootstrap. **FIXED.**

Codex: "cache from first frame" waits forever for an already-live idle child (subscribe emits no
frame; the pool record exposes only `stored_session_id`, not the live id).

VERIFIED TRUE. `register_downstream`/`subscribe` emit no frame (employee_child_relay.py:184,210);
`EmployeeChildRecord` carries `stored_session_id` only (employee_child_pool.py:53-59); native RPCs
need the LIVE id.

FIX (in plan §3, request table): attach bootstraps the live id via `session.active_list {}`
(server.py:6057; not denylisted). The pool holds exactly one live session per child
(one-child-one-session), so its sole entry gives the live `session_id` deterministically, no
frame-wait. Cached; refreshed opportunistically from frames; re-bootstrapped on respawn. Test: attach
to an already-existing idle child asserts `session.active_list` issued and the id used.

## #3 — BLOCKING — history-on-attach uses denied `session.resume`. **FIXED (pre-review).**

Codex fix = `session.history`. Already applied before this review: `session.resume` IS denylisted
(employee_child_relay.py:45) AND already consumed by the pool at spawn
(employee_child_pool.py:287-313), so the translator cannot re-issue it. Plan now issues
`session.history {session_id}` (server.py:7803-7823; not denylisted; returns
`{count, messages:_history_to_messages(history)}` from the child's own session + DB — the durable
session, never Panels DB). Area-5 test scripts `session.history` and asserts NO `session.resume`
issued.

## #4 — BLOCKING — the actual relay-facing downstream queue remains unbounded. **FIXED.**

Codex: `register_downstream()` builds an unbounded `asyncio.Queue()` (employee_child_relay.py:187);
relay fan-out writes into THAT queue; bounding a later second queue does not satisfy "bounded
downstream queues."

VERIFIED TRUE. The plan originally bounded a private second queue. FIX (plan §6): the neutral route
replaces `conn.outbound` with an `OverflowSignallingQueue(asyncio.Queue, maxsize=
NEUTRAL_OUTBOUND_MAX_FRAMES)` immediately after `register_downstream()`, before any frame. Verified
the relay reads `conn.outbound` FRESH each enqueue (put_nowait, lines 469/475) and drain
(relay_route.py:37) — no cached reference — so swapping the attribute is adapter-only, no S1 change.
The relay wraps `put_nowait` in `except Exception: pass`, so a plain bounded queue would drop
silently; the custom queue instead SETS an `overflowed` flag on its own `QueueFull`, which the route's
writer loop polls and, when set, closes the connection + `unregister_downstream` (disconnect-slow-
consumer). Area-7 test drives overflow THROUGH the relay fan-out and asserts close + unregister.

## #5 — BLOCKING — Translation Area 2 omits required known frames. **FIXED.**

Codex: contract requires tool phases started/progress/completed and every S0 inventory kind; stock
emits `tool.generating` (server.py:3861) and S0 inventories `session.info` (s0-spike.md:34) — neither
had a row.

VERIFIED TRUE. FIX (plan §3 table + area-2 test): `tool.generating` → `ToolActivityEvent(phase=
progress)` (payload `{name}` only; tool_id/preview empty) — this is the `progress` phase the contract
names, previously missing. `session.info` and `status.update` → explicit `PassthroughEvent` rows
(tested as exact rows, not accidental fallthrough). New `test_s0_inventory_kinds_have_explicit_rows`
and the three tool phases asserted in `test_each_native_type_maps_to_its_neutral_event`.

## #6 — BLOCKING — several neutral outputs have no defined shape or specific assertion. **PARTIAL FIX + ESCALATED (contract).**

Codex: correlated catalog/model results become "the relevant neutral event," but the event union has
no such event and `PassthroughEvent` was keyed on native `params.type`; non-catalog rejection was
only "a neutral error event." PRINCIPLES requires specific stated values.

VERIFIED TRUE — and it exposes a contract tension: the contract enumerates the REQUESTS "list model
options"/"list command catalog" and says the pane renders the picker on them (contract.md:44), but
its EVENTS list names no result event for them. FIXES (plan §2/§3/§4): (a) `PassthroughEvent` field
renamed `native_type`→`source`, now carrying either a native `params.type` OR the RPC method name for
a consumed result; the model-options/catalog results ride back as
`PassthroughEvent(source="model.options"|"commands.catalog", payload_json=<result>)` — no new typed
event kind, so the enumerated event list is NOT exceeded. (b) non-catalog rejection is the exact
`TurnFailedEvent(reason=agent_error, detail=f"unknown command: {name}")` — already in the vocabulary.
Area-3 gains exact-value tests for both results and the rejection detail string. **Escalated**: this
is the plan's reading of a request-list-vs-event-list gap; if the owner wants dedicated typed result
events instead of passthrough, that is a contract amendment. Surfaced with #1.

## #7 — BLOCKING — the failed-turn tee test can pass while dropping required assistant text. **FIXED.**

Codex: failed `message.complete` carries `text` (server.py:9137), but the failed-turn test asserted
only the user row.

VERIFIED TRUE. FIX (area-6 test split): `test_failed_message_complete_appends_user_and_assistant_text`
drives `message.complete(status="error", text="partial answer before failure")` and asserts BOTH
exact rows in order; `test_native_error_frame_appends_user_text_only` covers the bare-`error` case
where no final assistant text exists (user row only). Specific values, no silent-pass hole.

## #8 — NON-BLOCKING — tee SQLite connection crosses threads. **FIXED.**

Codex: the repo connection factory uses default SQLite thread affinity (no `check_same_thread=False`,
db.py:204); a connection opened at construction and written from the worker thread would raise.

VERIFIED TRUE. FIX (plan §5): `observe` never touches SQLite (only enqueues + returns); the worker
thread OPENS, OWNS, and CLOSES its own connection entirely on the worker thread. No cross-thread
connection use.

## #9 — NON-BLOCKING — inline tunables violate PRINCIPLES. **FIXED.**

Codex: PRINCIPLES requires tunable limits in an isolated configuration module (PRINCIPLES.md:8); the
plan placed queue/preview constants inline with a "not worth a file" judgment.

VERIFIED — a standing rule is not overridden by a convenience judgment. FIX: added
`src/planner/hermes_backend/neutral_relay_config.py` to the layout + allowlist; `NEUTRAL_OUTBOUND_MAX_
FRAMES`, `TOOL_PREVIEW_MAX_CHARS`, and the tee work-queue bound live there; modules import them.

---

## Outcome

Seven findings fixed in the plan (#2, #3, #4, #5, #6-mechanics, #7, #8, #9). Two contract-touching
items were escalated and are now RULED (contract amended 2026-07-18) and applied to the plan:

- **#1 (model-switch) — RULED option (c):** model-switch is a first-class neutral `SetModelRequest`
  mapping to `config.set {key:"model", value, session_id}`, with the native `4013` mid-turn
  rejection surfacing as the request's error. The mediated command surface does not special-case
  `/model`; the catalog normalization may drop `/model` (the picker owns switching). Plan updated:
  new `SetModelRequest` in the vocabulary + request→RPC table, new area-3 tests
  (`test_set_model_maps_to_config_set`, `test_set_model_mid_turn_rejection_surfaces_as_request_error`),
  §4 rewritten.
- **#6 (listing results) — RULED typed events, NOT passthrough:** the contract now enumerates
  `model options result` and `command catalog result` as typed events; passthrough is for unknown
  native frames only. Plan updated: new `ModelOptionsResultEvent`/`CommandCatalogResultEvent` with
  `NeutralModelOption`/`NeutralCommandEntry` normalized shapes; `PassthroughEvent` reverted to
  `native_type` (native-frames-only); area-3 tests now assert the exact normalized typed events.

Loop discipline (D-codex-loop-cap): this was ONE plan review round. It drove material fixes, so one
confirming round is permitted but NOT required — the fixes were verified directly against source
(every finding re-checked at file:line), so no confirming round is run. The plan is review-clean on
every axis. Proceeding to STEP 3 (implementation).

---

## Post-implementation note — scope re-amendment + rework (2026-07-18)

After STEP 3 first landed green against the then-current contract (SetModelRequest + typed catalog),
the owner re-amended the contract to `D-only-free-hermes-features` (final): model selection and
generic mediated command execution CUT; `compact` (→`session.compress`) and `list catalog`
(→`commands.catalog`, payload-as-is `CatalogResultEvent`) IN; an unknown/cut request kind must be
rejected with a neutral error and never reach the child. A stale intermediate ruling (set-model
first-class + typed result events) was reconciled against the newer on-disk contract and the owner
confirmed the cut is canonical.

The plan was revised to the final contract and the implementation reworked to match (cut vocabulary/
tests/`mediated_command_catalog.py` removed; `CompactRequest`/`ListCatalogRequest`/`CatalogResultEvent`
+ cut-request rejection added; acceptance areas renumbered 7→6). Per D-codex-loop-cap a pure deletion
warrants no new PLAN review round. Focused gates re-run green by the orchestrator (no parent verify
running):
- pytest (8 S2a + 7 S1 hermes_backend test files): **87 passed**.
- `ruff check .` (repo-wide): **All checks passed**.
- `mypy --strict` (6 S2a source files): **no issues**.
- No forbidden `pytest.skip`; no cut-symbol references in any test.

STEP 4 (Codex implementation-diff review, one round per D-codex-loop-cap) is running against the
final-scope diff.
