# P1 Implementation Plan (rev 2) — Extract `EmployeeChildRegistry`; relay rides it; flag-on unchanged

Revised per Codex plan review (8 findings) and the orchestrator's target-architecture decision.
This rev replaces the "pool builds the on_frame callback" shortcut with the real shared seam: the
**registry owns each child and emits frames; the consumer subscribes**. All file:line anchors are
against the current tree.

Binding constraints (unchanged): the flag-on tests construct `EmployeeChildPool` **and**
`RawFrameChildTransport` directly, reach into pool/reader privates, and may not be edited. Every new
seam is additive behind frozen signatures.

---

## 0. Reshaped boundary — registry owns, consumer subscribes

```
   minds/ (provider-neutral)                         hermes_backend/ (relay consumer)
   ─────────────────────────                         ────────────────────────────────
   EmployeeChildRegistry
     · owns each child (spawn via reader_factory,     EmployeeChildPool  ── is the ──▶ ChildFrameSubscriber
       generation counter, no-reap, 1-deadline           (thin adapter)                (attach/deliver/fold/
       shutdown-all)                                                                     on_dead/detach/detach_now)
     · sole issuer of session.create/resume/           · builds a default reader_factory that news up
       close/interrupt via reader.request()              the concrete RawFrameChildTransport
     · owns per-frame dispatch order sink1→2→3         · keeps TurnSubmission + submit_step_prompt
     · exposes request_on() for the pool's             · proxies its old privates to the registry
       prompt.submit
        │  depends on Protocols only (no hermes_backend import)
        ├── ReaderFactory (Callable) ──news up──▶ RawFrameChildTransport
        ├── ChildReader (Protocol)                  · gains request()/reply + pre-send id hook (C1/C2)
        └── ChildFrameSubscriber (Protocol)         · frame dispatch: on_deliver → settle_responders → on_fold
```

Per-frame order is preserved **exactly** as today (Finding 2 — do NOT reorder):
**sink1** subscriber DELIVER (relay-deliver, queued on the loop) → **sink2** the reader's own
session-RPC responder settle → **sink3** subscriber FOLD (step-fold). The welded `on_frame`
(`employee_child_pool.py:384-411`) is split into two subscriber phases (deliver, fold) so the
reader's own responder settle slots between them, byte-unchanged.

Why the registry can own children yet stay free of any `hermes_backend` import: it depends only on
Protocols/Callables it declares in `minds/`; the concrete `RawFrameChildTransport` is newed up by an
injected `reader_factory` (Finding 3), and the relay wiring lives entirely behind the injected
`ChildFrameSubscriber`. The registry never names the relay or the transport.

---

## 1. Neutral error contract (Finding 7)

The registry is in `minds/` and must not reference `RawFrameTransportError` (hermes_backend).

- **NEW `ChildReaderError(Exception)`** in `minds/employee_child_registry.py`, next to `ChildReader`.
- In `raw_frame_transport.py`: `class RawFrameTransportError(ChildReaderError)` — import
  `ChildReaderError` from `minds/` (hermes_backend → minds is the allowed direction). All existing
  `raise RawFrameTransportError(...)` sites keep their **exact message strings**, so
  `pool_step_gateway._is_busy` still matches `str(BUSY_CODE)` in the message
  (`pool_step_gateway.py:172-176`; the busy string is produced in `request()` §5).
- The registry **catches** `ChildReaderError` where the pool caught `RawFrameTransportError`
  (`interrupt_live_turn`, `employee_child_pool.py:753`), and **raises** `ChildReaderError` for its
  own validation failures (the `_create_or_resume_session` "no non-empty stored_session_id" case,
  `:536`). The reader keeps raising the `RawFrameTransportError` subclass for transport failures
  (ready timeout, RPC error, child died), so tests asserting `pytest.raises(RawFrameTransportError)`
  on ready/create failure still pass — those raises originate in the reader
  (`test_hermes_backend_pool.py:232,246`).
- **Move `CHILD_CLEANUP_BUDGET_SECONDS`** (`employee_child_pool.py:42` = `SHUTDOWN_GRACE_DEFAULT`)
  into the registry as the lifecycle constant used by `_shutdown_transport_bounded` (`:816-817`).
  No test imports it (verified).

Because `RawFrameTransportError` subclasses `ChildReaderError`, both `except ChildReaderError`
(registry) and `except RawFrameTransportError` (hermes_backend: `pool_step_gateway.py:62,85`;
`submit_step_prompt`) keep firing on the reader's raises.

---

## 2. `ChildReader` Protocol — honest per C1/C6 (Finding 4)

Declared in `minds/employee_child_registry.py`. Honest about the whole interface — frame
subscription, send, and death signal alongside request/lifecycle:

```python
class ChildReader(Protocol):
    def register_frame_sinks(
        self, *,
        on_deliver: Callable[[JsonDict], None],
        on_fold: Callable[[JsonDict], None] | None,
        on_dead: Callable[[], None],
    ) -> None: ...                                              # the ordered 2-phase subscription (C6)
    def start_reading(self) -> None: ...
    def wait_ready(self, timeout: float) -> None: ...
    def request(
        self, method: str, params: JsonDict, *,
        timeout: float, on_request_id: Callable[[int], None] | None = None,
    ) -> JsonDict: ...                                          # request/reply (C2) + pre-send id hook (C1)
    def enqueue_frame(self, frame: JsonDict) -> None: ...       # "send"
    def shutdown(self, *, deadline: float) -> None: ...
    @property
    def alive(self) -> bool: ...
    @property
    def dead_event(self) -> threading.Event: ...               # death signal
```

`register_frame_sinks` is the honest subscription surface. `RawFrameChildTransport.__init__` keeps
its frozen kwargs and forwards to it internally: `on_frame` (frozen) → `on_deliver`, plus the new
optional `on_fold`, plus `on_dead` (§5). So the frozen tests wire sinks via the constructor while the
Protocol still declares the subscription; the registry's `reader_factory` constructs with the sinks
(which forward to `register_frame_sinks`) and never calls the method post-hoc.

GatewayChild-satisfiable in P2 (sanity check, no edit): it already has `request`
(`minds/gateway.py:326`), `wait_ready` (`:316`), `shutdown` (`:392`), `alive` (`:409`), and a death
`Event` (`_dead`, `:243`). P2 adds `on_request_id` to its `request` (id at `:344`), a two-phase
`register_frame_sinks` adapter over its session-event ingress + pending table, `start_reading`
(currently starts in `__init__` at `:251`), and `enqueue_frame`. None is raw-frame-specific.

---

## 3. The two injected seams the registry depends on

### 3.1 `ReaderFactory` (Callable) — news up the concrete reader (Finding 3)

```python
ReaderFactory = Callable[..., ChildReader]
# invoked as:
#   reader_factory(generation=int, employee_entity_id=str, env=Mapping[str,str],
#                  on_deliver=Callable, on_fold=Callable, on_dead=Callable) -> ChildReader
```

Construct-only (no relay register — that is `attach`, §3.2, so the R3-B slot publish can slot before
register; see §4). The default factory (pool-provided) news up `RawFrameChildTransport` with the
three sinks forwarded and `allocate_request_id` bound to the relay's per-child counter (§5.2). This
Callable is the "frame subscription (deliver/fold)" surface the registry actually depends on — the
deliver/fold sinks are explicit in its signature.

### 3.2 `ChildFrameSubscriber` (Protocol) — the relay consumer (Finding 2 + F8)

```python
class ChildFrameSubscriber(Protocol):
    def attach(self, *, generation: int, employee_entity_id: str, reader: ChildReader) -> None: ...
    def deliver(self, *, generation: int, employee_entity_id: str, frame: JsonDict) -> None: ...   # sink1
    def fold(self, *, generation: int, employee_entity_id: str, frame: JsonDict) -> None: ...       # sink3
    def on_dead(self, *, generation: int, employee_entity_id: str) -> None: ...
    def detach(self, *, generation: int) -> None: ...       # on-loop unregister (F8: sites 475/646)
    def detach_now(self, *, generation: int) -> None: ...   # sync unregister   (F8: sites 373/848)
```

The pool implements this (§6). `attach` = `relay.register_child`; `deliver` = the loop-queued
`relay.deliver_child_frame` (sink1); `fold` = the step-fold (`observe_ack_frame`/`fold_frame`,
sink3); `on_dead` = loop-queued `relay.deliver_child_death` + `TurnSubmission.fold_dead`; the two
detach variants are the **exact** F8 split (see §7). The registry holds one subscriber and wires
per-child closures binding `generation`/`employee_entity_id` into the reader via the factory. In P2
the legacy consumer is a second `ChildFrameSubscriber` its own registry instance is built with — the
seam the reviewer wants established.

---

## 4. `EmployeeChildRegistry` — API, state, and per-frame dispatch

`minds/employee_child_registry.py`, keyed by `employee_entity_id`. Loop-free and relay-free (all
loop/relay scheduling is behind the subscriber).

### Constructor

```python
def __init__(
    self, *,
    reader_factory: ReaderFactory,
    subscriber: ChildFrameSubscriber,
    identity_env_strategy: Callable[[str], dict[str, str]],   # C3 — injected
    session_source: str,                                       # F6 — REQUIRED (no default)
    session_cols: int,                                         # F6 — REQUIRED
    on_stored_session_bound: Callable[[str, str, str | None], None] | None = None,
    stored_session_resolver: Callable[[str], str | None] | None = None,
    ready_timeout: float = READY_TIMEOUT_DEFAULT,
    request_timeout: float = REQUEST_TIMEOUT_DEFAULT,
) -> None: ...
```

### State (lifted from the pool)

