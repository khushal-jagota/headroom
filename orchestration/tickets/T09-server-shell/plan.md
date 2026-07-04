# T09 — Server shell: implementation plan

Scope per `orchestration/tickets/T09-server-shell/ticket.md`. Contracts are law and unmodified:
`core/server.py` (T01 stub, completed here), `core/errors.py`, `core/config.py`, `core/clock.py`,
`core/events.py`, `core/db.py`, `core/contracts.py`, `core/adapters/registry.py`,
`dispatch/logic/claims.py`, `dispatch/data.py`, `days/logic/dates.py`.

Files touched — exactly these, nothing else:

| File | Action |
|---|---|
| `src/planner/core/server.py` | complete the T01 factory (lifespan, dirs, test-router mount, WS delegation) |
| `src/planner/core/ws.py` | new — the event tailer |
| `src/planner/core/authctx.py` | new — §7.6 request context + claim validation |
| `src/planner/core/testmode.py` | new — the `/api/test/*` router |
| `orchestration/tickets/T09-server-shell/smoke.py` | new — scripted self-smoke (not under `tests/`) |

T11 concurrently owns `dispatch/runtime.py`, `days/scheduler.py`, `core/adapters/real.py`,
`core/loops.py`. This plan never creates or edits those; every coupling point is a lazy
runtime import per the pinned seam (ticket.md "Pinned seam with T11", binding).

---

## 1. `src/planner/core/authctx.py` — §7.6 request context (new)

Implements SPEC §7.6 (write validation, ~165-168) and the seam-pinned public API consumed by
T10: `RequestContext`, `request_context`, `require_claim`, `reject_agents`. Header names are
the T01 route-table headers (`X-Plan-Run-Id`, `X-Plan-Claim`, `X-Plan-Actor`, filled by the CLI
from `PLAN_RUN_ID`/`PLAN_CLAIM`/`PLAN_ACTOR`).

Imports: stdlib (`dataclasses`, `sqlite3`, `typing`), `fastapi.Request`,
`planner.core.contracts.JsonDict`, `planner.core.errors`, and
`planner.dispatch.logic.claims` (`is_expired`, `has_active_claim`) — server-side code importing
pure domain logic, the layering the orchestrator ruling blesses (server.py already imports
domain routers). The lease comparison is **never re-derived**: expiry is decided only by
`is_expired` (half-open: expired iff `claim_expires <= now`, §7.3).

```python
X_PLAN_RUN_ID: Final = "X-Plan-Run-Id"
X_PLAN_CLAIM: Final = "X-Plan-Claim"
X_PLAN_ACTOR: Final = "X-Plan-Actor"
_DEFAULT_AGENT_ACTOR: Final = "agent"    # §7.6: PLAN_ACTOR default "agent"
_HUMAN_ACTOR: Final = "human"

@dataclass(frozen=True)
class RequestContext:                     # seam-pinned shape, exactly these five fields
    actor: str
    run_id: str | None
    claim: str | None
    is_claimed_agent: bool
    is_human: bool

def _normalize(raw: str | None) -> str | None
    # strip(); "" or whitespace-only -> None. Empty headers are absent headers.

def _classify(run_id: str | None, claim: str | None, actor: str | None) -> RequestContext
    # pure; inputs pre-normalized; the truth table below, one place.

def request_context(request: Request) -> RequestContext
    # FastAPI dependency (sync def). Reads the three headers off request.headers,
    # normalizes, delegates to _classify. No DB, no clock.

def _reject_stale(
    reason: str, message: str, ctx: RequestContext, ticket_id: str,
    active_claim_present: bool, extra: JsonDict | None = None,
) -> NoReturn
    # builds the detail payload (section 1.2) and raises PlannerError(stale_claim, ...).

def require_claim(conn: sqlite3.Connection, ctx: RequestContext, ticket_id: str, now: int) -> None
    # §7.6 validation, order in section 1.3. now is unix seconds (int), matching
    # dispatch.logic.claims and dispatch.data conventions.

def reject_agents(ctx: RequestContext) -> None
    # (H) routes: raises PlannerError(agent_forbidden) unless ctx.is_human.
    # errors.py pins agent_forbidden as "claim/agent request hits a human-only action" —
    # both agent classes rejected, not just claim-carrying ones.
    # detail: {"actor": ctx.actor, "is_claimed_agent": ctx.is_claimed_agent}
```

