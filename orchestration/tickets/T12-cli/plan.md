# T12 — CLI wiring: implementation plan

Fill every stubbed `NotImplementedError` handler in `src/planner/cli/main.py` with a
real HTTP call, add one tiny client module `src/planner/cli/http.py`, and write the
scripted acceptance smoke `orchestration/tickets/T12-cli/smoke.py`. The verb tree,
flags, env defaults, and exit-code contract are already fixed on disk (T01 §14 +
amendments); this ticket implements them exactly. No new verbs, no resolution verbs,
no changes to the tree or flags. `serve` stays byte-for-byte as it is.

Files the implementation may touch — and only these:
1. `src/planner/cli/http.py` (new)
2. `src/planner/cli/main.py` (fill handler bodies + add module-level helpers)
3. `orchestration/tickets/T12-cli/smoke.py` (new)

---

## 0. Layering / import discipline (D4, binding)

- `http.py` imports **stdlib + `httpx` only**. No `planner.*` import at all. Client-side
  error envelopes are plain `dict` literals (`{"error": {"code": ..., "message": ...,
  "detail": {}}}`) — never `ErrorCode`, never `PlannerError`, never `core.config`.
- `main.py` imports **stdlib + `click` + the local `planner.cli.http` module**. It keeps
  its existing lazy imports inside `serve` (uvicorn/config/db/server), which are the one
  sanctioned exception and must not move. No domain/data/server import is added anywhere
  else in `main.py`.
- Rationale for no `core.contracts.JsonDict` import: annotating dicts with stdlib
  `dict[str, Any]` keeps `http.py` fully `planner`-free and removes any ambiguity about
  whether a contracts import counts as a domain import. This is deliberate.

---

## 1. `src/planner/cli/http.py` — the client seam

One module owning **all** URL construction, header assembly, request execution, and —
critically — **every stdout/stderr/exit-code decision**. No handler in `main.py`
duplicates any of this: a handler only builds a route + body, calls `send`, and calls
`emit` (or a client-side `fail_validation`).

### Module constants

```python
from __future__ import annotations
import json
import os
import sys
from pathlib import Path
from typing import Any, NoReturn
import httpx

_DEFAULT_BASE_URL = "http://127.0.0.1:8767"   # §8 / config port default
_TIMEOUT = 30.0                                # seconds; sane, generous

EXIT_OK = 0
EXIT_ERROR = 1        # structured {"error":...} or any non-2xx
EXIT_CONNECTION = 2   # httpx.TransportError family
```

### URL + headers

```python
def _base_url() -> str:
    raw = os.environ.get("PLAN_SERVER_URL", "").strip()
    return raw or _DEFAULT_BASE_URL

def _url(path: str) -> str:
    # path always begins with "/api/..."
    return _base_url().rstrip("/") + path

def _headers() -> dict[str, str]:
    # X-Plan-Actor is ALWAYS sent (default "agent"); run/claim only when set & non-empty.
    headers = {"X-Plan-Actor": os.environ.get("PLAN_ACTOR", "").strip() or "agent"}
    run_id = os.environ.get("PLAN_RUN_ID", "").strip()
    if run_id:
        headers["X-Plan-Run-Id"] = run_id
    claim = os.environ.get("PLAN_CLAIM", "").strip()
    if claim:
        headers["X-Plan-Claim"] = claim
    return headers
```

Header names verified against `core/authctx.py`: `X-Plan-Run-Id`, `X-Plan-Claim`,
`X-Plan-Actor`; empty/whitespace headers are treated as absent server-side (`_normalize`),
so stripping-then-omitting on the client is consistent.

### The request seam — `send`

```python
def send(method: str, path: str, *, as_json: bool,
         json_body: Any | None = None,
         params: dict[str, Any] | None = None) -> Any:
    """Execute one request and apply the failure half of the exit contract.
    Transport failure -> stderr + exit 2. Non-2xx OR an {"error":...} body ->
    stderr (JSON verbatim under --json, else terse line from code+message) + exit 1.
    On 2xx: return the parsed JSON body (dict/list/None) to the caller — no output,
    no exit; the caller decides output via emit()."""
    try:
        resp = httpx.request(method, _url(path), json=json_body, params=params,
                             headers=_headers(), timeout=_TIMEOUT)
    except httpx.TransportError as exc:
        _fail_connection(exc, as_json)          # NoReturn -> exit 2
    try:
        data: Any = resp.json()
    except ValueError:
        data = None
    if resp.is_success:                          # 2xx
        return data
    _fail_response(resp, data, as_json)          # NoReturn -> exit 1
```

