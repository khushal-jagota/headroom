# AD06 implementation plan — deep canonical Chat turn

## Outcome and fixed boundary

Replace the remaining function chain with one server-composed lifecycle owner:

```text
POST /api/chat/{entity_id}/turns  ─┐
POST /api/messages/chief         ─┴─> ChatTurnLifecycle.start_human_turn(entity_id, request)
POST /api/chat/{entity_id}/pause ───> ChatTurnLifecycle.pause_active_turn(entity_id)
                                    |
                                    +-- atomic human admission and owned execution
                                    +-- causal durable-session binding
                                    +-- typed gateway observations
                                    +-- cross-origin visible-turn Pause and settlement
```

`ChatTurnLifecycle.start_human_turn` owns human admission and execution.
`pause_active_turn` is deliberately cross-origin visible Chat/session control: it may
interrupt and settle a human- or worker-origin Panels turn, but it never owns Employee
delivery or Ticket settlement. `EmployeeStepRunner`, `SharedGateway.run_ticket_step`,
Worker-context delivery/acknowledgement, and worker Chat projection remain separate.
State, history, status, and command-catalogue reads remain ordinary services/routes.

This is an internal concentration. Keep the current HTTP paths, request JSON, response
JSON, database schema, `ChatTurn`/`ChatState` shape, frontend, visible text/roles,
activity labels, event kinds/payloads, image behavior, command behavior, session
continuity, and error wording unless this plan names an internal duplicate that is
being deleted. No compatibility surface remains for the old runner, chunk, callback,
or gateway method.

## Current inventory and disposition

### Human admission, durable writers, and reads

- `chat/api.py::start_chat_turn` validates generic JSON, strips the generic `text`,
  constructs `ChatTurnRequest`, then currently passes nine service arguments.
  `start_chief_message` accepts exactly one nonblank `text` field but deliberately
  passes the original whitespace-preserving text. Preserve that difference exactly:
  generic `/turns` supplies stripped text; hosted Chief supplies the original string.
- `service.start_human_turn` resolves/materializes the entity, validates all managed
  images, computes visible/model text, calls `chat_data.start_turn`, creates a raw
  daemon thread, and returns its `ChatTurn`.
- `service._run_human_turn` owns the current callback/chunk state machine and directly
  calls `_persist_key`, `attach_session_key`, `record_turn_activity`,
  `set_turn_activity`, `append_turn_output`, `finish_turn`, and `fail_turn`.
- `service.pause_turn` already targets the active visible turn without checking its
  origin, interrupts its durable session, and calls `finish_turn(interrupted)` without
  changing Ticket status. Its cross-origin behavior moves to `pause_active_turn` on
  the class; there is no forwarding wrapper.
- `service._reject_if_ticket_worker_running` is a pre-transaction check repeated at
  callback time. Preserve its early admission error precedence, repeat the final
  status check under the turn-creation write lock, then delete the callback-time check:
  the complete automatic eligibility decision now excludes every active Panels turn.
- `chat_data.start_turn` is shared by human and worker projection and currently opens
  its own transaction. Split its insertion body into an explicit
  `start_turn_in_transaction` primitive; keep `start_turn` as the worker-facing
  transaction wrapper.
- `chat_data.attach_session_key` remains the worker projection writer. Human binding
  uses the new atomic entity-plus-turn writer. `set_turn_activity` has no remaining
  caller once human activity is a typed observation and is deleted.
- `finish_turn` and `fail_turn` remain worker-service low-level writers. Human
  completion/failure and cross-origin Pause call `settle_chat_turn`; that name and
  validation admit either visible-turn origin. It may share one private transaction
  body with the worker writers without changing Employee settlement ownership.
- `service.history` is nominally a read but may resume a rotated key and persist it via
  `_persist_key`; `state` can still call `history` and `_legacy_state_messages` when
  Panels has no visible rows. Rename the helper to `_persist_history_session_key` for
  honesty, but do not change this behavior. AD09, not AD06, removes silent history
  fallback.

The automatic Employee claimant commits `ticket_status=agent_running_step` under
`BEGIN IMMEDIATE`, then the runner creates its worker Chat turn. Extend the sole
`is_eligible_for_automatic_employee_step` decision with an eighth factor: no
`status='running'` Panels Chat turn exists for the Ticket. Discovery and final claim
already consume that exact function; neither caller repeats the query and
`tickets_data.claim_automatic_employee_step` remains free of a private pre-status
rule. A false final decision returns before status/event/prompt/worker-turn effects.
Direct revision continues to bypass automatic eligibility and remains unchanged.

Chat completion, error, and Pause do not deliver an eligibility wake. Once a visible
turn settles, SQLite reflects the changed eighth factor and the canonical periodic
`AutomaticEmployeeStepDiscoveryLoop` timer observes it on its next poll. Do not add a
wake, payload, or settle-to-discovery coupling.

### GatewayAdapter implementations

Every production implementation of the human `GatewayAdapter` operation migrates in
the same replacement:

1. `GatewayAdapter` protocol — replace `stream` with required `run_human_turn`.
2. `EchoGatewayAdapter` — bind the minted/reused candidate, use the returned effective
   key for fake history and its recorded delivery, then yield typed observations. For
   exact command `/new`, mint one fresh fake candidate even when a key exists, bind it
   first, and preserve the same system completion/reuse semantics as SharedGateway.