### 1.1 Classification truth table (exact; after `_normalize`)

R = `X-Plan-Run-Id`, C = `X-Plan-Claim`, A = `X-Plan-Actor`. "set" = non-empty after strip.

| R | C | A | is_claimed_agent | is_human | actor |
|---|---|---|---|---|---|
| set | set | set | True | False | A |
| set | set | absent | True | False | `"agent"` |
| set | absent | any | True | False | A or `"agent"` |
| absent | set | any | True | False | A or `"agent"` |
| absent | absent | set | False | False | A |
| absent | absent | absent | False | True | `"human"` |

Rows 3–4 are the half-present pair: classified as a claimed-agent **attempt** (orchestrator
ruling 2) — `require_claim` rejects it with `stale_claim` naming the missing half; it never
falls back to the plain-agent proposal path. Row 5 is the §7.6 non-dispatched agent context
(planner-main chat agent): proposer from `PLAN_ACTOR`. Row 6 is the human (UI requests carry
no agent headers).

### 1.2 `stale_claim` detail payloads (exact)

Base keys on every `require_claim` rejection — the T01 plan's pinned §7.6 shape
(T01 plan.md ~line 849 block) plus `ticket_id` (the dispatch.data convention):

```json
{
  "reason": "<see table>",
  "ticket_id": "<ticket id>",
  "presented_run_id": <ctx.run_id or null>,
  "presented_claim": <ctx.claim or null>,
  "active_claim_present": <bool: has_active_claim(claim_lock, claim_expires, now)>
}
```

Only the **presented** values are ever echoed; the stored `tickets.claim_lock` token appears in
no message and no detail, on any path (smoke asserts this literally).

| reason | HTTP | message | extra keys |
|---|---|---|---|
| `"missing_header"` | 409 | `claim headers incomplete` | `"missing": [<absent header names, from [X-Plan-Run-Id, X-Plan-Claim]>]` |
| `"none_active"` | 409 | `ticket has no active claim` | — |
| `"expired"` | 409 | `claim expired` | `"claim_expires": <int or null>` (mirrors dispatch.data.heartbeat) |
| `"foreign"` | 409 | `claim token does not match the ticket's active claim` | — |
| `"run_mismatch"` | 409 | `run id does not match the ticket's running run` | — |

Unknown ticket is not `stale_claim`:
`PlannerError(not_found, "ticket not found", {"ticket_id": ...})` → 404, identical to
`dispatch.data.clear_auto_block`'s shape.

`reason` vocabulary = T01's three (`none_active`, `expired`, `foreign`) plus exactly two new
values this ticket's checks require (`missing_header`, `run_mismatch`) — every rejection names
which check failed (orchestrator ruling 2).

### 1.3 `require_claim` validation order (exact)

1. `SELECT claim_lock, claim_expires FROM tickets WHERE id=?` — no row → **not_found**.
   Coerce like dispatch.data does (`str`/`int` or None). Compute
   `active = has_active_claim(claim_lock, claim_expires, now)` for the detail payload.
2. `ctx.run_id is None or ctx.claim is None` → **missing_header** (lists each absent header;
   covers half-present and both-absent).
3. `claim_lock is None` → **none_active**.
4. `is_expired(claim_expires, now)` → **expired** (the single half-open comparison, §7.3 —
   at exactly `claim_expires` the write is rejected).
5. `ctx.claim != claim_lock` → **foreign**.
6. `SELECT id FROM runs WHERE ticket_id=? AND status='running' ORDER BY started_at DESC LIMIT 1`
   — no row, or `id != ctx.run_id` → **run_mismatch**. (By construction — claim CAS +
   finalize CAS in dispatch.data — at most one running run exists per actively claimed ticket.)
7. Return `None`.

Ordering rationale: ruling 2 lists the checks in exactly this sequence (ticket exists →
active claim → unexpired → token → run id); a foreign token against an expired lease therefore
reports `expired`, matching heartbeat's behavior where liveness is checked before anything else.

---

## 2. `src/planner/core/ws.py` — event tailer (new)

Implements SPEC §9 (~184): `WS /api/events?since=<id>` tails the events table, pushing
`{events, cursor}` batches; an invalidation signal — non-empty batches only, no
reconciliation. Poll cadence and read limit come in as plain ints (config stays in server.py;
events.py stays config-free per its own docstring).

