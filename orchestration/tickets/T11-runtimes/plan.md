# T11 implementation plan — dispatcher runtime, boundary scheduler, real adapters

Blueprint for the implementer. Follow verbatim. Files created/modified, exactly:
`src/planner/dispatch/runtime.py` (new), `src/planner/days/scheduler.py` (new),
`src/planner/core/adapters/real.py` (replace stub bodies only), `src/planner/core/loops.py` (new),
`tests/unit/test_runtimes.py` (new). Everything else — `base.py`, `fakes.py`, `registry.py`,
`dispatch/data.py`, `days/boundary.py`, `days/data.py`, conftest — is a frozen contract: import,
never edit. Sibling tickets T09/T10 are concurrently editing `core/server.py`, `core/ws.py`,
`core/authctx.py`, `core/testmode.py`, `days/api.py` — do not import from, depend on, or touch any
of those.

Pinned public seam (T09 calls these lazily by name; do not rename):
- `planner.dispatch.runtime.run_tick(conn_factory, config, clock, adapters) -> dict`
- `planner.days.scheduler.run_boundary_tick(conn_factory, config, clock, adapters) -> dict`
- `planner.core.loops.start_background_loops(config, clock, adapters, conn_factory)` → object with async `.stop()`
  (note the different parameter order on this one — it is pinned by the ticket).

`conn_factory` is `Callable[[], sqlite3.Connection]`; connections come from `core.db.connect`
(autocommit `isolation_level=None`, `sqlite3.Row` factory), so no explicit commits anywhere.

---

## 1. `src/planner/dispatch/runtime.py` (new)

**Module docstring gist:** "The dispatcher tick (§7.1). One synchronous tick: fail-safe
dispatch_enabled re-read, machine-wide advisory flock, reclaim sweep, run-timeout enforcement,
eligibility recompute, claim-then-spawn up to the concurrency cap. In test mode no code path ever
signals a real pid (fake pids may collide with live processes). The lock is acquired once and held
by the process; `release_dispatcher_lock` is the explicit release for loops.stop() and tests."

**Imports:** `from __future__ import annotations`; stdlib `fcntl`, `os`, `signal`, `sqlite3`,
`collections.abc.Callable`, `pathlib.Path`, `typing.Final`; project:
`planner.core.adapters.base.SpawnRequest`, `planner.core.adapters.registry.Adapters`,
`planner.core.clock.Clock`, `planner.core.config` (`HOST`, `Config`, `read_dispatch_enabled`),
`planner.core.contracts.JsonDict`, `planner.core.errors.PlannerError`,
`planner.dispatch.contracts.RunStatus`,
`planner.dispatch.data` (`claim`, `close_run`, `load_candidates`, `sweep_reclaims`),
`planner.dispatch.logic` (`is_eligible`, `ordering_key`).

**Type alias:** `ConnFactory = Callable[[], sqlite3.Connection]` (module-level; loops.py reuses it
by import).

**Module lock state:**
- `_LOCK_FDS: Final[dict[str, int]] = {}` — one cached OS fd per lock path. First successful
  acquisition caches the fd; the process holds the flock from then on (released only on process
  exit or explicit release).

**Functions (full signatures + behavior contracts):**

- `_ensure_dispatcher_lock(lock_path: str) -> bool`
  - If `lock_path in _LOCK_FDS` → `True` (already held by this process; re-acquisition is a no-op).
  - Else `Path(lock_path).parent.mkdir(parents=True, exist_ok=True)`;
    `fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o644)`;
    `fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)`.
  - On `BlockingIOError`: `os.close(fd)`, return `False` (held by another open file description —
    which includes a second fd inside this same process; that is what the lock-held test exploits).
  - On success: cache fd in `_LOCK_FDS`, return `True`.

- `release_dispatcher_lock(lock_path: str) -> None` (public)
  - `fd = _LOCK_FDS.pop(lock_path, None)`; if present: `fcntl.flock(fd, fcntl.LOCK_UN)` then
    `os.close(fd)`. Safe no-op when not held.

- `_pid_alive(pid: int) -> bool` — real-mode liveness probe: `os.kill(pid, 0)`;
  `ProcessLookupError` → `False`; `PermissionError` → `True`; no exception → `True`.
- `_pid_alive_always(_pid: int) -> bool` — returns `True`. Test-mode probe (defs, not lambdas —
  ruff E731 is enabled).
- `_sigterm(pid: int) -> None` — `os.kill(pid, signal.SIGTERM)` guarded
  `except ProcessLookupError: pass`.
- `_kill_noop(_pid: int) -> None` — returns `None`. Test-mode kill.

- `_enforce_run_timeouts(conn: sqlite3.Connection, now: int, run_max_seconds: int, failure_limit: int, kill: Callable[[int], None]) -> list[str]`
  - `SELECT id, pid FROM runs WHERE status='running' AND started_at <= ?` with
    `(now - run_max_seconds,)` — i.e. `now - started_at >= run_max_seconds`, fires at exactly the
    boundary.
  - Per row: if `pid` is not NULL → `kill(int(pid))` (kill first, then close);
    `close_run(conn, run_id, RunStatus.timed_out, now, failure_limit, error=f"exceeded run_max_seconds ({run_max_seconds}s)")`
    wrapped in `try/except PlannerError: continue` — a stale/foreign/reclaimed-raced run is the
    sweep's business, not ours. Append `run_id` to the result only on successful close.
  - Returns the run ids actually closed `timed_out`.
  - The `kill` parameter is what makes the SIGTERM path unit-testable with a recorder; no test ever
    routes a real signal.

- `run_tick(conn_factory: ConnFactory, config: Config, clock: Clock, adapters: Adapters) -> JsonDict`
  — the pinned seam. Exact order (§7.1 + ticket):
  - Build the report skeleton (shape below).
  - **(0) flag:** `read_dispatch_enabled()` (defaults: repo `config.yaml` + `os.environ`; any
    read/parse failure returns False inside it). Not enabled → `report["skipped"] = "dispatch_disabled"`,
    return. This happens BEFORE the lock and BEFORE any connection is opened.
  - **(0b) lock:** `_ensure_dispatcher_lock(config.dispatcher_lock_path)` → `False` →
    `report["skipped"] = "lock_held"`, return. Nothing else happens — zero DB reads or writes.
  - Open `conn = conn_factory()`; everything below inside `try:` with `finally: conn.close()`.
    `now = clock.now_unix()` once per tick.
  - **(1) reclaim:** `pid_alive = _pid_alive_always if config.test_mode else _pid_alive`;
    `report["reclaimed"] = sweep_reclaims(conn, now, pid_alive, config.failure_limit)`.
    (Under test mode expiry-based reclaim still fully works — `is_expired` needs no pid.)
  - **(2) timeout:** `kill = _kill_noop if config.test_mode else _sigterm`;
    `report["timed_out"] = _enforce_run_timeouts(conn, now, config.run_max_seconds, config.failure_limit, kill)`.
  - **(3) eligibility:** `candidates = load_candidates(conn, now)`;
    `eligible = sorted((c for c in candidates if is_eligible(c)), key=ordering_key)`.
  - **(4) spawn:** `active = int(conn.execute("SELECT COUNT(*) AS n FROM runs WHERE status='running'").fetchone()["n"])`;
    `budget = max(config.max_runs - active, 0)` — computed once per tick.
    `Path(config.logs_dir).mkdir(parents=True, exist_ok=True)` once, at the start of this stage.
    For each candidate in order, stop when `budget == 0`; each iteration decrements `budget` by one
    **before** knowing the outcome (every claim attempt consumes one unit, win or lose, spawn ok or
    failed — pinned semantics, do not "refund"):
    - `claimed = claim(conn, cand.ticket_id, now, config.claim_ttl_seconds)` (pid stays None at
      claim time; the `run_started` event therefore carries `pid: None` — expected). `None` = lost
      CAS → continue to the next candidate.
    - Build `SpawnRequest(ticket_id=cand.ticket_id, run_id=run_id, claim=token, server_url=f"http://{HOST}:{config.port}", log_path=str(Path(config.logs_dir) / f"{run_id}.log"), hermes_bin=config.hermes_bin, profile=config.hermes_profile, skill=config.worker_skill)`.
    - `result = adapters.spawn.spawn(request)` wrapped in `try/except Exception as exc:` →
      treat as `SpawnResult(ok=False, error=str(exc))` (adapter raise = spawn failure; recorded
      decision RD-9).
    - `result.ok` → `conn.execute("UPDATE runs SET pid=? WHERE id=?", (result.pid, run_id))`
      (direct UPDATE — `data.py` has no helper and is frozen); append
      `{"ticket_id": ..., "run_id": ..., "pid": result.pid}` to `report["spawned"]`.
    - else → `close_run(conn, run_id, RunStatus.spawn_failed, now, config.failure_limit, error=result.error or "spawn failed")`;
      append `{"ticket_id": ..., "run_id": ..., "error": result.error or "spawn failed"}` to
      `report["spawn_failed"]`.