3. `OfflineGatewayAdapter` — expose only the new method and raise the same
   `gateway_offline` error before binding or observation.
4. `RealGatewayAdapter` — keep its startup-placeholder behavior with the new method;
   it still raises the same offline error until production lifespan replaces it.
5. `EntityRoutingGateway` — forward the new method and binding function to the
   entity-selected `SharedGateway`, preserving Chief versus default routing and image
   order.
6. `SharedGateway` — implement the causal binding/use rules below and yield the closed
   observation union. `run_ticket_step` is not part of this replacement.

Injected test implementations that must be migrated rather than hidden with ignores:

- `test_minds.py::RecordingChatGateway`;
- `test_chat_seed.py::{BlockingGateway, ActivityGateway, InterruptGateway,
  RotatingHistoryGateway, RacingGateway, WorkerClaimsGateway, ReMintGateway,
  BusyGateway}` (the pause/history-only doubles retain only the capabilities their
  test invokes; all human-delivery doubles use `run_human_turn`);
- `test_chat_commands.py::{RacingGateway, RotatedKeyGateway, BusyGateway}`;
- both local `test_chat_images.py::RecordingGateway` definitions; and
- `test_chat_activity.py::HumanActivityGateway`.

`planner.minds.fake.FakeGateway` and the `ManualEventFake`/other subclasses in worker
tests implement the JSON-RPC child-process protocol, not `GatewayAdapter`; their
`send` methods remain. The `RecordingGateway` classes in core-loop/Employee tests and
`CapturingGateway` in request-identity tests exercise `run_ticket_step`, not human
delivery, and also remain outside the replacement.

### `ChatStreamChunk` producers and consumers

Production constructors are exactly:

- `EchoGatewayAdapter.stream`: session, token, and done chunks;
- `SharedGateway.stream`: literal-`/new` and initial session chunks;
- `SharedGateway._stream_done`: token and done chunks; and
- `SharedGateway._stream_accepted_submission`: activity, token, and done chunks.

The only production consumer is `service._run_human_turn`, which branches on all four
string tags and contains the untyped activity fallback. Delete all of those
constructors and that consumer.

Test constructors/shape consumers are confined to `test_chat_seed.py`,
`test_chat_commands.py`, `test_chat_images.py`, `test_chat_activity.py`,
`test_minds.py`, and the exact-shape guard in `test_chat_ingress_contract.py`. Migrate
them to `ChatActivityObservation`, `HumanChatOutputDelta`, and
`HumanChatCompletion`; no test-only chunk adapter or dictionary tag is allowed.
Direct `gateway.stream(...)` calls and `chunk.type`/optional-field assertions in
`test_minds.py` become `run_human_turn(..., identity_binder, ...)` plus exact variant
comparisons. The new static guard inventories these six paths and fails if
`ChatStreamChunk(`, `.stream(` on a named human gateway, or old optional fields return.

## Frozen declarations

### Product request and closed observations

Keep `ChatTurnRequest` exactly as the product request value:

```python
from typing import Literal, TypeAlias


ChattableEntityKind: TypeAlias = Literal["ticket", "day", "agent_chat_session"]


@dataclass(frozen=True)
class ChatTurnRequest:
    text: str
    mode: str = "message"
    image_references: tuple[str, ...] = ()
```

`ChattableEntityKind` lives in `chat/contracts.py` so `service.py` and `data.py` share
the exact kind without importing one another. Delete `ChatStreamChunk` and add these
human transport declarations there too:

```python
@dataclass(frozen=True)
class HumanChatOutputDelta:
    text: str

@dataclass(frozen=True)
class HumanChatCompletion:
    text: str
    role: Literal["assistant", "system"]

HumanChatObservation: TypeAlias = (
    ChatActivityObservation | HumanChatOutputDelta | HumanChatCompletion
)
```

The existing `ChatActivityObservation` is already the display-safe activity variant
and stays shared with worker projection. There is no session variant, tag field,
defaulted unrelated field, or generic observation dictionary. A completion always has
exactly final text plus its visible role.

### One human gateway operation and binding handshake

In `core/adapters/base.py` define the callback name and the one human operation
exactly:

```python
HumanSessionKeyBinder: TypeAlias = Callable[[str], str]

class GatewayAdapter(Protocol):
    def status(self) -> GatewayStatus: ...
    def history(self, session_key: str | None, entity_id: str) -> ChatHistory: ...
    def run_human_turn(
        self,
        session_key: str | None,
        entity_id: str,
        text: str,
        mode: str,
        bind_session_key: HumanSessionKeyBinder,
        image_paths: tuple[Path, ...] = (),
    ) -> Iterator[HumanChatObservation]: ...
    def interrupt(self, session_key: str, entity_id: str) -> None: ...
    def catalog(self) -> CommandCatalog: ...
```

`bind_session_key` is required and returns the durable key the gateway must actually
use. Delete the optional `on_session_key: Callable[[str], None]`, yielded session
chunks, completion session key, and `stream` method from the protocol and all five
concrete implementations. There is no old-signature overload or alias.

### One lifecycle owner and one admitted execution value

In `chat/service.py`, import `ChattableEntityKind` and define the exact private frozen
execution value and public lifecycle class:

```python
@dataclass(frozen=True)
class _AdmittedHumanChatTurn:
    entity_id: str
    entity_kind: ChattableEntityKind
    turn_id: str
    expected_session_key: str | None
    model_text: str
    mode: Literal["message", "command"]
    image_paths: tuple[Path, ...]
    gateway: GatewayAdapter
    force_fresh_session: bool


class ChatTurnLifecycle:
    def __init__(
        self,
        conn_factory: Callable[[], sqlite3.Connection],
        gateway_provider: Callable[[], GatewayAdapter],
        now: Callable[[], int],
        db_path: str | Path,
    ) -> None: ...

    def start_human_turn(self, entity_id: str, request: ChatTurnRequest) -> ChatTurn: ...

    def pause_active_turn(self, entity_id: str) -> ChatTurn: ...
```

`start_human_turn` receives the complete `ChatTurnRequest`, validates its invariants,
performs admission, snapshots the current gateway into exactly one
`_AdmittedHumanChatTurn`, launches execution, and returns the admitted `ChatTurn`.
`force_fresh_session` is true only for `request.mode == "command" and request.text ==
"/new"`; it is not accepted from HTTP or any other caller-controlled field.

`_execute_turn(self, admitted: _AdmittedHumanChatTurn) -> None` accepts only that
value. It keeps `effective_session_key` and the one-use force-pending flag as local
mutable execution state; neither is another dataclass field. The only raw human-turn
`threading.Thread` construction is private inside
`ChatTurnLifecycle._launch_execution`; it remains one daemon per admitted human turn
and creates no registry, queue, scheduler, join protocol, or recovery machinery.
Static coverage rejects any second/top-level human launcher.

`pause_active_turn` resolves the visible active turn regardless of `origin`, resolves
the current gateway for that call, interrupts the turn's attached session, and uses
the generic settlement door. For a worker-origin turn it preserves partial output,
leaves Ticket status untouched, emits no eligibility wake, and leaves the eventual
Employee/Ticket outcome with `EmployeeStepRunner`.

Delete top-level `start_human_turn`, `pause_turn`, `_run_human_turn`, and `_unix_now`.
There is no compatibility class, alias, old-signature forwarder, or extra admitted
execution field.

### App composition and thin HTTP marshalling

`create_app` installs one owner on app state:

```python
app.state.chat_turn_lifecycle = ChatTurnLifecycle(
    conn_factory,
    gateway_provider=lambda: app.state.adapters.gateway,
    now=clock.now_unix,
    db_path=config.db_path,
)
```

The provider is deliberately dynamic. Production lifespan replaces
`app.state.adapters` with `EntityRoutingGateway`, and unit tests replace it after app
creation; capturing the registry's `RealGatewayAdapter` placeholder or the original
test adapter would be wrong. `start_human_turn` resolves the provider once and stores
that gateway in `_AdmittedHumanChatTurn`; `pause_active_turn` resolves it at Pause
time. The owner itself is not recomposed during lifespan.

`start_chat_turn` retains the current HTTP type/shape checks, constructs the same
stripped generic `ChatTurnRequest`, and calls only
`lifecycle.start_human_turn(entity_id, turn_request)`. `start_chief_message` retains the
exact-key body rule, constructs `ChatTurnRequest(text=original_text)`, and calls the
same method with `CHIEF_OF_STAFF_ENTITY_ID`. `pause_chat_turn` calls only
`lifecycle.pause_active_turn(entity_id)`. These route bodies no longer open a connection or
pass gateway, clock, database path, individual text/mode/images, or thread timing.
Reads keep using the current dynamic app-state adapter.

## Exact transaction and concurrency boundaries

### 1. Admission

`ChatTurnLifecycle.start_human_turn` performs these steps in this order:

1. Validate `ChatTurnRequest` invariants without writing.
2. Open one connection. Preserve the current cheap Ticket Worker-running preflight
   before entity/image resolution so mixed invalid-image/running-Worker requests keep
   their existing error precedence. Then call the current `_resolve` once to preserve
   Ticket existence errors and Day/Chief materialization behavior, including the
   current materialization side effect before a later invalid-image error. This
   preflight is only an early rejection; step 4 remains the race-safe authority.
3. Resolve every ordered managed image and compute the existing exact split:
   visible text is original text plus ordered managed Markdown; model text is the
   request text or `"Please respond to the attached image."` for image-only input.
   Any invalid image exits before `chat_messages`, `chat_turns`, or their events.
4. Resolve the dynamic gateway provider once. Start `BEGIN IMMEDIATE`, re-resolve the
   entity under the lock, read the Ticket's final `ticket_status` when applicable, and
   reject `agent_running_step`. In the same transaction call
   `chat_data.start_turn_in_transaction` to insert the running human turn, visible
   human message, `chat_message_recorded`, and `chat_turn_started`.
5. Commit, then construct exactly one `_AdmittedHumanChatTurn` from the entity kind,
   lock-read session key, turn id, model text/mode/images, gateway snapshot, and the
   exact-literal `/new` force flag. Close the admission connection, launch that value,
   and return the same `ChatTurn` produced inside the transaction.

`start_turn_in_transaction` never begins or commits. `chat_data.start_turn` keeps its
current signature for worker projection and wraps that primitive in its own
`BEGIN IMMEDIATE`; no worker admission is routed through `ChatTurnLifecycle`.