`httpx.TransportError` is the base of `ConnectError`, `ConnectTimeout`, `ReadTimeout`,
`PoolTimeout`, etc. — the whole "server unreachable / transport broke" family → exit 2.
Domain/HTTP errors never route through `click.ClickException`; they exit via `sys.exit`
with the contract code.

### The output helpers (the only place printing/exit lives)

```python
def emit(data: Any, as_json: bool, human: str) -> NoReturn:
    """Success path. --json: raw JSON to stdout. Else: the terse human line. exit 0."""
    if as_json:
        print(json.dumps(data))
    else:
        print(human)
    sys.exit(EXIT_OK)

def fail_validation(message: str, as_json: bool,
                    detail: dict[str, Any] | None = None) -> NoReturn:
    """Client-side validation error (empty body, missing ticket/run id, unreadable
    file, bad flag combo). Rendered in the SAME style as a server error. exit 1."""
    payload = {"error": {"code": "validation", "message": message, "detail": detail or {}}}
    if as_json:
        print(json.dumps(payload), file=sys.stderr)
    else:
        print(f"error: validation: {message}", file=sys.stderr)
    sys.exit(EXIT_ERROR)

def _fail_connection(exc: httpx.TransportError, as_json: bool) -> NoReturn:
    payload = {"error": {"code": "connection", "message": str(exc), "detail": {}}}
    if as_json:
        print(json.dumps(payload), file=sys.stderr)
    else:
        print(f"error: connection: {exc}", file=sys.stderr)
    sys.exit(EXIT_CONNECTION)

def _fail_response(resp: httpx.Response, data: Any, as_json: bool) -> NoReturn:
    if isinstance(data, dict) and "error" in data:
        payload = data                                   # the server envelope, verbatim
    else:
        # Non-envelope non-2xx (FastAPI 422 pydantic, 404 routing, 500 HTML). Never
        # swallow: wrap so exit is 1 and stderr still carries a parseable {"error":...}.
        payload = {"error": {"code": "http_error",
                             "message": f"HTTP {resp.status_code}",
                             "detail": data if data is not None else resp.text}}
    if as_json:
        print(json.dumps(payload), file=sys.stderr)
    else:
        err = payload["error"]
        print(f"error: {err['code']}: {err['message']}", file=sys.stderr)
    sys.exit(EXIT_ERROR)
```

`NoReturn` on all four terminal helpers lets mypy see that any handler path reaching
them is dead, so locals stay bound. Under `--json`, success JSON is the parsed body
re-serialized single-line via `json.dumps` (functionally verbatim, guaranteed valid).

---

## 2. `src/planner/cli/main.py` — module-level helpers

Add these three helpers (below the existing constants, above `serve`). They own body
input and ticket-id resolution per amendment 6 (explicit `-` or `--body-file` only; no
implicit stdin). `_TICKET_ID_ENV` already exists (`"PLAN_TICKET_ID"`).

```python
from pathlib import Path
import sys
from planner.cli import http

def _read_source(spec: str, as_json: bool) -> str:
    """`-` -> stdin; else read the file. Unreadable file -> validation exit 1."""
    if spec == "-":
        return sys.stdin.read()
    try:
        return Path(spec).read_text(encoding="utf-8")
    except OSError:
        http.fail_validation(f"cannot read body file: {spec}", as_json)

def read_body(positional_ticket_id: str | None, body_file: str | None,
              as_json: bool) -> str:
    """Required-body verbs (propose / recap / note). Body source is EXPLICIT only:
      --body-file PATH | --body-file -   -> that source
      trailing positional is literally '-' -> stdin (the '-' is the stdin marker,
          NOT a ticket id)
      otherwise                          -> no body source -> validation exit 1
    Empty (whitespace-only) body -> validation exit 1. Returns the ORIGINAL text
    (unstripped) so markdown/newlines are preserved."""
    if body_file is not None:
        text = _read_source(body_file, as_json)
    elif positional_ticket_id == "-":
        text = sys.stdin.read()
    else:
        http.fail_validation(
            "body required: pass '-' for stdin or --body-file PATH", as_json)
    if not text.strip():
        http.fail_validation("empty body", as_json)
    return text

def read_optional_body(body_file: str | None, as_json: bool) -> str | None:
    """Optional-body verbs (item propose-status, idea create). Body comes ONLY from
    --body-file (which may be '-'); absent flag -> None. No positional stdin marker
    exists on these verbs (their positional is a required id, not a '-' slot)."""
    if body_file is None:
        return None
    return _read_source(body_file, as_json)

def resolve_ticket_id(positional: str | None, as_json: bool) -> str:
    """Effective ticket id. A real positional wins. A None or a '-' positional falls
    back to PLAN_TICKET_ID read from os.environ HERE — click's envvar fallback does not
    fire when the positional is provided (e.g. the '-' stdin marker), so we re-read.
    Missing -> validation exit 1."""
    if positional is not None and positional != "-":
        return positional
    env = os.environ.get(_TICKET_ID_ENV, "").strip()
    if env:
        return env
    http.fail_validation(
        "ticket id required: pass it or set PLAN_TICKET_ID", as_json)
```