Imports: `asyncio`, `sqlite3`, `collections.abc.Callable`, `fastapi.WebSocket`,
`fastapi.WebSocketDisconnect`, `planner.core.contracts` (`EventRow`, `JsonDict`),
`planner.core.events.read_events_since`.

```python
def _event_json(row: EventRow) -> JsonDict
    # The exact wire shape of one serialized EventRow — written literally, the five
    # contract fields and nothing else:
    # {"id": row.id, "entity_id": row.entity_id, "kind": row.kind,
    #  "payload": row.payload, "created_at": row.created_at}

async def tail_events(
    websocket: WebSocket,
    since: int,
    conn_factory: Callable[[], sqlite3.Connection],
    ws_poll_ms: int,
    events_read_limit: int,
) -> None
```

### 2.1 Message shape (exact)

```json
{"events": [{"id": 7, "entity_id": "t_ab12cd34", "kind": "ticket_created",
             "payload": {...}, "created_at": 1780560000}, ...],
 "cursor": 7}
```

`cursor` is the `id` of the last event in this batch; the client passes it back as `?since=`
on reconnect. Sent via `websocket.send_json`. Empty batches are never sent.

### 2.2 Polling algorithm (exact, per orchestrator ruling 5)

```
await websocket.accept()
conn = conn_factory()                # per-connection sqlite conn, created and used on the
try:                                 # event-loop thread only (sqlite3 same-thread safe)
    cursor = since
    while True:
        batch = read_events_since(conn, cursor, events_read_limit)
        if batch:
            cursor = batch[-1].id
            await websocket.send_json({"events": [_event_json(e) for e in batch],
                                       "cursor": cursor})
            if len(batch) == events_read_limit:
                continue             # full batch: re-poll immediately, drain the backlog
        else:
            await asyncio.sleep(ws_poll_ms / 1000)
except WebSocketDisconnect:
    pass                             # client went away mid-send: normal termination
finally:
    conn.close()                     # always, including cancellation
```

- `WebSocketDisconnect` (raised by starlette on send after the peer closed) ends the coroutine
  cleanly.
- `asyncio.CancelledError` (uvicorn shutdown / connection teardown) is **not** caught — it
  propagates after `finally` closes the connection. No blanket `except Exception`.
- A silently-dead idle client is detected at the next send attempt or at shutdown — accepted
  trade-off, see risk R2.

---

## 3. `src/planner/core/testmode.py` — test router (new)

Implements SPEC §9 (~186), §13 (~219-220), D5, D6, and seam items 1–2 of ticket.md. An
`APIRouter` built by an explicit factory (chosen over `app.state` reads: matches the
`create_app` dependency style, unit-exercisable standalone). Routes are declared under
`/test/...` and mounted by server.py with `prefix="/api"` exactly like the domain routers —
outside test mode the router is never built, so `/api/test/*` is a plain 404 (T01 plan ~849:
"by not being mounted", no PlannerError involved).

Imports: `importlib`, `sqlite3`, `collections.abc.Callable`, `typing.Any`,
`fastapi.APIRouter`, `fastapi.responses.JSONResponse`, `planner.core.clock`
(`Clock`, `TestClock`, `parse_fake_now`), `planner.core.config.Config`,
`planner.core.contracts.JsonDict`, `planner.core.adapters.registry.Adapters`,
`planner.core.errors`, `planner.days.logic.dates.planning_date` (static import — dates.py
exists, not a T11 file).

```python
def _not_wired() -> JSONResponse
    # HTTP 501, body EXACTLY as the seam pins it (returned directly, never raised as a
    # PlannerError, which the handler would map to 400):
    # {"error": {"code": "validation", "message": "runtime not wired yet"}}

def _lazy(module_name: str, attr: str) -> Any | None
    # importlib.import_module(module_name) then getattr; (ImportError, AttributeError) -> None.
    # importlib (not a from-import) so mypy strict never sees the not-yet-existing T11
    # modules; the two exceptions are exactly the seam's named failure modes.

def build_test_router(
    config: Config,
    clock: Clock,
    adapters: Adapters,
    conn_factory: Callable[[], sqlite3.Connection],
) -> APIRouter
```

