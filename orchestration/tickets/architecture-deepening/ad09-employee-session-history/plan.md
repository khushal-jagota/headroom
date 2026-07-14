# AD09 implementation plan — Explicit Employee session history

## 1. Fixed outcome, predecessor, and module split

Land AD09 only after the serial AD08 commit is integrated and its one full `./verify` is green. AD09 plans
against AD08's `web/src/lib/resourceCatalogue.ts`, its thirteen-resource public interface, and the generated
JavaScript asset hash that AD08 actually lands. Do not restore `eventMapping.mjs`, a free-standing event
mapper, or any pre-AD08 cache interface. At AD09 dispatch, record the landed AD08 commit and the JavaScript
chunk referenced by `web/dist/index.html`; those are the exact source and generated-asset predecessors.

The finished design has three distinct modules and no compatibility seam between them:

1. **Ticket Employee identity** owns the Ticket's nullable `employee_session_id`, its one canonical
   compare-and-write interface, and `employee_session_changed` events.
2. **Panels Chat** owns only durable `chat_messages`, the one live `chat_turn`, and display-safe activity.
   Its state read never asks Hermes to manufacture a visible transcript.
3. **Employee session history** is an explicit Ticket-domain read of the real Hermes conversation. It is
   authoritative for what Hermes received and produced, including internal context that Panels did not
   intentionally display.

Human Ticket Chat and automatic/revision Employee steps still deliver to the same durable Hermes
conversation. This ticket changes names and ownership, not the number of Hermes conversations. It does not
change gateway submission ordering, native busy/queued/steered handling, turn ownership, observation
normalization, interruption, Automatic Employee-step eligibility, Review, Worker workflow, Managed
Markdown, or mutation refresh policy.

Every live Ticket creation interface continues to require an explicit `worker_type`. There is no Python,
HTTP, SQLite, CLI, TypeScript, or fallback default to `coding`. The terminal database migration may retain
its historical-only classification of rows that genuinely predate stored Worker type. That lock-held
database rewrite is the sole `coding` concession. The retained standalone workspace seed importer is live
Python: its caller must supply `worker_type` explicitly, and the parser/importer must pass that exact value
through without inference or fallback.

### Contract wording correction already applied

The ticket originally asked malformed Employee-history data to preserve an “existing error envelope”. No
such envelope exists: `SharedGateway._normalize_history_messages` currently treats a non-list history as
empty and skips malformed entries. Commit `4a07e2f` amended the ticket with the orchestrator's
least-inventive correction, which this plan freezes:

- keep that lenient normalization;
- a non-list `messages` value becomes an empty tuple;
- non-object entries, entries with no usable text, and malformed individual entries are skipped;
- these cases return a successful normalized response and perform no database write; and
- `gateway_offline` remains reserved for transport/RPC failure.

The ticket and plan now agree. Do not invent a new malformed-history error envelope during implementation.

## 2. Complete current inventory and deletion boundary

### Ticket identity and every current writer/reader

The Ticket-owned value currently appears as:

- `tickets.chat_session_key` in the live DDL, terminal v19 table, schema recognizer, row migration, and
  direct test schemas in `src/planner/core/db.py` and `tests/unit/test_db.py`;
- `Ticket.chat_session_key`, `_row_to_ticket`, `ticket_json`, and `TicketDetail.chat_session_key`;
- `_persist_ticket_chat_session_key`, `claim_running_step_chat_session_key`,
  `read_ticket_by_session_key`, and the optional `session_key` arguments on the finish/error settlement
  writers;
- `GET /api/tickets/by-session/{session_key}` and the `panels worker my-ticket` caller, whose actual Hermes
  transport input is still the environment variable `HERMES_SESSION_KEY`;
- automatic/revision reads, pre-submit persistence, rotation, busy/error/completion/interruption settlement,
  and worker self-resolution in `EmployeeStepRunner`;
- revision admission in `tickets/logic/resolution.py`;
- the Ticket branch of Chat entity resolution and the generic human-turn binder;
- `ParsedTicket.chat_session_key`, the historical `Chat ID:` parser, and the seed importer insert. The
  importer currently also hard-codes `coding` instead of accepting its Worker type explicitly; AD09 removes
  that live inference while renaming the historical Chat id; and
- direct Ticket fixtures in the discovery, eligibility, external-work, generic-field, go/no-go,
  new-Worker-type, revision, seed, value-edit, and engine suites.

Current Ticket identity writes are fragmented. `_persist_ticket_chat_session_key` silently overwrites;
`claim_running_step_chat_session_key` has a running-status guard and writes `chat_session_created`; the
three settlement helpers may silently overwrite; `chat.data.bind_human_turn_session` writes the Ticket
directly; and `chat.service._persist_history_session_key` has a separate first-write/remint algorithm.
AD09 removes every one of those independent Ticket updates.

### Panels state and the silent history merge

`ChatState.session_key`, `ChatTurn.session_key`, `ChatMessage`, and `ChatHistory` currently make a Hermes
identity/history look like Panels state. `chat.data.read_state` accepts both a session key and synthetic
legacy messages. `chat.service.state` calls generic history when Panels has no message rows, and
`_legacy_state_messages` converts returned Hermes rows into negative-id product messages. That state read
may resume Hermes, rotate a Ticket key, append an event, and materialize Day or top-level-agent session
rows through `_resolve`.

The browser only consumes `active_turn.session_key` to decide when the Pause button becomes enabled.
Server-side Pause still needs the internal `chat_turns.session_key`; no other public consumer needs the
value. The database column is therefore honest internal Chat-turn transport state and remains, while the
public response becomes a boolean capability.

Delete:

- `GET /api/chat/{entity_id}/history`;
- `chat.service.history`, `_persist_history_session_key`, `_legacy_state_messages`, and every silent call
  from `state`;
- `ChatMessage`, `ChatHistory`, `ChatHistoryMessage`, and `ChatHistoryResponse` from the Chat/Panels
  contracts;
- the `session_key` fields on public Python/TypeScript `ChatTurn` and `ChatState`;
- negative-id fallback messages; and
- `worker_context.service.visible_prompt_text`, whose only purpose is to make the silent fallback look like
  the Panels-visible prompt.

Keep `chat_turns.session_key`, the generic human gateway's `session_key` and `bind_session_key`, Day and
top-level-agent `chat_session_key`, Hermes session-manager `stored_session_key`, `RunResult.session_key`, and
other transport-internal session-key vocabulary. These names describe real Chat/Hermes transport state;
they are not Ticket aliases.

### Generic history stack and callers

The generic stack being replaced is `GatewayAdapter.history`, the Echo/Offline/Real implementations,
`EntityRoutingGateway.history`, `SharedGateway.history`, and the Chat history service/route. Its only
production service caller is Chat. Test doubles in Chat image, activity, command, seed, human-turn, and
Minds tests currently implement `history` only to satisfy the broad adapter shape; remove those methods
when they no longer exercise Employee history.

The history normalizer recursively extracts text and preserves role/timestamp, but currently strips the
`[Pending worker context]` suffix from user messages. Explicit Employee history must use the same recursive
normalization without that stripping. It does not create Markdown attachment lines or reinterpret native
Hermes image content.

### Frontend and documentation

After AD08, `resourceCatalogue.ts` temporarily treats Ticket `chat_session_created` as Ticket plus Chat and
the four message/turn events as Chat only. `web/src/lib/types.ts` still exposes both history response types,
Ticket `chat_session_key`, `ChatTurn.session_key`, and top-level `ChatState.session_key`.
`ChatPanel.svelte` checks the active-turn key solely for Pause timing.

`docs/chat.md`, `docs/employee-runtime.md`, `docs/tickets-and-gates.md`, `docs/frontend.md`,
`docs/systems.md`, and synchronized `docs/systems.html` contain the old conflation. `CONTEXT.md` already
names the two concepts correctly but does not yet pin the Ticket field/route. `docs/worker-types.md`,
`docs/cli.md`, and the Worker/Chief skills contain no stale Ticket identity field or generic history route;
do not edit them merely to create churn.

## 3. Freeze the Ticket contracts and HTTP interfaces

### Python contracts

In `planner.tickets.contracts`, replace the Ticket field and add the explicit history/transition types:

```python
@dataclass
class Ticket:
    # all existing fields in their existing order, except:
    employee_session_id: str | None


@dataclass(frozen=True)
class EmployeeSessionIdTransition:
    expected_employee_session_id: str | None
    candidate_employee_session_id: str


@dataclass(frozen=True)
class EmployeeSessionHistoryMessage:
    role: str
    text: str
    created_at: int


@dataclass(frozen=True)
class EmployeeSessionHistory:
    messages: tuple[EmployeeSessionHistoryMessage, ...]
    employee_session_id: str | None
```

`ticket_json` always includes `"employee_session_id": <str-or-null>` and never includes
`chat_session_key`. No alias/property is retained.

The worker self-resolution data and HTTP interfaces become exactly:

```python
def read_ticket_by_employee_session_id(
    conn: sqlite3.Connection,
    employee_session_id: str,
) -> Ticket: ...


@router.get("/tickets/by-employee-session/{employee_session_id}")
async def get_my_ticket(
    employee_session_id: str,
    conn: DbConn,
    clk: Clk,
) -> JsonDict: ...
```

Delete `/tickets/by-session/{session_key}`. `panels worker my-ticket` continues to read the real Hermes
transport variable `HERMES_SESSION_KEY`, stores it locally as `employee_session_id`, and calls the new
route. The CLI command name and output do not change.

### Explicit Employee history owner and route

Add one framework-free owner, `src/planner/tickets/employee_session_history.py`, with exactly one public
operation:

```python
def read_employee_session_history(
    conn: sqlite3.Connection,
    gateway: GatewayAdapter,
    ticket_id: str,
    now: int,
) -> EmployeeSessionHistory: ...
```

The Ticket API exposes exactly:

```python
@router.get("/tickets/{ticket_id}/employee-session-history")
async def get_employee_session_history(
    ticket_id: str,
    request: Request,
    conn: DbConn,
    ctx: Ctx,
    clk: Clk,
) -> JsonDict: ...
```

The route calls `require_direct_write(ctx)`, passes the composed `request.app.state.adapters.gateway`, and
serializes the dataclass without reinterpretation. Its JSON shape is always:

```json
{
  "messages": [
    {"role": "user", "text": "actual Hermes content", "created_at": 123}
  ],
  "employee_session_id": "20260714_..."
}
```

`messages` is always an array and `employee_session_id` is always present as a string or `null`. With no
stored id the route returns `{"messages": [], "employee_session_id": null}` without calling the gateway.
A missing Ticket is `not_found`; an attributed agent is rejected by the existing direct-only envelope.
There is no Day or top-level-agent Employee-history route, no generic route alias, and no UI/cached resource
for this ordinary explicit read.

### Gateway interface

Replace only the generic history method on every gateway implementation:

```python
class GatewayAdapter(Protocol):
    def read_employee_session_history(
        self,
        employee_session_id: str,
        ticket_id: str,
    ) -> EmployeeSessionHistory: ...
```

