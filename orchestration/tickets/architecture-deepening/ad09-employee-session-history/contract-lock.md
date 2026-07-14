# AD09 contract lock

The orchestrator generated this lock after the corrected delegated implementation plan passed independent
read-only review. AD09 separates Panels Chat from Employee session history, gives the Ticket's Hermes
identity its honest name, and removes the last live implicit Worker-type choice. It preserves the existing
Hermes conversation, delivery ordering, Panels-visible behavior, and stored Stage.

## Three explicit owners

The finished system has three distinct boundaries:

1. A Ticket owns one nullable `employee_session_id`, written only by the Ticket domain's canonical
   compare-and-write transition.
2. Panels Chat owns only durable product-visible `chat_messages`, the live `chat_turn`, and display-safe
   activity. Its state read never asks Hermes for history and never writes.
3. Employee session history is an explicit Ticket-domain read of Hermes' authoritative record of what the
   Employee actually received and produced. It may include internal context that Panels never displayed.

Human Ticket Chat and automatic/revision Employee steps continue through the same durable Hermes
conversation. AD09 creates no second conversation, copied transcript, replay store, cached history resource,
or UI history pane. Day and top-level-agent Chat keep their honest Chat-specific `chat_session_key` fields.

## Public contracts

`Ticket.chat_session_key` becomes `Ticket.employee_session_id`; Ticket JSON always returns the new key as a
string or null and never returns the old alias. The Worker self-resolution route becomes:

```text
GET /api/tickets/by-employee-session/{employee_session_id}
```

The only public Employee history interface is:

```text
GET /api/tickets/{ticket_id}/employee-session-history
```

Its response is exactly:

```json
{
  "messages": [
    {"role": "user", "text": "actual Hermes content", "created_at": 123}
  ],
  "employee_session_id": "20260714_..."
}
```

`messages` is always an array and the id is always a string or null. With no stored id, the route returns
empty history without calling the gateway. There is no Day/agent version, generic Chat-history alias, or
production UI consumer. Missing Ticket and attributed-agent requests retain the existing Ticket direct-read
envelopes. Transport/RPC failure alone is `gateway_offline`.

The gateway protocol replaces generic `history` with the non-null, Ticket-specific operation:

```python
def read_employee_session_history(
    employee_session_id: str,
    ticket_id: str,
) -> EmployeeSessionHistory: ...
```

The normalizer keeps existing leniency: a non-list history is empty; malformed entries and entries with no
usable text are skipped. It preserves the actual recursively extracted Hermes content, role, and timestamp.
It no longer strips pending Worker context from user prompts. Malformed history performs no database write.

Panels Chat deletes the generic `/api/chat/{entity_id}/history` route, the Chat history service/contracts,
silent empty-state fallback, synthetic negative-id messages, and public session ids. Public `ChatTurn`
replaces its session id with `can_pause: bool`; `ChatState` contains only messages and the active turn. The
internal `chat_turns.session_key` remains for delivery and Pause.

## Explicit Worker type at every live boundary

Every live Ticket creation and import path requires an explicit Worker type. There is no Python, HTTP, CLI,
TypeScript, SQLite, registry-order, Stage-derived, source-derived, or fallback default to `coding`.

The retained one-time importer has these required keyword-only inputs:

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

The command is `python -m planner.seed --source <dir> --worker-type <id>`; `--worker-type` is required. The
importer resolves that exact id once, passes it through `ParsedTicket`, uses that definition's fields and
Stage validation, and inserts the exact id. An incompatible Stage rejects and rolls back the import. The
historical Markdown label remains literal `Chat ID:` but maps byte-for-byte to `employee_session_id`.

Only the lock-held terminal database migration may assign `coding` automatically to a genuinely historical
Ticket shape that has neither a stored `worker_type` nor the older `ticket_type`. Test calls may pass the
literal explicitly as fixture input; that is not a default.

## One canonical Employee-session writer

Only `tickets/data.py` may update `tickets.employee_session_id`. Its transition carries expected and
candidate ids plus the one-use force-fresh decision used by exact `/new`. Under the caller's transaction it:

- rejects a missing Ticket;
- performs an ordinary expected-value compare-and-write or the exact force-fresh transition;
- emits one `employee_session_changed {"employee_session_id": candidate}` event with the row update;
- treats same-value and repeated settlement as no-ops; and
- returns the durable winner so concurrent ordinary candidates adopt it instead of overwriting it.

Automatic claim, completion/error/interruption settlement, revision, human message/command/image binding,
exact `/new`, and explicit history rotation all call this writer. No other statement may update the Ticket
identity. Gateway calls never occur while the SQLite write lock is held.

Explicit history reads optimistically snapshot the id, call Hermes outside the write transaction, and use
the canonical transition if Hermes returns a rotated id. If another writer won, history retries with the
durable winner rather than overwriting it. No-id, malformed, not-found, authorization, or gateway failures
write nothing.

## Terminal v20 migration

Advance the schema to v20 and fold the rename into the existing single lock-held terminal Ticket migration:

- `tickets.employee_session_id` is nullable and has no default;
- v19 `chat_session_key` values, nulls, and canonical v20 values are preserved byte-for-byte;
- both identity columns together are rejected as ambiguous;
- recognized older Ticket shapes still reach the one canonical final table;
- Ticket `chat_session_created {"session_key": value}` events become
  `employee_session_changed {"employee_session_id": value}` in place;
- event id, order, timestamp, and unrelated payloads remain unchanged;
- Day/agent `chat_session_created` remains unchanged;
- old Ticket events with malformed/conflicting payloads fail the migration; and
- table, event, index, and foreign-key changes roll back together on failure.

There is no runtime alias, `COALESCE`, compatibility view, old-event decoder, or post-read patch.

## Resource and frontend consequence

AD08's thirteen-resource public catalogue stays unchanged. Exact dependencies after AD09 are:

| Entity/event | Exact catalogue identity |
| --- | --- |
| Ticket `employee_session_changed` | matching `ticket:<id>` only |
| Ticket four message/turn events | matching `chat:<id>` only |
| Day/agent `chat_session_created` or four message/turn events | matching `chat:<id>` only |

None reaches Board, current Sprint, Review, status, commands, manifests, or whole-screen refresh. Explicit
history is an ordinary uncached read. Ticket detail uses `employee_session_id`; Chat state exposes
`can_pause`, not a session id. The checked-in web bundle is rebuilt once from the final sources, replacing
only the AD08 JavaScript chunk referenced by `web/dist/index.html`; CSS, fonts, and all other assets remain
byte-identical.

## Verification and scope

Implementation follows the RED-first order and exact changed-path allowlist in reviewed plan sections
10–13. Tests prove interface deletion, explicit seed input, canonical writer races and `/new`, migration and
event rewriting, Panels-state purity, raw Hermes history, delivery/restart behavior, resource precision,
browser preservation, and absence of any live `coding` fallback.

The implementation sub-agent may not change the ticket, this lock, `PROGRESS.md`, or `decisions.md`. Any
need for a second Hermes conversation, history copy, UI history surface, compatibility alias, new error
envelope, different gateway ordering, mutable Worker type, Stage change, live Worker-type default, or path
outside the reviewed allowlist returns to the orchestrator.