`_lock`, `_records`, `_init_slots`, `_generation_counter`, `_stored_session_id_by_employee`,
`_live_session_id_by_employee`, `_rebind_locks`/`_rebind_locks_guard`, `_closing`, `_ready_timeout`,
`_request_timeout`, `_init_executor`, `_shutdown_executor` — all lifted verbatim from
`employee_child_pool.py:263-291`. `_InitSlot` (`:63-71`) moves too, `.transport` typed
`ChildReader | None`. `EmployeeChildRecord` (`:53-61`) moves; its `transport` field is retyped
`ChildReader` but **keeps the name** `transport` (tests read `record.transport.*`). The responder
registry does **not** live here — it folds into the reader (§5).

### Public methods → lifted pool code

| method | lifts | change |
| --- | --- | --- |
| `init_executor`/`shutdown_executor` (props) | `:293-299` | — |
| `get_or_spawn(employee)` | `child_for_employee` `:317-375` | `RawFrameChildTransport(...)` → `reader_factory(...)`; `register_child` → `subscriber.attach`; closing-race `unregister_child` (`:373`, sync) → `subscriber.detach_now` |
| `adopt_stored_session` | `:303-313` | verbatim |
| `rebind_fresh_session` | `:542-610` | `_transport_request(record.transport,gen,"session.close"/"session.create",…)` → `record.transport.request(...)`; discard-path retire (`:646`, on-loop) → `subscriber.detach` |
| `live_session_id_for` | `:727-731` | verbatim |
| `interrupt_live_turn` | `:733-755` | `_transport_request(… "session.interrupt" …)` → `record.transport.request(...)`; `except RawFrameTransportError` → `except ChildReaderError` |
| `shutdown(*, deadline)` | `:819-855` | in-progress `unregister_child` (`:848`, sync) → `subscriber.detach_now` |

**No `request_on` (Codex round-2 blocker A).** rev-2 had introduced a registry `request_on(employee, …)`
that re-resolved the reader by employee — that reopens the proven N/N+1 rebind race
(`test_hermes_backend_new_conversation.py:235`): the runner holds record N and N's live session id, but a
concurrent rebind/new-conversation could resolve N+1, sending N's session id through the wrong reader; and a
rebind-cleanup that removed the record would raise a bare `ChildReaderError` that bypasses
`pool_step_gateway`'s `except RawFrameTransportError`. Dropped entirely. `submit_step_prompt` issues on the
**captured** `record.transport` directly (§6.1) — exactly the original `_transport_request(record.transport, …)`
behavior: generation-stable, and the reader raises the `RawFrameTransportError` subclass on error/busy/death.
The registry's session-lifecycle RPCs (create/resume/close/interrupt) still go through `reader.request()`
internally; only the pool's `prompt.submit` is issued by the pool on the record it already holds.

### Private methods

`_spawn_and_bind` (`:377-482`), `_create_or_resume_session` (`:497-538`, using `session_source`/
`session_cols`), `_notify_stored_session_bound` (`:656-667`), `_discard_child_after_rebind_persist
_failure` (`:612-646`), `_rebind_lock_for` (`:648-654`), `_shutdown_transport_bounded` (`:816-817`)
— all lifted. The `on_frame`/`on_dead` closures (`:384-422`) do **not** live here; the registry
instead builds three thin closures that call the subscriber:

```python
def _spawn_and_bind(self, employee_entity_id, slot):
    with self._lock:
        self._generation_counter += 1
        generation = self._generation_counter
    env = self._identity_env_strategy(employee_entity_id)
    reader = self._reader_factory(
        generation=generation, employee_entity_id=employee_entity_id, env=env,
        on_deliver=lambda f: self._subscriber.deliver(
            generation=generation, employee_entity_id=employee_entity_id, frame=f),
        on_fold=lambda f: self._subscriber.fold(
            generation=generation, employee_entity_id=employee_entity_id, frame=f),
        on_dead=lambda: self._subscriber.on_dead(
            generation=generation, employee_entity_id=employee_entity_id),
    )
    with self._lock:                                   # R3-B: publish partial + closing recheck
        if self._closing: closing_now = True
        else: closing_now = False; slot.transport = reader; slot.generation = generation
    if closing_now:
        self._shutdown_transport_bounded(reader); raise ChildRegistryClosing("child registry is closing")
    registered = False
    try:
        self._subscriber.attach(generation=generation, employee_entity_id=employee_entity_id, reader=reader)
        registered = True
        reader.start_reading()
        reader.wait_ready(self._ready_timeout)
        live, stored, created_fresh = self._create_or_resume_session(reader, employee_entity_id, generation)
        if created_fresh:
            self._notify_stored_session_bound(employee_entity_id, stored, None)   # C5
        with self._lock:
            self._stored_session_id_by_employee[employee_entity_id] = stored
            self._live_session_id_by_employee[employee_entity_id] = live
    except BaseException:
        self._shutdown_transport_bounded(reader)
        if registered:
            self._subscriber.detach(generation=generation)   # on-loop retire (F8: site 475)
        raise
    return EmployeeChildRecord(employee_entity_id, reader, generation, stored)
```