`import os` is already implied by usage — add `import os` at module top (currently only
imported lazily inside `serve`; the helpers need it at module scope). `sqlite3`,
`Callable`, `Any`, `click` stay. Add `from planner.cli import http` and `from pathlib
import Path` and `import sys` at top.

Note the two-helper split of the `-` double duty: on a required-body verb, a bare `-`
positional means BOTH "read body from stdin" (`read_body`) AND "ticket id from env"
(`resolve_ticket_id`). Stdin is read exactly once (only `read_body` touches it;
`resolve_ticket_id` never reads stdin). To pass an explicit ticket id AND stdin body,
use `plan propose success t_x --body-file -`. This is exactly the amendment-6 contract
and the ground-truth line `PLAN_TICKET_ID=<id> plan propose success -`.

---

## 3. `main.py` — per-verb handler bodies

Every handler ends in exactly one of: `http.emit(...)`, or a `http.fail_validation(...)`
before any request. `send` returns parsed data on 2xx (else it has already exited). Route
mappings below were each verified against the live `@router` decorators; all mount under
`/api` (`core/server.py` `include_router(..., prefix="/api")`).

Convention for building bodies: include a key only when its flag is provided (non-None),
except required keys (title). `"none"` sentinels convert to JSON `null` client-side.

### seed
```python
def seed(source, demo, as_json):
    if (source is None) == (not demo):          # neither, or both -> exactly-one rule
        http.fail_validation("provide exactly one of --source or --demo", as_json)
    if demo:
        body = {"demo": True}
    else:
        body = {"source_dir": os.path.abspath(source)}   # absolutize: server resolves in ITS cwd
    data = http.send("POST", "/api/seed", as_json=as_json, json_body=body)
    http.emit(data, as_json, _seed_human(data))
```
Response is `asdict(MigrationReport)`: keys `sprints, sprint_items, deferred_items,
tickets, ideas, links, duplicates_skipped, skipped` (a list of
`{source_file, heading, reason, excerpt}`). `_seed_human` renders a small multi-line
table (this is the one verb whose human output is intentionally multi-line):
```
seed: 1 sprint(s), 12 item(s), 9 deferred, 4 ticket(s), 20 idea(s), 0 link(s), 0 duplicate(s)
skipped: <n>
  - <source_file> [<heading>]: <reason>
  ...            (omit the "skipped" block entirely when the list is empty)
```

### propose / recap / note (required body)
```python
def propose(field, ticket_id, body_file, as_json):
    body = read_body(ticket_id, body_file, as_json)
    tid = resolve_ticket_id(ticket_id, as_json)
    data = http.send("POST", f"/api/tickets/{tid}/propose/{field}",
                     as_json=as_json, json_body={"body": body})
    http.emit(data, as_json, f"proposed {field} on {data['id']}")

def recap(ticket_id, body_file, as_json):
    body = read_body(ticket_id, body_file, as_json)
    tid = resolve_ticket_id(ticket_id, as_json)
    data = http.send("PUT", f"/api/tickets/{tid}/recap",
                     as_json=as_json, json_body={"body": body})
    http.emit(data, as_json, f"recap written on {data['id']}")

def note(field, ticket_id, body_file, as_json):
    body = read_body(ticket_id, body_file, as_json)
    tid = resolve_ticket_id(ticket_id, as_json)
    data = http.send("PUT", f"/api/tickets/{tid}/notes/{field}",
                     as_json=as_json, json_body={"note": body})
    http.emit(data, as_json, f"note {field} written on {data['id']}")
```
Request models verified: `ProposeBody{body}`, `RecapBody{body}`, `NoteBody{note}`.
No claim headers are set for a plain agent, so `require_claim` is not triggered
server-side (the propose/recap/notes routes only call it when `ctx.is_claimed_agent`).

