# T13 — Chat service and seed endpoint — implementation plan

Stage 4. Turns three stubs into real route bodies plus one framework-free service
module, and adds one new test file. Owned files, and nothing else:

1. `src/planner/chat/service.py` — NEW, framework-free chat passthrough + persistence.
2. `src/planner/chat/api.py` — replace stub; two routes over the service.
3. `src/planner/seed/api.py` — replace stub; one route over `seed_from_source` / `seed_demo`.
4. `tests/unit/test_chat_seed.py` — NEW; TestClient over `create_app` with fake adapters.

Contracts and infra below are law — read, import, never edit:
`chat/contracts.py` (ChatSendResult, GatewayStatus), `core/adapters/base.py`
(GatewayAdapter protocol), `core/adapters/fakes.py` (EchoGatewayAdapter,
OfflineGatewayAdapter), `core/errors.py` (ErrorCode, PlannerError), `core/events.py`
(append_event), `core/contracts.py` (EventKind.chat_session_created),
`core/server.py` (app factory: `app.state.{config,clock,adapters,conn_factory}`;
PlannerError handler maps `gateway_offline`→503, `not_found`→404, default 400),
`seed/contracts.py` (MigrationReport, SkippedSection), `seed/importer.py`
(`seed_from_source(conn, source_dir) -> MigrationReport`), `seed/demo.py`
(`seed_demo(conn) -> None`; raises `db_not_empty` on any non-empty table),
`days/data.py` (`read_day(conn, day_id, now_unix)` materializes per §3.4),
`tickets/data.py` (`create_ticket`, `_txn` BEGIN IMMEDIATE style — reference only).

---

## A. File-by-file blueprint

### A1. `src/planner/chat/service.py` (NEW)

Module docstring: "The chat passthrough (§11). Resolves the chat entity (ticket or
day), sends through the gateway adapter, and — on the first reply only — persists
the minted session key onto the entity and logs one `chat_session_created` event
in a single transaction. Framework-free: no FastAPI/pydantic (SPEC §14 / server.py
docstring). Times arrive as unix-second ints from the caller's clock."

Imports: `from __future__ import annotations`; `import logging`, `import sqlite3`;
`from collections.abc import Iterator`, `from contextlib import contextmanager`;
`from planner.chat.contracts import ChatSendResult, GatewayStatus`;
`from planner.core.adapters.base import GatewayAdapter`;
`from planner.core.contracts import EventKind`;
`from planner.core.errors import ErrorCode, PlannerError`;
`from planner.core.events import append_event`;
`from planner.days.data import read_day`.

`_log = logging.getLogger("planner.chat")`

Dependency check (no cycle): service → adapters.base → chat.contracts (leaf);
service → days.data → days.contracts/core.events (no path back to chat). Clean.

#### `_txn(conn)` — contextmanager, mirrors `tickets/data.py`

```python
@contextmanager
def _txn(conn: sqlite3.Connection) -> Iterator[None]:
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    else:
        conn.execute("COMMIT")
```
One BEGIN IMMEDIATE per write, per SPEC §14 "one canonical writer per edge" and the
data-layer convention. (ticket line "in one BEGIN IMMEDIATE transaction".)

#### `_resolve(conn, entity_id, now) -> tuple[str, str | None]`

Returns `(kind, stored_session_key)` where `kind` is `"ticket"` or `"day"`.

```python
def _resolve(conn: sqlite3.Connection, entity_id: str, now: int) -> tuple[str, str | None]:
    if entity_id.startswith("t_"):
        row = conn.execute(
            "SELECT chat_session_key FROM tickets WHERE id = ?", (entity_id,)
        ).fetchone()
        if row is None:
            raise PlannerError(ErrorCode.not_found, "ticket not found", {"entity_id": entity_id})
        return "ticket", row["chat_session_key"]
    if entity_id.startswith("day_"):
        day = read_day(conn, entity_id, now)          # §3.4: materializes if absent
        return "day", day.chat_session_key
    raise PlannerError(ErrorCode.not_found, "no chattable entity for id", {"entity_id": entity_id})
```