Add the active-turn query only as the eighth conjunct inside
`runtime.automatic_employee_step_eligibility.is_eligible_for_automatic_employee_step`:
return false when a `chat_turns` row for `ticket.id` has `status='running'`. Keep the
function signature and the other seven factors exact. Discovery calls this function
unchanged for every current-day membership candidate. Final claim calls it unchanged
once under its existing `BEGIN IMMEDIATE`; `claim_automatic_employee_step` gains no
query, parameter, guard, condition, or runtime import. Therefore whichever write
transaction commits first establishes the exclusion:

- Employee first: human final status check rejects before message/turn/start event.
- Human first: the complete final eligibility call sees the running Chat turn and
  returns false before status change/event; the runner builds no prompt, worker turn,
  gateway call, or wake.

The unique running-turn index remains the same-schema backstop. Do not add a status,
table, lock row, claim token, in-process mutex, or claim-local rule. A settled Chat
turn makes the eighth factor true again without ringing the eligibility wake; the
periodic timer is the required observer.

### 2. Causal session binding

Add the one human binding writer in `chat/data.py`:

```python
def bind_human_turn_session(
    conn: sqlite3.Connection,
    turn_id: str,
    *,
    entity_kind: ChattableEntityKind,
    entity_id: str,
    expected_session_key: str | None,
    candidate_session_key: str,
    force_fresh_session: bool,
    now: int,
) -> str: ...
```

It owns one `BEGIN IMMEDIATE` transaction and:

1. verifies the named turn belongs to the entity, has `origin='human'`, and is still
   `running`; a settled/missing turn raises before the gateway may write;
2. reads the entity's current durable key from the fixed kind-to-table map;
3. when `force_fresh_session` is true, writes the candidate regardless of the current
   key (unless already equal), appends exactly one existing `chat_session_created`
   event when changed, and chooses the candidate as effective;
4. otherwise, if current equals the candidate, adopts it without an entity write/event;
5. otherwise, if current equals `expected_session_key`, writes the candidate and appends exactly
   one existing `chat_session_created {session_key}` event when the value changed;
6. otherwise adopts the non-null current database winner without overwriting it or
   appending a losing event;
7. writes that effective key to the running turn only when it differs and then appends
   the existing `chat_turn_updated {turn_id, session_key}` event; and
8. commits and returns the same effective key.

Entity-key update, event, turn attachment, and attachment event are atomic. Inside
`_execute_turn`, the binder closure starts from the admitted expected key and one-use
force flag, passes both to this writer, replaces its local expected key with every
returned result, and clears the force flag only after the first successful binding.
Ordinary same-key reports are no-ops; a later genuine rotation is one compare-and-set
plus one event; a first-write loser adopts the winner, preserving AD05's correction.
For exact `/new`, the first fresh candidate replaces even a concurrently changed key,
is returned as effective, and is attached to the turn; every later report uses ordinary
CAS/adopt-winner behavior. A callback after Pause/completion cannot attach or authorize
a prompt because the running-turn check fails before any write. No request, gateway
argument, or mode other than the owner's exact-literal predicate can set force true.

### 3. Gateway use of the returned key

`SharedGateway.run_human_turn` must not merely notify Panels. Introduce one private
`_bind_human_live_session` loop that takes a candidate/live handle and the binder,
calls the binder before a human write, and returns a live handle whose
`stored_session_key` equals the binder's returned effective key. If the returned key
differs, resume/reuse that returned key; if resume follows a continuation and exposes
another candidate, present that candidate and repeat. This loop may resolve identity;
it never retries an uncertain prompt or command write.

Apply it at every causal seam:

- initial create, stored-key resume, stale-key create recovery, continuation rotation,
  and live-handle reuse;
- literal `/new` as a branch before ordinary initial resume/create: create exactly one
  fresh candidate, present it as the execution's first binding, require the returned
  key to be that candidate, and use that live handle before emitting its system
  completion;
- `_submit_human_consequence` after the existing safe `LiveSessionDormant` recovery
  and before image attach or `prompt.submit`; and
- `_begin_human_command_operation` after its safe dormant recovery and before
  `slash.exec`, alias `command.dispatch`, or a derived `prompt.submit`.

Restructure the dormant paths rather than reporting their resolved key afterward.
The existing retry is permitted only when `LiveSessionDormant` proves no write was
admitted. Once image/command/prompt submission is accepted or uncertain, retain the
existing no-retry rule. Tests must make the binder return a different winner at the
top-level, dormant message, and dormant command seams and assert the actual Hermes
`session_id` used by `image.attach`, `prompt.submit`, `slash.exec`, and
`command.dispatch` belongs to that winner.

Echo must likewise append fake history and record its call against the returned key,
not its losing candidate. Entity routing must forward the return-capable binder
unchanged. Completion carries no session identity; all identity is established before
the write.

Literal `/new` creates one fresh candidate and binds it once. A deterministic test
changes the entity key after admission but immediately before this binder call; the
fresh candidate must replace that concurrent value in the entity and turn, and its
Hermes live `session_id` must produce the completion. The next ordinary message reuses
that already-live handle and issues neither `session.resume` nor `session.create`.
`/new` with arguments follows ordinary command dispatch and cannot force a key.