Everything except the closure/attach/detach substitution is byte-identical to `:377-482`. The gating
test (`test_hermes_backend_pool.py:304-344`) still parks inside `relay.register_child` (now reached
via `subscriber.attach`) **after** the partial `slot.transport` publish, so shutdown finds and tears
the partial reader → `start_reading` raises → bounded abort.

### Per-frame dispatch order (Finding 2 — the fix)

Order is enforced **inside the reader** (§5.3): `on_deliver` (sink1) → own responder settle (sink2)
→ `on_fold` (sink3). This restores the original `sink1 → sink2 → sink3` order the reviewer requires
(my rev-1 reorder is discarded). The deterministic proof
`test_hermes_backend_relay_routing.py:463-536` — which tokens `deliver` vs the monkeypatched
`_PoolSessionResponder.observe` and asserts `error_deliver_token < observe_token` — passes because
`on_deliver` (queuing `deliver_child_frame` on the wrapped loop) runs before the reader settles its
responders. `identity_env` (C3), `session_source`/`session_cols` (F6), no-reap, and one-deadline
shutdown-all (C4) are as in §3/§4/§7.

---

## 5. Fold request/reply into `raw_frame_transport.py` (C1 + C2 + Finding 1)

### 5.1 `_PoolSessionResponder` keeps its identity (Finding 1)

`test_hermes_backend_relay_routing.py:475-536` monkeypatches
`employee_child_pool._PoolSessionResponder.observe` and reads `self._request_id`/`self.done` on the
patched instance. So the class the reader instantiates **must be the same object** exported as
`employee_child_pool._PoolSessionResponder`:
- **Define `_PoolSessionResponder` in `raw_frame_transport.py`** (moved verbatim from
  `employee_child_pool.py:74-88` — same `_request_id`, `done`, `frame`, `observe`).
- **Re-export from the pool:** `from planner.hermes_backend.raw_frame_transport import
  _PoolSessionResponder`. Then `employee_child_pool._PoolSessionResponder` IS the class the reader
  creates; patching its `observe` on the class object affects the reader's live instances. Confirmed
  against the test: `patched_observe` sets `observe_token` when a live responder matches the error
  id — which only happens if the running responder is this class.

### 5.2 `request()` in the reader (C1/C2)

Add to `RawFrameChildTransport`:
- ctor `allocate_request_id: Callable[[], int] | None = None` — **optional** (the frozen
  constructions at `test_hermes_backend_verbatim_order.py:98,180,273` and
  `test_hermes_backend_child_death.py:227` pass no such arg and never call `request()`).