`EntityRoutingGateway`, `SharedGateway`, `EchoGatewayAdapter`, `OfflineGatewayAdapter`, and
`RealGatewayAdapter` implement that exact name and parameter vocabulary. The input is non-null because the
Ticket history owner handles the no-id case before crossing the gateway seam. SharedGateway resumes
`employee_session_id` lazily with `session.resume`; internally the Hermes RPC still uses `session_id` and
stored session-key vocabulary. A Hermes not-found response returns empty messages with the requested
Employee id; transport/RPC failures remain `gateway_offline`. A resumed/rotated durable id is returned in
`EmployeeSessionHistory.employee_session_id`, falling back to the requested stored id when Hermes omits a
replacement.

Rename the normalizer to `_normalize_employee_session_history_messages`. Keep its current lenient recursive
text, role, and time extraction, but remove `visible_prompt_text`: actual user prompts retain pending worker
context, revision guidance, system/tool rows, and other content that Hermes truthfully returns.

### Live seed import requires an explicit Worker type

The retained seed surface has no HTTP route and no `panels` command: `/api/seed` and `plan seed` remain
deleted. Its one live command caller is `python -m planner.seed` in `src/planner/seed/__main__.py`, and its
one programmatic entry is `seed_from_source`. Freeze their interfaces as:

```python
def seed_from_source(
    conn: sqlite3.Connection,
    source_dir: str | Path,
    *,
    worker_type: str,
    now: int,
) -> MigrationReport: ...


def parse_workspace(
    text: str,
    source_file: str,
    item_titles: Sequence[str],
    *,
    worker_type: str,
) -> tuple[list[ParsedTicket], list[SkippedSection]]: ...
```

Both keyword-only arguments are required and have no default. The standalone command adds required
`--worker-type <id>` and calls
`seed_from_source(conn, args.source, worker_type=args.worker_type, now=now)`. Its usage text becomes:

```text
python -m planner.seed --source <dir> --worker-type <id> [--db-path <path>] [--json]
```

`seed_from_source` calls `configured_worker_type_registry().require(worker_type)` once before opening the
import transaction or creating any Ticket rows, then passes the validated exact id to `parse_workspace`
and the resolved definition to `_import_tickets`.
`ParsedTicket` freezes the parser/import seam as:

```python
@dataclass
class ParsedTicket:
    title: str
    worker_type: str
    stage: str
    priority: Priority
    alias: str | None = None
    employee_session_id: str | None = None
    body: str = ""
    success: str | None = None
    approach: str | None = None
    item_title: str | None = None
```

The Markdown label remains literal `Chat ID:` because it is historical source vocabulary; the parser
copies its value unchanged into `employee_session_id`. Do not add a `Worker Type:` Markdown fallback or
derive a type from `Readiness`, `Mode`, Stage, registry order, the source directory, or the presence of a
Chat id. The explicit import argument is the sole Worker-type input for every Ticket in that import run.

`_import_tickets` receives that resolved definition, requires every parsed `ticket.worker_type` to equal its
`worker_type`, builds fields from its `field_ids()`, validates the parsed Stage/ceiling against it, and inserts
`ticket.worker_type` plus `ticket.employee_session_id`. Remove
`configured_worker_type_registry().require("coding")`, `coding_worker_type_definition`, and the literal
`"coding"` insert. If the selected definition does not admit a parsed legacy Stage, validation fails and the
existing import transaction rolls back; the importer does not silently substitute another type or Stage.

Direct parser tests pass `worker_type="coding"` explicitly because that is the fixture's declared test
input, not a default. The production fixture Markdown does not change. A caller choosing another registered
compatible test Worker type must see that exact id and definition drive every imported Ticket.

## 4. One canonical Ticket Employee-session writer

`tickets/data.py` exposes one and only one function that may change
`tickets.employee_session_id`:

```python
def write_employee_session_id_in_transaction(
    conn: sqlite3.Connection,
    ticket_id: str,
    *,
    transition: EmployeeSessionIdTransition,
    force_fresh_employee_session: bool,
    now: int,
) -> str: ...
```

The caller must already own the required write transaction. The function reads only the Ticket identity
row, validates that `candidate_employee_session_id` is a non-empty string, and returns the effective
durable winner. It does not resolve a Worker type, interpret a Stage, inspect Ticket status, or
start/commit/rollback a transaction; the caller owns those admission rules. It is the only production
statement of the form
`UPDATE tickets SET employee_session_id = ...`.

Its exact compare-and-write rules are:

1. If current equals the candidate, return it with no update and no event.
2. If `force_fresh_employee_session` is true and current differs, write the candidate.
3. Otherwise, if current equals `expected_employee_session_id`, write the candidate.
4. Otherwise, if current is non-null, adopt and return that concurrent winner without writing.
5. Otherwise the expected id changed to NULL unexpectedly; raise the existing `already_running` conflict
   envelope rather than guessing.

An actual write updates `updated_at` and appends exactly one
`employee_session_changed {"employee_session_id": candidate}` event in the same transaction. A no-op or
lost compare writes no event. No live writer clears the id to NULL.

### Callers and transaction rules

- Rename the Employee claim wrapper to `claim_running_step_employee_session_id`. It starts its existing
  transaction, reloads the Ticket, returns unchanged when status is no longer `agent_running_step`, and
  otherwise calls the canonical writer with the runner's expected/candidate transition.
- `bind_human_turn_session` keeps its generic transport parameter names because it serves Ticket, Day, and
  agent Chat. Inside its existing transaction, the Ticket branch constructs an
  `EmployeeSessionIdTransition` and calls the canonical Ticket writer. Day/agent branches retain their
  direct `chat_session_key` update and `chat_session_created {session_key}` event.