- `t_` prefix → tickets: entity ids are `t_<slug>` (ids.py `ID_PREFIXES["ticket"]="t"`).
  No other id prefix begins `t_` (sprint `sp_`, item `si_`, idea `idea_`, run `run_`),
  so the prefix uniquely selects tickets. Missing row → `not_found`
  (ticket line "must exist else not_found").
- `day_` prefix → days: `read_day` owns §3.4 materialization (ticket line
  "materializes via days.data semantics"); a nonexistent day is created empty and
  its stored key is `None`.
- unknown prefix → `not_found` (see Decisions D4).

Direct `SELECT chat_session_key` (not `read_ticket`) because only the key is needed
and the write is a targeted UPDATE; this keeps the module's cross-domain surface to
`days.data` alone. `read_day` is used for days because materialization must be honored
and that behavior lives only in `days.data`.

#### `send(conn, gateway, entity_id, text, now) -> ChatSendResult`

```python
def send(
    conn: sqlite3.Connection,
    gateway: GatewayAdapter,
    entity_id: str,
    text: str,
    now: int,
) -> ChatSendResult:
    kind, stored_key = _resolve(conn, entity_id, now)
    try:
        result = gateway.send(stored_key, entity_id, text)
    except PlannerError:
        raise                                          # already structured (offline → gateway_offline)
    except Exception as exc:                            # noqa: BLE001 — any real-adapter failure
        raise PlannerError(
            ErrorCode.gateway_offline, "gateway unavailable", {"cause": str(exc)}
        ) from exc
    if stored_key is None:                              # first reply → persist + one event
        table = "tickets" if kind == "ticket" else "days"
        with _txn(conn):
            conn.execute(
                f"UPDATE {table} SET chat_session_key = ?, updated_at = ? WHERE id = ?",
                (result.session_key, now, entity_id),
            )
            append_event(
                conn, entity_id, EventKind.chat_session_created,
                {"session_key": result.session_key}, now,
            )
    return result
```

Maps to: ticket line "pass the entity's stored chat_session_key to the gateway
adapter's send(), on first reply (stored key was NULL) persist the returned session
key on the entity and append a chat_session_created event ({session_key}) in one
BEGIN IMMEDIATE transaction; return reply + key" and §11 "The entity's
chat_session_key persists the session id."

- The gateway call happens **outside** the transaction (it is IO; never hold
  BEGIN IMMEDIATE across it). Persist opens its own short txn afterward.