Inner endpoints (closures over the factory args, all annotated for mypy strict):

**`POST /test/set-now`** — `async def set_now(body: dict[str, Any]) -> JsonDict` (D5, §13):
1. `raw = body.get("now")`; not a non-empty `str` →
   `PlannerError(validation, "body must carry an ISO 'now' string")` (400).
2. `parse_fake_now(raw)` — naive datetimes become local, the one §13 parsing rule, reused not
   re-implemented; `ValueError` → `PlannerError(validation, f"invalid ISO datetime: {raw!r}")`.
3. `isinstance(clock, TestClock)` false (test mode without `PLAN_FAKE_NOW` →
   `build_clock` returned a `RealClock`) →
   `PlannerError(validation, "clock is not a TestClock; start with PLAN_FAKE_NOW set")`
   (orchestrator ruling 4).
4. `clock.set(parsed)`; respond
   `{"now": clock.now().isoformat(), "planning_date": planning_date(clock.now(), config.boundary_hour).isoformat()}`.
   The response **is** the planning-date probe the acceptance needs (ruling 4's recommended
   shape, adopted — D5 pins no response shape, test-mode-only surface, no new public API).

**`POST /test/tick-boundary`** — `async def tick_boundary() -> JSONResponse` (D6, §9):
`fn = _lazy("planner.days.scheduler", "run_boundary_tick")`; `None` → `_not_wired()`; else
`report = fn(conn_factory, config, clock, adapters)` — the seam's exact positional signature —
and `JSONResponse(status_code=200, content=report)`.

**`POST /test/tick-dispatcher`** — identical with
`_lazy("planner.dispatch.runtime", "run_tick")`.

Ticks call the runtime synchronously on the event loop: single-user local server, fake
adapters under test mode, and blocking the loop serializes concurrent tick requests — which is
D6's exactly-one-tick semantics working for us (risk R4).

---

## 4. `src/planner/core/server.py` — factory completion (edit)

Everything the T01 stub already fixes is **preserved verbatim**: `http_status_for` +
`_STATUS_BY_CODE` (not_found→404, stale_claim→409, gateway_offline→503, else 400 — T01 plan
~849), the `PlannerError` handler and its `to_payload()` envelope, the six domain-router
inclusions under `/api`, `GET /api/meta` `{ui_debounce_ms, ws_poll_ms, test_mode}`, the
`/assets` StaticFiles mount, `GET /` shell page, `app.state` assignments, and the module
docstring's promise that FastAPI appears only here and in domain `api.py` files (ws.py,
authctx.py, testmode.py are core server-shell modules — same family as server.py; noted in
decisions, risk R10).

Changes, in place:

1. **Lifespan** (ticket.md seam item 2, D6, orchestrator ruling 6). New closure inside
   `create_app`, passed as `FastAPI(title="planner", version="2.0.0", lifespan=_lifespan)`:

   ```python
   @asynccontextmanager
   async def _lifespan(app_: FastAPI) -> AsyncIterator[None]:
       Path(config.db_path).parent.mkdir(parents=True, exist_ok=True)   # Path("x.db").parent
       Path(config.logs_dir).mkdir(parents=True, exist_ok=True)         #   == "." -> no-op
       loops: Any = None
       if not config.test_mode:                                         # D6: never in test mode
           try:
               module = importlib.import_module("planner.core.loops")
               start = module.start_background_loops
           except (ImportError, AttributeError):
               _log.warning("planner.core.loops unavailable; running without background loops")
           else:
               loops = start(config, clock, adapters, conn_factory)     # seam signature, sync
       try:
           yield
       finally:
           if loops is not None:
               await loops.stop()                                       # seam: .stop() awaited
   ```

   `_log = logging.getLogger("planner.server")` at module level. importlib for the same mypy
   reason as testmode (risk R5). The `title_max_chars == 200` assertion **stays at factory
   time** (first line of `create_app`, as today) — it must fire on every construction path,
   including TestClient apps that never enter lifespan; factory time is startup (ruling 6
   "startup keeps the assertion" — satisfied strictly earlier). Dir creation moves into
   lifespan as ruled; the CLI `serve` handler's own `makedirs` (not an owned file) stays and is
   harmlessly idempotent.