- Exact `/new` passes `force_fresh_employee_session=True` for the Ticket branch, so its one new candidate
  wins even when a prior id exists. Ordinary candidates pass false and adopt a concurrent winner.
- The same transaction still binds the returned effective Hermes key to the still-running
  `chat_turns.session_key` before the gateway submits input. A lost ordinary candidate therefore sends
  through the actual durable winner, not through the losing session.
- Replace the settlement sentinel and optional `session_key` arguments with
  `employee_session_transition: EmployeeSessionIdTransition | None = None` on
  `finish_run_if_still_running_step`, `mark_run_errored`, and
  `mark_run_errored_if_still_running_step`. When supplied, each calls the canonical writer inside its
  existing transaction before the status write. The expected/candidate pair prevents a stale completion
  from overwriting a newer rotation.
- The explicit history owner also opens a short `BEGIN IMMEDIATE` only after the gateway returns and calls
  the same writer with `force_fresh_employee_session=False`. It never holds the database lock while waiting
  on Hermes.

Remove `_Unset`, `_persist_ticket_chat_session_key`, all direct Ticket identity SQL outside the canonical
writer and terminal migration, and every live Ticket `chat_session_created` append.

## 5. Explicit history rotation and race algorithm

`read_employee_session_history` uses an optimistic read/compare loop:

1. Read the Ticket and its current `employee_session_id`. Missing Ticket fails before gateway work; NULL
   returns the empty response immediately.
2. Ask `gateway.read_employee_session_history(current_id, ticket_id)` without a database transaction.
3. Under `BEGIN IMMEDIATE`, call the canonical writer with expected `current_id`, candidate equal to the
   gateway's effective id (or `current_id` if omitted), and force false.
4. If the returned durable winner equals the id whose messages were read, return those normalized messages
   and that id.
5. If another writer installed a different winner, discard the stale history result, release the lock, and
   repeat from that winner. Never relabel stale messages as the newer session's history.

The loop has no arbitrary retry count: it only repeats after a proven concurrent identity change, performs
no gateway call under a SQLite lock, and returns as soon as the response and durable Ticket agree. A normal
rotation writes one event. An unchanged/repeated read writes none. A stale result cannot overwrite the
newer winner. If the Ticket is deleted while Hermes is being read, the canonical reload returns
`not_found`, and the external Hermes session remains untouched and unreachable through Panels.

An offline/RPC failure happens before the compare transaction and writes nothing. Lenient malformed-message
normalization follows section 1: with an unchanged effective id it returns the normalized empty/partial
history and writes nothing. Message shape never authorizes an identity change; only Hermes's separate,
valid effective-id result can enter the rotation algorithm.

## 6. Panels Chat response and read-only state

### Exact Python response contracts

Keep the internal `chat_turns.session_key` column, but change the public `ChatTurn` dataclass by replacing
its session field with a capability:

```python
@dataclass(frozen=True)
class ChatTurn:
    id: str
    entity_id: str
    origin: str
    mode: str
    status: str
    phase: str
    activity_label: str | None
    output_role: str
    output_text: str
    can_pause: bool
    error: str | None
    started_at: int
    updated_at: int
    completed_at: int | None
    activity_entries: tuple[ChatActivityEntry, ...] = ()


@dataclass(frozen=True)
class ChatState:
    messages: tuple[ChatStateMessage, ...]
    active_turn: ChatTurn | None
```

`_row_to_turn` sets `can_pause` true only when the row is still running and its internal session key is
non-null. Start-turn responses are false until binding; settled Pause/completion/error responses are false.
This preserves the exact moment the browser enables Pause without disclosing durable identity.

Add a narrow internal data read:

```python
def read_running_turn_session_key(
    conn: sqlite3.Connection,
    turn_id: str,
    *,
    entity_id: str,
) -> str | None: ...
```

`ChatTurnLifecycle.pause_active_turn` uses this only to resolve the server-side interrupt after finding the
active turn. No HTTP serializer exposes the returned key. When a turn session is attached, the
`chat_turn_updated` event payload records `{"turn_id": ..., "can_pause": true}` instead of the session id.
The internal Chat turn column and generic gateway interrupt still use the actual key.

### Pure state read

Freeze the state interfaces as:

```python
def chat.data.read_state(
    conn: sqlite3.Connection,
    entity_id: str,
) -> ChatState: ...


def chat.service.state(
    conn: sqlite3.Connection,
    entity_id: str,
) -> ChatState: ...
```

The API route no longer obtains a clock or gateway for state. Before `read_state`, service performs a
read-only chattable-entity validation:

- a Ticket id must name an existing Ticket;
- a `day_YYYY-MM-DD` id must be canonical but need not be materialized merely to return empty Chat state;
- a known top-level-agent id is valid without inserting `agent_chat_sessions`; and
- anything else is `not_found`.

The write-capable `_resolve` remains for human turn admission, where Day/agent materialization and their
Chat session keys are required. State never invokes it. `read_state` selects only ordered
`chat_messages`, the one running `chat_turn`, and that turn's ordered display-safe activity entries. Zero
messages stays zero. Remove the legacy-messages and session-key parameters completely.

`GET /api/chat/{entity_id}/state` remains direct-only and has exactly:

```json
{
  "messages": [],
  "active_turn": null
}
```

for an empty valid Panels conversation. It performs no gateway call, session resume, entity materialization,
Ticket/session update, event append, message insert, or turn insert.

### Exact TypeScript declarations

In `web/src/lib/types.ts`, delete the two generic history types, change `TicketDetail`, and freeze Chat as:

```ts
export type ChatTurn = {
  id: string;
  entity_id: string;
  origin: "human" | "worker" | "system" | string;
  mode: "message" | "command" | "worker_step" | string;
  status: "running" | "complete" | "errored" | "interrupted" | string;
  phase: "queued" | "thinking" | "doing" | "responding" | "settled" | string;
  activity_label: string | null;
  activity_entries: ChatActivityEntry[];
  output_role: "assistant" | "system" | string;
  output_text: string;
  can_pause: boolean;
  error: string | null;
  started_at: number;
  updated_at: number;
  completed_at: number | null;
};

export type ChatStateResponse = {
  messages: ChatStateMessage[];
  active_turn: ChatTurn | null;
};

export type TicketDetail = {
  // existing fields
  employee_session_id: string | null;
  // no chat_session_key
};
```

`ChatPanel.svelte` changes only `pauseDisabled={!activeTurn?.can_pause}` and its import/use of the AD08
catalogue remains intact. No Employee-history TypeScript type, fetch helper, catalogue resource, pane,
toggle, source badge, or new visible element is added.

## 7. Employee runner, revision, and delivery preservation

At the Ticket/runtime seam, rename all variables and callbacks to Employee-session vocabulary. At the
gateway call itself, `run_ticket_step` and `RunResult.session_key` may retain Hermes transport naming.
The runner explicitly translates that returned transport value into an
`EmployeeSessionIdTransition` before calling Ticket data.

Preserve this exact automatic/revision order:

1. claim the Ticket's running step or read the already-reserved revision;
2. create the Panels worker-origin `chat_turn`;
3. ask Hermes to resume/create the stored Employee session;
4. in `on_session_key`, persist/adopt the Ticket `employee_session_id` through
   `claim_running_step_employee_session_id`;
5. verify the candidate was retained and the Ticket is still `agent_running_step`;
6. attach the effective transport key to the worker Chat turn; and only then
7. submit the actual prompt.

This retains worker self-resolution during the turn. If the claim loses, no prompt is submitted. Automatic
work may create a session; direct revision still requires `ticket.employee_session_id`, resumes it with
`require_existing_session=True`, and errors rather than creating a replacement when the session is stale.
Revision guidance remains absent from `chat_messages` while explicit Employee history may truthfully show
it. Automatic visible prompt/reply mirroring, activity, busy handling, completion, interruption, and error
settlement remain unchanged.

The runner tracks the current durable Employee id separately from the gateway's returned `session_key`.
Every settlement call supplies an expected/candidate transition when a non-null returned key exists. Thus
a final rotated key can be persisted, an idempotent value writes no event, and an older completion cannot
overwrite a newer history/human rotation.

Human Ticket messages, commands, images, and exact `/new` retain AD06's atomic turn/identity bind before
Hermes receives input. Panels rows remain only the intentionally visible mirror and are never read as model
context or proof of delivery.

## 8. Terminal v20 migration and historical event rewrite

Advance `SCHEMA_VERSION` from 19 to 20 and fold the rename into the existing terminal migration rather than
adding a second live patch. Rename the final-table constant, detector, migration function, savepoint, error
messages, comments, and direct test calls from v19 to v20.

The canonical v20 Ticket column is exactly:

```sql
employee_session_id TEXT,
```

It is nullable and has no default. The canonical live DDL and `_V20_TICKETS_TABLE_SQL` agree. Day and
`agent_chat_sessions` retain `chat_session_key TEXT`; `chat_turns.session_key` also remains.

Under the existing foreign-key-off plus `BEGIN IMMEDIATE`/savepoint envelope:

- reject any source table containing both `employee_session_id` and `chat_session_key` as ambiguous;
- recognize the exact canonical v20 shape and leave its table bytes/rows unchanged;
- when rebuilding, copy `employee_session_id` unchanged if it is the sole identity source;
- otherwise copy a sole v19/older `chat_session_key` SQLite value unchanged, without `str()`, trimming,
  defaulting, or reinterpretation;
- when neither column exists, write NULL;
- preserve NULL as NULL and every other column/foreign key/index exactly under the existing migration
  rules; and
- retain `worker_type_source = "coding"` only for historical rows that have neither `worker_type` nor
  `ticket_type`. No canonical v20 field/default acquires that value.

Keep `_rewrite_v18_ticket_events` for its existing old Stage/event inputs. Add a separate
`_rewrite_ticket_employee_session_events` and invoke both inside the same terminal transaction after the
table swap and before foreign-key validation. It identifies Ticket events by the canonical entity-id prefix
`substr(entity_id, 1, 2) = 't_'`, not by event kind alone and not by joining only surviving Ticket rows.
This rewrites an orphan historical Ticket event consistently while leaving every `day_...` and
`agent_...` event untouched.

For each Ticket `chat_session_created` event, parse a JSON object, require a string `session_key`, reject a
conflicting/pre-existing `employee_session_id` or malformed/missing session key, preserve any other payload
members, rename the key to `employee_session_id`, and update that same event row to kind
`employee_session_changed`. Updating in place preserves event id/order and `created_at`. Existing canonical
`employee_session_changed` events are untouched, so repeated opens are idempotent. Any table-copy,
payload, event, or foreign-key failure rolls back both table and event changes and removes `tickets_new`.

The migration is the only production area allowed to contain the old Ticket column/event payload as
historical recognition input. It does not create a compatibility view, dual columns, runtime `COALESCE`,
read fallback, or live old-event decoder.

## 9. Resource Catalogue precision after AD08

Modify only AD08's catalogue definitions/tests. Define the event facts so these exact dependencies hold:

| Entity/event | Exact catalogue identities |
| --- | --- |
| Ticket `employee_session_changed` | matching `ticket:<id>` only |
| Ticket `chat_message_recorded` | matching `chat:<id>` only |
| Ticket `chat_turn_started` | matching `chat:<id>` only |
| Ticket `chat_turn_updated` | matching `chat:<id>` only |
| Ticket `chat_turn_finished` | matching `chat:<id>` only |
| Day `chat_session_created` or four message/turn events | matching `chat:<id>` only |
| Agent `chat_session_created` or four message/turn events | matching `chat:<id>` only |

`employee_session_changed` is a Ticket-detail event but not an ordinary Ticket aggregate event: it must not
reach Board, current Sprint, Review, Panels Chat, Day, status, commands, or manifests. Remove
`chat_session_created` from the Ticket Chat-kind set. Day Chat events must be excluded from ordinary
current-Day invalidation and routed to Chat; agent Chat behavior remains Chat-only. The four Ticket Panels
events remain excluded from normal Ticket aggregate dependencies.

Update the catalogue unit completeness fixture so every backend event kind uses a valid representative
entity prefix: `employee_session_changed` uses `t_backend`; Day/agent Chat events use their real prefixes.
Add exact negative assertions for all phantom/aggregate identities. The explicit history route is an
ordinary uncached read with no production consumer, so the catalogue interface and its thirteen-resource
inventory do not grow.

## 10. RED-first acceptance and preservation matrix

Add/modify tests before production rewiring in this order.

### A. Closed interfaces and deletion RED

Extend `tests/unit/test_chat_ingress_contract.py` and add focused declarations in
`tests/unit/test_employee_session_history.py` to fail on the old route, old worker lookup route,
`GatewayAdapter.history`, Chat history contracts/services, legacy-state fallback, public Chat session
fields, or Ticket JSON alias. Assert the exact new Python signatures/dataclass fields and openapi routes.

Add scoped source/schema checks that:

- no live Ticket contract, serializer, writer, query, route, runner, seed target, TypeScript type, docs, or
  built JavaScript contains Ticket `chat_session_key`;
- no live Ticket writer emits `chat_session_created`;
- old tokens are allowed only in terminal migration recognition/tests, literal historical `Chat ID:`
  parsing, Day/agent Chat storage, internal `chat_turns`, and Hermes transport modules; and
- no live Worker-type default/inference is introduced. The terminal database migration's rewrite of a
  genuinely historical row with no stored Worker type is the only explicit `coding` concession. The live
  seed module, its standalone command, and every test call must pass a Worker type explicitly.

### B. Migration RED

Extend `tests/unit/test_db.py` and `test_worker_type_stage_contracts.py` for:

- exact fresh-v20 schema, nullable/no-default identity, foreign keys, indexes, and `user_version=20`;
- every recognized historical Ticket shape already covered by the terminal migration;
- byte-preserving v19 values, NULL, a canonical v20 value, and a source with neither identity column;
- rejection/rollback of both-column ambiguity and malformed/conflicting Ticket session-event payloads;
- mixed Ticket, Day, agent, orphan Ticket, unrelated, and already-canonical events with exact id/order/time;
- table plus event rollback on event and foreign-key failure;
- exact canonical reopen idempotence; and
- one lock-held terminal rebuild, with no extra post-read migration.

Keep the existing Stage/Worker-type migration fidelity assertions. Replace expected target column names;
do not weaken unrelated preservation.

### C. Live seed Worker-type RED

Update `tests/unit/test_seed.py` and `tests/unit/test_worker_type_registry.py` before changing the importer:

- `inspect.signature(seed_from_source)` proves required keyword-only `worker_type` and `now`, with no
  default; `inspect.signature(parse_workspace)` proves the same required Worker-type input;
- every direct fixture/parser call supplies `worker_type="coding"` explicitly, and the resulting
  `ParsedTicket.worker_type` and imported `tickets.worker_type` are exact;
- the standalone `python -m planner.seed` parser requires `--worker-type`; omission exits through argparse
  before database/import work, and the supplied value is forwarded unchanged;
- an unknown Worker type fails with the existing validation envelope before any Sprint, item, Ticket, Idea,
  or event is inserted;
- a registered test Worker type with legacy-compatible Stage ids is passed explicitly, drives its own
  field set/validation, and is stored on every imported Ticket, proving the importer neither selects
  `coding` nor uses registry order;
- a selected Worker type whose definition does not admit a parsed legacy Stage rolls the whole import back
  rather than changing the type or Stage;
- the historical `Chat ID:` value still reaches `employee_session_id` byte-for-byte and NULL stays NULL;
- source/AST checks reject `require("coding")`, a literal `"coding"` Ticket insert, a defaulted
  `worker_type`, or any fallback expression in live seed code; and
- OpenAPI and the `panels` command tree still have no seed surface. This correction extends only the
  retained standalone cutover command.

Rename the old coding-specific registry test to assert definition-driven seed construction. The fixture
Markdown remains unchanged because `--worker-type`/the function argument is the explicit input for the
whole legacy import.

### D. Canonical writer, human binding, and `/new` RED

Update `test_tickets_engine.py`, `test_human_chat_turn.py`, `test_chat_seed.py`, and
`test_chat_commands.py` to prove:

- one actual transition updates the row and writes one exact new event;
- same-candidate and repeated settlement are idempotent;
- ordinary first bind and rotation use expected-value comparison;
- concurrent ordinary candidates adopt the durable winner and bind the live turn to it;
- stale settlement/history candidates cannot overwrite that winner;
- exact `/new` alone forces its one fresh candidate;
- Day/agent Chat still write `chat_session_created {session_key}`;
- Ticket human message/command/image delivery binds before gateway input; and
- public turn/state responses contain `can_pause` and no session id while server Pause receives the exact
  internal key.