### ticket create / show / list / set
```python
def ticket_create(title, priority, deadline, project, sprint, item, as_json):
    body = {"title": title}
    if priority is not None: body["priority"] = priority
    if deadline is not None: body["deadline"] = deadline
    if project  is not None: body["project"]  = project
    if sprint   is not None: body["sprint_id"] = sprint       # --sprint -> sprint_id
    if item     is not None: body["sprint_item_id"] = item    # --item   -> sprint_item_id
    data = http.send("POST", "/api/tickets", as_json=as_json, json_body=body)
    http.emit(data, as_json, f"{data['id']} {data['state']}")

def ticket_show(ticket_id, as_json):
    tid = resolve_ticket_id(ticket_id, as_json)
    data = http.send("GET", f"/api/tickets/{tid}", as_json=as_json)
    http.emit(data, as_json, f"{data['id']} {data['state']} {data['priority']} {data['title']}")

def ticket_list(state, project, sprint, as_json):
    params = _drop_none({"state": state, "project": project, "sprint_id": sprint})
    data = http.send("GET", "/api/tickets", as_json=as_json, params=params)
    http.emit(data["tickets"], as_json,
              _lines(data["tickets"], lambda t: f"{t['id']} {t['state']} {t['priority']} {t['title']}"))
```
`ticket create` route `POST /api/tickets` verified (`CreateTicketBody`). `ticket show`
→ `GET /api/tickets/{id}` (returns `ticket_detail`, which includes the `fields` slot map
— the pending-proposal key the smoke asserts). `ticket list` → `GET /api/tickets` with
`state/project/sprint_id` query params; response `{"tickets":[...]}`; `--json` emits the
array.

**`ticket set`** — the one multi-call verb. `--priority/--deadline/--sprint` → one PATCH;
`--day` → a day POST. Ordering and output are a delegated choice (see concerns):
```python
def ticket_set(ticket_id, priority, deadline, day, sprint, as_json):
    tid = resolve_ticket_id(ticket_id, as_json)
    patch = {}
    if priority is not None: patch["priority"] = priority
    if deadline is not None: patch["deadline"] = None if deadline == "none" else deadline
    if sprint   is not None: patch["sprint_id"] = None if sprint == "none" else sprint
    if not patch and day is None:
        http.fail_validation("nothing to set: pass --priority/--deadline/--sprint/--day", as_json)
    data = None
    if patch:
        data = http.send("PATCH", f"/api/tickets/{tid}", as_json=as_json, json_body=patch)
    if day is not None:
        data = http.send("POST", f"/api/day/{day}/tickets",   # day == 'today' passes through
                         as_json=as_json, json_body={"ticket_id": tid})
    # `data` is the LAST successful response: ticket_json if only PATCH, day view if --day.
    human = (f"day {data['id']}: {len(data['tickets'])} ticket(s)" if "tickets" in data
             else f"{data['id']} {data['state']}")
    http.emit(data, as_json, human)
```
`PATCH /api/tickets/{id}` recognizes `priority/deadline/sprint_id` (and title/project,
unused here); literal `"none"` → JSON `null` clears deadline/sprint server-side. If the
PATCH fails, `send` exits before the day POST is attempted (correct — do not half-apply).

### item create / show / list / set / propose-status
```python
def item_create(title, project, priority, deadline, sprint, as_json):
    body = {"title": title, "project": project}   # project is --required by click
    if priority is not None: body["priority"] = priority
    if deadline is not None: body["deadline"] = deadline
    if sprint   is not None: body["sprint_id"] = sprint
    data = http.send("POST", "/api/items", as_json=as_json, json_body=body)
    http.emit(data, as_json, f"{data['id']} {data['status']}")

def item_show(item_id, as_json):
    data = http.send("GET", f"/api/items/{item_id}", as_json=as_json)
    http.emit(data, as_json, f"{data['id']} {data['status']} {data['priority']} {data['title']}")

def item_list(status, project, backlog, as_json):
    params = _drop_none({"status": status, "project": project})
    if backlog: params["sprint_id"] = "null"        # views.list_items maps "null" -> IS NULL
    data = http.send("GET", "/api/items", as_json=as_json, params=params)
    http.emit(data["items"], as_json,
              _lines(data["items"], lambda i: f"{i['id']} {i['status']} {i['priority']} {i['title']}"))

def item_set(item_id, status, blocked_by, as_json):
    body = {}
    if status is not None: body["status"] = status
    if blocked_by is not None:
        body["blocked_by"] = [s for s in (x.strip() for x in blocked_by.split(",")) if s]
    if not body:
        http.fail_validation("nothing to set: pass --status/--blocked-by", as_json)
    data = http.send("PATCH", f"/api/items/{item_id}", as_json=as_json, json_body=body)
    http.emit(data, as_json, f"{data['id']} {data['status']}")

def item_propose_status(item_id, to_status, body_file, as_json):
    note = read_optional_body(body_file, as_json)
    data = http.send("POST", f"/api/items/{item_id}/propose-status",
                     as_json=as_json, json_body={"to": to_status, "note": note})
    http.emit(data, as_json, f"{data['id']} status proposal {to_status}")
```
`POST /api/items` (`CreateItemBody`, project required server-side too). `GET /api/items/{id}`.
`GET /api/items` params `status/project/sprint_id`. `PATCH /api/items/{id}` (comma
`--blocked-by` → JSON list under `"blocked_by"`; server rejects `blocked_by` without
`status` — pass-through). `POST /api/items/{id}/propose-status` (`ProposeStatusBody{to,note}`).