**Report shape, pinned key-by-key (always all five keys, JSON-able):**

```
{
  "skipped":      None | "dispatch_disabled" | "lock_held",
  "reclaimed":    list[str],                                  # run ids from sweep_reclaims
  "timed_out":    list[str],                                  # run ids closed timed_out this tick
  "spawned":      list[{"ticket_id": str, "run_id": str, "pid": int | None}],
  "spawn_failed": list[{"ticket_id": str, "run_id": str, "error": str}],
}
```

**SAFETY (non-negotiable):** with `config.test_mode` true, `run_tick` must never reach `os.kill` —
neither the signal-0 probe nor SIGTERM. The two test-mode substitutions above are the entire
mechanism; there is no third call site. A test monkeypatches `os.kill` to raise `AssertionError`
and runs a full timeout tick under test mode to prove it.

**Error handling per path:** flag unreadable → skip (inside `read_dispatch_enabled`); flock
`BlockingIOError` → skip; `close_run` raising `PlannerError` inside timeout enforcement → skip that
run; spawn adapter raising → `spawn_failed` close. Anything else (DB errors) propagates to the
caller — loops.py logs it, test-endpoint callers surface it.

---

## 2. `src/planner/days/scheduler.py` (new)

**Module docstring gist:** "The boundary scheduler tick (§6.2) and the serialized latest-wins
replan queue (§6.3, R5). One tick = run the idempotent boundary job for the current planning date,
then drain the pending replan slot. The queue holds at most one pending request; a newer
submission overwrites the older and invalidates any in-flight execution (generation check).
Deterministic replan inputs are recomputed from the DB via boundary.py's reader helpers."

**Imports:** `from __future__ import annotations`; stdlib `sqlite3`, `threading`,
`collections.abc.Callable`, `concurrent.futures.ThreadPoolExecutor`, `dataclasses`
(`dataclass`, `replace`), `datetime` (`date`, `timedelta`), `functools.partial`,
`typing.TypeVar`; project: `planner.core.adapters.base.BoundaryInputs`,
`planner.core.adapters.registry.Adapters`, `planner.core.clock.Clock`,
`planner.core.config.Config`, `planner.core.contracts` (`EventKind`, `JsonDict`),
`planner.core.events.append_event`, `planner.core.ids.day_id`,
`planner.days.boundary` (`run_boundary`, and the module-private readers `_boundary_ran`,
`_error_text`, `_read_approval_candidates`, `_read_overdue_candidates`,
`_read_yesterday_tickets` — reuse by import is ACCEPTED per the ticket: same domain, avoids
duplicating SQL; recorded decision RD-5), `planner.days.contracts` (`NodeStatus`, `PlanTree`),
`planner.days.data` (`load_plan`, `store_plan`),
`planner.days.logic.carryover` (`approvals_digest`, `carryover_candidates`, `overdue_list`),
`planner.days.logic.dates.planning_date`,
`planner.days.logic.effects` (`ReplanChild`, `ReplanRequest`, `ReplanRoot`),
`planner.days.logic.tree` (`as_proposed`, `tree_to_dict`).

**Queue state structures, pinned:**

```python
@dataclass(frozen=True)
class _PendingReplan:
    day_id: str
    request: ReplanRequest
    generation: int

class _ReplanQueue:            # plain class, not a dataclass (holds a Lock)
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.pending: _PendingReplan | None = None
        self.generation = 0

_QUEUE = _ReplanQueue()        # module singleton
```

**Public functions:**

- `submit_replan(day_id: str, request: ReplanRequest) -> None`
  Under `_QUEUE.lock`: `generation += 1`; `pending = _PendingReplan(day_id, request, generation)`.
  Overwrites whatever was there (latest wins). Never executes anything — T10's invalidate
  endpoints call this from the request path; execution happens only in the consumer.

- `reset_replan_queue() -> None` (test helper)
  Under the lock: `pending = None`, `generation = 0`.

- `process_pending_replan(conn: sqlite3.Connection, config: Config, clock: Clock, adapters: Adapters) -> JsonDict | None`
  The single consumer. Loop until the slot is empty:
  1. Under the lock, snapshot `_QUEUE.pending`; `None` → break.
  2. Execute the snapshot OUTSIDE the lock (below), producing one attempt entry.
  3. Append the entry to `attempts`; continue the loop (a submission that arrived during
     execution is consumed by the next iteration — "loop until stable").
  Returns `None` when `attempts` is empty (nothing was pending), else `{"attempts": attempts}`.

  **Executing one snapshot** (private helper `_execute_pending(conn, config, clock, adapters, snapshot) -> JsonDict`):
  - `now = clock.now_unix()`; `day = snapshot.day_id`; `piso = day.removeprefix("day_")`;
    `scope, node = ("root", "root")` for `ReplanRoot`, `("child", request.position)` for
    `ReplanChild`.
  - `old_tree = load_plan(conn, day)`.
  - **Stale target** (no adapter call, no event): `ReplanChild` with `old_tree is None` or no child
    at `request.position` → `_consume_if_current(snapshot.generation)`, outcome `"stale_target"`.
    (`ReplanRoot` with `old_tree is None` proceeds — a full replan does not need the old tree;
    `old_tree: None` goes in the payload. RD-8.)
  - `inputs = _replan_inputs(conn, piso)` (below).
  - Adapter call under the caller-owned timeout, using `functools.partial` (not a lambda — ruff
    B023): root → `partial(adapters.boundary.replan_root, day, inputs)`; child →
    `partial(adapters.boundary.replan_child, day, old_child, inputs)` where `old_child` is the
    matched `PlanNode`. Run through `_call_with_timeout(fn, config.boundary_timeout_seconds)`.
  - **Failure/timeout** (`except Exception as exc`): `current = _consume_if_current(snapshot.generation)`.
    If current: `append_event(conn, day, EventKind.boundary_failed, {"error": _error_text(exc, config.boundary_timeout_seconds)}, now)`;
    plan left untouched; outcome `"failed"`. If not current (superseded mid-flight): write nothing,
    outcome `"discarded"` (the generation check governs ALL writes, events included — RD-6). The
    request is consumed either way; never retried.
  - **Success:** `current = _consume_if_current(snapshot.generation)`. Not current → discard the
    result entirely (no store, no event), outcome `"discarded"`, loop continues with the newer
    request. Current →
    - root: `new_tree = as_proposed(adapter_result)`; `store_plan(conn, day, new_tree, now)`;
      `append_event(conn, day, EventKind.plan_replanned, {"old_tree": tree_to_dict(old_tree) if old_tree is not None else None, "scope": "root", "node": "root"}, now)`.
    - child: spliced children = `[replace(c) for c in old_tree.children]` with the node at the
      target position replaced by
      `replace(adapter_result, status=NodeStatus.proposed, position=old_child.position)` —
      status forced proposed, position preserved, root and siblings byte-identical;
      `store_plan(conn, day, PlanTree(root=replace(old_tree.root), children=spliced), now)`;
      `append_event(..., EventKind.plan_replanned, {"old_tree": tree_to_dict(old_tree), "scope": "child", "node": old_child.position}, now)`.
    - outcome `"stored"`.
  - Attempt entry shape, pinned: `{"day_id": str, "scope": "root"|"child", "node": "root"|int, "outcome": "stored"|"discarded"|"failed"|"stale_target"}`.