### E. Panels state and explicit history RED

`test_employee_session_history.py`, `test_chat_seed.py`, `test_chat_activity.py`, and
`test_chat_images.py` prove:

- a Ticket with non-empty fake Hermes history and zero `chat_messages` returns exactly empty Panels state;
- state makes zero gateway calls and zero row/event changes, including canonical unmaterialized Day/agent
  ids;
- existing Panels rows return exactly and survive a new app/process composition;
- explicit history returns the actual Hermes rows, including a user prompt with pending context that differs
  from the intentionally visible Panels message;
- explicit history never inserts/rewrites Panels messages or turns;
- no Employee id means no gateway call/spawn/resume/write;
- missing Ticket and direct-auth failures use existing envelopes and write nothing;
- offline/RPC failure writes nothing;
- non-list messages become empty and malformed entries are skipped successfully with no normalization-
  driven write;
- a valid rotated id writes exactly one event, repeated read writes none, and a lost stale result is
  discarded/re-read from the newer winner; and
- native image history is whatever Hermes returns, with no synthetic Panels Markdown line.

Remove the old Chief/Day generic-history tests because those endpoints no longer exist; keep their Panels
state/send tests.

### F. Employee runtime and worker lookup RED

Update `test_employee_step_runner.py`, `test_return_for_revision.py`, `test_worker_my_ticket.py`, and the
Ticket engine/eligibility suites to prove:

- created/resumed identity is queryable by the new route before prompt submission;
- an old/losing rotated id does not resolve a Ticket;
- automatic work creates/resumes, mirrors its visible prompt/reply, and settles exactly as before;
- actual explicit history retains the submitted prompt plus internal pending context;
- revision strictly resumes the existing id, sends hidden guidance through Hermes, and leaves that guidance
  out of Panels rows;
- human message, command, image, `/new`, busy, completion, error, and interruption preserve delivery and
  visible state; and
- restart resumes the stored Employee conversation without replaying input.

Mechanically rename direct Ticket fixture columns/properties in discovery, eligibility, external-work,
generic-field, go/no-go, new-Worker-type, seed, value-edit, and worker-stage contract tests. Do not rename
Day/agent/internal Hermes fixtures.

### G. Catalogue and browser RED

Update `web/tests/resource-catalogue.test.mjs`, `tests/unit/test_frontend_event_mapping.py`, and
`tests/e2e/test_resource_catalogue.py` for section 9's exact identity/network sets and completeness.

Update `tests/e2e/test_live_chat_state.py` and `test_flows_a.py` to preserve send, remount, hard reload,
active activity, Pause, and WebSocket behavior with `can_pause`. Add one load-bearing browser proof: send a
Ticket message through the fake gateway, remove only that Ticket's Panels `chat_messages`/settled turns
while leaving its Employee id and fake Hermes history intact, hard-reload the Ticket screen, and assert that
the old Employee history does not appear. Existing Panels rows in the companion restart/remount case must
still appear exactly.

No existing assertion is weakened into “some transcript appeared”. Each old history assertion is moved to
either exact Panels rows or the explicit Employee-history interface.

## 11. Focused commands and final gate

Run focused checks as their RED group turns green:

```text
.venv/bin/pytest tests/unit/test_db.py tests/unit/test_worker_type_stage_contracts.py
.venv/bin/pytest tests/unit/test_tickets_engine.py tests/unit/test_human_chat_turn.py tests/unit/test_chat_commands.py
.venv/bin/pytest tests/unit/test_employee_session_history.py tests/unit/test_chat_seed.py tests/unit/test_chat_activity.py tests/unit/test_chat_images.py tests/unit/test_chat_ingress_contract.py
.venv/bin/pytest tests/unit/test_employee_step_runner.py tests/unit/test_return_for_revision.py tests/unit/test_worker_my_ticket.py tests/unit/test_minds.py
.venv/bin/pytest tests/unit/test_automatic_employee_step_discovery_loop.py tests/unit/test_automatic_employee_step_eligibility_actions.py tests/unit/test_external_work_generic.py tests/unit/test_generic_field_storage.py tests/unit/test_go_no_go_gate.py tests/unit/test_new_worker_type.py tests/unit/test_seed.py tests/unit/test_worker_type_registry.py tests/unit/test_value_edit_logic.py
npm --prefix web run check
npm --prefix web test
.venv/bin/pytest tests/unit/test_frontend_event_mapping.py
.venv/bin/pytest tests/e2e/test_resource_catalogue.py tests/e2e/test_flows_a.py tests/e2e/test_live_chat_state.py tests/e2e/test_chat_images.py
```

After source, tests, docs, and checked-in built assets are complete, run one full `./verify`. Its full output
is the only completeness claim. Do not rerun it only to quote a result, and do not advance over a failure.

Before integration, run the required independent read-only implementation review against this plan, the
AD09 ticket, AD06's Chat-turn lock, AD08's catalogue lock, and the actual diff. Address or refute every
finding in writing. Spot-check the canonical Employee writer, history CAS loop, terminal migration/event
rewrite, pre-submit runner ordering, and resource dependencies before serial integration.

## 12. Documentation, built assets, and memory handoff

Update the live plain-language docs to say:

- a Ticket stores `employee_session_id`, and both human Ticket Chat and Employee steps deliver through that
  durable conversation;
- Panels Chat is only intentionally visible durable messages/live turn state;
- Chat state never loads or merges Employee history;
- Employee session history is available only through the explicit Ticket route and may include internal
  context, revision guidance, system/tool content, or other real Hermes content absent from Panels Chat;
- a restart resumes the stored Employee conversation;
- a Panels row alone is never Employee delivery; and
- Day/agent Chat session keys remain Chat-specific, not Employee identity.