- `_responders: list[_PoolSessionResponder]` + `_responders_lock` (one reader == one child; no
  generation key, unlike the pool's per-generation dict `:283-284`).
- `request()` = `_transport_request` `:766-812` verbatim except `rid = self._allocate_request_id()`
  (was `self._relay.next_child_request_id(generation)`) and the flat responder list. Message strings
  unchanged (busy match preserved). Raises the `RawFrameTransportError` subclass on rpc error / child
  death / timeout (unchanged).
- **Optional allocator normalization (Codex round-2 should-fix).** `allocate_request_id` is typed
  `Callable[[], int] | None`, so `request()` must not call it unguarded (strict mypy rejects an
  Optional call; runtime `TypeError` if `None`). At the top of `request()`: `alloc =
  self._allocate_request_id;` `if alloc is None: raise RawFrameTransportError("request() called on a
  reader constructed without an id allocator")`, then `rid = alloc()`. The frozen direct constructions
  never call `request()`, so this guard is unreachable for them and preserves their behavior.

### 5.3 Dispatch: `on_deliver` → settle responders → `on_fold` (preserves sink1→2→3)

`_stdout_loop` (`raw_frame_transport.py:127-148`) keeps ready-gate handling; its per-frame tail
becomes:

```python
self._on_deliver(frame)          # sink1 — subscriber DELIVER (relay-deliver, queued on loop)
self._settle_responders(frame)   # sink2 — our own request/reply responders (observe-not-consume)
if self._on_fold is not None:
    self._on_fold(frame)         # sink3 — subscriber FOLD (step-fold)
```

- `_settle_responders(frame)` snapshots `self._responders` under the lock and calls `r.observe(frame)`
  on each — verbatim of the pool `on_frame` sink2 (`:393-396`). This is where the monkeypatched
  `observe` runs, **after** `on_deliver` (sink1) — satisfying the ordering proof (§4).
- `register_frame_sinks(on_deliver, on_fold, on_dead)` stores the three sinks; the constructor
  forwards `on_frame → on_deliver`, `on_fold`, `on_dead`. Frozen tests pass `on_frame` only →
  `on_fold=None` → sink3 skipped; `on_deliver` still receives every frame in emission order, so the
  verbatim-order collectors (`test_hermes_backend_verbatim_order.py:107-168`) see
  `ready,e1,e2,response,e3` unchanged.
- `_mark_dead` (`:192-202`) calls `self._on_dead()` (unchanged) and does **not** wake responders —
  `request()` polls `dead_event` (unchanged). `enqueue_frame`, `wait_ready`, `shutdown`,
  `start_reading` untouched.

### 5.4 The id space stays the relay's (C1 / id coupling)

`allocate_request_id = lambda: relay.next_child_request_id(generation)` is bound by the default
reader_factory (§6). It is first invoked at the spawn-time `session.create/resume`, **after**
`subscriber.attach` (= `register_child`), so the binding exists (`employee_child_relay.py:167`
KeyErrors otherwise). This keeps the pool's session RPCs and the relay's downstream forwards on one
per-child counter — `test_hermes_backend_relay_ids.py:121-146` (`req_ids == [1,2,3]`) is the
regression guard. This invariant is unchanged from rev 1 and remains the single most important one.

---

## 6. `EmployeeChildPool` — subscriber + default factory + proxies (Finding 3)

Frozen constructor (`:236-250`) plus **one optional additive kwarg** (Finding 3):

```python
def __init__(self, *, hermes_python, planner_home, base_env, relay, loop, spawn=spawn_popen,
             chief_entity_id=CHIEF_OF_STAFF_ENTITY_ID, ready_timeout=READY_TIMEOUT_DEFAULT,
             request_timeout=REQUEST_TIMEOUT_DEFAULT, on_stored_session_bound=None,
             stored_session_resolver=None,
             reader_factory: ReaderFactory | None = None):   # composition MAY inject (P2)
```

The pool:
1. **Keeps** `TurnSubmission` (`:91-233`), `_turn_submissions`/`_turn_submissions_lock`
   (`:266-267`), `register_turn_submission`/`unregister_turn_submission`/`_turn_submission_for`
   (`:671-692`), `submit_step_prompt` (`:694-725`). `submit_step_prompt` issues directly on the
   **captured** record's reader (Codex round-2 blocker A): `record.transport.request("prompt.submit",
   {"session_id": live_session_id, "text": text}, timeout=self._request_timeout,
   on_request_id=submission.expect_ack)` — the exact analogue of the original
   `_transport_request(record.transport, record.child_generation, "prompt.submit", …)`. It never
   re-resolves by employee, so a concurrent rebind to N+1 cannot redirect the prompt; a dead captured
   child raises the reader's `RawFrameTransportError` (subclass), still caught by `pool_step_gateway`.
   It keeps its own `self._request_timeout`. Signature unchanged (pool_step_gateway calls it as-is).
2. **Builds** the registry, injecting itself as the subscriber + the default (or injected) factory:
   ```python
   self._registry = EmployeeChildRegistry(
       reader_factory=reader_factory or self._default_reader_factory,
       subscriber=self,
       identity_env_strategy=self._build_identity_env,     # the old _env_for_employee body
       session_source=RELAY_SESSION_SOURCE, session_cols=SESSION_COLS,   # F6 (still pool constants)
       on_stored_session_bound=on_stored_session_bound,
       stored_session_resolver=stored_session_resolver,
       ready_timeout=ready_timeout, request_timeout=request_timeout,
   )
   ```
   Passing a half-built `self` is safe: no subscriber/factory method runs during construction.
3. **Is the `ChildFrameSubscriber`:**
   ```python
   def attach(self, *, generation, employee_entity_id, reader):
       self._relay.register_child(generation, employee_entity_id, reader)
   def deliver(self, *, generation, employee_entity_id, frame):
       self._loop.call_soon_threadsafe(self._relay.deliver_child_frame, generation, frame)  # sink1
   def fold(self, *, generation, employee_entity_id, frame):
       submission = self._turn_submission_for(employee_entity_id)                            # sink3
       if submission is not None:
           try:
               if "id" in frame: submission.observe_ack_frame(frame)
               else:             submission.fold_frame(frame)
           except BaseException: pass
   def on_dead(self, *, generation, employee_entity_id):
       self._loop.call_soon_threadsafe(self._relay.deliver_child_death, generation)
       submission = self._turn_submission_for(employee_entity_id)
       if submission is not None:
           try: submission.fold_dead()
           except BaseException: pass
   def detach(self, *, generation):      # F8 on-loop  (sites 475, 646)
       self._loop.call_soon_threadsafe(self._relay.unregister_child, generation)
   def detach_now(self, *, generation):  # F8 sync     (sites 373, 848)
       self._relay.unregister_child(generation)
   ```
   This is the literal C6 re-expression: registry emits ordered frames; the pool subscribes in two
   phases (deliver/fold) with the reader's responder settle between.
4. **Default reader_factory:**
   ```python
   def _default_reader_factory(self, *, generation, employee_entity_id, env, on_deliver, on_fold, on_dead):
       return RawFrameChildTransport(
           hermes_python=str(self._hermes_python), env=env,
           on_frame=on_deliver, on_fold=on_fold, on_dead=on_dead, spawn=self._spawn,
           allocate_request_id=lambda: self._relay.next_child_request_id(generation),
       )
   ```
5. **Delegates** lifecycle: `child_for_employee` → `registry.get_or_spawn`; `adopt_stored_session`,
   `rebind_fresh_session`, `live_session_id_for`, `interrupt_live_turn`, `shutdown` → same-name
   registry methods.
6. **Proxies its old privates** (frozen test reads, §10): `init_executor`/`shutdown_executor` →
   props; `_records`/`_stored_session_id_by_employee`/`_live_session_id_by_employee` → props
   returning the registry's live dict; `_closing`/`_ready_timeout` → props **with setter** proxying
   the registry.
7. **Drops** the moved code and adds the re-exports (§10). `pool_step_gateway.py` is **unchanged**;
   its `StepGateway` surface is byte-identical.

`submit_step_prompt` uses the captured `record.transport` directly (no registry `request_on`, no
by-employee re-resolution) — this is the round-2 blocker-A fix and is byte-for-byte the original's
target-transport discipline. The registry mediates only its own session-lifecycle RPCs, which it issues
on the reader it just spawned/holds; the pool's `prompt.submit` rides the record the caller already has.

---

## 7. C4 (lifecycle) + C5 (fail-closed persist) + the two retire methods (Finding 8)

**F8 retire split — exact sync-vs-loop parity per site:**
- `subscriber.detach` (on-loop `call_soon_threadsafe(relay.unregister_child, …)`): the FIFO cases
  where the retire must land after an already-queued `deliver_child_frame` — spawn except path
  (`:475`) and rebind-discard (`:646`).
- `subscriber.detach_now` (synchronous `relay.unregister_child(…)`): the closing/shutdown cases —
  `get_or_spawn` closing-race (`:373`) and `shutdown` in-progress slots (`:848`).

This corrects rev 1 (which routed `:848` through the loop). Sites verified: 373 sync, 475 loop, 646
loop, 848 sync. Green guards: `test_pool_ready_failure_shuts_down_partial_child`,
`test_pool_session_create_failure_shuts_down_partial_child` (relay-child unregistered on failure,
`test_hermes_backend_pool.py:226-253`), the shutdown suite (`:256-401`), and
`test_failed_session_rpc_response_is_visible_and_teed_before_binding_retire`
(`test_hermes_backend_relay_routing.py:463`).

**C4** — one-deadline shutdown-all + no-reap live in `EmployeeChildRegistry.shutdown`/`get_or_spawn`
(lifted `:819-855`/`:317-375`). The never-join-an-unstarted-thread guard stays in
`RawFrameChildTransport.shutdown`/`start_reading` (`:114-123`, `:225-258`) — untouched (green:
`test_hermes_backend_verbatim_order.py:198-313`).

**C5** — both persist sites stay inside the registry's guarded blocks: first-create
(`_spawn_and_bind`, `:453-461`) and rebind (`rebind_fresh_session` + the generation-guarded
`_discard_child_after_rebind_persist_failure`, `:588-646`, lifted verbatim). Green guards:
`test_first_create_persist_failure_leaves_no_live_owner`,
`test_rebind_persist_failure_tears_down_child_and_keeps_old_durable_key`,
`test_rebind_persist_failure_cleanup_spares_newer_generation`
(`test_hermes_backend_new_conversation.py:156-315`) — the last calls `pool.child_for_employee` from
inside the persist callback; it still works (`get_or_spawn` uses `_lock`/`_init_slots`, never
`rebind_lock`).

---

## 8. Relay type widening (Finding 5)

The relay calls only `binding.transport.alive` (`employee_child_relay.py:175`) and
`binding.transport.enqueue_frame` (`:336,350`) — both on `ChildReader`. So **widen**
`EmployeeChildRelay.register_child`'s `transport` param and `ChildBinding.transport` from
`RawFrameChildTransport` to `ChildReader` (import `ChildReader` from `minds/` — allowed direction),
resolving the `subscriber.attach(reader: ChildReader)` → `register_child(...)` mismatch with no cast.
`RawFrameChildTransport` satisfies `ChildReader`, so real calls type-check; the test stubs
(`_StubTransport`, `_DeadTransport`) remain non-full-ChildReader, so their existing
`# type: ignore[arg-type]` on `register_child` stays used (no `warn_unused_ignores` breakage — I
cannot edit tests). `next_child_request_id`/pending logic never touches `transport`, so nothing else
shifts. Confirm `mypy --strict` clean over `minds/` + `hermes_backend/` after the change.