- `_consume_if_current(generation: int) -> bool` — under the lock: if `pending is not None and
  pending.generation == generation` → clear the slot, return True; else return False.

- `_replan_inputs(conn: sqlite3.Connection, piso: str) -> BoundaryInputs` — recompute the
  deterministic inputs for the day's planning date, reusing boundary.py's private readers:
  `yid = day_id(date.fromisoformat(piso) - timedelta(days=1))`;
  `carry = carryover_candidates(_read_yesterday_tickets(conn, yid))`;
  `overdue = overdue_list(*_read_overdue_candidates(conn), piso)`;
  `approvals = approvals_digest(*_read_approval_candidates(conn))`;
  `BoundaryInputs(planning_date=piso, carryover=carry, overdue=overdue, approvals_digest=approvals)`.

- `_call_with_timeout(fn: Callable[[], _T], timeout_s: int) -> _T` (`_T = TypeVar("_T")`) — the
  exact pattern of `boundary._judgment_with_timeout`, generalized:
  `ThreadPoolExecutor(max_workers=1)`, `submit(fn).result(timeout=timeout_s)`,
  `finally: executor.shutdown(wait=False, cancel_futures=True)`. `FuturesTimeout` and adapter
  raises both surface to the caller as exceptions; `_error_text` renders them.

- `run_boundary_tick(conn_factory: Callable[[], sqlite3.Connection], config: Config, clock: Clock, adapters: Adapters) -> JsonDict`
  — the pinned seam:
  - `piso = planning_date(clock.now(), config.boundary_hour).isoformat()`.
  - `conn = conn_factory()`; `try/finally conn.close()`.
  - `existed = _boundary_ran(conn, piso)` (check BEFORE the call — this is what defines `ran`).
  - `run_boundary(conn, clock, config, adapters.boundary)` — always called; it is idempotent per
    planning date via `boundary_runs`, so a repeat call is a cheap no-op.
  - Read `SELECT judgment FROM boundary_runs WHERE planning_date=?`; `judgment` = the row value as
    `str`, or `None` if no row (defensive only — after a first call a row always exists).
  - `ran = (not existed) and row is not None` — whether THIS call did the work.
  - `replan = process_pending_replan(conn, config, clock, adapters)`.
  - Report, pinned: `{"planning_date": piso, "ran": bool, "judgment": "ok"|"skipped"|"failed"|None, "replan": {"attempts": [...]} | None}`.

**Error handling per path:** adapter failure/timeout → `boundary_failed` event, request consumed
(inside `run_boundary` for the judgment pass; inside `_execute_pending` for replans). Unexpected DB
errors propagate. No flock here — the boundary is guarded by `boundary_runs`, and the replan
consumer is serialized by being called only from this tick (test endpoints and the single boundary
loop).

---

## 3. `src/planner/core/adapters/real.py` (replace stub bodies)

Class names and `__init__(self, config: Config)` signatures stay exactly as-is — `registry.py`
constructs them and is frozen. Keep the `TYPE_CHECKING` Config import (annotations are lazy).

**Module docstring gist:** "Real adapters: subprocess spawn of the hermes worker (§7.4),
non-interactive hermes boundary/replan invocation (best-effort — live use is §18.4), and the
guarded tui_gateway chat gateway (§11). Never imported into a test's execution path except
construction and the echo-script smoke."

**New imports:** stdlib `importlib`, `json`, `os`, `subprocess`, `dataclasses.asdict`,
`typing.Final`; project adds `planner.core.errors` (`ErrorCode`, `PlannerError`),
`planner.days.contracts.NodeStatus`, `planner.days.logic.tree.tree_from_dict`.

**Module constants:**
- `BOUNDARY_SKILL: Final = "planning-boundary"` — §17 skill name. Config deliberately has no key
  for it (§13 names no such key); module constant, recorded decision RD-3.
- `_GATEWAY_MODULE: Final = "tui_gateway.ws"`.

### `RealSpawnAdapter.spawn(self, request: SpawnRequest) -> SpawnResult`

- `env = dict(os.environ)` overlaid with exactly the four §7.4 vars, all from the request:
  `PLAN_SERVER_URL=request.server_url`, `PLAN_TICKET_ID=request.ticket_id`,
  `PLAN_RUN_ID=request.run_id`, `PLAN_CLAIM=request.claim`.
- `command = [request.hermes_bin, "-p", request.profile, "--skills", request.skill, "chat", "-q", f"work planning ticket {request.ticket_id}"]`.
- ```
  try:
      with open(request.log_path, "wb") as log_file:          # parent fd closed right after Popen
          process = subprocess.Popen(command, start_new_session=True,
                                     stdin=subprocess.DEVNULL, stdout=log_file,
                                     stderr=subprocess.STDOUT, env=env)
  except OSError as exc:
      return SpawnResult(ok=False, error=str(exc))
  return SpawnResult(ok=True, pid=process.pid)
  ```
  `open` mode `"wb"` — run ids are unique, one fresh log per run (RD-17). The `with` block closes
  the parent's fd after Popen; the child holds its own dup. The Popen handle is deliberately
  dropped (RD-15: a finished, unreaped child is a zombie until Python's subprocess machinery reaps
  it opportunistically, so `pid_alive` can briefly report a finished worker alive — the TTL-expiry
  reclaim covers that window; expiry is checked first, D14).

### `RealBoundaryAdapter`

- `_invoke(self, prompt: str) -> str` (private): `subprocess.run([self._config.hermes_bin, "-p",
  self._config.hermes_profile, "--skills", BOUNDARY_SKILL, "chat", "-q", prompt],
  capture_output=True, text=True, check=False)`; non-zero returncode →
  `raise RuntimeError(f"hermes exited {returncode}: {stderr tail}")`; returns stdout. No subprocess
  timeout — the CALLER owns the timeout (boundary.py / scheduler.py wrap every call).