Apply that wording to `CONTEXT.md`, `docs/chat.md`, `docs/employee-runtime.md`,
`docs/tickets-and-gates.md`, `docs/frontend.md`, `docs/systems.md`, and synchronized `docs/systems.html`.
Correct the current frontend promise that Ticket session events refresh both Ticket and Chat and remove the
empty-transcript Hermes-history retry description. Keep the docs readable without code context.

Also update `docs/cli.md` and `docs/worker-types.md` only for the retained seed seam. Keep the statement that
there is no `/api/seed` or `panels seed` importer. Name the standalone one-time command exactly as
`python -m planner.seed --source <dir> --worker-type <id>` and explain that it resolves the explicitly
selected Worker type for Stage/field validation; it never defaults or infers one. Update the module usage
text in `src/planner/seed/__main__.py` to match.

The orchestrator, not the implementation sub-agent, updates `PROGRESS.md` and `decisions.md` after the
reviewed commit. In particular it corrects the now-superseded decision sentence that direct Hermes history
strips internal context, records the lenient malformed-history correction, and records AD09 as the final
isolated architecture-deepening commit.

Rebuild `web/dist` once from the final AD09 sources because FastAPI serves it. Resolve
`<AD08-final-js-hash>` from the serially landed `web/dist/index.html` immediately before implementation.
The bounded generated diff is:

- `web/dist/index.html` changes only to reference the AD09 JavaScript hash;
- `web/dist/assets/index-<AD08-final-js-hash>.js` is deleted;
- exactly one `web/dist/assets/index-<AD09-generated-hash>.js` is added; and
- CSS, fonts, shared assets, and every other built file remain byte-identical.

Any extra chunk, CSS, font, or unrelated generated change stops for inspection rather than widening scope.

## 13. Bounded changed-path allowlist

Implementation may change only:

```text
CONTEXT.md
src/planner/chat/api.py
src/planner/chat/contracts.py
src/planner/chat/data.py
src/planner/chat/service.py
src/planner/cli/main.py
src/planner/core/adapters/base.py
src/planner/core/adapters/fakes.py
src/planner/core/adapters/real.py
src/planner/core/contracts.py
src/planner/core/db.py
src/planner/minds/shared_gateway.py
src/planner/runtime/employee_step_runner.py
src/planner/seed/__main__.py
src/planner/seed/contracts.py
src/planner/seed/importer.py
src/planner/seed/logic/workspace.py
src/planner/tickets/api.py
src/planner/tickets/contracts.py
src/planner/tickets/data.py
src/planner/tickets/employee_session_history.py                 (add)
src/planner/tickets/logic/resolution.py
src/planner/tickets/views.py
src/planner/worker_context/service.py
web/src/components/ChatPanel.svelte
web/src/lib/resourceCatalogue.ts
web/src/lib/types.ts
web/tests/resource-catalogue.test.mjs
tests/unit/test_automatic_employee_step_discovery_loop.py
tests/unit/test_automatic_employee_step_eligibility_actions.py
tests/unit/test_chat_activity.py
tests/unit/test_chat_commands.py
tests/unit/test_chat_images.py
tests/unit/test_chat_ingress_contract.py
tests/unit/test_chat_seed.py
tests/unit/test_db.py
tests/unit/test_employee_session_history.py                     (add)
tests/unit/test_employee_step_runner.py
tests/unit/test_external_work_generic.py
tests/unit/test_frontend_event_mapping.py
tests/unit/test_generic_field_storage.py
tests/unit/test_go_no_go_gate.py
tests/unit/test_human_chat_turn.py
tests/unit/test_minds.py
tests/unit/test_new_worker_type.py
tests/unit/test_return_for_revision.py
tests/unit/test_seed.py
tests/unit/test_tickets_engine.py
tests/unit/test_value_edit_logic.py
tests/unit/test_worker_my_ticket.py
tests/unit/test_worker_type_registry.py
tests/unit/test_worker_type_stage_contracts.py
tests/e2e/test_flows_a.py
tests/e2e/test_live_chat_state.py
tests/e2e/test_resource_catalogue.py
docs/chat.md
docs/cli.md
docs/employee-runtime.md
docs/frontend.md
docs/tickets-and-gates.md
docs/systems.md
docs/systems.html
docs/worker-types.md
web/dist/index.html
web/dist/assets/index-<AD08-final-js-hash>.js                    (delete)
web/dist/assets/index-<AD09-generated-hash>.js                   (add exactly one)
```

`src/planner/days/api.py`, `days/contracts.py`, and `days/data.py` are explicitly outside the allowlist:
their Chat-specific field remains `chat_session_key`. So are `minds/contracts.py`, `minds/runner.py`, and
`minds/sessions/`: their `session_key` vocabulary is honest Hermes transport state. `chat_turns.session_key`
remains in `core/db.py` and Chat data as an internal Pause/delivery key, not a Ticket property.

Also unchanged: AD08's catalogue public resource inventory, `resources.svelte.ts`, `ws.ts`, API fetch
helpers, frontend routes/layout/CSS, `package.json`, `package-lock.json`, all Worker/Chief skills, Worker-type
manifests, Automatic Employee-step eligibility rules, Review shapes, Managed Markdown, and every unrelated
test/doc. The implementation sub-agent does not edit `ticket.md`, `PROGRESS.md`, or `decisions.md`.

Any need for a second Hermes conversation, Employee-history database copy, UI/history resource, transcript
merge, old alias/route/event decoder, new error envelope, live Worker-type default, changed gateway ordering,
or path outside this allowlist returns to the orchestrator before implementation.