### sprint show / idea create / idea list
```python
def sprint_show(as_json):
    data = http.send("GET", "/api/sprint/current", as_json=as_json)
    s = data["sprint"]
    human = "no current sprint" if s is None else f"sprint {s['name']} {s['date_start']}..{s['date_end']}"
    http.emit(data, as_json, human)

def idea_create(title, project, body_file, as_json):
    body = {"title": title, "body": read_optional_body(body_file, as_json) or ""}
    if project is not None: body["project"] = project
    data = http.send("POST", "/api/ideas", as_json=as_json, json_body=body)
    http.emit(data, as_json, f"{data['id']} {data['title']}")

def idea_list(as_json):
    data = http.send("GET", "/api/ideas", as_json=as_json)
    http.emit(data["ideas"], as_json, _lines(data["ideas"], lambda i: f"{i['id']} {i['title']}"))
```
`GET /api/sprint/current` (`{"sprint": None|{...}, "groups", "loose_tickets"}`).
`POST /api/ideas` (`CreateIdeaBody{title,body,project}`). `GET /api/ideas`
(`{"ideas":[...]}`).

### day show / add-ticket / remove-ticket
```python
def day_show(date, as_json):
    seg = date or "today"                       # server resolves the planning date; no date math here
    data = http.send("GET", f"/api/day/{seg}", as_json=as_json)
    http.emit(data, as_json, f"day {data['id']}: {len(data['tickets'])} ticket(s)")

def day_add_ticket(ticket_id, date, as_json):
    seg = date or "today"
    data = http.send("POST", f"/api/day/{seg}/tickets",
                     as_json=as_json, json_body={"ticket_id": ticket_id})
    http.emit(data, as_json, f"day {data['id']}: {len(data['tickets'])} ticket(s)")

def day_remove_ticket(ticket_id, date, as_json):
    seg = date or "today"
    data = http.send("DELETE", f"/api/day/{seg}/tickets/{ticket_id}", as_json=as_json)
    http.emit(data, as_json, f"day {data['id']}: {len(data['tickets'])} ticket(s)")
```
`{date}` accepts literal `today` (amendment 10; `resolve_day_id`). Routes:
`GET /api/day/{date}`, `POST /api/day/{date}/tickets` (`AddDayTicketBody{ticket_id}`),
`DELETE /api/day/{date}/tickets/{ticket_id}`. All three return the day view
(`{id, brief, notes, plan, ..., tickets:[ticket_json,...]}`).

### link add / rm
```python
def link_add(from_id, to_id, kind, as_json):
    data = http.send("POST", "/api/links", as_json=as_json,
                     json_body={"from_id": from_id, "to_id": to_id, "kind": kind})
    http.emit(data, as_json, f"linked {from_id} -{kind}-> {to_id}")

def link_rm(from_id, to_id, kind, as_json):
    data = http.send("DELETE", "/api/links", as_json=as_json,
                     params={"from_id": from_id, "to_id": to_id, "kind": kind})
    http.emit(data, as_json, f"unlinked {from_id} -{kind}-> {to_id}")
```
`POST /api/links` (`LinkBody{from_id,to_id,kind}`, echoes `{from_id,to_id,kind}`).
`DELETE /api/links` with query params `from_id,to_id,kind` (returns `{"ok": true}`).

### run heartbeat / close (run id from PLAN_RUN_ID)
```python
def _require_run_id(as_json):
    rid = os.environ.get("PLAN_RUN_ID", "").strip()
    if not rid:
        http.fail_validation("run id required: set PLAN_RUN_ID", as_json)
    return rid

def run_heartbeat(as_json):
    rid = _require_run_id(as_json)
    data = http.send("POST", f"/api/runs/{rid}/heartbeat", as_json=as_json)
    http.emit(data, as_json, f"heartbeat: claim_expires {data['claim_expires']}")

def run_close(outcome, summary, as_json):
    rid = _require_run_id(as_json)
    summary_text = _read_source(summary, as_json) if summary is not None else None
    data = http.send("POST", f"/api/runs/{rid}/close",
                     as_json=as_json, json_body={"outcome": outcome, "summary": summary_text})
    http.emit(data, as_json, f"run closed {data['run']['status']}")
```
Run id comes from `PLAN_RUN_ID` for BOTH the URL path segment and the `X-Plan-Run-Id`
header (the header is added by `http._headers`; the path segment is read here). Both
routes also require `PLAN_CLAIM` (header) — `require_claim` runs server-side. `--summary`
reads via `_read_source` (`-` = stdin, else file). Responses: heartbeat `{run,
claim_expires}`, close `{run}`. `AGENT_CLOSE_OUTCOMES` restricts outcome to done/blocked
(click already enforces the choice).