---

## 9. Required `session_source`/`session_cols` (Finding 6)

Registry constructor takes them as **required** args (no default referencing a hermes_backend
constant). The pool passes `RELAY_SESSION_SOURCE`/`SESSION_COLS`, which **stay defined in
`employee_child_pool.py`** (`:40-41`; `test_hermes_backend_pool.py:18-19,165` import them and assert
`session.create` params). The registry stays honestly neutral (no "panels-relay" literal); P3 injects
the legacy values.

---

## 10. Composition + re-exports + frozen-surface audit

- **`composition.py`** — unchanged for P1 (builds the pool via the frozen ctor with no
  `reader_factory`; the pool builds the default registry). The seam for composition to inject a
  factory in P2 now exists (§6). The composition test (`test_hermes_backend_ticket_step
  _composition.py:76-96`) drives this path.
- **Re-exports added to `employee_child_pool.py`** (frozen imports must keep resolving): `PoolError`
  (= the registry's `ChildRegistryClosing`, Finding E), `_PoolSessionResponder` (from the reader,
  Finding 1), `EmployeeChildRecord` (from the registry), `INIT_EXECUTOR_MAX_WORKERS` (moved to the
  registry). `RELAY_SESSION_SOURCE`/`SESSION_COLS`/`TurnSubmission` stay defined in the pool.
  **Ruff F401 (Codex round-2 should-fix):** every re-export-only name the pool no longer uses
  internally must be the redundant-alias form `from … import X as X` (or listed in `__all__`), or Ruff
  flags it imported-but-unused. Applies to `_PoolSessionResponder`, `EmployeeChildRecord`,
  `INIT_EXECUTOR_MAX_WORKERS`, and `ChildRegistryClosing` (aliased to `PoolError`).