### 4. One visible Chat settlement door

Add the lifecycle's terminal writer, named for both origins it may settle:

```python
def settle_chat_turn(
    conn: sqlite3.Connection,
    turn_id: str,
    *,
    entity_id: str,
    status: Literal["complete", "errored", "interrupted"],
    reply_text: str,
    output_role: Literal["assistant", "system"],
    error: str | None,
    now: int,
) -> ChatTurn: ...
```

Validate the internal combination: complete/interrupted have no error; errored has a
nonempty error and appends no assistant/system message. Under one `BEGIN IMMEDIATE`,
read the turn and return it unchanged if it is no longer running. Otherwise clear its
activity entries, settle phase/status/timestamps once, preserve `reply_text or
output_text` for complete/interrupted, append at most one final visible message, and
append exactly one existing `chat_turn_finished` event (including `error` only for
errored). This preserves today's errored-turn behavior and today's interrupted partial
reply visibility.

`ChatTurnLifecycle` calls this function for human typed completion/failure and for
`pause_active_turn` regardless of the active turn's origin. It does not require
`origin='human'`. Human gateway `PlannerError`, unexpected exception, unsupported
observation, and exhausted iterator (`"chat turn ended without completion"`) use the
same writer. Activity and delta writers already update only a running turn; late
observations are ignored. A racing second settlement returns the first durable result
without another clear, message, event, or overwrite.

For a worker-origin Pause, use the active turn's current `output_role` and partial
output, interrupt only its attached session, leave the Ticket's
`ticket_status=agent_running_step` unchanged, and issue no eligibility wake. The
Employee runner may later observe its gateway terminal and remains the only owner of
Ticket settlement and its existing runner-settlement wake. Its ordinary worker Chat
completion/failure functions may continue using `finish_turn`/`fail_turn`; no Employee
prompt is routed through `ChatTurnLifecycle`.

## SharedGateway observation and command/image behavior

Rename the human-only private helpers so the deleted public `stream` abstraction does
not survive as internal compatibility vocabulary:

- `_stream_human_consequence` -> `_observe_human_consequence`;
- `_stream_command` -> `_observe_human_command`;
- `_stream_accepted_submission` -> `_observe_accepted_human_submission`; and
- `_stream_done` -> `_human_command_completion`.

They yield only the closed union. Preserve these exact behaviors:

- normalized reasoning/tool/command activity remains display-safe and ordered;
- nonempty deltas append in order; when completion has text but no prior delta, the
  gateway still yields one matching `HumanChatOutputDelta` before completion;
- complete and interrupted Hermes terminals both become one completion observation,
  as today; error terminals remain gateway errors;
- skill/send command results stay model-backed assistant completions; exec/plugin and
  ordinary display results stay system completions; alias dispatch and warning joining
  remain exact;
- Worker-context prepare/ack timing remains at native human submission acceptance;
- managed images attach in the supplied order on the *effective bound session* before
  prompt submit; attach failure submits nothing and detaches earlier images; rejected
  submit detaches all; and
- busy/offline/unknown transport errors retain their current `PlannerError` mapping and
  settle the already-visible human turn asynchronously as errored.

## RED-first test matrix and existing-test disposition

### New `tests/unit/test_human_chat_turn.py`

Write this file first and show focused RED against AD05. It owns the new deep-module
contract rather than turning `test_chat_seed.py` into another mixed architecture test.
It must prove:

1. **Owner/API/composition.** Install a recording lifecycle on app state and prove
   `/turns` and hosted Chief each pass one exact `ChatTurnRequest`; generic text is
   stripped and Chief whitespace is preserved. Pause calls `pause_active_turn`. AST guards
   prove routes pass no connection/gateway/clock/db path, server composition uses a
   dynamic adapter provider, and no top-level/alternate human admission or thread
   launcher exists.
2. **Exact declarations/deletions.** Assert the `run_human_turn` signature, required
   return-capable binder, exact observation fields/union members, exact ordered fields
   and types on frozen `_AdmittedHumanChatTurn`, and absence of
   `ChatStreamChunk`, `GatewayAdapter.stream`, implementation `stream` methods,
   yielded session observations, top-level `start_human_turn`, top-level `pause_turn`,
   `_run_human_turn`, `_reject_if_ticket_worker_running`, and compatibility wrappers.
   Assert the only lifecycle class is named exactly `ChatTurnLifecycle`, the only human thread
   constructor is `ChatTurnLifecycle._launch_execution`, `_execute_turn` accepts only
   `_AdmittedHumanChatTurn`, and the class exposes exactly `start_human_turn` and
   `pause_active_turn` as public lifecycle operations.
3. **Atomic race in both orders.** With two SQLite connections and barriers inserted
   only by monkeypatching functions already called inside each transaction, force
   (a) human admission to hold the lock while automatic claim waits and (b) automatic
   claim to hold it while human admission waits. Exactly one side wins. The loser has
   no message, turn, `chat_turn_started`, Ticket status event, worker projection,
   gateway call, prompt, or wake as applicable. Assert the claim writer contains no
   `chat_turns` query or second rule: its unchanged callback supplies the eighth factor.