### queue approvals / pickup / overdue (GET /api/queues, print the selected section)
```python
def queue_approvals(as_json):
    data = http.send("GET", "/api/queues", as_json=as_json)
    section = data["approvals"]
    http.emit(section, as_json,
              _lines(section, lambda e: f"{e['entity_type']} {e['entity_id']} {e['kind']} {e['title']}"))

def queue_pickup(as_json):
    data = http.send("GET", "/api/queues", as_json=as_json)
    section = data["pickup"]
    http.emit(section, as_json,
              _lines(section, lambda e: f"{e['ticket_id']} {e['state']} {e['priority']} {e['title']}"))

def queue_overdue(as_json):
    data = http.send("GET", "/api/queues", as_json=as_json)
    section = data["overdue"]
    http.emit(section, as_json,
              _lines(section, lambda e: f"{e['entity_type']} {e['id']} {e['state']} {e['priority']} {e['title']}"))
```
One route (`GET /api/queues` → `{approvals, pickup, overdue}`); each verb selects its
section and emits only that (array under `--json`). Section shapes verified in
`tickets/views.queues_view`: approvals entries `{entity_id, entity_type, kind, title,
waiting_since}`; pickup `{ticket_id, title, state, priority, deadline}`; overdue
`{id, entity_type, title, state, priority, deadline}`.

### Two tiny `main.py` render helpers
```python
def _drop_none(d): return {k: v for k, v in d.items() if v is not None}
def _lines(rows, fmt): return "\n".join(fmt(r) for r in rows) if rows else "(none)"
```

---

## 4. Terse human-line format per verb (one line unless noted)

| verb | human line |
|---|---|
| seed | multi-line table (counts) + optional `skipped:` block |
| propose FIELD | `proposed <field> on <id>` |
| recap | `recap written on <id>` |
| note FIELD | `note <field> written on <id>` |
| ticket create | `<id> <state>` |
| ticket show | `<id> <state> <priority> <title>` |
| ticket list | one line/ticket: `<id> <state> <priority> <title>` (`(none)` if empty) |
| ticket set | `<id> <state>` (PATCH result) or `day <day_id>: <n> ticket(s)` (with --day) |
| item create | `<id> <status>` |
| item show | `<id> <status> <priority> <title>` |
| item list | one line/item: `<id> <status> <priority> <title>` |
| item set | `<id> <status>` |
| item propose-status | `<id> status proposal <to>` |
| sprint show | `sprint <name> <start>..<end>` or `no current sprint` |
| idea create | `<id> <title>` |
| idea list | one line/idea: `<id> <title>` |
| day show/add/remove | `day <day_id>: <n> ticket(s)` |
| link add | `linked <from> -<kind>-> <to>` |
| link rm | `unlinked <from> -<kind>-> <to>` |
| run heartbeat | `heartbeat: claim_expires <ts>` |
| run close | `run closed <status>` |
| queue approvals | one line/entry: `<entity_type> <entity_id> <kind> <title>` |
| queue pickup | one line/entry: `<ticket_id> <state> <priority> <title>` |
| queue overdue | one line/entry: `<entity_type> <id> <state> <priority> <title>` |

(The human never uses the CLI; minimal id-bearing lines are correct. All error output
goes to stderr via the `http` helpers; only success lines go to stdout.)

---

## 5. `orchestration/tickets/T12-cli/smoke.py` — scripted acceptance

A standalone Python script (run by `.venv/bin/python`), not a pytest test — it lives
under `orchestration/`, outside `tests/`, so the verify skip-scan never sees it and it
never counts toward `N/36`. It starts a real server and drives the real `plan` binary.

### Setup / teardown
```
REPO = Path(__file__).resolve().parents[3]      # .../planning-v2
PLAN = REPO / ".venv/bin/plan"
PY   = REPO / ".venv/bin/python"
- tmpdir = tempfile.mkdtemp(); db = tmpdir/"planning.db"
- port = free port (socket bind 127.0.0.1:0 -> getsockname()[1] -> close)
- dead_port = a SECOND free port grabbed and closed (used for the exit-2 case)
- server_env = os.environ + {PLAN_TEST_MODE:1, PLAN_DB_PATH:str(db), PLAN_PORT:str(port)}
- proc = subprocess.Popen([str(PLAN), "serve"], cwd=str(REPO), env=server_env,
                          stdout=PIPE, stderr=STDOUT, text=True)
- poll GET http://127.0.0.1:{port}/api/meta (httpx or urllib) until 200, ~15s budget,
  0.2s between tries; if it never comes up -> FAIL, dump captured server output.
- try: <steps> finally: proc.terminate(); proc.wait(5) (kill on timeout); rmtree(tmpdir)
```