- **`PoolError` (Finding E, confirmed):** define `class ChildRegistryClosing(ChildReaderError)` in
  the registry; in the pool `PoolError = ChildRegistryClosing`. `get_or_spawn`/`shutdown` raise
  `ChildRegistryClosing`; `pytest.raises((RawFrameTransportError, PoolError))`
  (`test_hermes_backend_pool.py:283,298,339,359`) catches the same class object. No test asserts the
  message (verified — grep for "relay pool is closing": none), so the neutral message is fine.
- **Frozen-surface re-audit (per the directive) — complete list, all handled:**
  - Direct constructions: `EmployeeChildPool` (7 test files), `RawFrameChildTransport` (2 test
    files) — signatures frozen; only additive optional kwargs (`reader_factory`, `on_fold`,
    `allocate_request_id`).
  - Monkeypatched private: **only** `employee_child_pool._PoolSessionResponder.observe`
    (`test_hermes_backend_relay_routing.py:479,532,536`) — handled via the same-object re-export
    (§5.1). Grep for `monkeypatch|setattr|\.observe\s*=` across the flag-on set finds no other.
  - Read/written pool privates: `_records`, `_closing`, `_ready_timeout`,
    `_stored_session_id_by_employee`, `_live_session_id_by_employee` (proxied), `_turn_submissions`
    (stays a real pool attr).
  - Reader private: `transport._stdout_thread` (`test_hermes_backend_verbatim_order.py:213`) — the
    reader's thread lifecycle is **untouched** by P1, so it stays.
  - `record.transport` — field name preserved (type widened to `ChildReader`).
  - Module names from the pool: `EmployeeChildPool`, `TurnSubmission`, `PoolError`,
    `INIT_EXECUTOR_MAX_WORKERS`, `RELAY_SESSION_SOURCE`, `SESSION_COLS`, `_PoolSessionResponder` — all
    resolvable.

---

## 11. Test plan

### 11.1 New — `EmployeeChildRegistry` isolation (`tests/unit/test_employee_child_registry.py`)

Drive the registry directly with a fake `ReaderFactory` (returns a scriptable `FakeChildReader`) + a
recording `ChildFrameSubscriber` + a fake identity strategy + fake persist/resolver — no pool, no
relay. Assert: spawn-on-demand + single-child reuse; respawn-dead + resume-own-session; identity-env
strategy reaches the factory `env`; adopt beats resolver, resolver used only when no key held;
`session.create`/`resume`/`close`/`interrupt` originate here; **dispatch order** (the fake reader
records the order it invoked `on_deliver` → responder-settle → `on_fold` and the test asserts
sink1→sink2→sink3); fail-closed persist on first-create and rebind (raises, reader shut down,
`subscriber.detach`/`detach_now` called at the right sites); no-reap; one-deadline shutdown-all under
the deadline with a hung fake reader.

### 11.2 New — C1 mutation proofs (reader-level, `tests/unit/test_raw_frame_request_reply.py`)

- **Pre-send id hook (C1).** Spy `enqueue_frame`; pass an `on_request_id` that appends to a shared
  list; assert order `[("on_request_id", rid), ("enqueue", rid)]`. A mutation that sends before
  calling `on_request_id` flips it → fails.
- **sink1-before-sink2 (ordered delivery).** Script the fake child to emit event, then the RPC
  response, then event; register `on_deliver` collector; call `request()`. Assert that when
  `request()` returns, the collector already holds the response frame (deliver ran before the
  responder settled). A mutation settling the responder before `on_deliver` fails.

These plus the retained pool-level pre-ACK/queued tests (`test_pool_step_gateway.py:529-738`), the
deliver-before-observe ordering proof (`test_hermes_backend_relay_routing.py:463`), and the relay
id-space test (`test_hermes_backend_relay_ids.py:121`) form the C1 net.

### 11.3 Unchanged green guard + `./verify`

The full flag-on set (contract §Acceptance-1) with **zero edits**, then `./verify`.

---

## 12. Per-file edit list + landing order