4. **Binding writer.** Exercise first-write win/loss, same-key repeat, real later
   rotation, terminal-turn callback, entity/turn atomic rollback, and one-use forced
   binding. For exact `/new`, change the entity key immediately before the first bind;
   assert the fresh candidate overwrites it, is attached, is the actual Hermes session,
   and the next report is ordinary CAS. Assert exact `chat_session_created` /
   `chat_turn_updated` counts and order, and prove no non-literal input sets force.
5. **Typed projection and settlement.** Script activity, deltas, assistant/system
   completion, empty iterator, unsupported object, busy/offline, pause-before-bind,
   late delta/completion, and deterministic pause/completion races. Assert exact
   `ChatState`, roles/text, partial preservation, cleared activity, one final message,
   and one terminal event; run both first-settlement winners. Pause a worker-origin
   visible turn and assert interrupt, interrupted Chat settlement, partial preservation,
   unchanged Ticket status, and zero eligibility wakes.
6. **Employee separation.** AST- and behavior-lock that
   `employee_step_runner.py` imports/calls neither `ChatTurnLifecycle` nor
   `run_human_turn`; automatic/revision fixtures continue through `run_ticket_step`.
   This is paired with the full existing Employee preservation runs below.

### Migrate existing human/gateway tests

- `test_chat_ingress_contract.py`: replace the AD05 frozen chunk/stream assertions
  with the exact new owner/protocol/union/deletion inventory. Keep retired HTTP route
  assertions and public docs scans. Its named implementation list remains the six
  production classes above.
- `test_chat_seed.py`: migrate its local delivery fakes and retain state/history,
  Chief exact body/spacing, Day/Ticket/Chief routing/materialization, worker-running,
  offline/busy, pause/follow-up, stale-key, first-write, `/new`, `/new args`, status,
  authorization, and unknown-entity assertions. Move duplicate deep binding/race
  mechanics to the new file where appropriate, but do not weaken public outcomes.
- `test_chat_commands.py`: migrate ordinary skill/alias/display/compress/busy/offline
  fakes and retain exact role/text, direct-only, key/event, rotation, and validation
  assertions.
- `test_chat_activity.py`: replace its direct `_run_human_turn` invocation with a
  `ChatTurnLifecycle.start_human_turn` execution through the public owner. Keep worker activity
  tests on the existing worker functions and keep all activity cleanup/normalization
  assertions.
- `test_chat_images.py`: migrate only the two local gateway method/observation shapes.
  Keep every image assertion and test name unchanged: ordered resolved paths, cue,
  visible Markdown, rejection before admission, storage safety, and serving behavior.
- `test_minds.py`: migrate every direct human gateway call and observation assertion.
   Add causal-use proofs for initial lost winner, stale recovery/rotation, literal
  `/new` forced-winner/reuse, dormant message recovery, dormant command recovery
  (including alias), and effective-key image attachment. Preserve exact JSON-RPC method order,
  consequence ownership, context acknowledgement, interrupt, restart/no-replay,
  child-role isolation, command ordering, and image cleanup. All Employee/session
  manager tests stay on their current contracts.
- `test_automatic_employee_step_eligibility.py`: extend the complete decision table to
  eight factors with a running human- or worker-origin Chat turn. Assert settled
  complete/errored/interrupted turns are not blockers, the function signature is
  unchanged, and the claim writer still contains no `chat_turns` query or private
  pre-status rule.
- `test_automatic_employee_step_discovery_loop.py`: add the smallest read-only proof
  that an otherwise eligible current-day Ticket with an active Chat turn is not passed
  to the runner. Start the real loop with a short interval while the turn is active,
  observe the first ineligible poll, settle the turn without calling `wake()`, and
  assert a later periodic-timer poll passes the Ticket to the recording runner.
- `test_employee_step_runner.py`: add `active_chat_turn` to the existing stale-
  discovery conjunct matrix. The final claim must return before status event, worker
  Chat projection, gateway call, prompt, or wake. Keep the claim seam unchanged.

No e2e or frontend source should need migration because the public HTTP/JSON shape is
unchanged. `test_live_chat_state.py` and both image suites are immutable preservation
contracts; a changed visible assertion is a stop-and-review condition.

### Preservation suites

Run, without editing them merely to accommodate a new result:

- complete `tests/unit/test_chat_images.py` and `tests/e2e/test_chat_images.py`;
- complete `tests/e2e/test_live_chat_state.py` for remount, activity, Pause, late
  completion, and worker projection;
- command/session/context coverage in `test_chat_commands.py`, `test_minds.py`, and
  `test_minds_sessions.py`;
- authorization/hosted ingress in `test_authctx_routes.py` and
  `test_trusted_ingress.py`;
- automatic and revision separation in `test_employee_step_runner.py`,
  `test_return_for_revision.py`,
  `test_automatic_employee_step_eligibility.py`, and
  `test_automatic_employee_step_discovery_loop.py`, including the eighth factor at
  discovery and final claim and the no-wake/timer behavior; and
- affected browser command/message flows in complete `test_flows_a.py` plus the two
  complete Chat browser files above.

## Production deletion list and static guards

Delete, not alias:

- `ChatStreamChunk` and every constructor/import/field consumer;
- `GatewayAdapter.stream` and all five concrete implementation methods;
- optional `on_session_key` on the human operation and all human session chunks;
- top-level `start_human_turn`, `pause_turn`, `_run_human_turn`, `_unix_now`, and
  `_reject_if_ticket_worker_running`;