### Driving the CLI
```
base_env = os.environ + {PLAN_SERVER_URL: f"http://127.0.0.1:{port}"}
def run_plan(args, *, env_extra=None, stdin=None):
    env = {**base_env, **(env_extra or {})}
    return subprocess.run([str(PLAN), *args], cwd=str(REPO), env=env,
                          input=stdin, capture_output=True, text=True)
```
A `check(name, cond)` prints `PASS <name>` / `FAIL <name>` and records failures.

### Steps (each an acceptance line from ticket.md)
1. **ticket create --json** → `r = run_plan(["ticket","create","--title","Smoke A","--json"])`;
   `tid = json.loads(r.stdout)["id"]`; check `r.returncode == 0` and `tid`.
2. **env-pinned propose with multi-line stdin (also the no-`--json` terse-line check)** →
   `body = "line one\n\nline two\n"`;
   `r = run_plan(["propose","success","-"], env_extra={"PLAN_TICKET_ID": tid}, stdin=body)`;
   check `r.returncode == 0` and `tid in r.stdout` (terse line `proposed success on <tid>`).
3. **ticket show --json — pending proposal at its exact key** →
   `r = run_plan(["ticket","show","--json"], env_extra={"PLAN_TICKET_ID": tid})`;
   `d = json.loads(r.stdout)`;
   check `d["fields"]["success"]["proposal"]["body"] == body`.
4. **queue approvals --json lists it** →
   `r = run_plan(["queue","approvals","--json"])`; `rows = json.loads(r.stdout)`;
   check `tid in [e["entity_id"] for e in rows]`.
5. **item create + set** →
   `iid = json.loads(run_plan(["item","create","--title","Item A","--project","Vylo","--json"]).stdout)["id"]`;
   `d = json.loads(run_plan(["item","set",iid,"--status","active","--json"]).stdout)`;
   check `d["status"] == "active"`.
6. **day add-ticket / show / remove-ticket** (uses literal `today`) →
   add: `d = json.loads(run_plan(["day","add-ticket",tid,"today","--json"]).stdout)`;
        check `tid in [t["id"] for t in d["tickets"]]`.
   show: `d = json.loads(run_plan(["day","show","today","--json"]).stdout)`;
        check `tid in [t["id"] for t in d["tickets"]]`.
   remove: `d = json.loads(run_plan(["day","remove-ticket",tid,"today","--json"]).stdout)`;
        check `tid not in [t["id"] for t in d["tickets"]]`.
7. **link add / rm** → create a 2nd ticket `tid2`;
   add: `d = json.loads(run_plan(["link","add",tid2,tid,"--kind","blocks","--json"]).stdout)`;
        check `d["kind"] == "blocks"`.
   rm:  `d = json.loads(run_plan(["link","rm",tid2,tid,"--kind","blocks","--json"]).stdout)`;
        check `d.get("ok") is True`.
8. **validation error → exit 1 + parseable {"error":...} on stderr (client-side)** →
   `r = run_plan(["propose","success","-","--json"], env_extra={"PLAN_TICKET_ID": tid}, stdin="")`;
   check `r.returncode == 1` and `json.loads(r.stderr)["error"]["code"] == "validation"`.
9. **server structured error → exit 1 (HTTP path through http.send)** →
   `r = run_plan(["ticket","show","t_does_not_exist","--json"])`;
   check `r.returncode == 1` and `json.loads(r.stderr)["error"]["code"] == "not_found"`.
10. **dead-port → exit 2 (transport)** →
   `r = run_plan(["ticket","show",tid,"--json"], env_extra={"PLAN_SERVER_URL": f"http://127.0.0.1:{dead_port}"})`;
   check `r.returncode == 2`.

End: if any failure recorded → `sys.exit(1)`; else print `SMOKE OK` and `sys.exit(0)`.
Server is always terminated + tmpdir removed in `finally`, even on assertion failure or
exception. Steps 8 and 9 are distinct code paths on purpose — step 8 exercises the
client-built envelope (`fail_validation`), step 9 exercises the server-envelope
rendering inside `http._fail_response`.

---

## 6. Verification commands (run in order; all must be clean)

```
.venv/bin/ruff check src/planner/cli/
.venv/bin/mypy src/
.venv/bin/pytest tests/unit -q
.venv/bin/python orchestration/tickets/T12-cli/smoke.py
```
The unit suite must stay green (T12 adds no unit tests and changes no logic modules).
`smoke.py` prints per-step PASS/FAIL and exits nonzero on any failure. `smoke.py` is
excluded from `tests/`, so `./verify`'s skip-scan and 36-item scoreboard are unaffected.