2. **WS route delegates to ws.py** — same path and query contract as T01:

   ```python
   @app.websocket("/api/events")
   async def events_ws(websocket: WebSocket, since: int = 0) -> None:
       await tail_events(websocket, since, conn_factory,
                         config.ws_poll_ms, config.events_read_limit)
   ```

3. **Test router mount replaces the inline stubs** — the three `NotImplementedError` routes
   under `if config.test_mode:` are deleted; instead:

   ```python
   if config.test_mode:
       app.include_router(
           build_test_router(config, clock, adapters, conn_factory), prefix="/api"
       )
   ```

4. **Imports added** (ruff I-sorted): `importlib`, `logging`, `contextlib.asynccontextmanager`,
   `collections.abc.AsyncIterator`, `pathlib.Path`, `planner.core.testmode.build_test_router`,
   `planner.core.ws.tail_events`. `authctx` is intentionally **not** imported by server.py —
   it is T10's dependency surface; importing it here would add coupling with no consumer yet.

Signatures unchanged: `create_app(config, clock, adapters, conn_factory) -> FastAPI`,
`http_status_for(code: ErrorCode) -> int`. `plan serve` (cli/main.py, untouched) keeps working
as-is: it builds config/clock/adapters/conn_factory and calls this factory.

---

## 5. Smoke script — `orchestration/tickets/T09-server-shell/smoke.py`

A straight-line script: numbered checks, `print(f"ok {n:02d} — ...")` after each, any failure
raises (assert) → traceback + non-zero exit; `try/finally` guarantees the server subprocess is
terminated and the temp dir removed. Dependencies: stdlib + `httpx`, `fastapi.testclient`,
`websockets.sync.client` (websockets==16.0 pinned — orchestrator ruling 1) — all in
requirements.txt. Not under `tests/`; never touched by pytest or the §18.2 scan.

**Exact run command (from the repo root):**

```
cd /Users/khushaljagota/.hermes/planning-v2 && .venv/bin/python orchestration/tickets/T09-server-shell/smoke.py
```

Setup: `REPO = Path(__file__).resolve().parents[3]`; `tempfile.TemporaryDirectory()` for DB,
logs, lock; `FAKE_NOW = "2026-07-04T12:00:00"`; `PORT = 8799`.

### Phase A — in-process `require_claim` exercises (acceptance: "stale-claim rejection unit-exercisable by calling require_claim directly")

On a temp-file DB via `db.connect` + `create_schema`; two tickets inserted by plain SQL
(`INSERT INTO tickets (id, title, created_at, updated_at) VALUES (...)`, defaults fill the
rest); `run_id, token = dispatch.data.claim(conn, "t_claimed", now, ttl_seconds=900)` — the
real claim machinery, not hand-set columns.

1. **Classification truth table**: `authctx._classify` over the six rows of §1.1 asserting all
   five `RequestContext` fields (private helper, deliberate — this is a self-smoke inside the
   repo, and `request_context` itself needs a live Request; see check 14 for the header path).
2. **Happy path**: ctx(run_id, token) → `require_claim(conn, ctx, "t_claimed", now)` returns
   `None`; again at `now + 899` (one second inside the lease) → still `None`.
3. **Foreign token**: ctx(run_id, `"claim_000000000000"`) → `PlannerError` with
   `code == stale_claim`, `detail["reason"] == "foreign"`, **and**
   `token not in json.dumps(exc.to_payload())` — the stored token is never echoed.
4. **Expired (half-open boundary)**: happy ctx at `now + 900` (exactly `claim_expires`) →
   `reason == "expired"` — asserts the `<=` rule from `claims.is_expired`, not a re-derivation.
5. **Absent claim**: fabricated ctx against unclaimed `t_bare` → `reason == "none_active"`.
6. **Missing half**: ctx(run_id, claim=None) → `reason == "missing_header"`,
   `"X-Plan-Claim" in detail["missing"]`; symmetric ctx(None, token) →
   `"X-Plan-Run-Id" in detail["missing"]`.
7. **Run mismatch**: ctx(`"run_other"`, token) → `reason == "run_mismatch"`.
8. **Unknown ticket** → `PlannerError` with `code == not_found`.
9. **reject_agents**: human ctx passes (returns None); claimed-agent and plain-agent ctxs both
   raise `agent_forbidden`.