- `stored_key is None` is the first-reply predicate. On a reused key the branch is
  skipped: no UPDATE, no event (ticket line "second send reuses the key and mints no
  second event"). The EchoGatewayAdapter returns the same key it was handed, so the
  persisted value is stable.
- `table` is chosen from a fixed two-value map, never from request input, so the
  f-string carries no injection surface.
- Exception wrapping (Decisions D5): `PlannerError` re-raised as-is (the
  OfflineGatewayAdapter already raises `gateway_offline`); any other exception from a
  real adapter is wrapped into `gateway_offline` so the route always yields the
  structured 503 (ticket line "also decide how non-PlannerError exceptions … are
  wrapped").

#### `status(gateway) -> GatewayStatus`

```python
def status(gateway: GatewayAdapter) -> GatewayStatus:
    result = gateway.status()
    if result.detail is not None:
        _log.info("gateway status detail: %s", result.detail)   # logged, not shown
    return result
```
Maps to §11 availability signal. `detail` is logged here and never returned to the
client; the route surfaces only `available` (Decisions D6). No exception wrapping —
`status()` is a total availability probe by contract (the OfflineGatewayAdapter
returns `available=False` rather than raising); adding speculative catch-all here
would violate PRINCIPLES "no speculative resilience".

### A2. `src/planner/chat/api.py` (replace stub)

Docstring keeps "Chat routes (§9): send a message, read gateway availability."

Imports: `from __future__ import annotations`; `from dataclasses import asdict`;
`import sqlite3`; `from collections.abc import Callable`; `from typing import Any`;
`from fastapi import APIRouter, Request`; `from planner.chat import service`;
`from planner.core.adapters.registry import Adapters`;
`from planner.core.clock import Clock`;
`from planner.core.errors import ErrorCode, PlannerError`.

```python
router = APIRouter()


@router.post("/chat/{entity_id}/send")
async def send_message(entity_id: str, body: dict[str, Any], request: Request) -> dict[str, Any]:
    text = body.get("text")
    if not isinstance(text, str):
        raise PlannerError(ErrorCode.validation, "text is required")
    clock: Clock = request.app.state.clock
    adapters: Adapters = request.app.state.adapters
    conn_factory: Callable[[], sqlite3.Connection] = request.app.state.conn_factory
    conn = conn_factory()
    try:
        result = service.send(conn, adapters.gateway, entity_id, text, clock.now_unix())
    finally:
        conn.close()
    return asdict(result)


@router.get("/chat/{entity_id}/status")
async def gateway_status(entity_id: str, request: Request) -> dict[str, Any]:
    adapters: Adapters = request.app.state.adapters
    result = service.status(adapters.gateway)
    return {"available": result.available}
```

- `app.state.*` reads are annotated with their concrete types so mypy strict keeps
  the `Any` from `request.app.state` from leaking (server.py sets exactly these four).
- `conn` is opened per request and always closed in `finally` (WAL: don't leak
  connections across requests). Errors from `service.send` (not_found /
  gateway_offline) propagate through `finally` to the PlannerError handler; the
  service's own txn has already rolled back, so the connection is clean at close.
- `status` ignores `{entity_id}` for resolution (Decisions D7): gateway availability
  is entity-agnostic (`GatewayAdapter.status()` takes no args). The path segment is
  retained only to keep the per-entity route shape from the stub / §9.
- `text` presence is minimal input validation (a string is required to send); it is
  not actor gating (Decisions D1).

### A3. `src/planner/seed/api.py` (replace stub)

Docstring keeps "Seed route (§9): POST /api/seed runs a migration and returns a
MigrationReport."

Imports: `from __future__ import annotations`; `from dataclasses import asdict`;
`import sqlite3`; `from collections.abc import Callable`; `from pathlib import Path`;
`from typing import Any`; `from fastapi import APIRouter, Request`;
`from planner.core.errors import ErrorCode, PlannerError`;
`from planner.seed.contracts import MigrationReport`;
`from planner.seed.demo import seed_demo`;
`from planner.seed.importer import seed_from_source`.

```python
router = APIRouter()


def _demo_report(conn: sqlite3.Connection) -> MigrationReport:
    def count(sql: str) -> int:
        return int(conn.execute(sql).fetchone()[0])
    return MigrationReport(
        sprints=count("SELECT COUNT(*) FROM sprints"),
        sprint_items=count("SELECT COUNT(*) FROM sprint_items WHERE sprint_id IS NOT NULL"),
        deferred_items=count("SELECT COUNT(*) FROM sprint_items WHERE sprint_id IS NULL"),
        tickets=count("SELECT COUNT(*) FROM tickets"),
        ideas=count("SELECT COUNT(*) FROM ideas"),
        links=count("SELECT COUNT(*) FROM links"),
        duplicates_skipped=0,
        skipped=[],
    )


@router.post("/seed")
async def seed(body: dict[str, Any], request: Request) -> dict[str, Any]:
    source_dir = body.get("source_dir")
    demo = bool(body.get("demo", False))
    has_source = source_dir is not None
    if has_source == demo:                                   # both, or neither
        raise PlannerError(
            ErrorCode.validation, "provide exactly one of source_dir or demo"
        )
    conn_factory: Callable[[], sqlite3.Connection] = request.app.state.conn_factory
    conn = conn_factory()
    try:
        if demo:
            seed_demo(conn)                                  # raises db_not_empty if not empty
            report = _demo_report(conn)
        else:
            if not isinstance(source_dir, str) or not Path(source_dir).is_dir():
                raise PlannerError(
                    ErrorCode.validation, "source_dir must be an existing directory"
                )
            report = seed_from_source(conn, source_dir)
    finally:
        conn.close()
    return asdict(report)
```

Maps to: ticket line "POST /api/seed with body {source_dir} or {demo:true}: validate
exactly-one; source_dir must exist and be a directory; no default path, no reaching
into ~/.hermes/planning; run seed_from_source or seed_demo against a connection from
app.state.conn_factory; respond with the MigrationReport serialized exactly from the
dataclass."

- **Exactly-one** via `has_source == demo`: both-present → `True == True`; neither →
  `False == False`; both raise. Exactly one → mismatch → proceed.
- **No default path**: `source_dir` is only ever the caller's value; there is no
  fallback to any live directory (§12 "The live directory is never read"; PRINCIPLES
  "Never touch ~/.hermes/planning").
- **Path validation** (`is_dir()`) catches both "missing dir" and "file-not-dir"
  before touching the importer (the importer additionally rejects a valid dir with no
  `sprints/current/sprint-kickoff.md`, also as `validation`).
- **Demo report derivation** (Decisions D2): `seed_demo` returns `None`, so the report
  is rebuilt from post-run row counts. `sprint_items` = items with a sprint;
  `deferred_items` = items with NULL sprint; `links` = all link rows;
  `duplicates_skipped=0`, `skipped=[]` because a demo run never dedupes or skips.
- `db_not_empty` from `seed_demo` propagates as a PlannerError → default 400 (it is
  not in `_STATUS_BY_CODE`).
- `_demo_report` doing SQL count is thin route glue (reads only); it stays in the
  route because `demo.py` is out of this ticket's scope.

---

## B. Response JSON shapes (justified from the contract dataclasses)

### B1. `POST /api/chat/{entity_id}/send` → 200

`asdict(ChatSendResult)` where `ChatSendResult(reply_text: str, session_key: str)`:

```json
{ "reply_text": "echo: <text>", "session_key": "<key>" }
```

For the echo adapter with text `"hello"` on a fresh app: `reply_text="echo: hello"`,
`session_key="fake-sess-1"` (EchoGatewayAdapter mints `fake-sess-<n>` from `n=1` on a
`None` key). Direct from the contract dataclass — no invented fields. (ticket line
"return reply + key"; §11 echo behavior.)

### B2. `GET /api/chat/{entity_id}/status` → 200

Constructed explicitly from `GatewayStatus.available` only:

```json
{ "available": true }     // echo
{ "available": false }    // offline
```

`GatewayStatus.detail` is intentionally omitted (contract comment: "logged, not
shown"). The route builds the dict field-by-field rather than `asdict`, guaranteeing
`detail` can never reach the wire. (ticket acceptance "status returns {available:
false}".)

### B3. `POST /api/seed` → 200

`asdict(MigrationReport)`; `SkippedSection` (frozen dataclass) serializes recursively:

```json
{
  "sprints": 0, "sprint_items": 0, "deferred_items": 0, "tickets": 0,
  "ideas": 0, "links": 0, "duplicates_skipped": 0,
  "skipped": [ { "source_file": "...", "heading": "... | null", "reason": "...", "excerpt": "..." } ]
}
```

Demo dataset (from `_demo_report`): `{"sprints":1, "sprint_items":3,
"deferred_items":0, "tickets":8, "ideas":0, "links":3, "duplicates_skipped":0,
"skipped":[]}` — matches the §12 demo counts named in the ticket.

Fixture import (`tests/fixtures/planning-md`, `seed_from_source` result, ground truth
from `test_a19`): `{"sprints":1, "sprint_items":6, "deferred_items":3, "tickets":4,
"ideas":3, "links":1, "duplicates_skipped":0, "skipped":[...6 entries...]}`.

### B4. Error envelope (all failures)

`PlannerError.to_payload()` → `{"error": {"code", "message", "detail"}}`, mapped by
the server handler: `gateway_offline`→503, `not_found`→404, `db_not_empty`/
`validation`→400.

---

## C. Transaction / event sequence for first-reply key persistence

Single request `POST /api/chat/{entity_id}/send`:

1. Route: `now = clock.now_unix()`, `conn = conn_factory()`.
2. `service.send` → `_resolve`:
   - ticket: `SELECT chat_session_key` (autocommit read). Missing → `not_found`,
     nothing written.
   - day: `read_day` materializes if absent (autocommit INSERT + `day_created`
     event), returns `chat_session_key` (`None` for a new day).
3. `gateway.send(stored_key, entity_id, text)` — outside any transaction.
   - Offline adapter raises `PlannerError(gateway_offline)` → re-raised → no persist,
     no `chat_session_created` event; entity key stays as read.
   - Real-adapter non-PlannerError → wrapped to `gateway_offline` → same: no persist.
4. If `stored_key is None` (first reply), one `BEGIN IMMEDIATE` txn:
   a. `UPDATE tickets|days SET chat_session_key = result.session_key, updated_at = now
      WHERE id = entity_id`.
   b. `append_event(entity_id, chat_session_created, {"session_key": result.session_key}, now)`.
   c. `COMMIT` (or `ROLLBACK` on any failure inside).
5. Return `ChatSendResult`; route closes `conn` in `finally`; serialize `asdict`.

Second send to the same entity: `_resolve` returns the persisted key (not `None`) →
step 4 skipped entirely → exactly one `chat_session_created` event ever, key stable.

`chat_session_key` is infrastructure state (like `updated_at`), not a resolution-engine
value (`fields.*.value` / `state`), so the chat service writing it directly is
consistent with "one canonical writer per edge" — the chat service is that writer for
this field, for both entity kinds.

---

## D. Test list — `tests/unit/test_chat_seed.py` (NEW)

Module-level helpers (local to this file; `conftest.py` and `test_seed.py` untouched):

```python
FIXTURE = Path(__file__).resolve().parent.parent / "fixtures" / "planning-md"

def _make_app(tmp_path, gateway="fake"):
    db_path = tmp_path / "planning-test.db"
    boot = connect(str(db_path)); create_schema(boot); boot.close()
    config = load_config(path=None, env={
        "PLAN_TEST_MODE": "1",
        "PLAN_GATEWAY_ADAPTER": gateway,          # "fake" → echo, "offline" → offline
        "PLAN_DB_PATH": str(db_path),
    })
    clock = build_clock(config)
    adapters = build_adapters(config)
    def conn_factory(): return connect(str(db_path))
    return create_app(config, clock, adapters, conn_factory), db_path
```

- `env=` isolates config from the process environment (conftest `cfg` uses `env={}`).
- `test_mode=1` keeps the lifespan from starting background loops and keeps the DB the
  tmp file; `gateway_adapter` explicit so `auto` never resolves to `real`.
- Imports: `pytest`, `json`, `sqlite3.connect` (via `planner.core.db.connect`),
  `create_schema`, `load_config`, `build_clock`, `build_adapters`, `create_app`,
  `create_ticket` (from `planner.tickets.data`, to mint a real ticket id),
  `fastapi.testclient.TestClient`. Each test runs inside `with TestClient(app) as
  client:` so the lifespan (startup/shutdown) fires. A helper `_ticket(db_path)` opens
  a connection, calls `create_ticket(conn, title="Chat me.", actor="human", now=0,
  title_max_chars=200)`, closes, and returns `.id`.

Tests (each builds its own app + tmp DB → full isolation; the echo adapter's session
counter therefore starts at 1 in every test):

1. **`test_chat_send_echo_persists_key_and_event`** (gateway=fake)
   - Create ticket `tid`. `POST /api/chat/{tid}/send {"text":"hello"}` → 200; body ==
     `{"reply_text":"echo: hello","session_key":"fake-sess-1"}`.
   - DB: `SELECT chat_session_key FROM tickets WHERE id=tid` == `"fake-sess-1"`.
   - Events: exactly one row `entity_id=tid AND kind='chat_session_created'`; its JSON
     payload == `{"session_key":"fake-sess-1"}`.

2. **`test_chat_send_second_send_reuses_key_no_new_event`** (gateway=fake)
   - Create ticket `tid`. First `send {"text":"hello"}` → session `fake-sess-1`.
   - Second `send {"text":"again"}` → 200; body ==
     `{"reply_text":"echo: again","session_key":"fake-sess-1"}` (key reused).
   - DB key still `"fake-sess-1"`; `COUNT` of `chat_session_created` events for `tid`
     == 1.

3. **`test_chat_send_day_materializes_and_persists`** (gateway=fake)
   - `POST /api/chat/day_2026-07-04/send {"text":"plan check"}` → 200; body ==
     `{"reply_text":"echo: plan check","session_key":"fake-sess-1"}`.
   - DB: `days` row `day_2026-07-04` exists with `chat_session_key="fake-sess-1"`.
   - Events for `day_2026-07-04` include one `day_created` and exactly one
     `chat_session_created` with payload `{"session_key":"fake-sess-1"}`.

4. **`test_chat_send_ticket_not_found`** (gateway=fake)
   - `POST /api/chat/t_missing/send {"text":"x"}` → 404; body ==
     `{"error":{"code":"not_found", ...}}` (`t_` prefix, no such ticket).

5. **`test_chat_send_unknown_prefix_not_found`** (gateway=fake)
   - `POST /api/chat/xyz/send {"text":"x"}` → 404; `error.code == "not_found"`
     (neither `t_` nor `day_`; Decisions D4).

6. **`test_chat_send_offline_is_503_and_no_persist`** (gateway=offline)
   - Create ticket `tid`. `POST /api/chat/{tid}/send {"text":"hello"}` → 503; body ==
     `{"error":{"code":"gateway_offline", ...}}`.
   - DB: `chat_session_key` for `tid` is still `NULL`; zero `chat_session_created`
     events for `tid` (adapter raised before persist).

7. **`test_chat_status_offline_false`** (gateway=offline)
   - `GET /api/chat/t_anything/status` → 200; body == `{"available": false}` exactly
     (`detail` absent — D6).

8. **`test_chat_status_echo_true`** (gateway=fake)
   - `GET /api/chat/t_anything/status` → 200; body == `{"available": true}`.

9. **`test_seed_demo_counts`** (gateway=fake, empty DB)
   - `POST /api/seed {"demo": true}` → 200; body == `{"sprints":1,"sprint_items":3,
     "deferred_items":0,"tickets":8,"ideas":0,"links":3,"duplicates_skipped":0,
     "skipped":[]}`.

10. **`test_seed_demo_twice_is_db_not_empty`** (gateway=fake)
    - First `POST /api/seed {"demo": true}` → 200.
    - Second `POST /api/seed {"demo": true}` → 400; `error.code == "db_not_empty"`.

11. **`test_seed_source_dir_imports_fixture`** (gateway=fake, empty DB)
    - `POST /api/seed {"source_dir": str(FIXTURE)}` → 200; assert the count fields ==
      `{"sprints":1,"sprint_items":6,"deferred_items":3,"tickets":4,"ideas":3,
      "links":1,"duplicates_skipped":0}` and `len(body["skipped"]) == 6`; spot-check
      `body["skipped"][0]` has keys `source_file, heading, reason, excerpt`.

12. **`test_seed_missing_dir_is_validation`** (gateway=fake)
    - `POST /api/seed {"source_dir": str(tmp_path / "nope")}` → 400;
      `error.code == "validation"`.

13. **`test_seed_file_not_dir_is_validation`** (gateway=fake)
    - Write a file `f = tmp_path / "a.md"` with any text. `POST /api/seed
      {"source_dir": str(f)}` → 400; `error.code == "validation"`.

14. **`test_seed_neither_key_is_validation`** (gateway=fake)
    - `POST /api/seed {}` → 400; `error.code == "validation"`.

15. **`test_seed_both_keys_is_validation`** (gateway=fake)
    - `POST /api/seed {"source_dir": str(FIXTURE), "demo": true}` → 400;
      `error.code == "validation"`.

Coverage vs acceptance: echo send + persist + event (1–3), reuse/no second event (2),
offline 503 + status false (6,7), demo counts (9), demo-twice db_not_empty (10),
source_dir validation missing/file/neither/both (12–15), fixture import ground truth
(11). ruff + mypy strict + this suite land through `./verify`.

---

## E. Decisions

**D1 — authctx / actor gating: none.** Neither route imports `core.authctx`; no
`require_claim`, no `reject_agents`. SPEC §11 (chat) and §12 (seed) name no actor
restriction, and §9's human-only (H) actions list (accept, approve, unblock, grant,
sprint create/freeze, day-plan, drop) does **not** include chat or seed. Chat is used
by both the planner-main chat agent and the human; seed is a migration/dogfood
operation the CLI (T12) drives. Adding a gate would invent policy the SPEC does not
name, violating the ticket's "invent no gating the SPEC doesn't name". The only
request checking done is minimal input shape validation (chat: `text` must be a
string; seed: exactly-one key + directory existence).

**D2 — demo report derivation.** `seed_demo` returns `None`, so the route rebuilds the
`MigrationReport` from post-run row counts (`_demo_report`). `sprint_items` counts
items with a non-NULL sprint (the 3 demo items all carry the sprint); `deferred_items`
counts NULL-sprint items (0); `links` counts all link rows (3 = 2 belongs_to + 1
blocks); `duplicates_skipped=0` and `skipped=[]` since a demo run neither dedupes nor
skips. This yields exactly the §12 demo numbers. Deriving from the DB (rather than
threading a report out of `demo.py`, which is out of scope) is the natural fit named in
the ticket.

**D3 — seed report serialization.** Return `dataclasses.asdict(report)` verbatim for
both paths, so the JSON is exactly the `MigrationReport` (and nested `SkippedSection`)
shape — no hand-written dict, no field renaming (ticket: "serialized exactly from the
dataclass").

**D4 — unknown-prefix entity ids → `not_found` (404).** An id that is neither `t_…`
nor `day_…` maps to no chattable entity, so `_resolve` raises `not_found`. `validation`
was considered (structurally unsupported id) but rejected: the route's job is to
resolve an entity, and "no such chattable entity" is uniformly a 404, matching the
missing-ticket case the ticket already specifies. The panel treats it like any missing
entity.

**D5 — wrapping unexpected adapter exceptions.** In `send`, `PlannerError` is
re-raised unchanged (the OfflineGatewayAdapter already raises the structured
`gateway_offline`); any other exception from a real adapter (e.g. connection refused
from the live gateway) is wrapped into `PlannerError(gateway_offline, …, {"cause": …})`
so the route **always** yields the structured 503 the panel consumes (§11 "the panel
renders 'gateway offline' and the rest of the UI is unaffected"). No wrapping in
`status`: `status()` is a total availability probe by contract — it returns
`available=False` rather than raising (OfflineGatewayAdapter demonstrates this), and
speculative catch-all there would breach PRINCIPLES "no speculative resilience".

**D6 — status `detail` is logged, not shown.** `service.status` logs `detail` at INFO
when present; the route returns `{"available": result.available}` built field-by-field
(never `asdict`), so `detail` cannot leak to the client. Honors the contract comment
and the acceptance body `{"available": false}` exactly.

**D7 — status is entity-agnostic.** `GatewayAdapter.status()` takes no arguments;
availability is gateway-wide, not per-entity. The `{entity_id}` path segment is kept
for route-shape symmetry with `/send` (and §9's per-entity chat surface) but is not
used to resolve anything in the status handler.

**D8 — connection lifecycle.** Each route opens one connection from
`app.state.conn_factory()` and closes it in `finally` (WAL: avoid leaking connections /
file locks across requests). The service's `_txn` rolls back on any in-transaction
failure, so the connection is never mid-transaction at close. `app.state` reads are
locally annotated (`Clock`, `Adapters`, `Callable[[], sqlite3.Connection]`) to satisfy
mypy strict against Starlette's `Any`-typed `state`.

---

## F. Binding amendments (post codex plan review — see plan-review.md)

These override the corresponding sections above.

**A1 — canonical day ids only; no materialization for malformed `day_` ids**
(overrides the `day_` branch of `_resolve`, section A1). Before calling `read_day`,
the suffix after `day_` must be a canonical ISO date:

```python
if entity_id.startswith("day_"):
    raw = entity_id[len("day_"):]
    try:
        canonical = date.fromisoformat(raw).isoformat() == raw
    except ValueError:
        canonical = False
    if not canonical:
        raise PlannerError(ErrorCode.not_found, "no chattable entity for id", {"entity_id": entity_id})
    day = read_day(conn, entity_id, now)
    return "day", day.chat_session_key
```

The round-trip comparison pins the exact `YYYY-MM-DD` form (`fromisoformat` alone
accepts compact variants). Error code is `not_found`, consistent with D4: an id that
cannot name a day names no chattable entity. Add `from datetime import date` to the
service imports. New test (D-list item 16):

16. **`test_chat_send_malformed_day_id_rejected_no_materialization`** (gateway=fake)
    - `POST /api/chat/day_bogus/send {"text":"x"}` → 404; `error.code == "not_found"`.
    - DB: `SELECT COUNT(*) FROM days` == 0 and zero events (nothing materialized).

**A2 — guarded first-reply persist; at most one key and one event ever**
(overrides step 4 of the `send` flow, sections A1/C). The persist transaction
re-checks under the write lock:

```python
    if stored_key is None:
        table = "tickets" if kind == "ticket" else "days"
        with _txn(conn):
            cursor = conn.execute(
                f"UPDATE {table} SET chat_session_key = ?, updated_at = ? "
                "WHERE id = ? AND chat_session_key IS NULL",
                (result.session_key, now, entity_id),
            )
            if cursor.rowcount == 1:
                append_event(
                    conn, entity_id, EventKind.chat_session_created,
                    {"session_key": result.session_key}, now,
                )
            else:                       # a concurrent first send won; adopt its key
                row = conn.execute(
                    f"SELECT chat_session_key FROM {table} WHERE id = ?", (entity_id,)
                ).fetchone()
                result = ChatSendResult(
                    reply_text=result.reply_text, session_key=row["chat_session_key"]
                )
    return result
```

The `AND chat_session_key IS NULL` guard makes the winner unambiguous inside
`BEGIN IMMEDIATE`; the loser emits no event and returns the persisted (winning) key
so the panel never sees a discarded session. No per-entity locking around the
gateway call (see plan-review.md, finding 2 disposition). New service-level test
(D-list item 17), deterministic, no threads:

17. **`test_chat_send_lost_race_adopts_winner_key_no_event`** — calls
    `service.send` directly with a stub gateway (local class implementing the
    `GatewayAdapter` protocol) whose `send()` writes `chat_session_key = "winner-key"`
    for the entity through its own DB connection before returning
    `ChatSendResult(reply_text="echo: x", session_key="loser-key")`. Assert the
    returned `session_key == "winner-key"`, the DB keeps `"winner-key"`, and zero
    `chat_session_created` events exist for the entity.

**A3 — `MigrationReport.links` counts belongs_to only** (overrides `_demo_report`
in section A3, the demo JSON in B3, and test 9). The contract defines `links` as
"belongs_to links made by title match", so:

```python
        links=count("SELECT COUNT(*) FROM links WHERE kind = 'belongs_to'"),
```

Expected demo report becomes `{"sprints":1, "sprint_items":3, "deferred_items":0,
"tickets":8, "ideas":0, "links":2, "duplicates_skipped":0, "skipped":[]}` — the demo's
blocks link is real in the DB but is not a report-countable link. Test 9's expected
body changes to `links: 2` accordingly. Fixture test 11 is unaffected (the importer
builds its own report; ground truth stays `links: 1`).