---

## 7. Concerns / resolved ambiguities

1. **`ticket set` multi-call ordering & output (delegated).** With both PATCH-flags and
   `--day`, the plan does PATCH first, then the day POST, and prints/`--json`-emits the
   LAST successful response (day view when `--day` given, else ticket JSON). No-flags →
   client validation exit 1. This satisfies "plan the ordering and printed output when
   both are given, and the no-flags case." An agent using `--json` can re-fetch the
   ticket if it needs both; printing the last response keeps the seam one-emit-per-verb.
2. **Success `--json` is re-serialized, not raw bytes.** `emit` prints `json.dumps(data)`
   (single-line, valid) rather than `resp.text`. Functionally verbatim; chosen so output
   is always well-formed and mypy-clean. Error envelopes under `--json` are likewise
   `json.dumps(payload)` to stderr. If strict byte-verbatim is later required, only
   `emit`/`_fail_response` change.
3. **Non-envelope non-2xx responses.** FastAPI can return non-`{"error":...}` bodies
   (422 pydantic `{"detail":[...]}`, 404 routing, 500). `_fail_response` wraps these in a
   synthetic `{"error":{"code":"http_error","message":"HTTP <code>",...}}` so the
   contract ("exit 1, never swallow, parseable error on stderr") holds for every non-2xx.
   The common domain path (server `PlannerError` → `{"error":...}` envelope) is emitted
   verbatim.
4. **Empty-body test uses `.strip()`.** `read_body` rejects whitespace-only bodies, not
   only the empty string, so the smoke's `stdin=""` and any `echo ""` both deterministically
   validate as empty. The body actually SENT on the success path is the original,
   unstripped text (markdown/newlines preserved), matching the multi-line ground-truth line.
5. **`http.py` is `planner`-free (D4 tightened).** It imports stdlib + `httpx` only and
   builds error dicts as literals — no `core.contracts`/`ErrorCode` import at all. D4 would
   permit importing contracts, but avoiding it removes any "is a contracts import a domain
   import" ambiguity. `main.py` adds only `from planner.cli import http` (plus stdlib
   `os`/`sys`/`pathlib`); its `serve` lazy imports are untouched.
6. **No contract mismatches found on disk.** Every route, request-model field, header
   name, env var, exit code, the `fields.<field>.proposal.body` proposal key, the
   approvals `entity_id` key, `list_items` "null"→IS NULL, and the `today` literal on day
   routes were each verified against the live source. `serve` and the verb tree/flags are
   left exactly as stubbed; only handler bodies and the three helpers are added.
7. **`__main__.py` exists** (`python -m planner` == `plan`); it is not in scope and is not
   modified. The smoke drives `.venv/bin/plan` directly.

---

## 8. Binding amendments after codex plan review (override the body above)

Per orchestration/tickets/T12-cli/plan-review.md:

**A1 — `send()` also treats a 2xx `{"error": ...}` body as an error.** After the
`resp.is_success` check, if the parsed body is a `dict` containing an `"error"` key,
route it to `_fail_response` (stderr + exit 1) instead of returning it. No server path
produces this today, but the ticket's contract is unconditional and no success view
emits a top-level `"error"` key, so the check is free and airtight.

**A2 — list verbs emit the raw response object under `--json`.** `ticket list`,
`item list`, `idea list`: `--json` prints the full response (`{"tickets": [...]}`,
`{"items": [...]}`, `{"ideas": [...]}`), NOT the sliced array. The terse human lines are
unchanged (still rendered from the array inside the response). The section-3 sketches
`http.emit(data["tickets"], ...)` etc. are corrected to
`http.emit(data, as_json, _lines(data["tickets"], ...))`.

**A3 — queue verbs emit the single-section object.** `queue approvals|pickup|overdue`
under `--json` print `{"approvals": [...]}` / `{"pickup": [...]}` / `{"overdue": [...]}`
— the raw `/api/queues` response filtered to the verb's section with its key preserved
(three verbs must stay distinguishable; emitting the whole three-section response from
each would collapse them). Sketch correction:
`section = data["approvals"]; http.emit({"approvals": section}, as_json, _lines(section, ...))`.
Smoke step 4 becomes: `rows = json.loads(r.stdout)["approvals"]`.

**A4 — `run close --summary` stays optional (codex finding 4 refuted).** The fixed click
stub declares `--summary` with `default=None`, and required-ness is part of the fixed
flag semantics; the server model (`CloseRunBody.summary: str | None`) agrees. Absent →
`"summary": null`; given → read via `_read_source` (`-` = stdin, else file path).