1. **NEW `src/planner/minds/employee_child_registry.py`** — `ChildReaderError`,
   `ChildRegistryClosing`, `ChildReader`/`ChildFrameSubscriber`/`ReaderFactory`,
   `EmployeeChildRecord`, `_InitSlot`, `INIT_EXECUTOR_MAX_WORKERS`, `CHILD_CLEANUP_BUDGET_SECONDS`,
   `EmployeeChildRegistry`. Unused on creation → green.
2. **`raw_frame_transport.py`** — `RawFrameTransportError(ChildReaderError)`; move
   `_PoolSessionResponder` here; add `_responders`/`_responders_lock`/`request()`/
   `register_frame_sinks`; add optional `on_fold`/`allocate_request_id`; split `_stdout_loop` tail
   into `on_deliver → settle_responders → on_fold`. Additive; the pool still uses `_transport_request`
   and the reader's empty responder list keeps the split inert until cutover → green.
3. **`employee_child_pool.py`** — atomic cutover: build+hold the registry (subscriber=self, default
   factory), implement the `ChildFrameSubscriber`, identity closure, delegations, private proxies,
   the optional `reader_factory` kwarg, drop moved code, add re-exports. Green after.
4. **`employee_child_relay.py`** — widen `register_child`/`ChildBinding.transport` to `ChildReader`
   (Finding 5), type-only.
5. **`composition.py`** — no change (verify only).
6. **`pool_step_gateway.py`** — no change.
7. **NEW tests** (§11.1, §11.2); then `./verify`.

Steps 1–2 land green; step 3 is the atomic cutover; step 4 is type-only.

---

## 13. Findings resolution + residual flags

| Codex finding | resolution |
| --- | --- |
| **F1** monkeypatched `_PoolSessionResponder` | class moved to the reader, **re-exported same-object** from the pool; `_request_id`/`done`/`observe` preserved (§5.1). Verified against `relay_routing.py:475-536`. |
| **F2** don't reorder sink1→2→3 | dispatch split into `on_deliver` → responder-settle → `on_fold` **in the reader**, exact original order (§4/§5.3). |
| **F3** composition-injectable | optional `reader_factory` kwarg on the pool ctor (defaults to the pool's own); registry constructor is the reusable seam P2's legacy consumer subscribes to (§6). |
| **F4** honest `ChildReader` | Protocol now carries `register_frame_sinks` (deliver/fold), `enqueue_frame` (send), `dead_event` (death) + request/lifecycle (§2); GatewayChild-satisfiable checked. |
| **F5** register type mismatch | widen `relay.register_child`/`ChildBinding.transport` to `ChildReader`; relay uses only `.alive`/`.enqueue_frame` (§8); mypy-strict confirm. |
| **F6** required session params | `session_source`/`session_cols` REQUIRED on the registry; pool passes its constants (§9). |
| **F7** neutral error | `ChildReaderError` in `minds/`; `RawFrameTransportError` subclasses it; `CHILD_CLEANUP_BUDGET_SECONDS` moved; busy string preserved (§1). |
| **F8** two retire methods | `detach` (on-loop, 475/646) + `detach_now` (sync, 373/848), exact per-site parity (§3.2/§7). |
| **E** PoolError | `PoolError = ChildRegistryClosing` re-exported (§10). |

**Residual flags for Codex diff review:**
- **`register_frame_sinks` realization.** Modeled as a Protocol method the constructor forwards to,
  so the frozen constructor and the honest Protocol coexist. If Codex prefers the deliver/fold to be
  *only* factory params (not a Protocol method), that is a one-line drop — but it weakens F4's
  "in the Protocol" intent, so I kept the method.
- **`request_on` re-resolution — RESOLVED (Codex round-2 blocker A).** Dropped entirely.
  `submit_step_prompt` calls `record.transport.request(...)` on the captured record (§4/§6.1), so no
  by-employee re-resolution and no wrong-generation / bare-`ChildReaderError` path. This restores the
  original target-transport discipline exactly.
- **Optional allocator — RESOLVED (round-2 should-fix).** `request()` guards `allocate_request_id is
  None` and raises `RawFrameTransportError` before calling it (§5.2); no unguarded Optional call.
- **Ruff F401 re-exports — RESOLVED (round-2 should-fix).** Re-export-only names use the
  `import X as X` redundant-alias form / `__all__` (§10).
- **sink3 for the ACK now after sink2 (restored original).** The arm-on-ACK (`observe_ack_frame`)
  runs in `on_fold`, i.e. after the responder settle — exactly as the pre-refactor pool. The pre-ACK
  buffering / queued-skip accounting is entirely inside `TurnSubmission`, still fed in emission order
  on the single reader thread. Guarded by `test_pool_step_gateway.py:529-738`.
- **No scope creep:** `employee_child_relay.py` is touched only for the F5 type widening;
  `pool_step_gateway.py`, `minds/gateway.py`, and the legacy path are untouched; the `GatewayChild`
  adapter and legacy identity/session-source injection remain P2/P3.
