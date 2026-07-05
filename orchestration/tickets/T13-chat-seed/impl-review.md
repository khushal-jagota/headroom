# T13 — Codex implementation review

- **Ticket:** T13 — Chat service and seed endpoint (stage 4)
- **Files audited:** `src/planner/chat/service.py`, `src/planner/chat/api.py`, `src/planner/seed/api.py`
- **Contract references:** `orchestration/tickets/T13-chat-seed/ticket.md`, SPEC.md §11 (Chat), §12 (Seed and migration)
- **Reviewer:** Codex CLI (`codex exec`, model gpt-5.5, xhigh reasoning), read-only sandbox
- **Session id:** 019f2fdf-7097-7122-a8e8-a92fb0edd03d
- **Verdict:** **NO VIOLATIONS** + 3 optional suggestions

## Verdict

Codex traced every requested behavior against concrete line references and the surrounding contracts (`core/errors.py`, `core/server.py` status map, `core/adapters/base.py`, `core/adapters/fakes.py`, `chat/contracts.py`, `seed/contracts.py`, `seed/demo.py`, `seed/importer.py`) before ruling. Confirmed faithful:

- First-reply session-key persistence for both ticket and day entities, with exactly one `chat_session_created` event, guarded by `WHERE ... chat_session_key IS NULL` so a lost race adopts the winner's key and emits no second event.
- Gateway-offline path returns the structured `gateway_offline` error mapped to 503 (server.py `http_status_for` maps `gateway_offline → 503`); the offline fake raises the structured error; `status` reports `{available: false}` with `detail` logged-not-shown; the rest of the app is unaffected.
- `POST /api/seed` handles `source_dir` and `demo: true` with exactly-one validation, returns the full `MigrationReport` shape via `asdict` (no hidden/silent skips), and `seed_demo` on a non-empty DB yields `db_not_empty`.
- No path reads outside the given `source_dir`: the importer builds only fixed child paths under the provided root; there is no default/fallback path and no reach into `~/.hermes/planning/`.
- Routes are thin — no domain rules embedded in the handlers.

## Suggestions and dispositions

**1. Move `_demo_report` from `seed/api.py:21` into the seed domain module to keep the API file thinner.**
Disposition: **Refute (out of scope).** The demo path (`seed/demo.py`) returns `None`, so the report is derived from post-run row counts. Relocating `_demo_report` would require editing `seed/demo.py`, which is outside T13's owned files. This was a deliberate, documented scope decision in the ticket plan (Decision D2: "Deriving from the DB rather than threading a report out of demo.py, which is out of scope"). The count derivation is thin, read-only glue and correctly stays in the route for this ticket. Fixed count SQL, no injection surface. No change.

**2. Add explicit symlink/canonical path checks around `source_dir` if physical path confinement matters.**
Disposition: **Refute (speculative, not contract-required).** The contract requirement is "source_dir must exist and be a directory; no default path, no reaching into `~/.hermes/planning/` by default" — all satisfied. `source_dir` is a caller-supplied path for a human-driven migration/dogfood operation (CLI T12 drives it), not untrusted network input. The importer already restricts itself to fixed child paths under the given root. Adding symlink/realpath confinement would be speculative resilience against a threat the SPEC does not name (PRINCIPLES: "no speculative resilience"). No change.

**3. Tighten `demo` parsing at `seed/api.py:40` to require literal JSON `true` rather than arbitrary truthy values.**
Disposition: **Refute (cosmetic, no contract backing; deliberate choice).** `demo = bool(body.get("demo", False))` coerces truthy inputs. The contract and acceptance only exercise `demo: true`; nothing requires rejecting other truthy encodings, and lenient bool coercion is the plan's chosen shape. Changing it would alter the exactly-one interaction (e.g. `{"demo": 1}` would flip from a demo run to a `validation` error) without any contract mandate. This is a taste nit, not a defect. No change.

## Fixes applied

None. Codex returned NO VIOLATIONS; all three items are optional suggestions that are either out of the ticket's owned-file scope or speculative/cosmetic. No code was touched.

## Gate results (post-review, no code changes)

- `ruff check` on the three T13 files: **All checks passed!** (exit 0)
- `.venv/bin/mypy src/`: **Success: no issues found in 83 source files**
- `.venv/bin/pytest tests/unit/test_chat_seed.py -q`: **17 passed** (StarletteDeprecationWarning only)

---

## Full codex transcript