- alternate lifecycle class/method names and a human-only settlement boundary; the
  retained exact class method is AST-distinguished from the deleted top-level
  `start_human_turn`;
- human use of `chat_data.attach_session_key`, `finish_turn`, and `fail_turn`;
- now-unused `chat_data.set_turn_activity`; and
- the four old `_stream_*` private helper names listed above.

AST/static checks are path- and class-specific. They must not flag
`Request.stream()` in file upload, `GatewayChild.send`, CLI HTTP `send`, WebSocket
streams, `SharedGateway.run_ticket_step`, worker `on_session_key`, or runtime/discovery
threads. The guard permits exactly one human-turn daemon constructor inside the named
owner method and no other human execution launcher. A separate guard proves
`claim_automatic_employee_step` contains no `chat_turns` read or partial eligibility
condition, while the sole eligibility function contains the active-turn query. The
`force_fresh_session` request surface is absent; production sets it only from exact
command `/new` when constructing `_AdmittedHumanChatTurn`.

## Documentation

- `docs/chat.md`: replace “service consumes the gateway stream” with the one deep
  `ChatTurnLifecycle`: one human request, atomic visible admission, pre-delivery
  effective-key binding, typed progress, and first-wins settlement. Explain that
  `pause_active_turn` controls either origin's visible session without settling the
  Ticket. Preserve image, forced-fresh `/new`, restart/rotation, command catalogue,
  Chief shell, and the warning that a Panels row is not Employee delivery.
- `docs/systems.md` and `docs/systems.html`: keep the synchronized Chat/Gateway
  sections aligned. Show `ChatTurnLifecycle` as the single coordinator, explain
  that every Hermes human write uses the key returned by atomic binding, and keep the
  `EmployeeStepRunner -> run_ticket_step` lane separate. Replace the current
  session-key “special writer friction” with the resolved owner/data boundary; retain
  gateway-bootstrap friction because production still replaces app-state adapters.
  Update their Automatic Employee-step decision lists from seven to the same eight
  factors and state the no-wake/periodic-timer settlement behavior.
- `docs/employee-runtime.md`: change the complete decision from seven to eight facts,
  naming “no active Panels Chat turn” as the eighth. State that both discovery and
  final claim consume it, Chat completion/error/Pause do not wake eligibility, and the
  SQLite-backed periodic timer observes eligibility after settlement.
- Do not edit AD09's explicit Employee-history/visible-Chat separation language.

## Bounded changed-path allowlist

Only these paths may change during implementation:

```text
src/planner/chat/api.py
src/planner/chat/contracts.py
src/planner/chat/data.py
src/planner/chat/service.py
src/planner/core/adapters/base.py
src/planner/core/adapters/fakes.py
src/planner/core/adapters/real.py
src/planner/core/server.py
src/planner/minds/shared_gateway.py
src/planner/runtime/automatic_employee_step_eligibility.py # eighth factor only
tests/unit/test_human_chat_turn.py                       # add
tests/unit/test_chat_ingress_contract.py
tests/unit/test_chat_seed.py
tests/unit/test_chat_commands.py
tests/unit/test_chat_activity.py
tests/unit/test_chat_images.py                           # adapter-shape migration only
tests/unit/test_minds.py
tests/unit/test_automatic_employee_step_eligibility.py   # eighth-factor table/static guard
tests/unit/test_automatic_employee_step_discovery_loop.py # discovery + timer observation
tests/unit/test_employee_step_runner.py                  # final-claim stale-factor case only
docs/chat.md
docs/employee-runtime.md
docs/systems.md
docs/systems.html
```

Other `src/planner/runtime/` files, `src/planner/tickets/data.py`,
`employee_step_runner.py`, session-manager internals, `worker_context/`, database
DDL/migrations, frontend source, `web/dist`, e2e files,
`test_return_for_revision.py`, `test_minds_sessions.py`, and `PROGRESS.md`/
`decisions.md` are inspected or preservation contracts, not edit targets.
If the owner declarations, transaction design, or tests require a schema/public/
visible change or another path, stop for orchestrator review rather than widening the
allowlist during implementation.

## Implementation order

1. Add `test_human_chat_turn.py` and update the exact AD05 static guard to the new
   declarations/deletion rules. Extend eligibility, discovery, and final-claim tests
   with the active-Chat factor. Record RED for the old owner, chunk, method, callback,
   atomic race, forced `/new`, cross-origin Pause, and eighth-factor behavior.
2. Add the closed observation contracts, binder type, `run_human_turn` protocol, and
   exact `_AdmittedHumanChatTurn` plus `ChatTurnLifecycle` skeleton. Wire server/app-
   state and thin API calls. At this
   point type/static failures enumerate every old consumer; do not add shims.
3. Add the active-turn conjunct to the sole eligibility function, leaving discovery
   and claim consumers unchanged. Implement admission transaction primitives, atomic
   ordinary/forced session binding, and `settle_chat_turn`. Make focused owner/data/
   eligibility tests green before transport rewiring.
4. Rewire Echo/Offline/Real/EntityRouting/Shared gateways. Restructure initial and
   dormant message/command binding before writes, then migrate all direct gateway
   tests to typed observations. Make the actual-Hermes-session assertions green.