### Phase B — TestClient checks (acceptance: test endpoints 404 off / 501 unlanded)

Apps built in-process with `load_config(path=None, env={...explicit...})`, all three adapters
pinned `"fake"`, plain `TestClient(app)` (no context manager — lifespan intentionally not
run; none of these endpoints needs it).

10. **test_mode off** (`env` without `PLAN_TEST_MODE`): `POST /api/test/set-now`,
    `/api/test/tick-boundary`, `/api/test/tick-dispatcher` → all **404** (router unmounted).
11. **test_mode on, T11 unlanded**: for each tick endpoint, if
    `importlib.util.find_spec("planner.dispatch.runtime")` (resp. `"planner.days.scheduler"`)
    is `None` → assert **501** and body **exactly**
    `{"error": {"code": "validation", "message": "runtime not wired yet"}}`; if the module
    exists (T11 landed first — integration-order tolerant) → assert 200 and a JSON dict.
12. **set-now via TestClient**: valid body → 200 with `now` + `planning_date` keys; body
    `{}` → 400 with `code == "validation"`.
13. **set-now without PLAN_FAKE_NOW**: app built with `PLAN_TEST_MODE=1` but no fake-now →
    `POST /api/test/set-now` → 400 `validation` (RealClock in play, ruling 4).

### Phase C — live server (acceptance: boot, meta, WS streams an append, set-now probe, SIGINT)

14. **Boot**: `subprocess.Popen([str(REPO/".venv/bin/plan"), "serve"], cwd=REPO, env=os.environ |
    {PLAN_TEST_MODE: "1", PLAN_FAKE_NOW: FAKE_NOW, PLAN_DB_PATH: tmp/planning.db,
    PLAN_PORT: "8799", PLAN_LOGS_DIR: tmp/logs, PLAN_DISPATCHER_LOCK_PATH: tmp/dispatcher.lock},
    stdout/stderr → tmp/server.log)`. Readiness: poll `GET http://127.0.0.1:8799/api/meta`
    (httpx, 0.25s interval, 15s budget). On timeout, dump server.log and fail.
15. **GET /api/meta** → 200, body has exactly the three keys with `test_mode is True` (§9,
    ticket acceptance).
16. **WS receives an appended event**: connect
    `ws://127.0.0.1:8799/api/events?since=0` via `websockets.sync.client.connect`; from the
    smoke process open a second `db.connect(tmp/planning.db)` and
    `events.append_event(conn2, "t_smoke", EventKind.ticket_created, {"title": "smoke"}, now)`;
    `recv(timeout=5)` → JSON with `events` containing `entity_id == "t_smoke"`,
    `kind == "ticket_created"`, and `cursor == events[-1]["id"]` (§9 shape, section 2.1).
    Append a second event, `recv` again, assert the cursor advanced — continued tailing.
    Close this WS (exercises the server-side `WebSocketDisconnect` path).
17. **set-now changes the planning date** (D5, §6.1): `POST /api/test/set-now
    {"now": "2026-07-06T04:59:00"}` → 200, `planning_date == "2026-07-05"`; then
    `{"now": "2026-07-06T05:00:00"}` → `planning_date == "2026-07-06"` — the probe moves
    across the 05:00 boundary, proving both the mutation and the boundary_hour math.
18. **SIGINT → clean exit**: open a fresh WS `?since=0`, drain its backlog message, **leave it
    open** (a tailer parked mid-poll — exercises cancellation cleanup);
    `proc.send_signal(signal.SIGINT)`; `proc.wait(timeout=15)`; assert `returncode == 0`.
19. `finally`: `proc.kill()` if still alive; close sockets/conns; temp dir auto-removed;
    print `SMOKE PASS (19 checks)`.

---

## 6. Quality gates

- `.venv/bin/ruff check .` — zero findings on the five owned files (rules E,F,W,I,UP,B,
  line 100 per pyproject). Import blocks written pre-sorted; no B008 exposure (no `Depends`
  defaults in this ticket).
- `.venv/bin/mypy src/` — strict, zero errors attributable to owned files. Load-bearing
  choices: `importlib.import_module` keeps the four not-yet-existing T11 symbols out of the
  static import graph; every closure endpoint and the lifespan are fully annotated
  (`AsyncIterator[None]`, `JSONResponse`, `JsonDict`); `isinstance(clock, TestClock)` narrows
  the `Clock` protocol; T11-returned handles are `Any` by construction (runtime seam).
  smoke.py is outside `files=["src"]` — ruff-clean is its gate.