```text
Reading additional input from stdin...
OpenAI Codex v0.142.5
--------
workdir: /Users/khushaljagota/.hermes/planning-v2
model: gpt-5.5
provider: openai
approval: never
sandbox: read-only
reasoning effort: xhigh
reasoning summaries: none
session id: 019f2fdf-7097-7122-a8e8-a92fb0edd03d
--------
user
Audit three small files against their contract. Read orchestration/tickets/T13-chat-seed/ticket.md and SPEC.md sections 11 and 12, then src/planner/chat/service.py, src/planner/chat/api.py, src/planner/seed/api.py. Check concretely: session-key persistence on first reply for both ticket and day entities with the chat_session_created event; gateway-offline path returns the structured gateway_offline error (503) and status endpoint reports available:false while the rest of the app is unaffected; POST /api/seed handles source_dir and demo:true, returns the full MigrationReport shape, demo on non-empty DB yields db_not_empty; no path reads outside the given source dir; routes are thin (no domain rules in handlers). Number findings with file/line; if faithful output NO VIOLATIONS plus up to 3 suggestions.
deprecated: `[features].web_search_request` is deprecated because web search is enabled by default.
Set `web_search` to `"live"`, `"indexed"`, `"cached"`, or `"disabled"` at the top level (or under a profile) in config.toml if you want to override it.
warning: Skill descriptions were shortened to fit the 2% skills context budget. Codex can still see every skill, but some descriptions are shorter. Disable unused skills or plugins to leave more room for the rest.
codex
I’ll read the contract first, then the three implementation files and check each requested behavior against concrete line references.
exec
/bin/zsh -lc "sed -n '1,240p' orchestration/tickets/T13-chat-seed/ticket.md" in /Users/khushaljagota/.hermes/planning-v2
 succeeded in 0ms:
# T13 — Chat service and seed endpoint (stage 4)

## Scope

The chat passthrough and the seed API. Small ticket.

Contracts: `chat/contracts.py`, `seed/` importer+demo (T07), adapter registry (gateway), `core/authctx.py`. SPEC §11, §12.

## Files owned

- `src/planner/chat/service.py` — send(entity_id, text): resolve entity (ticket `t_`/day `day_` prefix), pass its `chat_session_key` to the gateway adapter, persist a newly minted session key on first reply (`chat_session_created` event), return reply + key. Gateway unavailable → `gateway_offline` structured error (the panel renders the notice; the rest of the UI unaffected).
- `src/planner/chat/api.py` — `POST /api/chat/{entity_id}/send`, `GET /api/chat/{entity_id}/status` over the service.
- `src/planner/seed/api.py` — `POST /api/seed` `{source_dir}` or `{demo: true}` → runs importer/demo inside the server process against its DB, returns the MigrationReport JSON (structured shape from seed contracts). Path validation: source_dir must exist and be a directory; never resolves outside given path semantics (no reaching into `~/.hermes/planning/` by default anywhere in code).

## Acceptance for integration

Self-smoke: echo-gateway server → send on a ticket returns `echo: <text>` and persists session key (visible via ticket JSON); offline-gateway server → 503 gateway_offline and status `{available: false}`; `POST /api/seed {demo}` on empty temp DB → report with the §12 demo counts; second demo call → db_not_empty error. ruff + mypy strict clean; unit suite stays green.

exec
/bin/zsh -lc "nl -ba src/planner/chat/service.py | sed -n '1,260p'" in /Users/khushaljagota/.hermes/planning-v2
 succeeded in 0ms:
     1	"""The chat passthrough (§11). Resolves the chat entity (ticket or day), sends
     2	through the gateway adapter, and — on the first reply only — persists the minted
     3	session key onto the entity and logs one chat_session_created event in a single
     4	transaction. Framework-free: no FastAPI/pydantic (SPEC §14 / server.py docstring).
     5	Times arrive as unix-second ints from the caller's clock."""
     6	
     7	from __future__ import annotations
     8	
     9	import logging
    10	import sqlite3
    11	from collections.abc import Iterator
    12	from contextlib import contextmanager
    13	from datetime import date
    14	
    15	from planner.chat.contracts import ChatSendResult, GatewayStatus
    16	from planner.core.adapters.base import GatewayAdapter
    17	from planner.core.contracts import EventKind
    18	from planner.core.errors import ErrorCode, PlannerError
    19	from planner.core.events import append_event
    20	from planner.days.data import read_day
    21	
    22	_log = logging.getLogger("planner.chat")
    23	
    24	
    25	@contextmanager
    26	def _txn(conn: sqlite3.Connection) -> Iterator[None]:
    27	    conn.execute("BEGIN IMMEDIATE")
    28	    try:
    29	        yield
    30	    except BaseException:
    31	        conn.execute("ROLLBACK")
    32	        raise
    33	    else:
    34	        conn.execute("COMMIT")
    35	
    36	
    37	def _resolve(conn: sqlite3.Connection, entity_id: str, now: int) -> tuple[str, str | None]:
    38	    if entity_id.startswith("t_"):
    39	        row = conn.execute(
    40	            "SELECT chat_session_key FROM tickets WHERE id = ?", (entity_id,)
    41	        ).fetchone()
    42	        if row is None:
    43	            raise PlannerError(ErrorCode.not_found, "ticket not found", {"entity_id": entity_id})
    44	        key: str | None = row["chat_session_key"]
    45	        return "ticket", key
    46	    if entity_id.startswith("day_"):
    47	        # A day id is a canonical YYYY-MM-DD; round-trip the suffix because
    48	        # fromisoformat alone accepts compact variants. A non-canonical id names
    49	        # no day, so it is not_found rather than a materialized empty day.
    50	        raw = entity_id[len("day_") :]
    51	        try:
    52	            canonical = date.fromisoformat(raw).isoformat() == raw
    53	        except ValueError:
    54	            canonical = False
    55	        if not canonical:
    56	            raise PlannerError(
    57	                ErrorCode.not_found, "no chattable entity for id", {"entity_id": entity_id}
    58	            )
    59	        day = read_day(conn, entity_id, now)  # §3.4: materializes if absent
    60	        return "day", day.chat_session_key
    61	    raise PlannerError(ErrorCode.not_found, "no chattable entity for id", {"entity_id": entity_id})
    62	
    63	
    64	def send(
    65	    conn: sqlite3.Connection,
    66	    gateway: GatewayAdapter,
    67	    entity_id: str,
    68	    text: str,
    69	    now: int,
    70	) -> ChatSendResult:
    71	    kind, stored_key = _resolve(conn, entity_id, now)
    72	    # The gateway call is IO; it runs outside any transaction.
    73	    try:
    74	        result = gateway.send(stored_key, entity_id, text)
    75	    except PlannerError:
    76	        raise  # already structured (offline adapter raises gateway_offline)
    77	    except Exception as exc:  # noqa: BLE001
    78	        raise PlannerError(
    79	            ErrorCode.gateway_offline, "gateway unavailable", {"cause": str(exc)}
    80	        ) from exc
    81	    if stored_key is None:  # first reply → persist the key and log one event
    82	        table = "tickets" if kind == "ticket" else "days"  # fixed map, never request input
    83	        with _txn(conn):
    84	            cursor = conn.execute(
    85	                f"UPDATE {table} SET chat_session_key = ?, updated_at = ? "
    86	                "WHERE id = ? AND chat_session_key IS NULL",
    87	                (result.session_key, now, entity_id),
    88	            )
    89	            if cursor.rowcount == 1:
    90	                append_event(
    91	                    conn,
    92	                    entity_id,
    93	                    EventKind.chat_session_created,
    94	                    {"session_key": result.session_key},
    95	                    now,
    96	                )
    97	            else:  # a concurrent first send won under the write lock; adopt its key
    98	                row = conn.execute(
    99	                    f"SELECT chat_session_key FROM {table} WHERE id = ?", (entity_id,)
   100	                ).fetchone()
   101	                result = ChatSendResult(
   102	                    reply_text=result.reply_text, session_key=row["chat_session_key"]
   103	                )
   104	    return result
   105	
   106	
   107	def status(gateway: GatewayAdapter) -> GatewayStatus:
   108	    result = gateway.status()
   109	    if result.detail is not None:
   110	        _log.info("gateway status detail: %s", result.detail)  # logged, not shown
   111	    return result

exec
/bin/zsh -lc "nl -ba src/planner/chat/api.py | sed -n '1,260p'" in /Users/khushaljagota/.hermes/planning-v2
 succeeded in 0ms:
     1	"""Chat routes (§9): send a message, read gateway availability."""
     2	
     3	from __future__ import annotations
     4	
     5	import sqlite3
     6	from collections.abc import Callable
     7	from dataclasses import asdict
     8	from typing import Any
     9	
    10	from fastapi import APIRouter, Request
    11	
    12	from planner.chat import service
    13	from planner.core.adapters.registry import Adapters
    14	from planner.core.clock import Clock
    15	from planner.core.errors import ErrorCode, PlannerError
    16	
    17	router = APIRouter()
    18	
    19	
    20	@router.post("/chat/{entity_id}/send")
    21	async def send_message(
    22	    entity_id: str, body: dict[str, Any], request: Request
    23	) -> dict[str, Any]:
    24	    text = body.get("text")
    25	    if not isinstance(text, str):
    26	        raise PlannerError(ErrorCode.validation, "text is required")
    27	    clock: Clock = request.app.state.clock
    28	    adapters: Adapters = request.app.state.adapters
    29	    conn_factory: Callable[[], sqlite3.Connection] = request.app.state.conn_factory
    30	    conn = conn_factory()
    31	    try:
    32	        result = service.send(conn, adapters.gateway, entity_id, text, clock.now_unix())
    33	    finally:
    34	        conn.close()
    35	    return asdict(result)
    36	
    37	
    38	@router.get("/chat/{entity_id}/status")
    39	async def gateway_status(entity_id: str, request: Request) -> dict[str, Any]:
    40	    # Availability is gateway-wide; {entity_id} keeps the per-entity route shape.
    41	    adapters: Adapters = request.app.state.adapters
    42	    result = service.status(adapters.gateway)
    43	    return {"available": result.available}

exec
/bin/zsh -lc "nl -ba src/planner/seed/api.py | sed -n '1,320p'" in /Users/khushaljagota/.hermes/planning-v2
 succeeded in 0ms:
     1	"""Seed route (§9): POST /api/seed runs a migration and returns a MigrationReport."""
     2	
     3	from __future__ import annotations
     4	
     5	import sqlite3
     6	from collections.abc import Callable
     7	from dataclasses import asdict
     8	from pathlib import Path
     9	from typing import Any
    10	
    11	from fastapi import APIRouter, Request
    12	
    13	from planner.core.errors import ErrorCode, PlannerError
    14	from planner.seed.contracts import MigrationReport
    15	from planner.seed.demo import seed_demo
    16	from planner.seed.importer import seed_from_source
    17	
    18	router = APIRouter()
    19	
    20	
    21	def _demo_report(conn: sqlite3.Connection) -> MigrationReport:
    22	    def count(sql: str) -> int:
    23	        return int(conn.execute(sql).fetchone()[0])
    24	
    25	    return MigrationReport(
    26	        sprints=count("SELECT COUNT(*) FROM sprints"),
    27	        sprint_items=count("SELECT COUNT(*) FROM sprint_items WHERE sprint_id IS NOT NULL"),
    28	        deferred_items=count("SELECT COUNT(*) FROM sprint_items WHERE sprint_id IS NULL"),
    29	        tickets=count("SELECT COUNT(*) FROM tickets"),
    30	        ideas=count("SELECT COUNT(*) FROM ideas"),
    31	        links=count("SELECT COUNT(*) FROM links WHERE kind = 'belongs_to'"),
    32	        duplicates_skipped=0,
    33	        skipped=[],
    34	    )
    35	
    36	
    37	@router.post("/seed")
    38	async def seed(body: dict[str, Any], request: Request) -> dict[str, Any]:
    39	    source_dir = body.get("source_dir")
    40	    demo = bool(body.get("demo", False))
    41	    has_source = source_dir is not None
    42	    if has_source == demo:  # both present, or neither
    43	        raise PlannerError(ErrorCode.validation, "provide exactly one of source_dir or demo")
    44	    conn_factory: Callable[[], sqlite3.Connection] = request.app.state.conn_factory
    45	    conn = conn_factory()
    46	    try:
    47	        if demo:
    48	            seed_demo(conn)  # raises db_not_empty on a non-empty database
    49	            report = _demo_report(conn)
    50	        else:
    51	            if not isinstance(source_dir, str) or not Path(source_dir).is_dir():
    52	                raise PlannerError(
    53	                    ErrorCode.validation, "source_dir must be an existing directory"
    54	                )
    55	            report = seed_from_source(conn, source_dir)
    56	    finally:
    57	        conn.close()
    58	    return asdict(report)

exec
/bin/zsh -lc 'rg -n "''^##? 1[12]'"\\b|"'^### 1[12]'"\\b|Section 11|Section 12|Chat|Seed|Migration|gateway\" SPEC.md" in /Users/khushaljagota/.hermes/planning-v2
 succeeded in 0ms:
25:- PRINCIPLES.md at the repo root binds this build; where this spec is explicit, the spec overrides. One explicit override: the core/module registry rule is out of scope for v1 — the planner is a single product with no pluggable modules; the registry pattern applies to adapters only (spawn, boundary/replan, gateway).
175:- `plan seed --source <dir>` / `plan seed --demo` — Section 12.
198:1. **Day (home)**: the brief (rendered markdown); the day-plan tree with per-node Accept / Invalidate and top-level Accept-all / Reject-all; the day's ticket list (ordered); a Review entry showing the pending count; and the day chat panel (Section 11) — quick capture happens here, by telling the planner, which files tickets/ideas via the CLI (Section 17).
201:4. **Ticket**: all four fields as sections (value rendered as markdown; pending proposal shown with Accept/Edit, accepts carrying the onward grant; notes editable inline), recap, state control, ceiling and at-cap as plain pickers (no dial widgetry), a **Copy ticket** button (copies a plain-text block — title, state, priority, field values, recap, links — to the clipboard for pasting into any agent or editor), links, day/sprint assignment, run history, event log, and the chat panel (Section 11).
205:## 11. Chat
207:Each ticket and each day has at most one chat session. The chat panel connects through a **gateway adapter** wrapping the Hermes gateway (`tui_gateway.ws` import, same pattern as the old viewer). If the gateway is unreachable the panel renders "gateway offline" and the rest of the UI is unaffected. The entity's `chat_session_key` persists the session id. In tests the adapter is a fake that echoes. Live gateway behavior is a post-run human item, not an acceptance test.
209:## 12. Seed and migration
211:Migration from the markdown system is an in-run deliverable, not an afterthought. A frozen snapshot of the real planning data (taken 2026-07-04) is committed at `migration/source-snapshot/`; the build must migrate it successfully. The live directory is still never read at build/test time — the snapshot is the real-data target; final live cutover at switch time is the human's moment (§18.4).
213:`plan seed --source <dir>` reads a markdown planning directory with the current system's shapes and imports: `sprints/current/sprint-kickoff.md` → sprint kickoff fields; `sprint-tracking.md` items → sprint items (statuses: Todo→todo, In Progress→active, Done→done, Blocked→blocked, Deferred→deferred_next_sprint; `Priority:`/`Urgency:`/`Project:` fields mapped; Mode dropped); `sprint-review.md` → review fields; the latest `daily/YYYY-MM-DD/workspace.md` tickets → tickets (Readiness mapping: Concepts→needs_success, Needs Shaping→needs_approach, Ready→needs_plan, In Progress→in_progress; `Ticket ID:` preserved as an alias field; `Chat ID:` → `chat_session_key`; `Priority:` mapped; body/success/approach text into the matching fields' values) linked to sprint items by title match when unambiguous, else standalone with sprint assignment; `deferred.md` → sprint items with `sprint_id = NULL` (project sections and P-labels mapped); `ideas.md` → ideas. `plan seed --demo` creates a deterministic demo dataset for dogfooding and manual testing: 8 tickets spanning every state with varied priorities, deadlines, and ceilings; one blocking link; one parent sprint item with children; one planned day. It requires an empty database and errors otherwise.
215:Archives and historical daily folders are not imported — only the latest day's workspace feeds tickets [RULING R6 default]. Seeding is idempotent by alias/title: re-running against the same source creates no duplicates. Every seed run emits a migration report (printed, and with `--json` structured): counts imported per entity kind, plus an explicit list of skipped/unparseable sections — silent drops are forbidden. Unit-tested against synthetic fixtures in `tests/fixtures/`; additionally proven end-to-end against `migration/source-snapshot/` (item 34), whose known ground truth is: 1 sprint (2026-07-01 → 2026-07-12); 12 sprint items (6 todo, 5 active, 1 done — the done item titled "Ship waitlist mechanics."); 4 tickets from the 2026-07-03 workspace (mic-publish and app-typography at needs_plan; landing-gate-1 and durable-personas at in_progress; `Chat ID: 20260702_114500_0ec57a` preserved on landing-gate-1); 9 deferred items (NULL sprint, projects mapped from the Vylo/Tribe/Learning/Other headings); 20 ideas. The live directory is never read in tests.
226:- **Adapters at every external boundary**: spawn (subprocess), boundary/replan agent, gateway chat. Each is a small interface with a real and a fake implementation; tests use fakes; nothing in tests shells out to `hermes` or opens network connections beyond localhost Playwright↔server.
275:External boundaries (hermes spawn, boundary/replan agent, chat gateway) are faked in all tests. E2E uses a real server + temp SQLite DB + `PLAN_TEST_MODE=1`, driving ticks via the test endpoints and time via `PLAN_FAKE_NOW`. Multi-surface behavior uses two browser contexts.
296:19. Seed fixtures: importing `tests/fixtures/planning-md/` yields the exact expected entity counts and spot-checked values (item statuses, ticket states per the Readiness mapping, preserved Chat ID, deferred items with NULL sprint, idea with project); re-running seeds zero duplicates.
305:26. Chat panel: opens on Ticket with the fake gateway (echo); a sent message renders a reply; `chat_session_key` persisted (asserted via API); gateway-offline fake renders the offline notice.
312:33. Seed e2e: `plan seed --source tests/fixtures/planning-md/` against the test server, then Board/Sprint/Backlog show the fixture's expected titles and counts.
313:34. Snapshot migration e2e: `plan seed --source migration/source-snapshot/` imports the frozen real data with exactly the ground truth stated in Section 12 (1 sprint with those dates; 12 items split 6/5/1 with "Ship waitlist mechanics." done; the 4 named tickets in their mapped states with the Chat ID preserved; 9 deferred items; 20 ideas); the migration report lists zero silently-skipped sections (anything unparsed is enumerated in it); a second run imports zero duplicates.
329:Live Hermes gateway chat; live dispatcher spawning a real agent; the final cutover seed against the live `~/.hermes/planning/` at switch time (the in-run migration proves the machinery on the frozen snapshot; the live directory will have drifted since 2026-07-04 and its import is reviewed by the human); installing skills into `~/.hermes/skills/`; launchd supervision; UI look-and-feel judgment.

codex
The main chat service and API are small. I’m checking the surrounding contracts and error mapping now, because the requested behaviors depend on how `PlannerError`, gateway adapters, and `MigrationReport` serialize.
exec
/bin/zsh -lc "nl -ba SPEC.md | sed -n '205,216p'" in /Users/khushaljagota/.hermes/planning-v2
 succeeded in 0ms:
   205	## 11. Chat
   206	
   207	Each ticket and each day has at most one chat session. The chat panel connects through a **gateway adapter** wrapping the Hermes gateway (`tui_gateway.ws` import, same pattern as the old viewer). If the gateway is unreachable the panel renders "gateway offline" and the rest of the UI is unaffected. The entity's `chat_session_key` persists the session id. In tests the adapter is a fake that echoes. Live gateway behavior is a post-run human item, not an acceptance test.
   208	
   209	## 12. Seed and migration
   210	
   211	Migration from the markdown system is an in-run deliverable, not an afterthought. A frozen snapshot of the real planning data (taken 2026-07-04) is committed at `migration/source-snapshot/`; the build must migrate it successfully. The live directory is still never read at build/test time — the snapshot is the real-data target; final live cutover at switch time is the human's moment (§18.4).
   212	
   213	`plan seed --source <dir>` reads a markdown planning directory with the current system's shapes and imports: `sprints/current/sprint-kickoff.md` → sprint kickoff fields; `sprint-tracking.md` items → sprint items (statuses: Todo→todo, In Progress→active, Done→done, Blocked→blocked, Deferred→deferred_next_sprint; `Priority:`/`Urgency:`/`Project:` fields mapped; Mode dropped); `sprint-review.md` → review fields; the latest `daily/YYYY-MM-DD/workspace.md` tickets → tickets (Readiness mapping: Concepts→needs_success, Needs Shaping→needs_approach, Ready→needs_plan, In Progress→in_progress; `Ticket ID:` preserved as an alias field; `Chat ID:` → `chat_session_key`; `Priority:` mapped; body/success/approach text into the matching fields' values) linked to sprint items by title match when unambiguous, else standalone with sprint assignment; `deferred.md` → sprint items with `sprint_id = NULL` (project sections and P-labels mapped); `ideas.md` → ideas. `plan seed --demo` creates a deterministic demo dataset for dogfooding and manual testing: 8 tickets spanning every state with varied priorities, deadlines, and ceilings; one blocking link; one parent sprint item with children; one planned day. It requires an empty database and errors otherwise.
   214	
   215	Archives and historical daily folders are not imported — only the latest day's workspace feeds tickets [RULING R6 default]. Seeding is idempotent by alias/title: re-running against the same source creates no duplicates. Every seed run emits a migration report (printed, and with `--json` structured): counts imported per entity kind, plus an explicit list of skipped/unparseable sections — silent drops are forbidden. Unit-tested against synthetic fixtures in `tests/fixtures/`; additionally proven end-to-end against `migration/source-snapshot/` (item 34), whose known ground truth is: 1 sprint (2026-07-01 → 2026-07-12); 12 sprint items (6 todo, 5 active, 1 done — the done item titled "Ship waitlist mechanics."); 4 tickets from the 2026-07-03 workspace (mic-publish and app-typography at needs_plan; landing-gate-1 and durable-personas at in_progress; `Chat ID: 20260702_114500_0ec57a` preserved on landing-gate-1); 9 deferred items (NULL sprint, projects mapped from the Vylo/Tribe/Learning/Other headings); 20 ideas. The live directory is never read in tests.
   216	

exec
/bin/zsh -lc "nl -ba src/planner/seed/contracts.py | sed -n '1,260p'" in /Users/khushaljagota/.hermes/planning-v2
 succeeded in 0ms:
     1	"""Seed/migration shapes: the §12 mapping tables (targeting the real enums), the
     2	parsed intermediates the parser emits, and the migration report. Stdlib only."""
     3	
     4	from __future__ import annotations
     5	
     6	from dataclasses import dataclass, field
     7	from typing import Final
     8	
     9	from planner.core.contracts import Priority, Project
    10	from planner.sprints.contracts import ItemStatus
    11	from planner.tickets.contracts import TicketState
    12	
    13	# §12 mapping tables — exact source strings on the left.
    14	ITEM_STATUS_MAP: Final[dict[str, ItemStatus]] = {
    15	    "Todo": ItemStatus.todo,
    16	    "In Progress": ItemStatus.active,
    17	    "Done": ItemStatus.done,
    18	    "Blocked": ItemStatus.blocked,
    19	    "Deferred": ItemStatus.deferred_next_sprint,
    20	}
    21	
    22	READINESS_MAP: Final[dict[str, TicketState]] = {
    23	    "Concepts": TicketState.needs_success,
    24	    "Needs Shaping": TicketState.needs_approach,
    25	    "Ready": TicketState.needs_plan,
    26	    "In Progress": TicketState.in_progress,
    27	}
    28	
    29	# `Priority:` labels are literal P0..P3 in the source (verified in the snapshot) — identity map.
    30	PRIORITY_MAP: Final[dict[str, Priority]] = {p.value: p for p in Priority}
    31	
    32	# `Urgency:` is present-but-empty throughout the snapshot. Mapped as a fallback only:
    33	# consulted when a Priority label is absent; empty/unknown values are ignored (P3 default applies).
    34	URGENCY_MAP: Final[dict[str, Priority]] = {
    35	    "critical": Priority.P0, "high": Priority.P1, "medium": Priority.P2, "low": Priority.P3,
    36	}   # keys compared case-insensitively
    37	
    38	# Project headings/labels are the enum's exact strings — identity map.
    39	PROJECT_MAP: Final[dict[str, Project]] = {p.value: p for p in Project}
    40	
    41	# `Mode:` is dropped (§12); the parser records it nowhere but it is NOT a skipped
    42	# section (it is a recognised, deliberately-discarded field).
    43	
    44	
    45	# --- parsed intermediates (parser output, importer input; no DB types) ---
    46	
    47	@dataclass
    48	class ParsedSprint:
    49	    name: str
    50	    date_start: str
    51	    date_end: str
    52	    limiting_factor: str = ""
    53	    primary_bet: str = ""
    54	    supports: str = ""
    55	    premortem: str = ""
    56	    outcomes: str = ""
    57	    solo_reflection: str = ""
    58	    joint_discussion: str = ""
    59	    updates_to_thinking: str = ""
    60	    carry_forward: str = ""
    61	
    62	
    63	@dataclass
    64	class ParsedItem:                  # sprint-tracking.md item or deferred.md item
    65	    title: str
    66	    status: ItemStatus
    67	    priority: Priority
    68	    project: Project
    69	    body: str = ""
    70	    deadline: str | None = None
    71	    deferred: bool = False         # True -> sprint_id stays NULL
    72	
    73	
    74	@dataclass
    75	class ParsedTicket:                # workspace.md ticket
    76	    title: str
    77	    state: TicketState
    78	    priority: Priority
    79	    alias: str | None = None       # "Ticket ID:"
    80	    chat_session_key: str | None = None   # "Chat ID:"
    81	    body: str = ""                 # -> fields.success/approach values per §12
    82	    success: str | None = None
    83	    approach: str | None = None
    84	    item_title: str | None = None  # link target for unambiguous title match
    85	
    86	
    87	@dataclass
    88	class ParsedIdea:
    89	    title: str
    90	    body: str = ""
    91	    project: Project | None = None
    92	
    93	
    94	@dataclass(frozen=True)
    95	class SkippedSection:              # §12: silent drops forbidden — every skip is enumerated
    96	    source_file: str               # path relative to the seed source dir
    97	    heading: str | None            # nearest heading, if any
    98	    reason: str                    # human-readable why it could not be parsed
    99	    excerpt: str                   # first ~120 chars of the skipped text
   100	
   101	
   102	@dataclass
   103	class MigrationReport:             # printed, and structured under --json
   104	    sprints: int = 0
   105	    sprint_items: int = 0          # items with a sprint
   106	    deferred_items: int = 0        # items imported with sprint_id NULL
   107	    tickets: int = 0
   108	    ideas: int = 0
   109	    links: int = 0                 # belongs_to links made by title match
   110	    duplicates_skipped: int = 0    # idempotent re-run hits (alias/title match)
   111	    skipped: list[SkippedSection] = field(default_factory=list)

exec
/bin/zsh -lc "nl -ba src/planner/chat/contracts.py | sed -n '1,220p'" in /Users/khushaljagota/.hermes/planning-v2
 succeeded in 0ms:
     1	"""Chat domain shapes: messages, the gateway send result, and the availability
     2	signal for the panel (§11). Stdlib only."""
     3	
     4	from __future__ import annotations
     5	
     6	from dataclasses import dataclass
     7	
     8	
     9	@dataclass(frozen=True)
    10	class ChatMessage:
    11	    role: str                      # "user" | "assistant"
    12	    text: str
    13	    created_at: int
    14	
    15	
    16	@dataclass(frozen=True)
    17	class ChatSendResult:              # what the gateway adapter returns per send
    18	    reply_text: str
    19	    session_key: str               # persisted onto the entity's chat_session_key
    20	
    21	
    22	@dataclass(frozen=True)
    23	class GatewayStatus:               # availability signal for the panel (§11)
    24	    available: bool
    25	    detail: str | None = None      # e.g. "connection refused" — logged, not shown

exec
/bin/zsh -lc "nl -ba src/planner/core/errors.py | sed -n '1,260p'" in /Users/khushaljagota/.hermes/planning-v2
exec
/bin/zsh -lc "rg -n \"gateway_offline|PlannerError|exception_handler|status_code|include_router|/api\" src/planner -g '*.py'" in /Users/khushaljagota/.hermes/planning-v2
 succeeded in 0ms:
     1	"""Structured errors. Every domain rejection raises PlannerError with a stable
     2	code; the API renders it as {"error": {code, message, detail}} and the CLI keys
     3	its exit code off the presence of that envelope."""
     4	
     5	from __future__ import annotations
     6	
     7	from enum import StrEnum
     8	
     9	from planner.core.contracts import JsonDict
    10	
    11	
    12	class ErrorCode(StrEnum):
    13	    frozen_write = "frozen_write"                  # §5 kickoff/review writes after freeze
    14	    at_cap_stop = "at_cap_stop"                    # §4.3 agent proposal at ceiling with stop
    15	    grant_missing = "grant_missing"                # §4.4.7 accept without the full pair
    16	    grant_invalid = "grant_invalid"                # next_ceiling before the new state / unknown
    17	    stale_claim = "stale_claim"                    # §7.6 stale/foreign claim; detail names it
    18	    recap_too_early = "recap_too_early"            # §3.3 recap write at needs_success
    19	    title_too_long = "title_too_long"              # §3.3 > title_max_chars
    20	    sprint_overlap = "sprint_overlap"              # §3.1 overlapping date ranges
    21	    link_cycle = "link_cycle"                      # §3.6 blocks/parent_child transitive cycle
    22	    link_invalid = "link_invalid"                  # self-link, second belongs_to, bad endpoints
    23	    sprint_derived = "sprint_derived"              # §3.3 sprint_id write on a parented ticket
    24	    item_transition_forbidden = "item_transition_forbidden"  # §3.2 agent direct done/deferred
    25	    agent_forbidden = "agent_forbidden"            # claim/agent request hits a human-only action
    26	    gateway_offline = "gateway_offline"            # §11
    27	    db_not_empty = "db_not_empty"                  # §12 seed --demo on a non-empty DB
    28	    not_found = "not_found"
    29	    validation = "validation"                      # generic input validation
    30	
    31	
    32	class PlannerError(Exception):
    33	    def __init__(self, code: ErrorCode, message: str, detail: JsonDict | None = None) -> None:
    34	        super().__init__(message)
    35	        self.code = code
    36	        self.message = message
    37	        self.detail: JsonDict = detail if detail is not None else {}
    38	
    39	    def to_payload(self) -> JsonDict:
    40	        return {"error": {"code": self.code.value, "message": self.message, "detail": self.detail}}

 succeeded in 0ms:
src/planner/days/api.py:20:from planner.core.errors import ErrorCode, PlannerError
src/planner/days/api.py:61:        raise PlannerError(ErrorCode.validation, "invalid date", {"date": date_seg}) from None
src/planner/days/api.py:88:    raise PlannerError(
src/planner/days/api.py:96:        raise PlannerError(ErrorCode.validation, "day has no plan", {"day_id": did})
src/planner/days/api.py:102:        raise PlannerError(ErrorCode.validation, "no such plan node", {"node": position})
src/planner/days/api.py:119:        raise PlannerError(ErrorCode.validation, "no day fields to update", {})
src/planner/sprints/api.py:22:from planner.core.errors import ErrorCode, PlannerError
src/planner/sprints/api.py:83:        raise PlannerError(ErrorCode.validation, "idea title is required", {})
src/planner/sprints/api.py:106:            raise PlannerError(
src/planner/sprints/api.py:110:        raise PlannerError(
src/planner/sprints/api.py:123:        raise PlannerError(
src/planner/sprints/api.py:158:        raise PlannerError(
src/planner/sprints/api.py:169:        raise PlannerError(ErrorCode.validation, "project is required")
src/planner/sprints/api.py:211:            raise PlannerError(ErrorCode.validation, "unknown item field", {"field": key})
src/planner/sprints/api.py:213:        raise PlannerError(ErrorCode.validation, "no item fields to update", {})
src/planner/sprints/api.py:215:        raise PlannerError(ErrorCode.validation, "blocked_by requires status", {})
src/planner/sprints/api.py:259:            raise PlannerError(ErrorCode.validation, f"invalid {label}", {label: raw}) from None
src/planner/sprints/api.py:289:            raise PlannerError(ErrorCode.validation, "unknown sprint field", {"field": key})
src/planner/sprints/api.py:291:        raise PlannerError(ErrorCode.validation, "no sprint fields to update", {})
src/planner/chat/api.py:15:from planner.core.errors import ErrorCode, PlannerError
src/planner/chat/api.py:26:        raise PlannerError(ErrorCode.validation, "text is required")
src/planner/chat/service.py:18:from planner.core.errors import ErrorCode, PlannerError
src/planner/chat/service.py:43:            raise PlannerError(ErrorCode.not_found, "ticket not found", {"entity_id": entity_id})
src/planner/chat/service.py:56:            raise PlannerError(
src/planner/chat/service.py:61:    raise PlannerError(ErrorCode.not_found, "no chattable entity for id", {"entity_id": entity_id})
src/planner/chat/service.py:75:    except PlannerError:
src/planner/chat/service.py:76:        raise  # already structured (offline adapter raises gateway_offline)
src/planner/chat/service.py:78:        raise PlannerError(
src/planner/chat/service.py:79:            ErrorCode.gateway_offline, "gateway unavailable", {"cause": str(exc)}
src/planner/core/authctx.py:19:from planner.core.errors import ErrorCode, PlannerError
src/planner/core/authctx.py:111:    raise PlannerError(ErrorCode.stale_claim, message, detail)
src/planner/core/authctx.py:124:        raise PlannerError(ErrorCode.not_found, "ticket not found", {"ticket_id": ticket_id})
src/planner/core/authctx.py:188:    raise PlannerError(
src/planner/dispatch/data.py:18:from planner.core.errors import ErrorCode, PlannerError
src/planner/dispatch/data.py:76:        raise PlannerError(ErrorCode.not_found, "run not found", {"run_id": run_id})
src/planner/dispatch/data.py:78:        raise PlannerError(
src/planner/dispatch/data.py:90:        raise PlannerError(
src/planner/dispatch/data.py:171:        raise PlannerError(
src/planner/dispatch/data.py:176:        raise PlannerError(ErrorCode.not_found, "run not found", {"run_id": run_id})
src/planner/dispatch/data.py:179:        raise PlannerError(
src/planner/dispatch/data.py:191:        raise PlannerError(
src/planner/dispatch/data.py:198:        raise PlannerError(
src/planner/dispatch/data.py:266:        raise PlannerError(ErrorCode.not_found, "ticket not found", {"ticket_id": ticket_id})
src/planner/core/clock.py:28:    """Mutable clock for PLAN_FAKE_NOW and POST /api/test/set-now (D5)."""
src/planner/seed/demo.py:13:from planner.core.errors import ErrorCode, PlannerError
src/planner/seed/demo.py:26:            raise PlannerError(
src/planner/tickets/data.py:15:from planner.core.errors import ErrorCode, PlannerError
src/planner/tickets/data.py:72:        raise PlannerError(ErrorCode.not_found, "ticket not found", {"ticket_id": ticket_id})
src/planner/tickets/data.py:122:                raise PlannerError(
src/planner/tickets/data.py:126:                raise PlannerError(
src/planner/tickets/data.py:132:                raise PlannerError(ErrorCode.validation, "project is derived when parented")
src/planner/tickets/data.py:136:                raise PlannerError(
src/planner/tickets/data.py:330:            raise PlannerError(ErrorCode.not_found, "sprint not found", {"sprint_id": sprint_id})
src/planner/core/config.py:15:from planner.core.errors import ErrorCode, PlannerError
src/planner/core/config.py:65:    raise PlannerError(ErrorCode.validation, f"invalid boolean for {key}: {raw!r}")
src/planner/core/config.py:70:        raise PlannerError(ErrorCode.validation, f"invalid integer for {key}: {raw!r}")
src/planner/core/config.py:77:            raise PlannerError(ErrorCode.validation, f"invalid integer for {key}: {raw!r}") from exc
src/planner/core/config.py:78:    raise PlannerError(ErrorCode.validation, f"invalid integer for {key}: {raw!r}")
src/planner/core/config.py:89:        raise PlannerError(ErrorCode.validation, f"config root must be a mapping: {resolved}")
src/planner/core/server.py:1:"""FastAPI app factory shell. Mounts the domain routers under /api, serves the
src/planner/core/server.py:2:static assets and the app shell, exposes GET /api/meta and the event WebSocket,
src/planner/core/server.py:3:and — only in test mode — the /api/test/* control endpoints. The resolution
src/planner/core/server.py:28:from planner.core.errors import ErrorCode, PlannerError
src/planner/core/server.py:42:    ErrorCode.gateway_offline: 503,
src/planner/core/server.py:89:    @app.exception_handler(PlannerError)
src/planner/core/server.py:90:    async def handle_planner_error(request: Request, exc: PlannerError) -> JSONResponse:
src/planner/core/server.py:91:        return JSONResponse(status_code=http_status_for(exc.code), content=exc.to_payload())
src/planner/core/server.py:101:        app.include_router(domain_router, prefix="/api")
src/planner/core/server.py:103:    @app.get("/api/meta")
src/planner/core/server.py:111:    @app.websocket("/api/events")
src/planner/core/server.py:122:        app.include_router(
src/planner/core/server.py:123:            build_test_router(config, clock, adapters, conn_factory), prefix="/api"
src/planner/core/errors.py:1:"""Structured errors. Every domain rejection raises PlannerError with a stable
src/planner/core/errors.py:26:    gateway_offline = "gateway_offline"            # §11
src/planner/core/errors.py:32:class PlannerError(Exception):
src/planner/core/links.py:12:from planner.core.errors import ErrorCode, PlannerError
src/planner/core/links.py:41:        raise PlannerError(ErrorCode.link_invalid, "self-links are not allowed", detail)
src/planner/core/links.py:45:        raise PlannerError(
src/planner/core/links.py:51:        raise PlannerError(
src/planner/core/links.py:70:                raise PlannerError(
src/planner/core/links.py:83:                    raise PlannerError(
src/planner/core/links.py:105:            raise PlannerError(
src/planner/core/links.py:130:        raise PlannerError(ErrorCode.not_found, "link not found", detail)
src/planner/seed/api.py:1:"""Seed route (§9): POST /api/seed runs a migration and returns a MigrationReport."""
src/planner/seed/api.py:13:from planner.core.errors import ErrorCode, PlannerError
src/planner/seed/api.py:43:        raise PlannerError(ErrorCode.validation, "provide exactly one of source_dir or demo")
src/planner/seed/api.py:52:                raise PlannerError(
src/planner/dispatch/api.py:16:from planner.core.errors import ErrorCode, PlannerError
src/planner/dispatch/api.py:35:        raise PlannerError(ErrorCode.not_found, "run not found", {"run_id": run_id})
src/planner/dispatch/api.py:48:        raise PlannerError(
src/planner/dispatch/api.py:53:        raise PlannerError(ErrorCode.not_found, "run not found", {"run_id": run_id})
src/planner/sprints/data.py:3:enforces — mapping each verdict to a PlannerError or a SQL mutation plus its
src/planner/sprints/data.py:17:from planner.core.errors import ErrorCode, PlannerError
src/planner/sprints/data.py:132:        raise PlannerError(ErrorCode.not_found, "sprint not found", {"id": sprint_id})
src/planner/sprints/data.py:139:        raise PlannerError(ErrorCode.not_found, "sprint item not found", {"id": item_id})
src/planner/sprints/data.py:159:        raise PlannerError(ErrorCode.validation, "sprint name is required", {})
src/planner/sprints/data.py:161:        raise PlannerError(
src/planner/sprints/data.py:175:            raise PlannerError(
src/planner/sprints/data.py:214:        raise PlannerError(
src/planner/sprints/data.py:221:        raise PlannerError(
src/planner/sprints/data.py:280:        raise PlannerError(ErrorCode.validation, "addendum date is required", {})
src/planner/sprints/data.py:282:        raise PlannerError(ErrorCode.validation, "addendum text is required", {})
src/planner/sprints/data.py:317:        raise PlannerError(ErrorCode.validation, "item title is required", {})
src/planner/sprints/data.py:354:        raise PlannerError(
src/planner/sprints/data.py:362:            raise PlannerError(ErrorCode.validation, "invalid priority", {"value": value})
src/planner/sprints/data.py:366:            raise PlannerError(ErrorCode.validation, "invalid priority", {"value": value}) from exc
src/planner/sprints/data.py:369:            raise PlannerError(ErrorCode.validation, "invalid project", {"value": value})
src/planner/sprints/data.py:373:            raise PlannerError(ErrorCode.validation, "invalid project", {"value": value}) from exc
src/planner/sprints/data.py:408:        raise PlannerError(
src/planner/sprints/data.py:414:        raise PlannerError(
src/planner/sprints/data.py:447:        raise PlannerError(
src/planner/sprints/data.py:454:        raise PlannerError(
src/planner/sprints/data.py:504:        raise PlannerError(ErrorCode.validation, "no pending status proposal", {})
src/planner/tickets/api.py:34:from planner.core.errors import ErrorCode, PlannerError
src/planner/tickets/api.py:82:    PlannerError rolls back cleanly. Never wrap a ticket/sprint writer — those open
src/planner/tickets/api.py:98:        raise PlannerError(ErrorCode.validation, f"invalid {what}", {what: raw}) from None
src/planner/tickets/api.py:157:        raise PlannerError(
src/planner/tickets/api.py:168:        raise PlannerError(ErrorCode.grant_invalid, "unknown at_cap", {"at_cap": raw}) from None
src/planner/tickets/api.py:198:            raise PlannerError(ErrorCode.validation, "project is derived when parented")
src/planner/tickets/api.py:269:            raise PlannerError(ErrorCode.validation, "unknown ticket field", {"field": key})
src/planner/tickets/api.py:271:        raise PlannerError(ErrorCode.validation, "no ticket fields to update", {})
src/planner/tickets/api.py:364:        raise PlannerError(
src/planner/tickets/api.py:370:        raise PlannerError(
src/planner/tickets/api.py:376:        raise PlannerError(
src/planner/core/ws.py:1:"""§9 event tailer. WS /api/events?since=<id> tails the events table and pushes
src/planner/seed/logic/kickoff.py:9:from planner.core.errors import ErrorCode, PlannerError
src/planner/seed/logic/kickoff.py:42:    raise PlannerError(
src/planner/seed/importer.py:15:from planner.core.errors import ErrorCode, PlannerError
src/planner/seed/importer.py:39:        raise PlannerError(ErrorCode.validation, f"seed source directory not found: {root}")
src/planner/seed/importer.py:42:        raise PlannerError(
src/planner/tickets/logic/fields_codec.py:11:from planner.core.errors import ErrorCode, PlannerError
src/planner/tickets/logic/fields_codec.py:38:        raise PlannerError(ErrorCode.validation, "corrupt ticket fields JSON")
src/planner/dispatch/runtime.py:23:from planner.core.errors import PlannerError
src/planner/dispatch/runtime.py:96:    the boundary). A5: close FIRST (guarded by a PlannerError skip); only the run whose
src/planner/dispatch/runtime.py:115:        except PlannerError:
src/planner/core/testmode.py:1:"""§9/§13 test-mode control router. Mounted under /api only when config.test_mode,
src/planner/core/testmode.py:2:so /api/test/* is a plain 404 otherwise (the router is never built). set-now drives
src/planner/core/testmode.py:22:from planner.core.errors import ErrorCode, PlannerError
src/planner/core/testmode.py:27:    # Returned directly (never raised as a PlannerError, which the handler would map
src/planner/core/testmode.py:30:        status_code=501,
src/planner/core/testmode.py:57:            raise PlannerError(ErrorCode.validation, "body must carry an ISO 'now' string")
src/planner/core/testmode.py:61:            raise PlannerError(ErrorCode.validation, f"invalid ISO datetime: {raw!r}") from exc
src/planner/core/testmode.py:63:            raise PlannerError(
src/planner/core/testmode.py:78:        return JSONResponse(status_code=200, content=report)
src/planner/core/testmode.py:86:        return JSONResponse(status_code=200, content=report)
src/planner/core/adapters/real.py:22:from planner.core.errors import ErrorCode, PlannerError
src/planner/core/adapters/real.py:168:            raise PlannerError(
src/planner/core/adapters/real.py:169:                ErrorCode.gateway_offline, "chat gateway unavailable", {"detail": str(exc)}
src/planner/core/adapters/real.py:179:            raise PlannerError(
src/planner/core/adapters/real.py:180:                ErrorCode.gateway_offline, "chat gateway send failed", {"detail": str(exc)}
src/planner/core/adapters/fakes.py:15:from planner.core.errors import ErrorCode, PlannerError
src/planner/core/adapters/fakes.py:108:        raise PlannerError(ErrorCode.gateway_offline, "gateway offline")
src/planner/tickets/logic/resolution.py:12:from planner.core.errors import ErrorCode, PlannerError
src/planner/tickets/logic/resolution.py:156:        raise PlannerError(
src/planner/tickets/logic/resolution.py:193:        raise PlannerError(
src/planner/tickets/logic/resolution.py:205:        raise PlannerError(ErrorCode.validation, "use the drop action")
src/planner/tickets/logic/resolution.py:207:        raise PlannerError(ErrorCode.validation, "dropped is terminal")
src/planner/tickets/logic/resolution.py:209:        raise PlannerError(ErrorCode.validation, "ticket already in that state")
src/planner/tickets/logic/resolution.py:217:        raise PlannerError(
src/planner/tickets/logic/resolution.py:221:        raise PlannerError(
src/planner/core/adapters/registry.py:22:from planner.core.errors import ErrorCode, PlannerError
src/planner/core/adapters/registry.py:47:    raise PlannerError(ErrorCode.validation, f"unknown spawn_adapter: {config.spawn_adapter!r}")
src/planner/core/adapters/registry.py:56:    raise PlannerError(
src/planner/core/adapters/registry.py:69:    raise PlannerError(ErrorCode.validation, f"unknown gateway_adapter: {config.gateway_adapter!r}")
src/planner/tickets/logic/admission.py:3:Pure: contracts/errors + machine only; raises PlannerError as the rejection."""
src/planner/tickets/logic/admission.py:10:from planner.core.errors import ErrorCode, PlannerError
src/planner/tickets/logic/admission.py:23:        raise PlannerError(
src/planner/tickets/logic/admission.py:34:        raise PlannerError(
src/planner/tickets/logic/admission.py:41:        raise PlannerError(
src/planner/tickets/logic/admission.py:52:        raise PlannerError(
src/planner/tickets/logic/admission.py:65:        raise PlannerError(
src/planner/tickets/logic/admission.py:74:        raise PlannerError(
src/planner/tickets/logic/admission.py:83:        raise PlannerError(ErrorCode.validation, "title must be non-empty")
src/planner/tickets/logic/admission.py:85:        raise PlannerError(
src/planner/tickets/logic/admission.py:98:        raise PlannerError(
src/planner/tickets/logic/admission.py:105:        raise PlannerError(ErrorCode.validation, f"{what} must be non-empty")
src/planner/tickets/logic/machine.py:7:from planner.core.errors import ErrorCode, PlannerError
src/planner/tickets/logic/machine.py:27:        raise PlannerError(
src/planner/tickets/logic/machine.py:45:        raise PlannerError(
src/planner/tickets/logic/machine.py:70:        raise PlannerError(
src/planner/tickets/logic/machine.py:84:        raise PlannerError(
src/planner/tickets/logic/machine.py:91:        raise PlannerError(
src/planner/tickets/logic/machine.py:95:        raise PlannerError(

exec
/bin/zsh -lc "nl -ba src/planner/seed/demo.py | sed -n '1,260p'" in /Users/khushaljagota/.hermes/planning-v2
 succeeded in 0ms:
     1	"""The ``plan seed --demo`` dataset (§12): a deterministic-by-content sample —
     2	one current sprint, three items, eight tickets spanning every state, one blocking
     3	link, one parented pair, one planned day. Requires an empty database."""
     4	
     5	from __future__ import annotations
     6	
     7	import json
     8	import sqlite3
     9	import time
    10	from datetime import date, datetime, timedelta
    11	
    12	from planner.core.contracts import EventKind
    13	from planner.core.errors import ErrorCode, PlannerError
    14	from planner.core.events import append_event
    15	from planner.core.ids import ID_PREFIXES, day_id, new_id
    16	
    17	_TABLES = (
    18	    "sprints", "sprint_items", "tickets", "days", "day_tickets",
    19	    "ideas", "links", "events", "runs", "boundary_runs",
    20	)
    21	
    22	
    23	def seed_demo(conn: sqlite3.Connection) -> None:
    24	    for table in _TABLES:
    25	        if conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] > 0:
    26	            raise PlannerError(
    27	                ErrorCode.db_not_empty, "seed --demo requires an empty database", {"table": table}
    28	            )
    29	    now = int(time.time())
    30	    today = datetime.now().astimezone().date()
    31	    conn.execute("BEGIN IMMEDIATE")
    32	    try:
    33	        _build_demo(conn, now, today)
    34	        conn.execute("COMMIT")
    35	    except Exception:
    36	        conn.execute("ROLLBACK")
    37	        raise
    38	
    39	
    40	def _build_demo(conn: sqlite3.Connection, now: int, today: date) -> None:
    41	    sprint_id = _insert_sprint(conn, now, today)
    42	    parent_id = _insert_item(
    43	        conn, sprint_id, "Ship the demo feature end to end.", "active", "P1", "Vylo",
    44	        "Parent item for the demo ticket pair.", now,
    45	    )
    46	    _insert_item(
    47	        conn, sprint_id, "Research the search index options.", "todo", "P2", "Learning", "", now,
    48	    )
    49	    _insert_item(
    50	        conn, sprint_id, "Retire the legacy export job.", "done", "P3", "Other", "", now,
    51	    )
    52	
    53	    plus = {n: (today + timedelta(days=n)).isoformat() for n in (1, 3, 7)}
    54	    today_iso = today.isoformat()
    55	
    56	    _insert_ticket(
    57	        conn, now, title="Draft the onboarding email success criteria.", state="needs_success",
    58	        priority="P2", deadline=plus[7], ceiling="needs_success", at_cap="propose",
    59	        sprint_item_id=None, sprint_id=sprint_id, project="Vylo",
    60	        fields=_fields("Draft the onboarding email success criteria.", ()),
    61	    )
    62	    _insert_ticket(
    63	        conn, now, title="Choose the search indexing approach.", state="needs_approach",
    64	        priority="P1", deadline=plus[3], ceiling="needs_plan", at_cap="propose",
    65	        sprint_item_id=None, sprint_id=sprint_id, project="Learning",
    66	        fields=_fields("Choose the search indexing approach.", ("success",)),
    67	    )
    68	    t3 = _insert_ticket(
    69	        conn, now, title="Plan the demo feature rollout.", state="needs_plan",
    70	        priority="P1", deadline=None, ceiling="in_progress", at_cap="propose",
    71	        sprint_item_id=parent_id, sprint_id=None, project=None,
    72	        fields=_fields("Plan the demo feature rollout.", ("success", "approach")),
    73	    )
    74	    _insert_link(conn, now, t3, parent_id, "belongs_to")
    75	    t4 = _insert_ticket(
    76	        conn, now, title="Implement the demo feature slice.", state="in_progress",
    77	        priority="P0", deadline=plus[1], ceiling="needs_review", at_cap="propose",
    78	        sprint_item_id=parent_id, sprint_id=None, project=None,
    79	        fields=_fields("Implement the demo feature slice.", ("success", "approach", "plan")),
    80	    )
    81	    _insert_link(conn, now, t4, parent_id, "belongs_to")
    82	    t5 = _insert_ticket(
    83	        conn, now, title="Review the analytics dashboard numbers.", state="needs_review",
    84	        priority="P2", deadline=today_iso, ceiling="done", at_cap="propose",
    85	        sprint_item_id=None, sprint_id=sprint_id, project="Vylo",
    86	        fields=_fields(
    87	            "Review the analytics dashboard numbers.", ("success", "approach", "plan", "result"),
    88	            result_notes="Check the conversion query joins before approving.",
    89	        ),
    90	    )
    91	    _insert_ticket(
    92	        conn, now, title="Write the release notes.", state="done",
    93	        priority="P3", deadline=None, ceiling="done", at_cap="stop",
    94	        sprint_item_id=None, sprint_id=sprint_id, project="Tribe",
    95	        fields=_fields(
    96	            "Write the release notes.", ("success", "approach", "plan", "result"),
    97	        ),
    98	    )
    99	    _insert_ticket(
   100	        conn, now, title="Prototype the voice input toggle.", state="dropped",
   101	        priority="P3", deadline=None, ceiling="needs_approach", at_cap="stop",
   102	        sprint_item_id=None, sprint_id=sprint_id, project="Vylo",
   103	        fields=_fields("Prototype the voice input toggle.", ("success",)),
   104	    )
   105	    t8 = _insert_ticket(
   106	        conn, now, title="Fix the flaky login test.", state="in_progress",
   107	        priority="P0", deadline=today_iso, ceiling="in_progress", at_cap="stop",
   108	        sprint_item_id=None, sprint_id=sprint_id, project="Vylo",
   109	        fields=_fields("Fix the flaky login test.", ("success", "approach", "plan")),
   110	    )
   111	    _insert_link(conn, now, t8, t5, "blocks")
   112	
   113	    _insert_day(conn, now, today, t4, t8, t5)
   114	
   115	
   116	def _insert_sprint(conn: sqlite3.Connection, now: int, today: date) -> str:
   117	    sprint_id = new_id(ID_PREFIXES["sprint"])
   118	    date_start = (today - timedelta(days=3)).isoformat()
   119	    date_end = (today + timedelta(days=10)).isoformat()
   120	    conn.execute(
   121	        "INSERT INTO sprints (id, name, date_start, date_end, limiting_factor, primary_bet, "
   122	        "supports, premortem, created_at, updated_at) "
   123	        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
   124	        (
   125	            sprint_id, "Demo Sprint", date_start, date_end,
   126	            "Demo: one clear slice must ship.",
   127	            "Demo: shipping the feature slice unblocks the review queue.",
   128	            "- Demo support line.", "- Demo premortem line.", now, now,
   129	        ),
   130	    )
   131	    append_event(
   132	        conn, sprint_id, EventKind.sprint_created,
   133	        {"name": "Demo Sprint", "date_start": date_start, "date_end": date_end, "source": "demo"},
   134	        now,
   135	    )
   136	    return sprint_id
   137	
   138	
   139	def _insert_item(
   140	    conn: sqlite3.Connection,
   141	    sprint_id: str,
   142	    title: str,
   143	    status: str,
   144	    priority: str,
   145	    project: str,
   146	    body: str,
   147	    now: int,
   148	) -> str:
   149	    item_id = new_id(ID_PREFIXES["sprint_item"])
   150	    conn.execute(
   151	        "INSERT INTO sprint_items (id, title, body, status, priority, deadline, project, "
   152	        "current_state_note, sprint_id, created_at, updated_at) "
   153	        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
   154	        (item_id, title, body, status, priority, None, project, "", sprint_id, now, now),
   155	    )
   156	    append_event(
   157	        conn, item_id, EventKind.sprint_item_created,
   158	        {"title": title, "status": status, "sprint_id": sprint_id, "source": "demo"},
   159	        now,
   160	    )
   161	    return item_id
   162	
   163	
   164	def _fields(title: str, keys: tuple[str, ...], result_notes: str | None = None) -> str:
   165	    slots: dict[str, dict[str, str | None]] = {
   166	        name: {"value": None, "proposal": None, "notes": None}
   167	        for name in ("success", "approach", "plan", "result")
   168	    }
   169	    for key in keys:
   170	        slots[key]["value"] = f"Demo {key} for {title}"
   171	    if result_notes is not None:
   172	        slots["result"]["notes"] = result_notes
   173	    return json.dumps(slots)
   174	
   175	
   176	def _insert_ticket(
   177	    conn: sqlite3.Connection,
   178	    now: int,
   179	    *,
   180	    title: str,
   181	    state: str,
   182	    priority: str,
   183	    deadline: str | None,
   184	    ceiling: str,
   185	    at_cap: str,
   186	    sprint_item_id: str | None,
   187	    sprint_id: str | None,
   188	    project: str | None,
   189	    fields: str,
   190	) -> str:
   191	    ticket_id = new_id(ID_PREFIXES["ticket"])
   192	    conn.execute(
   193	        "INSERT INTO tickets (id, title, state, priority, deadline, project, sprint_item_id, "
   194	        "sprint_id, recap, ceiling, at_cap, auto_blocked, consecutive_failures, "
   195	        "chat_session_key, alias, fields, created_at, updated_at) "
   196	        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
   197	        (
   198	            ticket_id, title, state, priority, deadline, project, sprint_item_id, sprint_id,
   199	            "", ceiling, at_cap, 0, 0, None, None, fields, now, now,
   200	        ),
   201	    )
   202	    append_event(
   203	        conn, ticket_id, EventKind.ticket_created,
   204	        {"title": title, "state": state, "alias": None, "source": "demo"},
   205	        now,
   206	    )
   207	    return ticket_id
   208	
   209	
   210	def _insert_link(conn: sqlite3.Connection, now: int, from_id: str, to_id: str, kind: str) -> None:
   211	    conn.execute(
   212	        "INSERT INTO links (from_id, to_id, kind) VALUES (?, ?, ?)", (from_id, to_id, kind)
   213	    )
   214	    append_event(
   215	        conn, from_id, EventKind.link_added,
   216	        {"from_id": from_id, "to_id": to_id, "kind": kind, "source": "demo"},
   217	        now,
   218	    )
   219	
   220	
   221	def _insert_day(
   222	    conn: sqlite3.Connection, now: int, today: date, t4: str, t8: str, t5: str
   223	) -> None:
   224	    did = day_id(today)
   225	    plan_tree = {
   226	        "root": {"focus": "Ship the demo feature slice.", "status": "accepted"},
   227	        "children": [
   228	            {
   229	                "ticket_id": t4, "note": "Land the slice behind the flag.",
   230	                "status": "accepted", "position": 0,
   231	            },
   232	            {
   233	                "ticket_id": t8, "note": "Stabilise the login test before review.",
   234	                "status": "proposed", "position": 1,
   235	            },
   236	        ],
   237	    }
   238	    conn.execute(
   239	        "INSERT INTO days (id, brief, notes, plan, chat_session_key, created_at, updated_at) "
   240	        "VALUES (?, ?, ?, ?, ?, ?, ?)",
   241	        (
   242	            did, "Demo day: ship the feature slice and clear the review queue.", "",
   243	            json.dumps(plan_tree), None, now, now,
   244	        ),
   245	    )
   246	    append_event(conn, did, EventKind.day_created, {"source": "demo"}, now)
   247	    for ticket_id, position in ((t4, 0), (t8, 1), (t5, 2)):
   248	        conn.execute(
   249	            "INSERT INTO day_tickets (day_id, ticket_id, position) VALUES (?, ?, ?)",
   250	            (did, ticket_id, position),
   251	        )
   252	        append_event(
   253	            conn, did, EventKind.day_ticket_added,
   254	            {"ticket_id": ticket_id, "position": position, "cause": "demo", "source": "demo"},
   255	            now,
   256	        )

codex
So far I see the expected 503 mapping and structured error envelope in core error handling. I’m narrowing the remaining question to seed path confinement and whether the API returns the exact report shape without hiding skipped sections.
exec
/bin/zsh -lc "nl -ba src/planner/core/adapters/fakes.py | sed -n '88,118p'" in /Users/khushaljagota/.hermes/planning-v2
exec
/bin/zsh -lc "nl -ba src/planner/core/server.py | sed -n '34,106p'" in /Users/khushaljagota/.hermes/planning-v2
 succeeded in 0ms:
    88	    calls: list[tuple[str | None, str, str]] = field(default_factory=list)
    89	    next_session: int = 1
    90	
    91	    def status(self) -> GatewayStatus:
    92	        return GatewayStatus(available=True)
    93	
    94	    def send(self, session_key: str | None, entity_id: str, text: str) -> ChatSendResult:
    95	        self.calls.append((session_key, entity_id, text))
    96	        if session_key is None:
    97	            session_key = f"fake-sess-{self.next_session}"
    98	            self.next_session += 1
    99	        return ChatSendResult(reply_text=f"echo: {text}", session_key=session_key)
   100	
   101	
   102	@dataclass
   103	class OfflineGatewayAdapter:
   104	    def status(self) -> GatewayStatus:
   105	        return GatewayStatus(available=False, detail="gateway offline")
   106	
   107	    def send(self, session_key: str | None, entity_id: str, text: str) -> ChatSendResult:
   108	        raise PlannerError(ErrorCode.gateway_offline, "gateway offline")

 succeeded in 0ms:
    34	from planner.sprints.api import router as sprints_router
    35	from planner.tickets.api import router as tickets_router
    36	
    37	_log = logging.getLogger("planner.server")
    38	
    39	_STATUS_BY_CODE: dict[ErrorCode, int] = {
    40	    ErrorCode.not_found: 404,
    41	    ErrorCode.stale_claim: 409,
    42	    ErrorCode.gateway_offline: 503,
    43	}
    44	
    45	_SHELL = "<!doctype html><meta charset=utf-8><title>planner</title><div id=app></div>"
    46	
    47	
    48	def http_status_for(code: ErrorCode) -> int:
    49	    return _STATUS_BY_CODE.get(code, 400)
    50	
    51	
    52	def create_app(
    53	    config: Config,
    54	    clock: Clock,
    55	    adapters: Adapters,
    56	    conn_factory: Callable[[], sqlite3.Connection],
    57	) -> FastAPI:
    58	    # The DDL carries the literal 200 title cap; the config value is what write
    59	    # paths enforce. Guard that they agree (amendment 11). Factory time is startup
    60	    # and strictly earlier than any request, so this fires even for TestClient apps
    61	    # that never enter the lifespan.
    62	    assert config.title_max_chars == 200, "title_max_chars must equal the DDL literal (200)"
    63	
    64	    @asynccontextmanager
    65	    async def _lifespan(app_: FastAPI) -> AsyncIterator[None]:
    66	        Path(config.db_path).parent.mkdir(parents=True, exist_ok=True)
    67	        Path(config.logs_dir).mkdir(parents=True, exist_ok=True)
    68	        loops: Any = None
    69	        if not config.test_mode:  # D6: background loops never run in test mode
    70	            try:
    71	                module = importlib.import_module("planner.core.loops")
    72	                start = module.start_background_loops
    73	            except (ImportError, AttributeError):
    74	                _log.warning("planner.core.loops unavailable; running without background loops")
    75	            else:
    76	                loops = start(config, clock, adapters, conn_factory)
    77	        try:
    78	            yield
    79	        finally:
    80	            if loops is not None:
    81	                await loops.stop()
    82	
    83	    app = FastAPI(title="planner", version="2.0.0", lifespan=_lifespan)
    84	    app.state.config = config
    85	    app.state.clock = clock
    86	    app.state.adapters = adapters
    87	    app.state.conn_factory = conn_factory
    88	
    89	    @app.exception_handler(PlannerError)
    90	    async def handle_planner_error(request: Request, exc: PlannerError) -> JSONResponse:
    91	        return JSONResponse(status_code=http_status_for(exc.code), content=exc.to_payload())
    92	
    93	    for domain_router in (
    94	        tickets_router,
    95	        sprints_router,
    96	        days_router,
    97	        dispatch_router,
    98	        seed_router,
    99	        chat_router,
   100	    ):
   101	        app.include_router(domain_router, prefix="/api")
   102	
   103	    @app.get("/api/meta")
   104	    async def meta() -> dict[str, Any]:
   105	        return {
   106	            "ui_debounce_ms": config.ui_debounce_ms,

exec
/bin/zsh -lc "nl -ba src/planner/seed/importer.py | sed -n '1,260p'" in /Users/khushaljagota/.hermes/planning-v2
 succeeded in 0ms:
     1	"""The seed import pipeline: parse the source tree, land rows behind one
     2	transaction, and report counts + every skipped section. Self-contained direct
     3	SQL against the core DDL (canonical writers do not exist at build time);
     4	idempotent by alias (tickets) / title (items, ideas) / name (sprints)."""
     5	
     6	from __future__ import annotations
     7	
     8	import json
     9	import sqlite3
    10	import time
    11	from pathlib import Path
    12	from typing import cast
    13	
    14	from planner.core.contracts import EventKind
    15	from planner.core.errors import ErrorCode, PlannerError
    16	from planner.core.events import append_event
    17	from planner.core.ids import ID_PREFIXES, new_id
    18	from planner.seed.contracts import (
    19	    MigrationReport,
    20	    ParsedIdea,
    21	    ParsedItem,
    22	    ParsedSprint,
    23	    ParsedTicket,
    24	    SkippedSection,
    25	)
    26	from planner.seed.logic.blocks import REASON_DAILY, REASON_FILE, excerpt_of
    27	from planner.seed.logic.deferred import parse_deferred
    28	from planner.seed.logic.ideas import parse_ideas
    29	from planner.seed.logic.kickoff import parse_kickoff, parse_review
    30	from planner.seed.logic.latest import pick_latest_daily
    31	from planner.seed.logic.tracking import parse_tracking
    32	from planner.seed.logic.workspace import parse_workspace
    33	
    34	
    35	def seed_from_source(conn: sqlite3.Connection, source_dir: str | Path) -> MigrationReport:
    36	    root = Path(source_dir)
    37	    now = int(time.time())
    38	    if not root.exists():
    39	        raise PlannerError(ErrorCode.validation, f"seed source directory not found: {root}")
    40	    kickoff_path = root / "sprints" / "current" / "sprint-kickoff.md"
    41	    if not kickoff_path.exists():
    42	        raise PlannerError(
    43	            ErrorCode.validation, "seed source has no sprints/current/sprint-kickoff.md"
    44	        )
    45	
    46	    report = MigrationReport()
    47	    skipped = report.skipped
    48	
    49	    def rel(path: Path) -> str:
    50	        return path.relative_to(root).as_posix()
    51	
    52	    sprint, sk = parse_kickoff(kickoff_path.read_text(), rel(kickoff_path))
    53	    skipped.extend(sk)
    54	
    55	    tracking_items: list[ParsedItem] = []
    56	    tracking_path = root / "sprints" / "current" / "sprint-tracking.md"
    57	    if tracking_path.exists():
    58	        tracking_items, sk = parse_tracking(tracking_path.read_text(), rel(tracking_path))
    59	        skipped.extend(sk)
    60	
    61	    review: dict[str, str] = {}
    62	    review_path = root / "sprints" / "current" / "sprint-review.md"
    63	    if review_path.exists():
    64	        review, sk = parse_review(review_path.read_text(), rel(review_path))
    65	        skipped.extend(sk)
    66	
    67	    tickets: list[ParsedTicket] = []
    68	    daily_dir = root / "sprints" / "current" / "daily"
    69	    if daily_dir.exists():
    70	        folders: list[tuple[str, bool]] = [
    71	            (child.name, (child / "workspace.md").exists())
    72	            for child in sorted(daily_dir.iterdir())
    73	            if child.is_dir()
    74	        ]
    75	        chosen, skipped_folders = pick_latest_daily(folders)
    76	        for name in skipped_folders:
    77	            skipped.append(
    78	                SkippedSection(f"sprints/current/daily/{name}", None, REASON_DAILY, "")
    79	            )
    80	        if chosen is not None:
    81	            chosen_dir = daily_dir / chosen
    82	            for file in sorted(chosen_dir.iterdir()):
    83	                if file.is_file() and file.name != "workspace.md":
    84	                    skipped.append(
    85	                        SkippedSection(rel(file), None, REASON_FILE, excerpt_of(file.read_text()))
    86	                    )
    87	            ws_path = chosen_dir / "workspace.md"
    88	            if ws_path.exists():
    89	                tickets, sk = parse_workspace(
    90	                    ws_path.read_text(), rel(ws_path), [item.title for item in tracking_items]
    91	                )
    92	                skipped.extend(sk)
    93	
    94	    deferred_items: list[ParsedItem] = []
    95	    deferred_path = root / "deferred.md"
    96	    if deferred_path.exists():
    97	        deferred_items, sk = parse_deferred(deferred_path.read_text(), rel(deferred_path))
    98	        skipped.extend(sk)
    99	
   100	    ideas: list[ParsedIdea] = []
   101	    ideas_path = root / "ideas.md"
   102	    if ideas_path.exists():
   103	        ideas, sk = parse_ideas(ideas_path.read_text(), rel(ideas_path))
   104	        skipped.extend(sk)
   105	
   106	    conn.execute("BEGIN IMMEDIATE")
   107	    try:
   108	        sprint_id = _import_sprint(conn, sprint, review, report, now)
   109	        items_by_title: dict[str, str] = {}
   110	        _import_items(conn, tracking_items, sprint_id, items_by_title, report, now, deferred=False)
   111	        _import_tickets(conn, tickets, sprint_id, items_by_title, report, now)
   112	        _import_items(conn, deferred_items, None, items_by_title, report, now, deferred=True)
   113	        _import_ideas(conn, ideas, report, now)
   114	        conn.execute("COMMIT")
   115	    except Exception:
   116	        conn.execute("ROLLBACK")
   117	        raise
   118	    return report
   119	
   120	
   121	def _import_sprint(
   122	    conn: sqlite3.Connection,
   123	    sprint: ParsedSprint,
   124	    review: dict[str, str],
   125	    report: MigrationReport,
   126	    now: int,
   127	) -> str:
   128	    row = conn.execute("SELECT id FROM sprints WHERE name = ?", (sprint.name,)).fetchone()
   129	    if row is not None:
   130	        report.duplicates_skipped += 1
   131	        return cast(str, row["id"])
   132	    sprint_id = new_id(ID_PREFIXES["sprint"])
   133	    conn.execute(
   134	        "INSERT INTO sprints (id, name, date_start, date_end, limiting_factor, primary_bet, "
   135	        "supports, premortem, outcomes, solo_reflection, joint_discussion, updates_to_thinking, "
   136	        "carry_forward, created_at, updated_at) "
   137	        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
   138	        (
   139	            sprint_id, sprint.name, sprint.date_start, sprint.date_end,
   140	            sprint.limiting_factor, sprint.primary_bet, sprint.supports, sprint.premortem,
   141	            review.get("outcomes", ""), review.get("solo_reflection", ""),
   142	            review.get("joint_discussion", ""), review.get("updates_to_thinking", ""),
   143	            review.get("carry_forward", ""), now, now,
   144	        ),
   145	    )
   146	    append_event(
   147	        conn, sprint_id, EventKind.sprint_created,
   148	        {
   149	            "name": sprint.name, "date_start": sprint.date_start,
   150	            "date_end": sprint.date_end, "source": "seed",
   151	        },
   152	        now,
   153	    )
   154	    report.sprints += 1
   155	    return sprint_id
   156	
   157	
   158	def _import_items(
   159	    conn: sqlite3.Connection,
   160	    items: list[ParsedItem],
   161	    sprint_id: str | None,
   162	    items_by_title: dict[str, str],
   163	    report: MigrationReport,
   164	    now: int,
   165	    deferred: bool,
   166	) -> None:
   167	    for item in items:
   168	        row = conn.execute(
   169	            "SELECT id FROM sprint_items WHERE title = ?", (item.title,)
   170	        ).fetchone()
   171	        if row is not None:
   172	            report.duplicates_skipped += 1
   173	            items_by_title[item.title] = cast(str, row["id"])
   174	            continue
   175	        item_id = new_id(ID_PREFIXES["sprint_item"])
   176	        conn.execute(
   177	            "INSERT INTO sprint_items (id, title, body, status, priority, deadline, project, "
   178	            "current_state_note, sprint_id, created_at, updated_at) "
   179	            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
   180	            (
   181	                item_id, item.title, item.body, item.status.value, item.priority.value,
   182	                item.deadline, item.project.value, "", sprint_id, now, now,
   183	            ),
   184	        )
   185	        append_event(
   186	            conn, item_id, EventKind.sprint_item_created,
   187	            {
   188	                "title": item.title, "status": item.status.value,
   189	                "sprint_id": sprint_id, "source": "seed",
   190	            },
   191	            now,
   192	        )
   193	        if deferred:
   194	            report.deferred_items += 1
   195	        else:
   196	            report.sprint_items += 1
   197	        items_by_title[item.title] = item_id
   198	
   199	
   200	def _import_tickets(
   201	    conn: sqlite3.Connection,
   202	    tickets: list[ParsedTicket],
   203	    sprint_id: str,
   204	    items_by_title: dict[str, str],
   205	    report: MigrationReport,
   206	    now: int,
   207	) -> None:
   208	    for ticket in tickets:
   209	        if ticket.alias is not None:
   210	            row = conn.execute("SELECT id FROM tickets WHERE alias = ?", (ticket.alias,)).fetchone()
   211	        else:
   212	            row = conn.execute("SELECT id FROM tickets WHERE title = ?", (ticket.title,)).fetchone()
   213	        if row is not None:
   214	            report.duplicates_skipped += 1
   215	            continue
   216	        ticket_id = new_id(ID_PREFIXES["ticket"])
   217	        item_id = items_by_title.get(ticket.item_title) if ticket.item_title is not None else None
   218	        if item_id is not None:
   219	            sprint_item_id: str | None = item_id
   220	            row_sprint_id: str | None = None
   221	        else:
   222	            sprint_item_id = None
   223	            row_sprint_id = sprint_id
   224	        fields = {
   225	            "success": {"value": ticket.success, "proposal": None, "notes": ticket.body or None},
   226	            "approach": {"value": ticket.approach, "proposal": None, "notes": None},
   227	            "plan": {"value": None, "proposal": None, "notes": None},
   228	            "result": {"value": None, "proposal": None, "notes": None},
   229	        }
   230	        conn.execute(
   231	            "INSERT INTO tickets (id, title, state, priority, deadline, project, sprint_item_id, "
   232	            "sprint_id, recap, ceiling, at_cap, auto_blocked, consecutive_failures, "
   233	            "chat_session_key, alias, fields, created_at, updated_at) "
   234	            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
   235	            (
   236	                ticket_id, ticket.title, ticket.state.value, ticket.priority.value, None, None,
   237	                sprint_item_id, row_sprint_id, "", ticket.state.value, "propose", 0, 0,
   238	                ticket.chat_session_key, ticket.alias, json.dumps(fields), now, now,
   239	            ),
   240	        )
   241	        append_event(
   242	            conn, ticket_id, EventKind.ticket_created,
   243	            {
   244	                "title": ticket.title, "state": ticket.state.value,
   245	                "alias": ticket.alias, "source": "seed",
   246	            },
   247	            now,
   248	        )
   249	        report.tickets += 1
   250	        if item_id is not None:
   251	            conn.execute(
   252	                "INSERT INTO links (from_id, to_id, kind) VALUES (?, ?, 'belongs_to')",
   253	                (ticket_id, item_id),
   254	            )
   255	            append_event(
   256	                conn, ticket_id, EventKind.link_added,
   257	                {"from_id": ticket_id, "to_id": item_id, "kind": "belongs_to", "source": "seed"},
   258	                now,
   259	            )
   260	            report.links += 1

exec
/bin/zsh -lc "nl -ba src/planner/core/adapters/real.py | sed -n '148,186p'" in /Users/khushaljagota/.hermes/planning-v2
exec
/bin/zsh -lc "rg -n \"open\\(|read_text|read_bytes|glob\\(|rglob\\(|Path\\(|source_dir|expanduser|resolve|\\.parent|\\.\\.\" src/planner/seed -g '*.py'" in /Users/khushaljagota/.hermes/planning-v2
 succeeded in 0ms:
   148	            status=NodeStatus.proposed,
   149	            position=child.position,
   150	        )
   151	
   152	
   153	class RealGatewayAdapter:
   154	    def __init__(self, config: Config) -> None:
   155	        self._config = config
   156	
   157	    def status(self) -> GatewayStatus:
   158	        try:
   159	            importlib.import_module(_GATEWAY_MODULE)
   160	        except ImportError as exc:
   161	            return GatewayStatus(available=False, detail=str(exc))
   162	        return GatewayStatus(available=True)
   163	
   164	    def send(self, session_key: str | None, entity_id: str, text: str) -> ChatSendResult:
   165	        try:
   166	            module = importlib.import_module(_GATEWAY_MODULE)
   167	        except ImportError as exc:
   168	            raise PlannerError(
   169	                ErrorCode.gateway_offline, "chat gateway unavailable", {"detail": str(exc)}
   170	            ) from exc
   171	        try:
   172	            raw = module.send_message(
   173	                session_key=session_key, entity_id=entity_id, text=text
   174	            )
   175	            return ChatSendResult(
   176	                reply_text=str(raw["reply_text"]), session_key=str(raw["session_key"])
   177	            )
   178	        except Exception as exc:
   179	            raise PlannerError(
   180	                ErrorCode.gateway_offline, "chat gateway send failed", {"detail": str(exc)}
   181	            ) from exc

 succeeded in 0ms:
src/planner/seed/demo.py:164:def _fields(title: str, keys: tuple[str, ...], result_notes: str | None = None) -> str:
src/planner/seed/contracts.py:29:# `Priority:` labels are literal P0..P3 in the source (verified in the snapshot) — identity map.
src/planner/seed/api.py:39:    source_dir = body.get("source_dir")
src/planner/seed/api.py:41:    has_source = source_dir is not None
src/planner/seed/api.py:43:        raise PlannerError(ErrorCode.validation, "provide exactly one of source_dir or demo")
src/planner/seed/api.py:51:            if not isinstance(source_dir, str) or not Path(source_dir).is_dir():
src/planner/seed/api.py:53:                    ErrorCode.validation, "source_dir must be an existing directory"
src/planner/seed/api.py:55:            report = seed_from_source(conn, source_dir)
src/planner/seed/logic/tracking.py:20:from planner.seed.logic.fieldmap import parse_project, resolve_priority
src/planner/seed/logic/tracking.py:70:        priority=resolve_priority(priority_raw, urgency_raw),
src/planner/seed/logic/blocks.py:39:    """Split on ``^## `` lines (H2 only). Returns (preamble, [(heading, body), ...])."""
src/planner/seed/logic/workspace.py:24:from planner.seed.logic.fieldmap import resolve_priority
src/planner/seed/logic/workspace.py:96:                priority = resolve_priority(value, None)
src/planner/seed/logic/fieldmap.py:14:def resolve_priority(priority_raw: str | None, urgency_raw: str | None) -> Priority:
src/planner/seed/importer.py:35:def seed_from_source(conn: sqlite3.Connection, source_dir: str | Path) -> MigrationReport:
src/planner/seed/importer.py:36:    root = Path(source_dir)
src/planner/seed/importer.py:52:    sprint, sk = parse_kickoff(kickoff_path.read_text(), rel(kickoff_path))
src/planner/seed/importer.py:58:        tracking_items, sk = parse_tracking(tracking_path.read_text(), rel(tracking_path))
src/planner/seed/importer.py:64:        review, sk = parse_review(review_path.read_text(), rel(review_path))
src/planner/seed/importer.py:85:                        SkippedSection(rel(file), None, REASON_FILE, excerpt_of(file.read_text()))
src/planner/seed/importer.py:90:                    ws_path.read_text(), rel(ws_path), [item.title for item in tracking_items]
src/planner/seed/importer.py:97:        deferred_items, sk = parse_deferred(deferred_path.read_text(), rel(deferred_path))
src/planner/seed/importer.py:103:        ideas, sk = parse_ideas(ideas_path.read_text(), rel(ideas_path))

exec
/bin/zsh -lc "nl -ba src/planner/days/data.py | sed -n '1,150p'" in /Users/khushaljagota/.hermes/planning-v2
 succeeded in 0ms:
     1	"""The day data layer. Materializes a day on first read/write (no "missing day"
     2	state), owns the ordered day-ticket list (contiguous positions from 0), plan JSON
     3	storage, brief/notes writes, and applies the logic layer's plan-tree effects.
     4	Every state transition here appends exactly one canonical event. No FastAPI/
     5	pydantic; times come in as unix-second ints from the caller's clock."""
     6	
     7	from __future__ import annotations
     8	
     9	import json
    10	import sqlite3
    11	
    12	from planner.core.contracts import EventKind
    13	from planner.core.events import append_event
    14	from planner.days.contracts import Day, DayTicket, PlanTree
    15	from planner.days.logic.effects import (
    16	    AddTicketToDay,
    17	    Effect,
    18	    EmitEvent,
    19	    ReplanChild,
    20	    ReplanRequest,
    21	    ReplanRoot,
    22	)
    23	from planner.days.logic.tree import tree_from_dict, tree_to_dict
    24	
    25	
    26	def materialize_day(conn: sqlite3.Connection, day_id: str, now_unix: int) -> None:
    27	    """§3.4: create the day row if absent (brief='', notes='', plan=NULL,
    28	    chat_session_key=NULL, created_at=updated_at=now_unix) and append a
    29	    day_created event. Idempotent: a present day → no write, no event."""
    30	    if conn.execute("SELECT 1 FROM days WHERE id = ?", (day_id,)).fetchone() is not None:
    31	        return
    32	    conn.execute(
    33	        "INSERT INTO days (id, brief, notes, plan, chat_session_key, created_at, updated_at) "
    34	        "VALUES (?, '', '', NULL, NULL, ?, ?)",
    35	        (day_id, now_unix, now_unix),
    36	    )
    37	    append_event(conn, day_id, EventKind.day_created, {}, now_unix)
    38	
    39	
    40	def read_day(conn: sqlite3.Connection, day_id: str, now_unix: int) -> Day:
    41	    """§3.4 'reading a nonexistent day materializes it empty'. Materializes, then
    42	    SELECTs the row and builds a Day (plan parsed via tree_from_dict when set)."""
    43	    materialize_day(conn, day_id, now_unix)
    44	    row = conn.execute(
    45	        "SELECT id, brief, notes, plan, chat_session_key, created_at, updated_at "
    46	        "FROM days WHERE id = ?",
    47	        (day_id,),
    48	    ).fetchone()
    49	    assert row is not None
    50	    plan_json = row["plan"]
    51	    return Day(
    52	        id=row["id"],
    53	        brief=row["brief"],
    54	        notes=row["notes"],
    55	        plan=tree_from_dict(json.loads(plan_json)) if plan_json is not None else None,
    56	        chat_session_key=row["chat_session_key"],
    57	        created_at=row["created_at"],
    58	        updated_at=row["updated_at"],
    59	    )
    60	
    61	
    62	def list_day_tickets(conn: sqlite3.Connection, day_id: str) -> list[DayTicket]:
    63	    """day_tickets for the day, ordered by position (contiguous from 0)."""
    64	    rows = conn.execute(
    65	        "SELECT day_id, ticket_id, position FROM day_tickets WHERE day_id = ? ORDER BY position",
    66	        (day_id,),
    67	    ).fetchall()
    68	    return [
    69	        DayTicket(day_id=row["day_id"], ticket_id=row["ticket_id"], position=row["position"])
    70	        for row in rows
    71	    ]
    72	
    73	
    74	def load_plan(conn: sqlite3.Connection, day_id: str) -> PlanTree | None:
    75	    """Parse days.plan JSON → PlanTree, or None (absent day or NULL plan)."""
    76	    row = conn.execute("SELECT plan FROM days WHERE id = ?", (day_id,)).fetchone()
    77	    if row is None or row["plan"] is None:
    78	        return None
    79	    return tree_from_dict(json.loads(row["plan"]))
    80	
    81	
    82	def store_plan(
    83	    conn: sqlite3.Connection, day_id: str, tree: PlanTree | None, now_unix: int
    84	) -> None:
    85	    """Materialize, then write days.plan = tree_to_dict(tree) JSON (or NULL) and
    86	    bump updated_at. No event here (callers emit the specific plan_* event)."""
    87	    materialize_day(conn, day_id, now_unix)
    88	    plan_json = json.dumps(tree_to_dict(tree)) if tree is not None else None
    89	    conn.execute(
    90	        "UPDATE days SET plan = ?, updated_at = ? WHERE id = ?",
    91	        (plan_json, now_unix, day_id),
    92	    )
    93	
    94	
    95	def store_judgment(
    96	    conn: sqlite3.Connection, day_id: str, brief: str, tree: PlanTree, now_unix: int
    97	) -> None:
    98	    """Boundary success path: write days.brief + days.plan (serialized) +
    99	    updated_at in one UPDATE. No day_updated event (that kind is reserved for
   100	    manual edits); the boundary emits plan_proposed separately."""
   101	    conn.execute(
   102	        "UPDATE days SET brief = ?, plan = ?, updated_at = ? WHERE id = ?",
   103	        (brief, json.dumps(tree_to_dict(tree)), now_unix, day_id),
   104	    )
   105	
   106	
   107	def add_day_ticket(
   108	    conn: sqlite3.Connection, day_id: str, ticket_id: str, now_unix: int, cause: str = "manual"
   109	) -> bool:
   110	    """§3.4: append at end with the next contiguous position (= current count).
   111	    Idempotent: if (day_id, ticket_id) already present → return False, no event.
   112	    On add → INSERT, append day_ticket_added {ticket_id, position, cause}, bump
   113	    updated_at, return True. Materializes the day first (write path)."""
   114	    materialize_day(conn, day_id, now_unix)
   115	    present = conn.execute(
   116	        "SELECT 1 FROM day_tickets WHERE day_id = ? AND ticket_id = ?",
   117	        (day_id, ticket_id),
   118	    ).fetchone()
   119	    if present is not None:
   120	        return False
   121	    position = conn.execute(
   122	        "SELECT COUNT(*) AS n FROM day_tickets WHERE day_id = ?", (day_id,)
   123	    ).fetchone()["n"]
   124	    conn.execute(
   125	        "INSERT INTO day_tickets (day_id, ticket_id, position) VALUES (?, ?, ?)",
   126	        (day_id, ticket_id, position),
   127	    )
   128	    append_event(
   129	        conn,
   130	        day_id,
   131	        EventKind.day_ticket_added,
   132	        {"ticket_id": ticket_id, "position": position, "cause": cause},
   133	        now_unix,
   134	    )
   135	    conn.execute("UPDATE days SET updated_at = ? WHERE id = ?", (now_unix, day_id))
   136	    return True
   137	
   138	
   139	def remove_day_ticket(
   140	    conn: sqlite3.Connection, day_id: str, ticket_id: str, now_unix: int
   141	) -> None:
   142	    """§3.4: delete the association only (ticket state untouched). If nothing was
   143	    deleted (rowcount 0) → no-op, no event, no re-pack. Else re-pack remaining
   144	    positions to 0..n-1 in existing position order, append day_ticket_removed
   145	    {ticket_id}, bump updated_at."""
   146	    cursor = conn.execute(
   147	        "DELETE FROM day_tickets WHERE day_id = ? AND ticket_id = ?",
   148	        (day_id, ticket_id),
   149	    )
   150	    if cursor.rowcount == 0:

exec
/bin/zsh -lc "nl -ba src/planner/seed/importer.py | sed -n '260,380p'" in /Users/khushaljagota/.hermes/planning-v2
 succeeded in 0ms:
   260	            report.links += 1
   261	
   262	
   263	def _import_ideas(
   264	    conn: sqlite3.Connection,
   265	    ideas: list[ParsedIdea],
   266	    report: MigrationReport,
   267	    now: int,
   268	) -> None:
   269	    for idea in ideas:
   270	        row = conn.execute("SELECT id FROM ideas WHERE title = ?", (idea.title,)).fetchone()
   271	        if row is not None:
   272	            report.duplicates_skipped += 1
   273	            continue
   274	        idea_id = new_id(ID_PREFIXES["idea"])
   275	        conn.execute(
   276	            "INSERT INTO ideas (id, title, body, project, created_at, updated_at) "
   277	            "VALUES (?, ?, ?, ?, ?, ?)",
   278	            (
   279	                idea_id, idea.title, idea.body,
   280	                idea.project.value if idea.project is not None else None, now, now,
   281	            ),
   282	        )
   283	        append_event(
   284	            conn, idea_id, EventKind.idea_created,
   285	            {"title": idea.title, "source": "seed"},
   286	            now,
   287	        )
   288	        report.ideas += 1

codex
The chat path matches the requested first-reply flow for both entity kinds at the static level. I’m doing one more pass over the adapter and seed contracts to avoid calling something faithful when the typed contract says otherwise.
exec
/bin/zsh -lc "nl -ba src/planner/core/adapters/base.py | sed -n '1,140p'" in /Users/khushaljagota/.hermes/planning-v2
 succeeded in 0ms:
     1	"""Adapter protocols and their request/result shapes: the three external
     2	boundaries (spawn subprocess, boundary/replan agent, chat gateway). Stdlib only.
     3	The dependency arrow is core-adapters -> domain-contracts, never the reverse."""
     4	
     5	from __future__ import annotations
     6	
     7	from dataclasses import dataclass
     8	from typing import Any, Protocol
     9	
    10	from planner.chat.contracts import ChatSendResult, GatewayStatus
    11	from planner.days.contracts import PlanNode, PlanTree
    12	
    13	
    14	@dataclass(frozen=True)
    15	class SpawnRequest:                # §7.4 — everything the subprocess boundary needs
    16	    ticket_id: str
    17	    run_id: str
    18	    claim: str
    19	    server_url: str
    20	    log_path: str                  # per-run log file under logs_dir
    21	    hermes_bin: str
    22	    profile: str                   # R3: "default"
    23	    skill: str                     # R3: "planning-worker"
    24	
    25	
    26	@dataclass(frozen=True)
    27	class SpawnResult:
    28	    ok: bool
    29	    pid: int | None = None         # set when ok
    30	    error: str | None = None       # set when not ok -> run status spawn_failed
    31	
    32	
    33	class SpawnAdapter(Protocol):
    34	    def spawn(self, request: SpawnRequest) -> SpawnResult: ...
    35	
    36	
    37	@dataclass(frozen=True)
    38	class BoundaryInputs:              # §6.2 deterministic-pass outputs, DB-internal only (R4)
    39	    planning_date: str             # ISO
    40	    carryover: list[dict[str, Any]]      # ticket digests: {id, title, state, priority}
    41	    overdue: list[dict[str, Any]]        # same digest shape, tickets and items
    42	    approvals_digest: list[dict[str, Any]]   # {entity_id, kind, waiting_since}
    43	
    44	
    45	@dataclass(frozen=True)
    46	class BoundaryJudgment:
    47	    brief_markdown: str
    48	    plan_tree: PlanTree
    49	
    50	
    51	class BoundaryAdapter(Protocol):
    52	    # Timeout (boundary_timeout_seconds, 60s) is owned by the CALLER — the boundary
    53	    # scheduler wraps calls; adapters just do the work or raise.
    54	    def judgment(self, inputs: BoundaryInputs) -> BoundaryJudgment: ...
    55	    def replan_root(self, day_id: str, inputs: BoundaryInputs) -> PlanTree: ...
    56	    def replan_child(self, day_id: str, child: PlanNode, inputs: BoundaryInputs) -> PlanNode: ...
    57	
    58	
    59	class GatewayAdapter(Protocol):
    60	    def status(self) -> GatewayStatus: ...
    61	    def send(self, session_key: str | None, entity_id: str, text: str) -> ChatSendResult: ...

exec
/bin/zsh -lc "rg -n \"CREATE TABLE (tickets|days|events)|chat_session_key|EventKind\" src/planner -g '*.py'" in /Users/khushaljagota/.hermes/planning-v2
exec
/bin/zsh -lc "rg -n \"MigrationReport|seed\\(|seed_demo|seed_from_source|chat_session_created|gateway_offline|db_not_empty|source_dir|demo\" tests src -g '*.py'" in /Users/khushaljagota/.hermes/planning-v2
 succeeded in 0ms:
tests/unit/test_chat_seed.py:4:the guarded first-reply race, and the demo/fixture migration reports."""
tests/unit/test_chat_seed.py:88:    assert _events(db_path, tid, "chat_session_created") == [{"session_key": "fake-sess-1"}]
tests/unit/test_chat_seed.py:101:    assert _events(db_path, tid, "chat_session_created") == [{"session_key": "fake-sess-1"}]
tests/unit/test_chat_seed.py:113:    assert _events(db_path, day, "chat_session_created") == [{"session_key": "fake-sess-1"}]
tests/unit/test_chat_seed.py:138:    assert response.json()["error"]["code"] == "gateway_offline"
tests/unit/test_chat_seed.py:140:    assert _events(db_path, tid, "chat_session_created") == []
tests/unit/test_chat_seed.py:159:def test_seed_demo_counts(tmp_path: Path) -> None:
tests/unit/test_chat_seed.py:162:        response = client.post("/api/seed", json={"demo": True})
tests/unit/test_chat_seed.py:176:def test_seed_demo_twice_is_db_not_empty(tmp_path: Path) -> None:
tests/unit/test_chat_seed.py:179:        first = client.post("/api/seed", json={"demo": True})
tests/unit/test_chat_seed.py:181:        second = client.post("/api/seed", json={"demo": True})
tests/unit/test_chat_seed.py:183:    assert second.json()["error"]["code"] == "db_not_empty"
tests/unit/test_chat_seed.py:186:def test_seed_source_dir_imports_fixture(tmp_path: Path) -> None:
tests/unit/test_chat_seed.py:189:        response = client.post("/api/seed", json={"source_dir": str(FIXTURE)})
tests/unit/test_chat_seed.py:220:        response = client.post("/api/seed", json={"source_dir": str(tmp_path / "nope")})
tests/unit/test_chat_seed.py:230:        response = client.post("/api/seed", json={"source_dir": str(target)})
tests/unit/test_chat_seed.py:246:        response = client.post("/api/seed", json={"source_dir": str(FIXTURE), "demo": True})
tests/unit/test_chat_seed.py:300:    assert _events(db_path, tid, "chat_session_created") == []
src/planner/seed/demo.py:1:"""The ``plan seed --demo`` dataset (§12): a deterministic-by-content sample —
src/planner/seed/demo.py:23:def seed_demo(conn: sqlite3.Connection) -> None:
src/planner/seed/demo.py:27:                ErrorCode.db_not_empty, "seed --demo requires an empty database", {"table": table}
src/planner/seed/demo.py:33:        _build_demo(conn, now, today)
src/planner/seed/demo.py:40:def _build_demo(conn: sqlite3.Connection, now: int, today: date) -> None:
src/planner/seed/demo.py:43:        conn, sprint_id, "Ship the demo feature end to end.", "active", "P1", "Vylo",
src/planner/seed/demo.py:44:        "Parent item for the demo ticket pair.", now,
src/planner/seed/demo.py:69:        conn, now, title="Plan the demo feature rollout.", state="needs_plan",
src/planner/seed/demo.py:72:        fields=_fields("Plan the demo feature rollout.", ("success", "approach")),
src/planner/seed/demo.py:76:        conn, now, title="Implement the demo feature slice.", state="in_progress",
src/planner/seed/demo.py:79:        fields=_fields("Implement the demo feature slice.", ("success", "approach", "plan")),
src/planner/seed/demo.py:133:        {"name": "Demo Sprint", "date_start": date_start, "date_end": date_end, "source": "demo"},
src/planner/seed/demo.py:158:        {"title": title, "status": status, "sprint_id": sprint_id, "source": "demo"},
src/planner/seed/demo.py:204:        {"title": title, "state": state, "alias": None, "source": "demo"},
src/planner/seed/demo.py:216:        {"from_id": from_id, "to_id": to_id, "kind": kind, "source": "demo"},
src/planner/seed/demo.py:226:        "root": {"focus": "Ship the demo feature slice.", "status": "accepted"},
src/planner/seed/demo.py:246:    append_event(conn, did, EventKind.day_created, {"source": "demo"}, now)
src/planner/seed/demo.py:254:            {"ticket_id": ticket_id, "position": position, "cause": "demo", "source": "demo"},
tests/unit/test_seed.py:3:idempotent re-runs. Plus supporting tests for the demo dataset and the pure
tests/unit/test_seed.py:19:from planner.seed.demo import seed_demo
tests/unit/test_seed.py:20:from planner.seed.importer import seed_from_source
tests/unit/test_seed.py:78:    report = seed_from_source(tmp_db, FIXTURE)
tests/unit/test_seed.py:257:    report2 = seed_from_source(tmp_db, FIXTURE)
tests/unit/test_seed.py:272:def test_seed_demo_requires_empty_db(tmp_db: Connection) -> None:
tests/unit/test_seed.py:278:        seed_demo(tmp_db)
tests/unit/test_seed.py:279:    assert excinfo.value.code == ErrorCode.db_not_empty
tests/unit/test_seed.py:282:def test_seed_demo_dataset_shape(tmp_db: Connection) -> None:
tests/unit/test_seed.py:283:    seed_demo(tmp_db)
tests/unit/test_seed.py:306:        "SELECT id FROM sprint_items WHERE title = 'Ship the demo feature end to end.'"
tests/unit/test_seed.py:338:        seed_demo(tmp_db)
tests/unit/test_seed.py:339:    assert excinfo.value.code == ErrorCode.db_not_empty
src/planner/cli/main.py:80:@click.option("--demo", is_flag=True, default=False, help="Create the deterministic demo dataset.")
src/planner/cli/main.py:82:def seed(source: str | None, demo: bool, as_json: bool) -> None:
src/planner/cli/main.py:83:    """Import a markdown planning directory, or create a demo dataset (§12)."""
tests/unit/test_runtimes.py:626:    assert exc.value.code is ErrorCode.gateway_offline
src/planner/seed/contracts.py:103:class MigrationReport:             # printed, and structured under --json
src/planner/seed/api.py:1:"""Seed route (§9): POST /api/seed runs a migration and returns a MigrationReport."""
src/planner/seed/api.py:14:from planner.seed.contracts import MigrationReport
src/planner/seed/api.py:15:from planner.seed.demo import seed_demo
src/planner/seed/api.py:16:from planner.seed.importer import seed_from_source
src/planner/seed/api.py:21:def _demo_report(conn: sqlite3.Connection) -> MigrationReport:
src/planner/seed/api.py:25:    return MigrationReport(
src/planner/seed/api.py:38:async def seed(body: dict[str, Any], request: Request) -> dict[str, Any]:
src/planner/seed/api.py:39:    source_dir = body.get("source_dir")
src/planner/seed/api.py:40:    demo = bool(body.get("demo", False))
src/planner/seed/api.py:41:    has_source = source_dir is not None
src/planner/seed/api.py:42:    if has_source == demo:  # both present, or neither
src/planner/seed/api.py:43:        raise PlannerError(ErrorCode.validation, "provide exactly one of source_dir or demo")
src/planner/seed/api.py:47:        if demo:
src/planner/seed/api.py:48:            seed_demo(conn)  # raises db_not_empty on a non-empty database
src/planner/seed/api.py:49:            report = _demo_report(conn)
src/planner/seed/api.py:51:            if not isinstance(source_dir, str) or not Path(source_dir).is_dir():
src/planner/seed/api.py:53:                    ErrorCode.validation, "source_dir must be an existing directory"
src/planner/seed/api.py:55:            report = seed_from_source(conn, source_dir)
src/planner/seed/importer.py:19:    MigrationReport,
src/planner/seed/importer.py:35:def seed_from_source(conn: sqlite3.Connection, source_dir: str | Path) -> MigrationReport:
src/planner/seed/importer.py:36:    root = Path(source_dir)
src/planner/seed/importer.py:46:    report = MigrationReport()
src/planner/seed/importer.py:125:    report: MigrationReport,
src/planner/seed/importer.py:163:    report: MigrationReport,
src/planner/seed/importer.py:205:    report: MigrationReport,
src/planner/seed/importer.py:266:    report: MigrationReport,
src/planner/core/server.py:42:    ErrorCode.gateway_offline: 503,
src/planner/chat/service.py:3:session key onto the entity and logs one chat_session_created event in a single
src/planner/chat/service.py:76:        raise  # already structured (offline adapter raises gateway_offline)
src/planner/chat/service.py:79:            ErrorCode.gateway_offline, "gateway unavailable", {"cause": str(exc)}
src/planner/chat/service.py:93:                    EventKind.chat_session_created,
src/planner/core/adapters/fakes.py:108:        raise PlannerError(ErrorCode.gateway_offline, "gateway offline")
src/planner/core/errors.py:26:    gateway_offline = "gateway_offline"            # §11
src/planner/core/errors.py:27:    db_not_empty = "db_not_empty"                  # §12 seed --demo on a non-empty DB
src/planner/core/adapters/real.py:169:                ErrorCode.gateway_offline, "chat gateway unavailable", {"detail": str(exc)}
src/planner/core/adapters/real.py:180:                ErrorCode.gateway_offline, "chat gateway send failed", {"detail": str(exc)}
src/planner/core/contracts.py:89:    chat_session_created = "chat_session_created"    # {session_key}

 succeeded in 0ms:
src/planner/days/data.py:12:from planner.core.contracts import EventKind
src/planner/days/data.py:28:    chat_session_key=NULL, created_at=updated_at=now_unix) and append a
src/planner/days/data.py:33:        "INSERT INTO days (id, brief, notes, plan, chat_session_key, created_at, updated_at) "
src/planner/days/data.py:37:    append_event(conn, day_id, EventKind.day_created, {}, now_unix)
src/planner/days/data.py:45:        "SELECT id, brief, notes, plan, chat_session_key, created_at, updated_at "
src/planner/days/data.py:56:        chat_session_key=row["chat_session_key"],
src/planner/days/data.py:131:        EventKind.day_ticket_added,
src/planner/days/data.py:161:    append_event(conn, day_id, EventKind.day_ticket_removed, {"ticket_id": ticket_id}, now_unix)
src/planner/days/data.py:172:    append_event(conn, day_id, EventKind.day_updated, {"field": "brief"}, now_unix)
src/planner/days/data.py:182:    append_event(conn, day_id, EventKind.day_updated, {"field": "notes"}, now_unix)
src/planner/days/scheduler.py:22:from planner.core.contracts import EventKind, JsonDict
src/planner/days/scheduler.py:174:                EventKind.boundary_failed,
src/planner/days/scheduler.py:194:            EventKind.plan_replanned,
src/planner/days/scheduler.py:220:            EventKind.plan_replanned,
src/planner/days/contracts.py:44:    chat_session_key: str | None
src/planner/days/api.py:76:        "chat_session_key": day.chat_session_key,
src/planner/days/logic/tree.py:10:from planner.core.contracts import EventKind, JsonDict
src/planner/days/logic/tree.py:84:        return new_tree, [EmitEvent(EventKind.plan_node_accepted, {"node": "root"})]
src/planner/days/logic/tree.py:95:    effects: list[Effect] = [EmitEvent(EventKind.plan_node_accepted, {"node": ref})]
src/planner/days/logic/tree.py:110:    effects: list[Effect] = [EmitEvent(EventKind.plan_accepted_all, {})]
src/planner/days/logic/tree.py:124:        EmitEvent(EventKind.plan_node_invalidated, {"node": "root"}),
src/planner/days/logic/tree.py:141:        EmitEvent(EventKind.plan_node_invalidated, {"node": position}),
src/planner/days/logic/tree.py:151:        EmitEvent(EventKind.plan_rejected, {"old_tree": tree_to_dict(tree)})
src/planner/days/logic/effects.py:11:from planner.core.contracts import EventKind, JsonDict
src/planner/days/logic/effects.py:24:    """Append one events row. kind is an EventKind; payload is JSON-ready."""
src/planner/days/logic/effects.py:26:    kind: EventKind
src/planner/days/boundary.py:19:from planner.core.contracts import EventKind
src/planner/days/boundary.py:63:        EventKind.day_closed,
src/planner/days/boundary.py:82:            conn, ndid, EventKind.boundary_failed, {"error": _error_text(exc, timeout_s)}, now
src/planner/days/boundary.py:89:    append_event(conn, ndid, EventKind.plan_proposed, {"tree": tree_to_dict(proposed)}, now)
src/planner/dispatch/data.py:17:from planner.core.contracts import EventKind, JsonDict, Priority
src/planner/dispatch/data.py:66:    append_event(conn, ticket_id, EventKind.run_started, {"run_id": run_id, "pid": pid}, now)
src/planner/dispatch/data.py:100:        EventKind.claim_heartbeat,
src/planner/dispatch/data.py:206:        EventKind.run_closed,
src/planner/dispatch/data.py:214:            EventKind.auto_blocked,
src/planner/dispatch/data.py:253:            EventKind.claim_reclaimed,
src/planner/dispatch/data.py:271:    append_event(conn, ticket_id, EventKind.auto_block_cleared, {}, now)
src/planner/sprints/data.py:16:from planner.core.contracts import EventKind, Priority, Project
src/planner/sprints/data.py:203:            EventKind.sprint_created,
src/planner/sprints/data.py:236:            EventKind.sprint_updated,
src/planner/sprints/data.py:256:            append_event(conn, sprint_id, EventKind.kickoff_frozen, {"frozen_at": now}, now)
src/planner/sprints/data.py:272:            append_event(conn, sprint_id, EventKind.review_frozen, {"frozen_at": now}, now)
src/planner/sprints/data.py:296:            conn, sprint_id, EventKind.addendum_added, {"date": date, "text": text}, now
src/planner/sprints/data.py:343:            EventKind.sprint_item_created,
src/planner/sprints/data.py:383:            EventKind.item_updated,
src/planner/sprints/data.py:430:            EventKind.item_status_changed,
src/planner/sprints/data.py:466:                EventKind.proposal_superseded,
src/planner/sprints/data.py:488:            EventKind.proposal_filed,
src/planner/sprints/data.py:517:            EventKind.proposal_accepted,
src/planner/sprints/data.py:529:            EventKind.item_status_changed,
src/planner/sprints/data.py:552:            EventKind.item_updated,
src/planner/tickets/views.py:55:        "chat_session_key": ticket.chat_session_key,
src/planner/sprints/api.py:21:from planner.core.contracts import EventKind, JsonDict, Priority, Project
src/planner/sprints/api.py:91:        append_event(conn, idea_id, EventKind.idea_created, {"title": title, "source": "api"}, now)
src/planner/sprints/api.py:138:                EventKind.sprint_updated,
src/planner/sprints/api.py:146:                EventKind.sprint_updated,
src/planner/core/links.py:11:from planner.core.contracts import EventKind, LinkKind
src/planner/core/links.py:111:        append_event(conn, from_id, EventKind.link_added, detail, now)
src/planner/core/links.py:131:    append_event(conn, from_id, EventKind.link_removed, detail, now)
src/planner/core/events.py:10:from planner.core.contracts import EventKind, EventRow, JsonDict
src/planner/core/events.py:16:    kind: EventKind,
src/planner/core/contracts.py:37:class EventKind(StrEnum):
src/planner/seed/demo.py:12:from planner.core.contracts import EventKind
src/planner/seed/demo.py:132:        conn, sprint_id, EventKind.sprint_created,
src/planner/seed/demo.py:157:        conn, item_id, EventKind.sprint_item_created,
src/planner/seed/demo.py:195:        "chat_session_key, alias, fields, created_at, updated_at) "
src/planner/seed/demo.py:203:        conn, ticket_id, EventKind.ticket_created,
src/planner/seed/demo.py:215:        conn, from_id, EventKind.link_added,
src/planner/seed/demo.py:239:        "INSERT INTO days (id, brief, notes, plan, chat_session_key, created_at, updated_at) "
src/planner/seed/demo.py:246:    append_event(conn, did, EventKind.day_created, {"source": "demo"}, now)
src/planner/seed/demo.py:253:            conn, did, EventKind.day_ticket_added,
src/planner/core/db.py:71:  chat_session_key     TEXT,
src/planner/core/db.py:87:  chat_session_key TEXT,
src/planner/tickets/data.py:14:from planner.core.contracts import EventKind, Priority, Project
src/planner/tickets/data.py:59:        chat_session_key=row["chat_session_key"],
src/planner/tickets/data.py:142:            "chat_session_key, alias, fields, claim_lock, claim_expires, created_at, updated_at) "
src/planner/tickets/data.py:160:        append_event(conn, ticket_id, EventKind.ticket_created, {}, now)
src/planner/tickets/data.py:263:        append_event(conn, ticket_id, EventKind.note_updated, {"field": field.value}, now)
src/planner/tickets/data.py:276:        append_event(conn, ticket_id, EventKind.recap_updated, {}, now)
src/planner/tickets/data.py:293:            EventKind.ticket_updated,
src/planner/tickets/data.py:314:            EventKind.ticket_updated,
src/planner/tickets/data.py:339:            EventKind.ticket_updated,
src/planner/seed/contracts.py:80:    chat_session_key: str | None = None   # "Chat ID:"
src/planner/chat/contracts.py:19:    session_key: str               # persisted onto the entity's chat_session_key
src/planner/tickets/api.py:33:from planner.core.contracts import EventKind, JsonDict, LinkKind, Priority, Project
src/planner/tickets/api.py:186:            EventKind.ticket_updated,
src/planner/tickets/api.py:208:            EventKind.ticket_updated,
src/planner/tickets/contracts.py:111:    chat_session_key: str | None
src/planner/tickets/logic/resolution.py:11:from planner.core.contracts import EventKind
src/planner/tickets/logic/resolution.py:38:    return EventSpec(EventKind.state_changed, {"from": old.value, "to": new.value, "cause": cause})
src/planner/tickets/logic/resolution.py:59:                EventKind.proposal_superseded,
src/planner/tickets/logic/resolution.py:65:            EventKind.proposal_accepted,
src/planner/tickets/logic/resolution.py:84:                EventKind.grant_changed,
src/planner/tickets/logic/resolution.py:129:                EventKind.proposal_superseded,
src/planner/tickets/logic/resolution.py:135:            EventKind.proposal_filed,
src/planner/tickets/logic/resolution.py:178:            EventKind.proposal_accepted,
src/planner/tickets/logic/resolution.py:235:            EventKind.grant_changed,
src/planner/tickets/logic/decisions.py:9:from planner.core.contracts import EventKind, JsonDict
src/planner/tickets/logic/decisions.py:15:    kind: EventKind
src/planner/chat/service.py:17:from planner.core.contracts import EventKind
src/planner/chat/service.py:40:            "SELECT chat_session_key FROM tickets WHERE id = ?", (entity_id,)
src/planner/chat/service.py:44:        key: str | None = row["chat_session_key"]
src/planner/chat/service.py:60:        return "day", day.chat_session_key
src/planner/chat/service.py:85:                f"UPDATE {table} SET chat_session_key = ?, updated_at = ? "
src/planner/chat/service.py:86:                "WHERE id = ? AND chat_session_key IS NULL",
src/planner/chat/service.py:93:                    EventKind.chat_session_created,
src/planner/chat/service.py:99:                    f"SELECT chat_session_key FROM {table} WHERE id = ?", (entity_id,)
src/planner/chat/service.py:102:                    reply_text=result.reply_text, session_key=row["chat_session_key"]
src/planner/seed/importer.py:14:from planner.core.contracts import EventKind
src/planner/seed/importer.py:147:        conn, sprint_id, EventKind.sprint_created,
src/planner/seed/importer.py:186:            conn, item_id, EventKind.sprint_item_created,
src/planner/seed/importer.py:233:            "chat_session_key, alias, fields, created_at, updated_at) "
src/planner/seed/importer.py:238:                ticket.chat_session_key, ticket.alias, json.dumps(fields), now, now,
src/planner/seed/importer.py:242:            conn, ticket_id, EventKind.ticket_created,
src/planner/seed/importer.py:256:                conn, ticket_id, EventKind.link_added,
src/planner/seed/importer.py:284:            conn, idea_id, EventKind.idea_created,
src/planner/seed/logic/workspace.py:125:        chat_session_key=chat,

exec
/bin/zsh -lc "nl -ba tests/unit/test_chat_seed.py | sed -n '1,330p'" in /Users/khushaljagota/.hermes/planning-v2
 succeeded in 0ms:
     1	"""T13 acceptance: the chat send/status routes and the seed route, driven through
     2	a TestClient over create_app with fake adapters. Covers echo persistence + one
     3	event, key reuse, day materialization, not_found/validation/offline error codes,
     4	the guarded first-reply race, and the demo/fixture migration reports."""
     5	
     6	from __future__ import annotations
     7	
     8	import json
     9	from pathlib import Path
    10	from sqlite3 import Connection
    11	
    12	from fastapi.testclient import TestClient
    13	
    14	from planner.chat import service
    15	from planner.chat.contracts import ChatSendResult, GatewayStatus
    16	from planner.core.adapters.registry import build_adapters
    17	from planner.core.clock import build_clock
    18	from planner.core.config import load_config
    19	from planner.core.db import connect, create_schema
    20	from planner.core.server import create_app
    21	from planner.tickets.data import create_ticket
    22	
    23	FIXTURE = Path(__file__).resolve().parent.parent / "fixtures" / "planning-md"
    24	
    25	
    26	def _make_app(tmp_path: Path, gateway: str = "fake") -> tuple[object, Path]:
    27	    db_path = tmp_path / "planning-test.db"
    28	    boot = connect(str(db_path))
    29	    create_schema(boot)
    30	    boot.close()
    31	    config = load_config(
    32	        path=None,
    33	        env={
    34	            "PLAN_TEST_MODE": "1",
    35	            "PLAN_GATEWAY_ADAPTER": gateway,  # "fake" -> echo, "offline" -> offline
    36	            "PLAN_DB_PATH": str(db_path),
    37	        },
    38	    )
    39	    clock = build_clock(config)
    40	    adapters = build_adapters(config)
    41	
    42	    def conn_factory() -> Connection:
    43	        return connect(str(db_path))
    44	
    45	    return create_app(config, clock, adapters, conn_factory), db_path
    46	
    47	
    48	def _ticket(db_path: Path) -> str:
    49	    conn = connect(str(db_path))
    50	    try:
    51	        ticket = create_ticket(conn, title="Chat me.", actor="human", now=0, title_max_chars=200)
    52	    finally:
    53	        conn.close()
    54	    return ticket.id
    55	
    56	
    57	def _events(db_path: Path, entity_id: str, kind: str) -> list[dict[str, object]]:
    58	    conn = connect(str(db_path))
    59	    try:
    60	        rows = conn.execute(
    61	            "SELECT payload FROM events WHERE entity_id = ? AND kind = ? ORDER BY id",
    62	            (entity_id, kind),
    63	        ).fetchall()
    64	    finally:
    65	        conn.close()
    66	    return [json.loads(row["payload"]) for row in rows]
    67	
    68	
    69	def _stored_key(db_path: Path, table: str, entity_id: str) -> object:
    70	    conn = connect(str(db_path))
    71	    try:
    72	        row = conn.execute(
    73	            f"SELECT chat_session_key FROM {table} WHERE id = ?", (entity_id,)
    74	        ).fetchone()
    75	    finally:
    76	        conn.close()
    77	    return None if row is None else row["chat_session_key"]
    78	
    79	
    80	def test_chat_send_echo_persists_key_and_event(tmp_path: Path) -> None:
    81	    app, db_path = _make_app(tmp_path)
    82	    tid = _ticket(db_path)
    83	    with TestClient(app) as client:
    84	        response = client.post(f"/api/chat/{tid}/send", json={"text": "hello"})
    85	    assert response.status_code == 200
    86	    assert response.json() == {"reply_text": "echo: hello", "session_key": "fake-sess-1"}
    87	    assert _stored_key(db_path, "tickets", tid) == "fake-sess-1"
    88	    assert _events(db_path, tid, "chat_session_created") == [{"session_key": "fake-sess-1"}]
    89	
    90	
    91	def test_chat_send_second_send_reuses_key_no_new_event(tmp_path: Path) -> None:
    92	    app, db_path = _make_app(tmp_path)
    93	    tid = _ticket(db_path)
    94	    with TestClient(app) as client:
    95	        first = client.post(f"/api/chat/{tid}/send", json={"text": "hello"})
    96	        assert first.json()["session_key"] == "fake-sess-1"
    97	        second = client.post(f"/api/chat/{tid}/send", json={"text": "again"})
    98	    assert second.status_code == 200
    99	    assert second.json() == {"reply_text": "echo: again", "session_key": "fake-sess-1"}
   100	    assert _stored_key(db_path, "tickets", tid) == "fake-sess-1"
   101	    assert _events(db_path, tid, "chat_session_created") == [{"session_key": "fake-sess-1"}]
   102	
   103	
   104	def test_chat_send_day_materializes_and_persists(tmp_path: Path) -> None:
   105	    app, db_path = _make_app(tmp_path)
   106	    day = "day_2026-07-04"
   107	    with TestClient(app) as client:
   108	        response = client.post(f"/api/chat/{day}/send", json={"text": "plan check"})
   109	    assert response.status_code == 200
   110	    assert response.json() == {"reply_text": "echo: plan check", "session_key": "fake-sess-1"}
   111	    assert _stored_key(db_path, "days", day) == "fake-sess-1"
   112	    assert _events(db_path, day, "day_created") == [{}]
   113	    assert _events(db_path, day, "chat_session_created") == [{"session_key": "fake-sess-1"}]
   114	
   115	
   116	def test_chat_send_ticket_not_found(tmp_path: Path) -> None:
   117	    app, _ = _make_app(tmp_path)
   118	    with TestClient(app) as client:
   119	        response = client.post("/api/chat/t_missing/send", json={"text": "x"})
   120	    assert response.status_code == 404
   121	    assert response.json()["error"]["code"] == "not_found"
   122	
   123	
   124	def test_chat_send_unknown_prefix_not_found(tmp_path: Path) -> None:
   125	    app, _ = _make_app(tmp_path)
   126	    with TestClient(app) as client:
   127	        response = client.post("/api/chat/xyz/send", json={"text": "x"})
   128	    assert response.status_code == 404
   129	    assert response.json()["error"]["code"] == "not_found"
   130	
   131	
   132	def test_chat_send_offline_is_503_and_no_persist(tmp_path: Path) -> None:
   133	    app, db_path = _make_app(tmp_path, gateway="offline")
   134	    tid = _ticket(db_path)
   135	    with TestClient(app) as client:
   136	        response = client.post(f"/api/chat/{tid}/send", json={"text": "hello"})
   137	    assert response.status_code == 503
   138	    assert response.json()["error"]["code"] == "gateway_offline"
   139	    assert _stored_key(db_path, "tickets", tid) is None
   140	    assert _events(db_path, tid, "chat_session_created") == []
   141	
   142	
   143	def test_chat_status_offline_false(tmp_path: Path) -> None:
   144	    app, _ = _make_app(tmp_path, gateway="offline")
   145	    with TestClient(app) as client:
   146	        response = client.get("/api/chat/t_anything/status")
   147	    assert response.status_code == 200
   148	    assert response.json() == {"available": False}
   149	
   150	
   151	def test_chat_status_echo_true(tmp_path: Path) -> None:
   152	    app, _ = _make_app(tmp_path)
   153	    with TestClient(app) as client:
   154	        response = client.get("/api/chat/t_anything/status")
   155	    assert response.status_code == 200
   156	    assert response.json() == {"available": True}
   157	
   158	
   159	def test_seed_demo_counts(tmp_path: Path) -> None:
   160	    app, _ = _make_app(tmp_path)
   161	    with TestClient(app) as client:
   162	        response = client.post("/api/seed", json={"demo": True})
   163	    assert response.status_code == 200
   164	    assert response.json() == {
   165	        "sprints": 1,
   166	        "sprint_items": 3,
   167	        "deferred_items": 0,
   168	        "tickets": 8,
   169	        "ideas": 0,
   170	        "links": 2,
   171	        "duplicates_skipped": 0,
   172	        "skipped": [],
   173	    }
   174	
   175	
   176	def test_seed_demo_twice_is_db_not_empty(tmp_path: Path) -> None:
   177	    app, _ = _make_app(tmp_path)
   178	    with TestClient(app) as client:
   179	        first = client.post("/api/seed", json={"demo": True})
   180	        assert first.status_code == 200
   181	        second = client.post("/api/seed", json={"demo": True})
   182	    assert second.status_code == 400
   183	    assert second.json()["error"]["code"] == "db_not_empty"
   184	
   185	
   186	def test_seed_source_dir_imports_fixture(tmp_path: Path) -> None:
   187	    app, _ = _make_app(tmp_path)
   188	    with TestClient(app) as client:
   189	        response = client.post("/api/seed", json={"source_dir": str(FIXTURE)})
   190	    assert response.status_code == 200
   191	    body = response.json()
   192	    counts = {
   193	        key: body[key]
   194	        for key in (
   195	            "sprints",
   196	            "sprint_items",
   197	            "deferred_items",
   198	            "tickets",
   199	            "ideas",
   200	            "links",
   201	            "duplicates_skipped",
   202	        )
   203	    }
   204	    assert counts == {
   205	        "sprints": 1,
   206	        "sprint_items": 6,
   207	        "deferred_items": 3,
   208	        "tickets": 4,
   209	        "ideas": 3,
   210	        "links": 1,
   211	        "duplicates_skipped": 0,
   212	    }
   213	    assert len(body["skipped"]) == 6
   214	    assert set(body["skipped"][0]) == {"source_file", "heading", "reason", "excerpt"}
   215	
   216	
   217	def test_seed_missing_dir_is_validation(tmp_path: Path) -> None:
   218	    app, _ = _make_app(tmp_path)
   219	    with TestClient(app) as client:
   220	        response = client.post("/api/seed", json={"source_dir": str(tmp_path / "nope")})
   221	    assert response.status_code == 400
   222	    assert response.json()["error"]["code"] == "validation"
   223	
   224	
   225	def test_seed_file_not_dir_is_validation(tmp_path: Path) -> None:
   226	    app, _ = _make_app(tmp_path)
   227	    target = tmp_path / "a.md"
   228	    target.write_text("not a directory")
   229	    with TestClient(app) as client:
   230	        response = client.post("/api/seed", json={"source_dir": str(target)})
   231	    assert response.status_code == 400
   232	    assert response.json()["error"]["code"] == "validation"
   233	
   234	
   235	def test_seed_neither_key_is_validation(tmp_path: Path) -> None:
   236	    app, _ = _make_app(tmp_path)
   237	    with TestClient(app) as client:
   238	        response = client.post("/api/seed", json={})
   239	    assert response.status_code == 400
   240	    assert response.json()["error"]["code"] == "validation"
   241	
   242	
   243	def test_seed_both_keys_is_validation(tmp_path: Path) -> None:
   244	    app, _ = _make_app(tmp_path)
   245	    with TestClient(app) as client:
   246	        response = client.post("/api/seed", json={"source_dir": str(FIXTURE), "demo": True})
   247	    assert response.status_code == 400
   248	    assert response.json()["error"]["code"] == "validation"
   249	
   250	
   251	def test_chat_send_malformed_day_id_rejected_no_materialization(tmp_path: Path) -> None:
   252	    app, db_path = _make_app(tmp_path)
   253	    with TestClient(app) as client:
   254	        response = client.post("/api/chat/day_bogus/send", json={"text": "x"})
   255	    assert response.status_code == 404
   256	    assert response.json()["error"]["code"] == "not_found"
   257	    conn = connect(str(db_path))
   258	    try:
   259	        assert conn.execute("SELECT COUNT(*) FROM days").fetchone()[0] == 0
   260	        assert conn.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 0
   261	    finally:
   262	        conn.close()
   263	
   264	
   265	def test_chat_send_lost_race_adopts_winner_key_no_event(tmp_path: Path) -> None:
   266	    db_path = tmp_path / "planning-test.db"
   267	    boot = connect(str(db_path))
   268	    create_schema(boot)
   269	    boot.close()
   270	    tid = _ticket(db_path)
   271	
   272	    class RacingGateway:
   273	        """First send writes the winning key through its own connection before
   274	        returning a different (losing) key, mimicking a concurrent first send."""
   275	
   276	        def status(self) -> GatewayStatus:
   277	            return GatewayStatus(available=True)
   278	
   279	        def send(self, session_key: str | None, entity_id: str, text: str) -> ChatSendResult:
   280	            other = connect(str(db_path))
   281	            try:
   282	                other.execute("BEGIN IMMEDIATE")
   283	                other.execute(
   284	                    "UPDATE tickets SET chat_session_key = ? WHERE id = ?",
   285	                    ("winner-key", entity_id),
   286	                )
   287	                other.execute("COMMIT")
   288	            finally:
   289	                other.close()
   290	            return ChatSendResult(reply_text="echo: x", session_key="loser-key")
   291	
   292	    conn = connect(str(db_path))
   293	    try:
   294	        result = service.send(conn, RacingGateway(), tid, "x", 0)
   295	    finally:
   296	        conn.close()
   297	
   298	    assert result.session_key == "winner-key"
   299	    assert _stored_key(db_path, "tickets", tid) == "winner-key"
   300	    assert _events(db_path, tid, "chat_session_created") == []

codex
The seed importer only builds fixed relative paths from the submitted root, and the API has no default fallback path. I’m checking the route tests around that invariant so the final audit can distinguish contract evidence from assumptions.
exec
/bin/zsh -lc "nl -ba tests/unit/test_chat_seed.py | sed -n '300,360p'" in /Users/khushaljagota/.hermes/planning-v2
 succeeded in 0ms:
   300	    assert _events(db_path, tid, "chat_session_created") == []

exec
/bin/zsh -lc "nl -ba src/planner/cli/main.py | sed -n '70,105p'" in /Users/khushaljagota/.hermes/planning-v2
 succeeded in 0ms:
    70	
    71	    app = create_app(config, clock, adapters, conn_factory)
    72	    uvicorn.run(app, host=HOST, port=config.port)
    73	
    74	
    75	# --- seed ---
    76	
    77	
    78	@main.command("seed")
    79	@click.option("--source", default=None, help="Path to a markdown planning directory.")
    80	@click.option("--demo", is_flag=True, default=False, help="Create the deterministic demo dataset.")
    81	@json_option
    82	def seed(source: str | None, demo: bool, as_json: bool) -> None:
    83	    """Import a markdown planning directory, or create a demo dataset (§12)."""
    84	    raise NotImplementedError
    85	
    86	
    87	# --- top-level proposal/recap/note verbs ---
    88	
    89	
    90	@main.command("propose")
    91	@click.argument("field", type=click.Choice(_FIELDS))
    92	@click.argument("ticket_id", required=False, envvar=_TICKET_ID_ENV)
    93	@click.option("--body-file", default=None, help="Path to the body, or - for stdin.")
    94	@json_option
    95	def propose(field: str, ticket_id: str | None, body_file: str | None, as_json: bool) -> None:
    96	    """File or replace a proposal on a field (agent path)."""
    97	    raise NotImplementedError
    98	
    99	
   100	@main.command("recap")
   101	@click.argument("ticket_id", required=False, envvar=_TICKET_ID_ENV)
   102	@click.option("--body-file", default=None, help="Path to the body, or - for stdin.")
   103	@json_option
   104	def recap(ticket_id: str | None, body_file: str | None, as_json: bool) -> None:
   105	    """Write a ticket recap (rejected before needs_approach)."""

codex
NO VIOLATIONS

Suggestions:
1. Move `_demo_report` from [src/planner/seed/api.py](/Users/khushaljagota/.hermes/planning-v2/src/planner/seed/api.py:21) into the seed domain module to keep the API file thinner.
2. If physical path confinement matters, add explicit symlink/canonical path checks around `source_dir`; current code uses only fixed child paths under the provided root. 
3. Tighten `demo` parsing at [src/planner/seed/api.py](/Users/khushaljagota/.hermes/planning-v2/src/planner/seed/api.py:40) to require literal JSON `true`, not arbitrary truthy values.
tokens used
77,075
NO VIOLATIONS

Suggestions:
1. Move `_demo_report` from [src/planner/seed/api.py](/Users/khushaljagota/.hermes/planning-v2/src/planner/seed/api.py:21) into the seed domain module to keep the API file thinner.
2. If physical path confinement matters, add explicit symlink/canonical path checks around `source_dir`; current code uses only fixed child paths under the provided root. 
3. Tighten `demo` parsing at [src/planner/seed/api.py](/Users/khushaljagota/.hermes/planning-v2/src/planner/seed/api.py:40) to require literal JSON `true`, not arbitrary truthy values.
```