5. Migrate state/command/activity/image tests through the public owner, prove worker-
   origin Pause, and remove all old symbols/helpers. Run Employee/session preservation.
6. Update Chat/systems plus the employee-runtime factor list. Run formatting, typing, frontend no-diff
   checks, complete image/live-Chat/browser preservation, changed-path audit, and
   `git diff --check`. Do not run `./verify`; the orchestrator runs the one canonical
   gate after independent implementation review.

## Focused commands

```bash
.venv/bin/pytest \
  tests/unit/test_human_chat_turn.py \
  tests/unit/test_chat_ingress_contract.py \
  tests/unit/test_automatic_employee_step_eligibility.py \
  tests/unit/test_automatic_employee_step_discovery_loop.py \
  tests/unit/test_employee_step_runner.py

.venv/bin/pytest \
  tests/unit/test_chat_seed.py \
  tests/unit/test_chat_commands.py \
  tests/unit/test_chat_activity.py \
  tests/unit/test_chat_images.py \
  tests/unit/test_minds.py

.venv/bin/pytest \
  tests/unit/test_minds_sessions.py \
  tests/unit/test_employee_step_runner.py \
  tests/unit/test_return_for_revision.py \
  tests/unit/test_automatic_employee_step_eligibility.py \
  tests/unit/test_automatic_employee_step_discovery_loop.py \
  tests/unit/test_authctx_routes.py \
  tests/unit/test_trusted_ingress.py

.venv/bin/ruff check \
  src/planner/chat \
  src/planner/core/adapters \
  src/planner/core/server.py \
  src/planner/minds/shared_gateway.py \
  src/planner/runtime/automatic_employee_step_eligibility.py \
  tests/unit/test_human_chat_turn.py \
  tests/unit/test_chat_ingress_contract.py \
  tests/unit/test_chat_seed.py \
  tests/unit/test_chat_commands.py \
  tests/unit/test_chat_activity.py \
  tests/unit/test_chat_images.py \
  tests/unit/test_minds.py \
  tests/unit/test_automatic_employee_step_eligibility.py \
  tests/unit/test_automatic_employee_step_discovery_loop.py \
  tests/unit/test_employee_step_runner.py
.venv/bin/mypy src/ tests/typing/

npm --prefix web run check
npm --prefix web test
npm --prefix web run build
git status --short web/dist

.venv/bin/pytest tests/e2e/test_chat_images.py
.venv/bin/pytest tests/e2e/test_live_chat_state.py
.venv/bin/pytest tests/e2e/test_flows_a.py

git diff --check
git diff --name-only
```

The two image files and live-Chat browser file run in full without `-k`; no generated
frontend diff is expected or allowed. The subsequent full `./verify` is the
orchestrator's completeness claim, not part of this delegated implementation cycle.

## Explicit AD07–AD09 and other exclusions

- **AD07:** no managed-Markdown render/edit/preview identity or teardown change.
- **AD08:** no resource catalogue, event mapping, cache, refresh, frontend, or built
  asset change.
- **AD09:** no change to `history`, `state`'s empty-Panels fallback,
  `_legacy_state_messages`, Hermes-history normalization, restart history recovery, or
  explicit Employee session-history access. History rotation continues through its
  existing dedicated persistence path for now.
- No database migration/schema/status, prompt queue, retry/timeout policy, thread
  registry, scheduler, journal, recovery daemon, generic workflow/session framework,
  or compatibility alias.
- `start_human_turn`/`run_human_turn` are not used by automatic or revision Employee
  delivery, and no Panels Chat row is treated as Employee/model context. Cross-origin
  `pause_active_turn` remains visible session control only.

## Delegated judgments

1. **Active Chat is the eighth complete eligibility factor.** The amended AD06
   decision explicitly supersedes only AD03's seven-factor enumeration. Discovery and
   final claim continue asking the same function; the claim writer stays free of a
   private condition. Settlement sends no wake, so SQLite plus the canonical polling
   timer observe the newly eligible Ticket.
2. **Pause is cross-origin visible-turn control.** `start_human_turn` owns only human
   admission/execution, while `pause_active_turn` may interrupt either origin's active
   Panels session. It settles Chat only; Ticket status, Employee delivery, and Employee
   settlement remain outside the class.
3. **Exact `/new` forces only its first bind.** The owner derives a private flag from
   exact command `/new`; that first fresh candidate replaces a concurrent entity key
   and is the Hermes handle used. Clearing the flag after successful binding restores
   ordinary CAS/adopt-winner behavior and preserves AD05's lost-first-write correction.
4. **The app owner uses a provider, not a captured gateway.** Production and tests
   both replace `app.state.adapters` after app construction. Resolving at lifecycle
   call time preserves that composition while still giving one execution one gateway
   owner.
5. **One private raw daemon remains.** The ticket forbids another scheduler or thread
   registry, while a server-owned asynchronous turn still needs a background
   execution. The lifecycle's one private launcher is the owned mechanism; static
   coverage forbids any parallel human launcher.
6. **History persistence remains deliberately separate.** Explicit/history-fallback
   reads can still observe and persist a rotated key until AD09. Pulling those reads
   into the human lifecycle or deleting their fallback here would violate the
   ticket's sequencing and make AD09 non-isolated.