- `./verify` — the existing unit suite stays green: no file under `tests/` is added or
  touched; no unit test imports `core.server`'s removed inline stubs (verified: the suite
  touches days/dispatch/seed/sprints/tickets/instrument only).
- Python 3.14 venv (`.venv/bin/python` = 3.14.3), websockets==16.0 + uvicorn 0.50 pinned
  (ruling 1).

Ticket done when: smoke exits 0 via the exact command in section 5, `./verify` full-suite
green, Codex plan/diff reviews report no violations.

---

## 7. Risks and decisions

- **R1 — 501 body is the literal seam shape, no `detail` key.** T01's envelope invariant says
  `detail` is never omitted, but the seam pins the exact body and is marked binding for both
  T09 and T11 — T11's tests may compare the whole document. Literal seam wins; divergence
  confined to this one non-PlannerError response.
- **R2 — idle dead WS clients linger until the next send or shutdown.** Ruling 5 pins the
  algorithm (sleep on empty; disconnect surfaces on send). A receive-watcher task would detect
  silent disconnects sooner but deviates from the pinned loop. Single-user localhost, every
  interaction appends events, uvicorn shutdown cancels stragglers — accepted.
- **R3 — title assertion stays at factory time; dirs move to lifespan.** The assertion must
  guard TestClient-built apps too (they may never run lifespan); factory time is strictly
  earlier than any request. Dir creation is a filesystem effect and belongs in lifespan;
  the CLI's duplicate `makedirs` is idempotent and its file is not owned here.
- **R4 — tick endpoints call the T11 runtime synchronously on the event loop.** The seam pins
  a plain call returning a report dict; blocking serializes overlapping tick requests, which
  matches D6's exactly-one-tick determinism. If T11 ships an async `run_tick` the seam itself
  changes — not this ticket's call.
- **R5 — importlib instead of `from ... import` at all three lazy seam points.** mypy strict
  fails on imports of modules that don't exist yet; `import_module` + attribute access defers
  to runtime and raises exactly the `ImportError`/`AttributeError` the seam names. The loops
  site catches both too (a stub module without the symbol is the same "not wired" condition).
- **R6 — `reject_agents` rejects plain agents, not only claim-carrying ones.** errors.py pins
  `agent_forbidden` as "claim/agent request hits a human-only action" and §8 makes the CLI
  agent-only; any agent-classified request on an (H) route is wrong.
- **R7 — set-now response `{"now", "planning_date"}`** — ruling 4's recommendation adopted:
  the acceptance's planning-date probe with zero new public surface (test-mode-only response;
  D5 pins no shape). `planning_date` computed via `days.logic.dates.planning_date(clock.now(),
  config.boundary_hour)`, never re-derived.
- **R8 — `not_found` outranks `missing_header`** (ruling 2 lists ticket-exists first), and
  **expiry outranks token equality** (a foreign token on a dead lease reports `expired`,
  consistent with heartbeat checking liveness before anything else).
- **R9 — empty/whitespace headers are absent.** An empty `X-Plan-Claim: ` header classifying
  as a claimed agent with token `""` would compare against real tokens; normalizing to absent
  makes the truth table total and predictable.
- **R10 — ws/authctx/testmode use FastAPI types under `core/`.** The server.py docstring says
  FastAPI appears only in server.py and domain api files; these three modules are the server
  shell split into parts (the ticket itself owns them under core/). Reading: the docstring's
  intent is "no FastAPI below the api layer", which holds — no logic/data module gains a
  FastAPI import.
- **R11 — smoke tolerates T11 landing first**: the 501-vs-200 assertion keys on
  `importlib.util.find_spec`, so integration order between T09 and T11 can't break the smoke.
- **R12 — non-integer `?since=`** hits FastAPI's own WS validation (connection rejected with
  policy-violation close). The T01 contract types `since: int = 0`; default framework behavior
  retained, no custom handling added.

---

## 8. Binding amendments (orchestrator sense-check after codex plan review)

These override the sections above where they differ. Full findings + dispositions in
`plan-review.md`.