- `_parse_json_object(text: str) -> dict[str, Any]` (module-private): slice from first `"{"` to
  last `"}"` inclusive; no braces → `raise ValueError("no JSON object in hermes output")`;
  `json.loads` (raises on bad JSON); non-dict result → `ValueError`. All parse failures raise;
  the caller event-logs them.
- `judgment(self, inputs: BoundaryInputs) -> BoundaryJudgment`: prompt = one-paragraph instruction
  ("You are the planning boundary judgment. Reply with exactly one JSON object
  `{\"brief_markdown\": string, \"plan_tree\": {\"root\": {\"focus\", \"status\"}, \"children\": [...]}}`
  and nothing else.") + `json.dumps(asdict(inputs))`. Parse; return
  `BoundaryJudgment(brief_markdown=str(payload["brief_markdown"]), plan_tree=tree_from_dict(payload["plan_tree"]))`
  — `KeyError` on missing keys is a parse failure and propagates.
- `replan_root(self, day_id: str, inputs: BoundaryInputs) -> PlanTree`: prompt asks for
  `{"plan_tree": {...}}` for a full-day replan of `day_id`; returns
  `tree_from_dict(payload["plan_tree"])`.
- `replan_child(self, day_id: str, child: PlanNode, inputs: BoundaryInputs) -> PlanNode`: prompt
  embeds `{"ticket_id": child.ticket_id, "note": child.note, "position": child.position}` plus the
  inputs, asks for `{"child": {"ticket_id": string|null, "note": string}}`; returns
  `PlanNode(ticket_id=<payload value or None>, note=str(...), status=NodeStatus.proposed,
  position=child.position)`. (Scheduler forces status/position again on splice — harmless
  double-guarantee.)

### `RealGatewayAdapter`

- `status(self) -> GatewayStatus`: `importlib.import_module(_GATEWAY_MODULE)` in `try/except
  ImportError as exc` → `GatewayStatus(available=False, detail=str(exc))`; importable →
  `GatewayStatus(available=True)`.
- `send(self, session_key: str | None, entity_id: str, text: str) -> ChatSendResult`:
  import guarded the same way; `ImportError` →
  `raise PlannerError(ErrorCode.gateway_offline, "chat gateway unavailable", {"detail": str(exc)})`.
  When importable, the pinned attribute usage (RD-14, invented seam, adjusted at §18.4 when the
  live gateway is in front of the human):
  ```
  raw = module.send_message(session_key=session_key, entity_id=entity_id, text=text)
  return ChatSendResult(reply_text=str(raw["reply_text"]), session_key=str(raw["session_key"]))
  ```
  wrapped in `except Exception as exc: raise PlannerError(ErrorCode.gateway_offline,
  "chat gateway send failed", {"detail": str(exc)}) from exc`.
  Mypy-clean without the module present: `import_module` returns `ModuleType`, whose typeshed
  `__getattr__` yields `Any`, and the two `str(...)` coercions keep strict mode quiet.

---

## 4. `src/planner/core/loops.py` (new)

**Module docstring gist:** "Production background loops: two asyncio tasks driving the dispatcher
tick and the boundary tick every tick_seconds via asyncio.to_thread. An iteration's exception is
logged and never kills the loop. Test mode never starts these — that gating lives in T09's
lifespan; this module stays directly callable for tests."

**Imports:** `from __future__ import annotations`; stdlib `asyncio`, `logging`,
`functools.partial`; project `planner.core.adapters.registry.Adapters`,
`planner.core.clock.Clock`, `planner.core.config.Config`, `planner.core.contracts.JsonDict`,
`planner.days.scheduler.run_boundary_tick`,
`planner.dispatch.runtime` (`ConnFactory`, `release_dispatcher_lock`, `run_tick`).

`_LOGGER = logging.getLogger(__name__)`.

- `async def _loop_forever(name: str, tick: Callable[[], JsonDict], interval: int) -> None`
  - `while True:` → `try: await asyncio.to_thread(tick)` /
    `except Exception: _LOGGER.exception("background %s tick failed", name)` — catch `Exception`
    only, never `BaseException`, so `CancelledError` propagates and cancellation works —
    then `await asyncio.sleep(interval)`. Tick first, then sleep (RD-19): the first dispatcher
    iteration also performs the once-per-process lock acquisition.

- `class BackgroundLoops:`
  - `__init__(self, tasks: list[asyncio.Task[None]], lock_path: str) -> None` — stores both.
  - `async def stop(self) -> None`: cancel every task; `await asyncio.gather(*tasks,
    return_exceptions=True)` (collects the CancelledErrors; also waits out an in-flight
    to_thread tick, so the lock is quiescent); `release_dispatcher_lock(self._lock_path)`.

- `def start_background_loops(config: Config, clock: Clock, adapters: Adapters, conn_factory: ConnFactory) -> BackgroundLoops`
  — pinned seam and pinned parameter order. Must be called on a running event loop
  (T09's lifespan / a test's `asyncio.run` body):
  ```
  dispatcher = asyncio.create_task(_loop_forever("dispatcher",
      partial(run_tick, conn_factory, config, clock, adapters), config.tick_seconds))
  boundary = asyncio.create_task(_loop_forever("boundary",
      partial(run_boundary_tick, conn_factory, config, clock, adapters), config.tick_seconds))
  return BackgroundLoops([dispatcher, boundary], config.dispatcher_lock_path)
  ```

---

## 5. `tests/unit/test_runtimes.py` (new)

**Module docstring gist:** "Stage-4 runtime tests: dispatcher tick, boundary tick, replan queue,
real adapters, background loops. Ticks are driven DIRECTLY as functions on a temp DB with fakes and
TestClock — never through HTTP, never spawning hermes (the one subprocess is a tmp /bin/sh echo
script). Descriptive names, no aNN anchors — no §18.3 checklist item is owned here; e2e items
28–30 exercise these runtimes later through T09's endpoints."

Forbidden anywhere in this file (the verify instrument scans and fails):
`@pytest.mark.skip`, `pytest.skip(`, the x-fail marker or that word in any comment, `.only`,
commented-out tests, empty test bodies.

**Imports:** `from __future__ import annotations`; stdlib `asyncio`, `contextlib`, `fcntl`,
`logging`, `os`, `time`, `collections.abc.Callable`, `dataclasses.replace`,
`datetime.datetime`, `pathlib.Path`, `sqlite3.Connection`; `pytest`; project:
`planner.core.adapters.base` (`BoundaryInputs`, `SpawnRequest`),
`planner.core.adapters.fakes` (`EchoGatewayAdapter`, `FakeBoundaryAdapter`, `FakeSpawnAdapter`),
`planner.core.adapters.real` (`RealBoundaryAdapter`, `RealGatewayAdapter`, `RealSpawnAdapter`),
`planner.core.adapters.registry` (`Adapters`, `build_adapters`),
`planner.core.clock.TestClock`, `planner.core.config.Config`,
`planner.core.contracts.EventKind`, `planner.core.db` (`connect`, `create_schema`),
`planner.core.errors` (`ErrorCode`, `PlannerError`), `planner.core.loops.start_background_loops`,
`planner.days.contracts` (`NodeStatus`, `PlanNode`, `PlanRoot`, `PlanTree`),
`planner.days.data` (`load_plan`, `store_plan`), `planner.days.logic.effects` (`ReplanChild`, `ReplanRoot`),
`planner.days.logic.tree.tree_to_dict`,
`planner.days.scheduler` (`process_pending_replan`, `reset_replan_queue`, `run_boundary_tick`, `submit_replan`),
`planner.dispatch.data` (as `data`),
`planner.dispatch.runtime` (`_enforce_run_timeouts`, `release_dispatcher_lock`, `run_tick`) —
importing the private helper into a test in this repo is fine and deliberate (injectable-kill
unit test).

**Shared helpers (top of file, plain defs — no lambdas assigned to names, ruff E731):**

- `_insert_ticket(conn, ticket_id, *, state="needs_success", priority="P3", deadline=None, ceiling="needs_success", at_cap="propose", created_at=1_000_000)` —
  crib the `test_dispatch.py` column-list INSERT pattern (id, title, state, priority, deadline,
  ceiling, at_cap, created_at, updated_at). Default shape is dispatch-eligible (branch (b): at
  ceiling with propose).
- `_db_path(conn: Connection) -> str` — `str(conn.execute("PRAGMA database_list").fetchone()["file"])`;
  no coupling to conftest's filename.
- `_conn_factory(conn: Connection) -> Callable[[], Connection]` — captures `_db_path(conn)`,
  returns an inner `def factory() -> Connection: return connect(path)`. This is the pinned
  pattern: `tmp_db` stays the test's seeding/assertion connection; `run_tick` gets its own
  connections to the same file and closes them freely.
- `_events(conn, entity_id, kind) -> list[dict]` — crib from test_dispatch.
- `_adapters(spawn=None, boundary=None) -> Adapters` — `Adapters(spawn or FakeSpawnAdapter(),
  boundary or FakeBoundaryAdapter(), EchoGatewayAdapter())`.
- `_test_cfg(cfg: Config, tmp_path: Path, **overrides) -> Config` —
  `replace(cfg, test_mode=True, dispatcher_lock_path=str(tmp_path / "dispatcher.lock"),
  logs_dir=str(tmp_path / "logs"), **overrides)`. Every run_tick/loops test goes through this, so
  no test ever runs a tick with `test_mode=False` (fake pids ⇒ never signal for real).
- Autouse fixture `_clean_replan_queue()` — `reset_replan_queue()` before and after each test
  (the queue is module state).
- Every test that wants the tick to actually run sets
  `monkeypatch.setenv("PLAN_DISPATCH_ENABLED", "1")` — independence from `config.yaml` content.

**Tests (fixtures used → seeding → every assertion):**

1. `test_tick_claims_and_spawns_eligible_ticket(tmp_db, cfg, fake_clock, tmp_path, monkeypatch)` —
   ticket item (a). Seed one default eligible ticket `t_e`. `cfg2 = _test_cfg(cfg, tmp_path)`;
   `fake = FakeSpawnAdapter()`; `report = run_tick(_conn_factory(tmp_db), cfg2, fake_clock, _adapters(spawn=fake))`.
   Assert, with `now = fake_clock.now_unix()`:
   - `report["skipped"] is None`, `report["reclaimed"] == []`, `report["timed_out"] == []`,
     `report["spawn_failed"] == []`; `len(report["spawned"]) == 1`; entry
     `{"ticket_id": "t_e", "run_id": run_id, "pid": 90001}` (first fake pid).
   - runs row: status `"running"`, `started_at == now`, `pid == 90001`, `ended_at` None.
   - ticket row: `claim_lock` non-null and `== fake.calls[0].claim`;
     `claim_expires == now + cfg2.claim_ttl_seconds`.
   - `len(fake.calls) == 1`; the SpawnRequest asserted field-by-field:
     `ticket_id == "t_e"`, `run_id == run_id`, `server_url == f"http://127.0.0.1:{cfg2.port}"`,
     `log_path == str(Path(cfg2.logs_dir) / f"{run_id}.log")`, `hermes_bin == "hermes"`,
     `profile == "default"`, `skill == "planning-worker"` (R3 defaults).
   - `Path(cfg2.logs_dir).is_dir()` (runtime created it).
   - `_events(tmp_db, "t_e", EventKind.run_started) == [{"run_id": run_id, "pid": None}]`
     (pid is written by direct UPDATE after spawn, not at claim time).

2. `test_second_tick_does_not_double_claim(...)` — same setup as 1; run two ticks without moving
   the clock. Assert: second report `spawned == []` (active claim ⇒ ineligible); runs count 1;
   `len(fake.calls) == 1`.

3. `test_tick_respects_max_runs_cap(...)` — ticket item (b). Seed three eligible tickets with
   `created_at` 100/200/300. `assert cfg.max_runs == 2` (§13 default). One tick →
   `[e["ticket_id"] for e in report["spawned"]] == ["t_one", "t_two"]` (ordering: same
   priority/deadline ⇒ created_at ascending); running-runs count 2; `len(fake.calls) == 2`.
   Second tick → `spawned == []`, running count still 2 (`budget = 2 - 2 = 0`).

4. `test_expired_claim_reclaimed_then_respawned_same_tick(...)` — ticket item (c), proves §7.1
   step order. Seed one ticket; tick 1 spawns `run1`. `fake_clock.set(datetime(2026, 7, 4, 12, 15, 1).astimezone())`
   (901s later > TTL 900). Tick 2 asserts: `report["reclaimed"] == [run1]`; `len(report["spawned"]) == 1`
   with the same ticket_id and a NEW run_id; run1 row status `"reclaimed"`; new run `"running"`;
   `_events(..., EventKind.claim_reclaimed) == [{"run_id": run1, "reason": "expired"}]`
   (test-mode pid_alive is constant-True, so only expiry can reclaim — exactly as pinned).

5. `test_timeout_helper_sigterms_and_closes_timed_out(tmp_db, cfg)` — ticket item (d), direct
   helper with recorders; no clock/config needed beyond literals. `T0 = 1_000_000`. Seed ticket;
   `won = data.claim(tmp_db, "t_to", T0, 1_000_000, pid=4242)` (huge TTL so the claim stays
   active). `kills: list[int] = []` + a recorder def. Assert:
   - `_enforce_run_timeouts(tmp_db, T0 + 1799, 1800, cfg.failure_limit, kill) == []` (boundary
     minus one: nothing fires, `kills == []`).
   - `_enforce_run_timeouts(tmp_db, T0 + 1800, 1800, cfg.failure_limit, kill) == [run_id]`
     (fires at exactly `now - started_at == run_max_seconds`); `kills == [4242]`.
   - run row: status `"timed_out"`, `ended_at == T0 + 1800`,
     `error == "exceeded run_max_seconds (1800s)"`; ticket `claim_lock` None,
     `consecutive_failures == 1` (timed_out is a breaker failure);
   - `_events(..., EventKind.run_closed) == [{"run_id": run_id, "status": "timed_out", "summary": None}]`.

6. `test_run_tick_times_out_without_real_signals(tmp_db, cfg, fake_clock, tmp_path, monkeypatch)` —
   ticket item (d), tick level. `cfg2 = _test_cfg(cfg, tmp_path, run_max_seconds=600, claim_ttl_seconds=1_000_000)`.
   Seed ticket; `data.claim(tmp_db, "t_long", fake_clock.now_unix(), cfg2.claim_ttl_seconds, pid=90055)`.
   Advance the clock exactly 600s. Monkeypatch `os.kill` (via
   `monkeypatch.setattr("planner.dispatch.runtime.os.kill", _forbid)` where `_forbid` raises
   `AssertionError("os.kill reached under test_mode")`). Run one tick. Assert:
   - no AssertionError escaped (test_mode suppressed both the liveness probe and SIGTERM);
   - `report["timed_out"] == [run1]`; run1 row `"timed_out"`;
   - `len(report["spawned"]) == 1` for the SAME ticket with a new run id — the timed-out close
     clears the lock, `consecutive_failures` is 1 < limit 2, and §7.1 recomputes eligibility after
     enforcement, so the same tick legitimately re-spawns (asserted deliberately, RD-11).

7. `test_tick_skipped_when_lock_held_on_second_fd(tmp_db, cfg, fake_clock, tmp_path, monkeypatch)` —
   ticket item (e). `cfg2 = _test_cfg(cfg, tmp_path)`. Seed one eligible ticket. The test itself
   opens `fd = os.open(cfg2.dispatcher_lock_path, os.O_RDWR | os.O_CREAT, 0o644)` and takes
   `fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)` (distinct open file description ⇒ contention
   even in-process). `try:` run one tick; assert `report == {"skipped": "lock_held", "reclaimed": [],
   "timed_out": [], "spawned": [], "spawn_failed": []}`; zero writes: runs count 0, events count 0,
   ticket `claim_lock` None; `fake.calls == []`. `finally: os.close(fd)`.

8. `test_tick_fails_safe_on_unreadable_dispatch_flag(...)` — ticket item (f).
   `monkeypatch.setenv("PLAN_DISPATCH_ENABLED", "garbage")` (do NOT set it to "1" here). One tick →
   `report["skipped"] == "dispatch_disabled"`; zero writes (runs count 0, ticket unclaimed,
   `fake.calls == []`); and `not Path(cfg2.dispatcher_lock_path).exists()` — the flag gate runs
   before the lock gate, so the lock file was never even created.

9. `test_boundary_tick_runs_once_per_planning_date(tmp_db, cfg, fake_clock, tmp_path)` — ticket
   item (g). `fake_clock.set(datetime(2026, 7, 5, 5, 1).astimezone())`; `boundary = FakeBoundaryAdapter()`;
   `report = run_boundary_tick(_conn_factory(tmp_db), cfg, fake_clock, _adapters(boundary=boundary))`.
   Assert:
   - `report == {"planning_date": "2026-07-05", "ran": True, "judgment": "ok", "replan": None}`;
   - day row `day_2026-07-05` exists; `brief == "# Brief for 2026-07-05"` (FakeBoundary);
   - stored plan: `load_plan(...)` root focus `"Fake focus"`, root status `NodeStatus.proposed`,
     `children == []` (no yesterday day-tickets ⇒ empty carryover);
   - exactly one `boundary_runs` row, judgment `"ok"`; `boundary.calls == ["judgment"]`.
   Second call same date: `{"planning_date": "2026-07-05", "ran": False, "judgment": "ok", "replan": None}`;
   still exactly one `boundary_runs` row; `boundary.calls` still `["judgment"]`.

10. `test_replan_latest_wins_discards_stale_result(tmp_db, cfg, fake_clock)` — ticket item (h).
    Define in the test file:
    ```python
    class ResubmittingBoundary(FakeBoundaryAdapter):
        def replan_root(self, day_id: str, inputs: BoundaryInputs) -> PlanTree:
            self.calls.append("replan_root")
            if self.calls.count("replan_root") == 1:
                submit_replan(day_id, ReplanRoot())          # newer request lands mid-flight
                return PlanTree(root=PlanRoot(focus="first result"), children=[])
            return PlanTree(root=PlanRoot(focus="second result"), children=[])
    ```
    Seed: `old = PlanTree(root=PlanRoot(focus="original", status=NodeStatus.proposed), children=[])`;
    `store_plan(tmp_db, "day_2026-07-04", old, fake_clock.now_unix())`.
    `submit_replan("day_2026-07-04", ReplanRoot())`; `report = process_pending_replan(tmp_db, cfg, fake_clock, _adapters(boundary=adapter))`.
    Assert:
    - `adapter.calls == ["replan_root", "replan_root"]` (called twice — first result discarded);
    - `report["attempts"]` == exactly
      `[{"day_id": "day_2026-07-04", "scope": "root", "node": "root", "outcome": "discarded"},
        {"day_id": "day_2026-07-04", "scope": "root", "node": "root", "outcome": "stored"}]`;
    - final stored tree: root focus `"second result"`, status proposed (as_proposed applied);
    - exactly ONE `plan_replanned` event on `day_2026-07-04`, payload
      `{"old_tree": tree_to_dict(old), "scope": "root", "node": "root"}` — old_tree is the
      originally stored tree (the discarded first result was never stored);
    - a second `process_pending_replan` returns `None` (queue drained).

11. `test_replan_child_splices_only_target_node(tmp_db, cfg, fake_clock)` — expansion covering the
    ReplanChild path. Seed a two-child tree: root accepted; child 0
    `PlanNode("t_keep", "keep me", accepted, 0)`; child 1 `PlanNode("t_redo", "old note", invalidated, 1)`.
    `submit_replan("day_2026-07-04", ReplanChild(1))`; process with a plain `FakeBoundaryAdapter`.
    Assert: attempt outcome `"stored"`, node `1`, scope `"child"`; stored tree — root still
    accepted with same focus, child 0 byte-identical (status accepted, note "keep me"), child 1 ==
    `PlanNode("t_redo", "Fake replanned child", proposed, 1)` (fake echoes ticket_id, note
    replaced, status forced proposed, position preserved); `plan_replanned` payload
    `{"old_tree": tree_to_dict(seeded), "scope": "child", "node": 1}`; `adapter.calls == ["replan_child"]`.

12. `test_replan_failure_emits_boundary_failed_and_consumes(tmp_db, cfg, fake_clock)` — expansion.
    Seed a stored plan; `submit_replan(day, ReplanRoot())`; process with
    `FakeBoundaryAdapter(fail=True)`. Assert: attempt outcome `"failed"`; stored plan unchanged
    (equal to seeded tree); exactly one `boundary_failed` event on the day with
    `payload["error"] == "fake boundary failure"` (`_error_text` of the fake's RuntimeError);
    NO `plan_replanned` event; second `process_pending_replan` returns `None` (consumed, not
    retried).

13. `test_real_adapters_constructed_from_config(cfg)` — ticket item (i).
    `bundle = build_adapters(replace(cfg, spawn_adapter="real", boundary_adapter="real", gateway_adapter="real"))`;
    assert `isinstance` of all three real classes. `status = bundle.gateway.status()`;
    `status.available is False` and `status.detail` truthy (tui_gateway is never importable in
    tests). `pytest.raises(PlannerError)` around `bundle.gateway.send(None, "t_x", "hello")`;
    `exc.value.code is ErrorCode.gateway_offline`.

14. `test_real_spawn_echo_smoke(cfg, tmp_path)` — ticket item (j); the only subprocess in the
    file, never hermes. Write `script = tmp_path / "fake-hermes.sh"`:
    ```sh
    #!/bin/sh
    echo "hi $PLAN_TICKET_ID"
    echo "pgid=$(ps -o pgid= -p $$ | tr -d ' ')"
    ```
    `script.chmod(0o755)`; `(tmp_path / "logs").mkdir()`. Build a `SpawnRequest` by hand:
    ticket_id `"t_smoke"`, run_id `"run_smoke"`, claim `"claim_x"`, server_url
    `"http://127.0.0.1:8767"`, log_path `str(tmp_path / "logs" / "run_smoke.log")`,
    hermes_bin `str(script)`, profile/skill from cfg. `result = RealSpawnAdapter(cfg).spawn(request)`.
    Assert `result.ok is True`, `result.error is None`, `isinstance(result.pid, int)` and `> 0`.
    Poll the log file (`time.monotonic()` deadline 10s, `time.sleep(0.05)` between reads) until it
    contains both `"hi t_smoke"` and a `"pgid="` line; on deadline, fail with the log contents.
    Then: parsed pgid `== result.pid` — `start_new_session` made the child its own session/group
    leader, proving detachment; `"hi t_smoke"` proves the env overlay and log redirection.
    Finally reap: `with contextlib.suppress(ChildProcessError): os.waitpid(result.pid, 0)`.

15. `test_background_loops_start_and_stop_cleanly(tmp_db, cfg, fake_clock, tmp_path, monkeypatch, caplog)` —
    ticket item (k). `cfg2 = _test_cfg(cfg, tmp_path, tick_seconds=1)`;
    `monkeypatch.setenv("PLAN_DISPATCH_ENABLED", "1")`; fakes; factory over `tmp_db`'s file;
    `caplog.set_level(logging.ERROR)`.
    ```python
    async def scenario() -> None:
        loops = start_background_loops(cfg2, fake_clock, _adapters(), _conn_factory(tmp_db))
        await asyncio.sleep(0.05)
        await loops.stop()
    asyncio.run(scenario())
    ```
    Assert: no records with `levelno >= logging.ERROR` in `caplog.records` (no swallowed loop
    crashes); the dispatcher lock was released by `stop()` — the test can now
    `os.open` + `fcntl.flock(fd, LOCK_EX | LOCK_NB)` on `cfg2.dispatcher_lock_path` without
    `BlockingIOError` (close the fd after). Side effects of the one boundary iteration (a
    `day_2026-07-04` row) are allowed and unasserted.

---

## 6. Implementation order and gates

1. `runtime.py` → 2. `scheduler.py` → 3. `real.py` → 4. `loops.py` → 5. `test_runtimes.py`.
Then, all fresh:
- `.venv/bin/ruff check src/planner/dispatch/runtime.py src/planner/days/scheduler.py src/planner/core/adapters/real.py src/planner/core/loops.py tests/unit/test_runtimes.py`
- `.venv/bin/mypy src/` — zero errors attributable to the owned files (strict; match the repo's
  annotation style: `from __future__ import annotations`, full parameter/return annotations,
  `JsonDict` for report dicts).
- `.venv/bin/pytest tests/unit/test_runtimes.py -q` green.
- `.venv/bin/pytest tests/unit -q` — the whole existing unit suite stays green.

Ruff traps already designed around: E731 (no named lambdas — use defs/partial), B023 (no
loop-variable closures — `functools.partial` in the scheduler), I001 (sort imports), line length
100.

---

## 7. Recorded decisions (for orchestrator ratification)

- **RD-1 Test-mode signal suppression.** Under `config.test_mode`, `pid_alive` is constant-True
  and the timeout kill is a no-op; `os.kill` is unreachable from `run_tick`. Real mode probes
  `os.kill(pid, 0)` (ProcessLookupError → dead, PermissionError → alive) and sends SIGTERM guarded
  against ProcessLookupError. Both are injectable parameters of private helpers so tests use
  recorders, never signals.
- **RD-2 Spawn-budget semantics.** Budget = `max_runs − active running`, computed once per tick;
  every claim attempt consumes one unit regardless of outcome (lost CAS, spawn ok, spawn failed).
  No refunds within a tick; a wasted slot self-heals next tick.
- **RD-3 Boundary skill constant.** `BOUNDARY_SKILL = "planning-boundary"` is a module constant in
  real.py (§17). Config deliberately has no key for it — §13 enumerates the config keys and this
  is not one.
- **RD-4 Replan failure consumes the request.** Adapter failure/timeout → one `boundary_failed`
  event `{error}`, plan untouched, slot cleared. Never retried automatically; a human re-invalidates.
- **RD-5 Private-helper reuse from boundary.py.** scheduler.py imports `_boundary_ran`,
  `_error_text`, `_read_yesterday_tickets`, `_read_overdue_candidates`,
  `_read_approval_candidates` — sanctioned by the ticket (same domain, one copy of the SQL).
- **RD-6 Generation check governs ALL writes.** A superseded in-flight execution writes nothing —
  no store, no `plan_replanned`, and no `boundary_failed` either — outcome `"discarded"`. Pure
  latest-wins; the superseding request produces the only observable result.
- **RD-7 Stale replan target.** `ReplanChild` whose day has no stored plan or no child at the
  position by execution time (e.g. reject-all raced the queue): request consumed, no adapter call,
  no event, attempt outcome `"stale_target"`. The human's explicit reject supersedes; an error
  event would be noise.
- **RD-8 ReplanRoot with a NULL stored plan proceeds.** A full replan needs no old tree; the
  `plan_replanned` payload carries `old_tree: null`.
- **RD-9 Spawn adapter exceptions = spawn failure.** `adapters.spawn.spawn()` raising is caught
  and treated as `SpawnResult(ok=False, error=str(exc))` → run closed `spawn_failed`. The real
  adapter already returns instead of raising; this guards foreign implementations.
- **RD-10 Timeout enforcement defers to the sweep on stale runs.** `close_run` raising
  `PlannerError` (expired claim, lost finalize CAS) inside the enforcer is swallowed for that run;
  step 1 of this or the next tick owns it. Note the default geometry: run_max (1800s) > TTL
  (900s), so a non-heartbeating run is reclaimed by TTL long before timeout — the enforcer
  exists for runs that keep heartbeating but overrun.
- **RD-11 Same-tick re-spawn after a timeout close is correct §7.1 behavior** (lock cleared at
  step 2, eligibility recomputed at step 3) and is asserted, not avoided, in the tick-level
  timeout test.
- **RD-12 Gates before the connection.** The dispatch-enabled and flock gates run before
  `conn_factory()` is called; a skipped tick opens no connection and performs zero reads/writes.
  Ticks that pass the gates open exactly one connection and close it in `finally`.
- **RD-13 Report shapes** as pinned in §1/§2 above (five fixed keys for the dispatcher tick; four
  fixed keys for the boundary tick; attempt entries `{day_id, scope, node, outcome}`).
- **RD-14 RealGateway seam attribute.** Pinned call: `module.send_message(session_key=...,
  entity_id=..., text=...)` returning a mapping with `reply_text` and `session_key`; ImportError →
  offline status / `gateway_offline` error; any send-path exception → `gateway_offline`. The
  module is never importable in tests; the exact attribute is adjusted, if needed, at the §18.4
  live-gateway human pass.
- **RD-15 RealSpawn keeps no Popen reference.** A finished worker is a zombie until Python's
  subprocess machinery reaps it, so the pid probe may briefly report a dead worker alive; the
  TTL-expiry reclaim (checked first, D14) covers that window. Accepted for v1.
- **RD-16 RealBoundary failure = raise.** Non-zero hermes exit, missing JSON object, bad JSON, or
  missing keys all raise; the caller (boundary.py / scheduler.py) owns timeout and failure
  handling and event-logs the text.
- **RD-17 Per-run log opened `"wb"`** — run ids are unique; one fresh file per run; parent fd
  closed immediately after Popen.
- **RD-18 Lock cache is per-path.** `_LOCK_FDS: dict[str, int]`; re-acquisition by the holding
  process is a no-op; `release_dispatcher_lock` is the explicit release used by `loops.stop()`
  and tests. Tests isolate via per-test tmp lock paths.
- **RD-19 Loop iteration = tick first, then sleep.** First dispatcher iteration performs the
  once-per-process lock acquisition. Iterations catch `Exception` (never `BaseException`) so
  cancellation propagates and `stop()` works.
- **RD-20 `process_pending_replan` drains until the slot is empty** — chained submissions arriving
  during execution are consumed within the same call, keeping R5 serialization in one consumer.
- **RD-21 `run_boundary_tick` always calls `run_boundary`** (idempotent via `boundary_runs`) and
  derives `ran` from row-existence before vs after; `judgment` is read back from the row.

---

## 8. Binding amendments (post codex plan review — these OVERRIDE anything above they touch)

- **A1 (supersedes the §4 shutdown claim).** Cancelling a task awaiting `asyncio.to_thread` does
  NOT wait for the worker thread; the original text was wrong. See A4 for the fix.

- **A2 — loops singleton guard.** `loops.py` keeps a module-level `_active: BackgroundLoops | None`.
  `start_background_loops` raises `RuntimeError("background loops already running")` when `_active`
  is a not-yet-stopped instance; sets `_active` on success. `BackgroundLoops.stop()` clears
  `_active` (idempotent: second `stop()` is a no-op). Prevents two same-process dispatcher loops
  sharing the cached flock fd.

- **A3 — scheduler tick mutex.** `scheduler.py` module-level `_TICK_MUTEX = threading.RLock()`.
  Both `run_boundary_tick` and `process_pending_replan` run their entire bodies under `with
  _TICK_MUTEX:` (RLock, so the nested call re-enters). Serializes boundary checks (run_boundary's
  read-guard-then-insert is not atomic and is frozen) and makes the replan consumer single-flight
  in-process. The generation check remains the backstop for supersession during execution.
  `reset_replan_queue` also takes the mutex.

- **A4 — cancellation-safe loop iteration.** `_loop_forever` body per iteration:
  `thread_task = asyncio.ensure_future(asyncio.to_thread(tick))`; `try: await
  asyncio.shield(thread_task)`; `except asyncio.CancelledError: with
  contextlib.suppress(BaseException): await thread_task` then `raise`; `except Exception:
  _LOGGER.exception(...)`; then `await asyncio.sleep(interval)`. `stop()` (cancel → gather →
  `release_dispatcher_lock`) therefore returns only after any in-flight tick thread has finished —
  the lock is never released under a running tick. `partial(run_tick, ...)` / `partial(
  run_boundary_tick, ...)` are constructed INSIDE `start_background_loops` from the loops-module
  globals, so tests may monkeypatch `planner.core.loops.run_tick` before starting (A9 relies on
  this: import the runtime functions at module top by name into loops.py).

- **A5 — close before kill (supersedes §1 `_enforce_run_timeouts` ordering).** Per overtime row:
  call `close_run(..., RunStatus.timed_out, ...)` FIRST inside `try/except PlannerError: continue`;
  only a successful close (this tick won the status CAS and owns the timeout) is followed by
  `kill(pid)` (pid non-NULL) and appended to the report. A run concurrently closed/reclaimed loses
  the CAS and is never signalled. Test 5's assertions change accordingly: the kill recorder fires
  only for runs whose close succeeded; at the 1799s probe neither close nor kill happens.

- **A6 — boundary report date snapshot.** `piso` is computed exactly once, before `run_boundary`;
  `ran`/`judgment` are defined by the `boundary_runs` row FOR THAT `piso` (existence before vs
  after, judgment read back). If a RealClock crosses the boundary hour mid-call (impossible under
  TestClock), this tick reports `ran=False` for its snapshot date and the next tick reports the new
  date — accepted residual imprecision; the event log is the source of truth.

- **A7 — spawn smoke asserts the full §7.4 surface.** The tmp script becomes:
  ```sh
  #!/bin/sh
  echo "argv=$@"
  echo "url=$PLAN_SERVER_URL"
  echo "run=$PLAN_RUN_ID"
  echo "claim=$PLAN_CLAIM"
  echo "ticket=$PLAN_TICKET_ID"
  echo "pgid=$(ps -o pgid= -p $$ | tr -d ' ')"
  ```
  Assertions added to test 14: the argv line is exactly
  `argv=-p default --skills planning-worker chat -q work planning ticket t_smoke` (profile/skill
  from the cfg defaults used in the request); the four env lines carry exactly the request's
  server_url/run_id/claim/ticket_id; pgid == `result.pid` as before.

- **A8 — three test additions/strengthenings.**
  1. New `test_spawn_failure_closes_run_and_trips_breaker(tmp_db, cfg, fake_clock, tmp_path,
     monkeypatch)`: FakeSpawnAdapter with `results=[SpawnResult(ok=False, error="boom")]` scripted
     before each of two ticks (re-scripted between ticks). Tick 1: report `spawn_failed` entry
     `{"ticket_id", "run_id", "error": "boom"}`, `spawned == []`; run row `spawn_failed`, ticket
     `claim_lock` NULL, `consecutive_failures == 1`, `auto_blocked` 0. Tick 2 (rescripted): second
     `spawn_failed` run, `consecutive_failures == 2`, `auto_blocked` 1, `auto_blocked` event
     present. Tick 3: `spawned == []` and `spawn_failed == []` (ticket ineligible — breaker).
  2. New `test_expired_and_overtime_run_is_reclaimed_not_timed_out(...)`: seed a claim+run via
     `data.claim(..., now=T0, ttl=100)` with `run_max_seconds=600` in cfg; advance the fake clock
     past BOTH (e.g. T0+700). One tick: run status `reclaimed`, `report["reclaimed"] == [run1]`,
     `report["timed_out"] == []` — reclaim (step 1) precedes timeout enforcement (step 2).
  3. Tests 2 and 3 additionally assert `report["skipped"] is None` on every tick they run.

- **A9 — stop-waits-for-in-flight-tick test.** New
  `test_stop_waits_for_inflight_tick(cfg, tmp_path, monkeypatch)`: monkeypatch
  `planner.core.loops.run_tick` with a recorder `def slow_tick(*args): started.set();
  time.sleep(0.3); done.append(True); return {}` (threading.Event + list). Start loops
  (`tick_seconds=60` so only the first immediate iteration runs), `await`-poll until
  `started.is_set()` (bounded), call `await loops.stop()`, then assert `done == [True]` — stop
  returned only after the in-flight thread finished. Also assert the second
  `start_background_loops` raises `RuntimeError` while the first is live (A2) and works after
  `stop()`. The boundary loop keeps the real `run_boundary_tick` on a temp DB (harmless single
  iteration) or is also monkeypatched — implementer's choice, assert nothing about it.

Dispositions for every codex finding (accepted/refuted with reasons) are in plan-review.md.
RD-5 (private-helper reuse) stands as written. All other RDs stand unless amended above.