- **A1 — reason vocabulary ruling (codex finding 1 refuted).** The `stale_claim` detail keeps
  T01's four base keys plus `ticket_id`, and `reason` is the strict superset
  `expired | foreign | none_active | missing_header | run_mismatch` — the T09 ticket's run-id
  validation and half-pair rejection cannot be truthfully named by T01's three values. The three
  inherited values keep their exact T01 meanings. Section 1.2 stands as written; this ruling is
  recorded for the integrator and T10.
- **A2 — Phase B DB bootstrap.** Phase B creates its own temp DB and runs `create_schema` on it
  before building apps; that path goes into the env consumed by `load_config` (`PLAN_DB_PATH`),
  so the T11-landed 200 path of check 11 runs against a real schema with fake adapters.
- **A3 — T11-readiness probe mirrors `_lazy`.** Everywhere the smoke predicts 501 vs 200
  (checks 11 and the new Phase C tick checks), the probe is
  `getattr(importlib.import_module(mod), fn)` under `except (ImportError, AttributeError)` —
  never `find_spec` — so a landed module missing the symbol predicts 501, exactly like the
  endpoint.
- **A4 — live tick endpoints.** Phase C additionally POSTs `/api/test/tick-boundary` and
  `/api/test/tick-dispatcher` on the live server: expected 501 with the exact seam body, or 200
  with a JSON dict, per the A3 probe. (Check count grows accordingly; final print reflects it.)
- **A5 — port guard.** `PORT = 8799` remains the default. Before boot the smoke attempts a bind
  on 127.0.0.1:8799; if occupied it takes an OS-assigned free port instead and passes it via
  `PLAN_PORT`. Check 15's `test_mode is True` doubles as a served-by-us identity check.
- **A6 — WS disconnect actually exercised.** After check 16 closes WS #1, the smoke appends one
  more event (server's next send hits the closed socket → `WebSocketDisconnect` path), then
  asserts continued health: `GET /api/meta` 200 and a fresh WS `?since=0` replays the full
  backlog including the post-close event. The SIGINT check (18) keeps its own parked-open WS.
- **A7 — disconnect watcher in the tailer (supersedes the §2.2 sleep-only loop and withdraws
  R2's trade-off).** Implementation revealed that the sleep-only loop breaks the ticket's
  "Ctrl-C exits clean" note: a parked tailer never awaits `receive`, so uvicorn (run by the
  un-owned cli/main.py with no `timeout_graceful_shutdown`) waits on it indefinitely — one
  SIGINT hangs the server whenever any idle WS is open. Fix inside the owned file: `tail_events`
  spawns a watcher task `_watch_disconnect(websocket)` that loops on `websocket.receive()` until
  it sees `{"type": "websocket.disconnect"}` (sent both on client close and by uvicorn's
  connection shutdown on SIGINT). The poll loop becomes `while not watcher.done():` and the
  empty-batch sleep becomes `await asyncio.wait({watcher}, timeout=ws_poll_ms/1000)` — same
  cadence, but wakes immediately on disconnect. `finally`: cancel the watcher, reap it with
  `asyncio.wait`, close the conn. `WebSocketDisconnect` on send stays caught; `CancelledError`
  still propagates; still no blanket except around the loop body. Smoke check 18 reverts to the
  strict form: ONE SIGINT with a parked-open WS → returncode 0 within 15s, no second-signal
  escalation. This also upgrades "clean disconnect handling": idle client disconnects are now
  detected promptly, not at the next send.
- **A8 — post-diff-review hardening (codex findings, impl-review.md).** (1) `presented_claim`
  in the stale_claim detail is redacted to the literal `"(redacted)"` whenever the presented
  token equals the stored `claim_lock` — the `expired`, `run_mismatch`, and correct-token
  `missing_header` paths otherwise echo the live stored token into a loggable detail, violating
  the "never echo the real token" rule; foreign/none_active paths still echo the presented
  value verbatim. Smoke checks 4 and 7 now assert the redaction and token absence. (2) The
  smoke's server subprocess is appended to a `procs` list handed to `main` immediately after
  `Popen`, so any Phase C failure still kills the server in `main`'s `finally`. (3) Phase B's
  env pins `PLAN_LOGS_DIR` and `PLAN_DISPATCHER_LOCK_PATH` into the temp dir so a T11-landed
  tick can never write the repo's default `data/` paths.
