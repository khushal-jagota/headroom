# T05 — Implementation plan: links/blocking, dispatch logic, dispatch data layer

Blueprint for the implementer. Zero judgment calls should remain: every signature, SQL
statement, event payload, error code, and test assertion is specified here. Contracts
(`src/planner/dispatch/contracts.py`, `src/planner/tickets/contracts.py`,
`src/planner/core/contracts.py`, `src/planner/core/errors.py`) are law and are never modified.

## Files created (nothing else touched)

1. `src/planner/dispatch/logic/__init__.py`
2. `src/planner/dispatch/logic/claims.py`
3. `src/planner/dispatch/logic/eligibility.py`
4. `src/planner/dispatch/logic/ordering.py`
5. `src/planner/dispatch/logic/breaker.py`
6. `src/planner/core/links.py`
7. `src/planner/dispatch/data.py`
8. `tests/unit/test_dispatch.py`

Out of scope (stage 4, not this ticket): the dispatcher tick loop, spawn adapter calls,
`max_runs` / `run_max_seconds` enforcement, and §7.6 claim validation of API writes
(SPEC lines 165–167). This ticket delivers the primitives those will call.
`src/planner/dispatch/api.py` and `src/planner/dispatch/__init__.py` are not touched.

---

## Design decisions and resolved ambiguities

Each numbered item here is a call I made where SPEC/contracts left room. The implementer
follows these verbatim.

**D1 — Expiry comparison: a claim is expired iff `claim_expires <= now`.**
The lease is the half-open interval `[claim_time, claim_time + ttl)`. Justification:
(a) with integer unix seconds, a claim taken at `t` with `ttl=900` is valid for exactly
900 clock values (`t` … `t+899`), matching "TTL 15 minutes" (SPEC line 153) with no
off-by-one; (b) at the boundary instant both sides agree the lease is dead — a heartbeat
at exactly `claim_expires` fails and a reclaim at exactly `claim_expires` proceeds, so
there is no instant where a writer and the reclaimer both consider the lease valid. This
errs on the safe side of the brief's invariant "no code path lets an expired claim keep
writing". Consequently `has_active_claim` = `claim_lock IS NOT NULL AND claim_expires > now`
(matches the contract comment "claim_lock set and unexpired"). One definition, in
`logic/claims.py`, used everywhere (candidate assembly, heartbeat guard, reclaim sweep).

**D2 — Heartbeat sets `claim_expires = now + ttl` exactly** (binding disambiguation from
the dispatch brief: "extends by exactly one TTL from now" — not from the previous expiry).

**D3 — Heartbeat on an expired or closed claim raises `stale_claim`.** An expired lease
cannot be revived by a late heartbeat, even before the reclaim sweep has run. Guards, in
order: run missing → `not_found`; run status ≠ `running` → `stale_claim`; ticket
`claim_lock IS NULL` or `claim_expires <= now` → `stale_claim`. (§7.6 token matching is
stage 4; these are data-integrity guards, not claim-header validation.)

**D4 — `close_run` on a run that is not `running` raises `stale_claim`** (not
`validation`): the caller's claim is stale by definition — the canonical case is closing
an already-reclaimed run. Detail payload names the run's current status. A missing run id
raises `not_found`.

**D5 — One door for run status + breaker.** Both `close_run` and the reclaim sweep route
through one private finalizer `_finalize_run` in `data.py`. Its runs-UPDATE is guarded by
`AND status='running'` (a CAS), so a close racing a reclaim has exactly one winner: the
loser gets rowcount 0 — `close_run` then raises `stale_claim`, the sweep silently skips
(the run was closed legitimately first). No other code path writes `runs.status` or the
breaker columns.

**D6 — Breaker arithmetic** (`logic/breaker.py`, pure): `crashed`/`timed_out`/
`spawn_failed` (== contract `FAILURE_STATUSES`) increment `consecutive_failures`; `done`
resets it to 0; every other status (`blocked`, `reclaimed`) leaves it unchanged — §7.5
(line 163) enumerates the increments and the reset exhaustively, so "neither" is the rule
for everything else. `auto_blocked` trips when an increment reaches `failure_limit`;
it is sticky (close never writes it back to 0). The `auto_blocked` event fires only on
the 0→1 transition of the flag.

**D7 — `updated_at`:** dispatch lease/breaker writes (claim, heartbeat, reclaim, close)
do **not** bump `tickets.updated_at`. The mandated literal CAS statement cannot include
it, which fixes the convention; lease and breaker state are operational and fully
journaled via events. `clear_auto_block` (a human action on the ticket) **does** set
`updated_at = now`.

**D8 — Link endpoint-kind validation by id prefix.** `errors.py` documents
`link_invalid` as covering "bad endpoints", and §3.6 (line 55) fixes each kind's endpoint
types, so `add_link` validates the prefix before the first `_`: `belongs_to` `t → si`;
`parent_child` `t → t`; `blocks` `t → (t | si)`; `relates` unchecked. Endpoint
**existence** is not checked (the links table deliberately has no FKs — endpoints are
polymorphic; a `blocks` link whose source ticket row does not exist simply never blocks,
because the blocked-derivation joins `tickets`).

**D9 — Duplicate identical link (same triple) → `link_invalid`.** Pre-checks catch
self-link and second-`belongs_to` deliberately; any `sqlite3.IntegrityError` from the
INSERT (PK duplicate, the partial unique index under a race, the `from_id <> to_id`
CHECK) is caught and re-raised as `PlannerError(link_invalid)` — a raw IntegrityError
never escapes.

**D10 — Cycle check is per-kind.** A `blocks` cycle is a cycle within `blocks` edges
only; `parent_child` within `parent_child` edges only (§3.6 line 56 names them as two
independently-checked relations). BFS from `to_id` following same-kind outgoing edges,
unbounded depth, visited-set guarded; if `from_id` is reachable, raise `link_cycle`.
`relates` and `belongs_to` are never cycle-checked.

**D11 — Concurrent cycle-creating inserts** (two connections adding `A→B` and `B→A`
simultaneously, each BFS seeing no path) are not serialized at the DB level. Accepted:
§3.6 requires a write-time transitive check, which this is; in production a single server
process serializes link writes. Not test-fenced.

**D12 — Link events:** `entity_id = from_id` (the owning entity), payload
`{from_id, to_id, kind}` with `kind` as its string value. `remove_link` of a nonexistent
link raises `not_found` (and logs nothing).

**D13 — `claim()` on an unknown ticket id returns `None`** (the CAS matches zero rows —
indistinguishable from losing, which is correct: the dispatcher only claims candidates it
just loaded). `claim()` does not re-check eligibility; the tick recomputes eligibility
immediately before claiming (§7.1), and the CAS is the sole arbiter against double-claim.

**D14 — Runs with `pid IS NULL` are never dead-PID-reclaimed** — only expiry can reclaim
them. When a run is both expired and dead, expiry is checked first: reason `"expired"`.

**D15 — `sweep_reclaims` takes `failure_limit`** because it shares `_finalize_run` with
`close_run`; a `reclaimed` status never changes the counter (D6), so the value is inert
on that path. `pid_alive: Callable[[int], bool]` is a parameter so the data layer stays
deterministic; stage 4 supplies the real `os.kill(pid, 0)` probe.

**D16 — Test filing:** the pure §7.2 gate matrix and the gating-pending assembly test are
filed under item 11 (`test_a11_*` — item 11 is "dispatch ordering over five *eligible*
tickets", and these pin down what "eligible" means); `parent_child` cycle, self-link,
`belongs_to` uniqueness, and link-event tests under item 9 (the links item). Fences allow
multiple tests per item.

**D17 — Times in tests are literal ints** (every data/logic function takes `now: int` /
returns ints); no clock object is needed. The shared `fake_clock` fixture goes unused;
`cfg` supplies `claim_ttl_seconds` and `failure_limit`, and the tests assert the SPEC
defaults (900, 2) once so the config tie-in is fenced.

**D18 — Item 14 builds its own two connections** from pytest's `tmp_path` (the shared
`tmp_db` fixture yields a single connection and hides the path). It calls
`planner.core.db.connect` twice against one file and `create_schema` once.

**D19 — Return shapes:** no new contract dataclasses. `claim` returns
`tuple[str, str] | None` = `(run_id, claim_token)` or `None` when lost;
`next_breaker_state` returns `tuple[int, bool]` = `(new_consecutive_failures,
tripped_this_close)`; the private finalizer uses a module-private `typing.NamedTuple`.

**D20 — `close_run` accepts exactly `AGENT_CLOSE_OUTCOMES | FAILURE_STATUSES`**
(= `{done, blocked, crashed, timed_out, spawn_failed}`, derived from the contract
constants, no new constant). `running` or `reclaimed` passed in → `PlannerError(validation)`.
`reclaimed` is reachable only through `sweep_reclaims`.

---

## File 1 — `src/planner/dispatch/logic/__init__.py`

Re-exports the public pure API so callers write `from planner.dispatch.logic import ...`:

```python
"""Pure dispatch rules (§7.2, §7.3 arithmetic, §7.5). Stdlib + contracts only —
no sqlite3, no FastAPI, no planner.core.db/events imports anywhere in this package."""

from planner.dispatch.logic.breaker import next_breaker_state
from planner.dispatch.logic.claims import expiry_at, has_active_claim, is_expired
from planner.dispatch.logic.eligibility import gating_field_pending, is_eligible
from planner.dispatch.logic.ordering import eligible_ordered, ordering_key

__all__ = [
    "eligible_ordered", "expiry_at", "gating_field_pending", "has_active_claim",
    "is_eligible", "is_expired", "next_breaker_state", "ordering_key",
]
```

(ruff RUF022 is not in the enabled set, but keep `__all__` sorted anyway; the import
block must satisfy isort `I`.)

## File 2 — `src/planner/dispatch/logic/claims.py`

Claim/TTL arithmetic — the single definition of lease time (D1, D2).

```python
def expiry_at(now: int, ttl_seconds: int) -> int:
    """Expiry for a claim taken or heartbeaten at `now`: exactly now + one TTL."""
    return now + ttl_seconds

def is_expired(claim_expires: int | None, now: int) -> bool:
    """Expired iff claim_expires <= now (lease is [start, start+ttl)); a NULL expiry
    counts as expired."""
    return claim_expires is None or claim_expires <= now

def has_active_claim(claim_lock: str | None, claim_expires: int | None, now: int) -> bool:
    """§7.2 'no active claim' / contract 'claim_lock set and unexpired'."""
    return claim_lock is not None and not is_expired(claim_expires, now)
```

No other module recomputes any of these comparisons — `data.py` imports them.

## File 3 — `src/planner/dispatch/logic/eligibility.py`

Imports: `planner.core.contracts.JsonDict`; `planner.dispatch.contracts.DispatchCandidate`;
`planner.tickets.contracts` (`ADVANCE_TARGET`, `AtCap`, `GATING_FIELD`, `STATE_ORDER`,
`TicketState`). Module-level precomputed index:

```python
_ORDER_INDEX: Final[dict[TicketState, int]] = {s: i for i, s in enumerate(STATE_ORDER)}
```

```python
def gating_field_pending(state: TicketState, fields: JsonDict) -> bool:
    """True iff the state's gating field carries a pending proposal in the parsed
    tickets.fields JSON. States without a gating field (needs_review, done, dropped)
    return False."""
    name = GATING_FIELD.get(state)
    if name is None:
        return False
    slot = fields.get(name.value)
    if not isinstance(slot, dict):
        return False
    return slot.get("proposal") is not None
```

```python
def is_eligible(candidate: DispatchCandidate) -> bool:
```

Implements §7.2 (SPEC line 149) exactly, checks in this order:

1. `candidate.state in (TicketState.done, TicketState.dropped, TicketState.needs_review)`
   → `False`. (This early return guarantees `ADVANCE_TARGET[state]` below never KeyErrors:
   the four remaining states are exactly its keys.)
2. `candidate.is_blocked` → `False` (§3.6 line 57).
3. `candidate.auto_blocked` → `False` (§7.5).
4. `candidate.has_active_claim` → `False`.
5. `candidate.gating_pending` → `False` ("current gating field has no pending proposal").
6. `target = ADVANCE_TARGET[candidate.state]`; if
   `_ORDER_INDEX[target] <= _ORDER_INDEX[candidate.ceiling]` → `True` (branch (a): the
   agent can move it).
7. Else → `candidate.state == candidate.ceiling and candidate.at_cap is AtCap.propose`
   (branch (b): at ceiling + propose eligible; at ceiling + stop `False`; state *past*
   the ceiling — human jumped it — `False`, since "at its ceiling" means equality).

`ceiling` is contract-restricted to `STATE_ORDER` members (never `dropped`), and `state`
is in `STATE_ORDER` after step 1, so `_ORDER_INDEX` lookups are total.

## File 4 — `src/planner/dispatch/logic/ordering.py`

Imports: `collections.abc.Iterable`; `planner.core.contracts.Priority`;
`planner.dispatch.contracts.DispatchCandidate`; `planner.dispatch.logic.eligibility.is_eligible`.

```python
_PRIORITY_RANK: Final[dict[Priority, int]] = {
    Priority.P0: 0, Priority.P1: 1, Priority.P2: 2, Priority.P3: 3,
}

def ordering_key(candidate: DispatchCandidate) -> tuple[int, int, str, int]:
    """§7.2 ordering: priority (P0 first), deadline ascending with NULLs last,
    created_at ascending. ISO date strings compare lexicographically ==
    chronologically; the NULL flag (0 dated / 1 undated) puts every dated ticket
    before every undated one regardless of the string component."""
    return (
        _PRIORITY_RANK[candidate.priority],
        0 if candidate.deadline is not None else 1,
        candidate.deadline or "",
        candidate.created_at,
    )

def eligible_ordered(candidates: Iterable[DispatchCandidate]) -> list[DispatchCandidate]:
    """Filter by is_eligible, sort by ordering_key. Sort is stable: ties beyond
    created_at keep input order."""
    return sorted((c for c in candidates if is_eligible(c)), key=ordering_key)
```

## File 5 — `src/planner/dispatch/logic/breaker.py`

Imports: `planner.dispatch.contracts` (`FAILURE_STATUSES`, `RunStatus`).

```python
def next_breaker_state(
    current_failures: int, status: RunStatus, failure_limit: int
) -> tuple[int, bool]:
    """§7.5: crashed/timed_out/spawn_failed increment; done resets to 0; any other
    status (blocked, reclaimed) leaves the counter unchanged. Returns
    (new_consecutive_failures, tripped) where tripped is True iff this close
    incremented the counter to >= failure_limit. Stickiness and the 0->1 event
    transition are the caller's concern (data.close_run)."""
    if status in FAILURE_STATUSES:
        new_failures = current_failures + 1
        return new_failures, new_failures >= failure_limit
    if status is RunStatus.done:
        return 0, False
    return current_failures, False
```

Total function: `running` falls into the unchanged branch harmlessly (the data layer
never passes it — D20 rejects it earlier).

## File 6 — `src/planner/core/links.py`

Module docstring: link create/remove over the `links` table plus the blocked-derivation
(§3.6, SPEC lines 54–57). Imports: `sqlite3`, `collections.deque`,
`planner.core.contracts` (`EventKind`, `LinkKind`), `planner.core.errors`
(`ErrorCode`, `PlannerError`), `planner.core.events.append_event`. This is core
infrastructure over the DB (like `events.py`) — sqlite3 is allowed here; it is not part
of `dispatch/logic/`.

Module constants:

```python
_CYCLE_CHECKED: Final[frozenset[LinkKind]] = frozenset(
    {LinkKind.blocks, LinkKind.parent_child}
)
# D8 endpoint prefix rules: (allowed from-prefixes, allowed to-prefixes); None = any.
_ENDPOINT_RULES: Final[dict[LinkKind, tuple[frozenset[str] | None, frozenset[str] | None]]] = {
    LinkKind.belongs_to: (frozenset({"t"}), frozenset({"si"})),
    LinkKind.parent_child: (frozenset({"t"}), frozenset({"t"})),
    LinkKind.blocks: (frozenset({"t"}), frozenset({"t", "si"})),
    LinkKind.relates: (None, None),
}
```

Prefix extraction: `entity_id.split("_", 1)[0]` (so `t_x → "t"`, `si_x → "si"`,
`day_2026-07-04 → "day"`).

### `def add_link(conn: sqlite3.Connection, from_id: str, to_id: str, kind: LinkKind, now: int) -> None:`

Steps, in order:

1. **Self-link** (§3.6 line 56): `from_id == to_id` →
   `PlannerError(ErrorCode.link_invalid, "self-links are not allowed", {"from_id": from_id, "to_id": to_id, "kind": kind.value})`.
2. **Endpoint kinds** (D8): look up `_ENDPOINT_RULES[kind]`; if a side's frozenset is not
   None and the id's prefix is not in it → `PlannerError(ErrorCode.link_invalid, ...)`
   with a message naming the expected endpoint types and the same detail keys.
3. **belongs_to uniqueness** (§3.6 line 56): if `kind is LinkKind.belongs_to`, run
   `SELECT 1 FROM links WHERE from_id=? AND kind='belongs_to' LIMIT 1`; a row →
   `PlannerError(ErrorCode.link_invalid, "ticket already has a belongs_to link", {"from_id": from_id})`.
4. **Cycle check** (§3.6 line 56, D10): if `kind in _CYCLE_CHECKED`, BFS from `to_id`:
   `deque([to_id])`, `visited: set[str] = {to_id}`; pop `node`, if `node == from_id` →
   `PlannerError(ErrorCode.link_cycle, "link would create a {kind} cycle", {"from_id": from_id, "to_id": to_id, "kind": kind.value})`;
   else push unvisited rows of
   `SELECT to_id FROM links WHERE from_id=? AND kind=?` (`(node, kind.value)`).
   Adding `from→to` creates a cycle iff `from` is reachable from `to` along same-kind
   edges — this covers the direct 2-cycle (`B→A` exists when adding `A→B`) and arbitrary
   transitive depth. The existing graph is acyclic by induction (every prior insert was
   checked), and the visited set terminates BFS regardless.
5. **Insert**: `INSERT INTO links (from_id, to_id, kind) VALUES (?, ?, ?)` inside
   `try/except sqlite3.IntegrityError as exc:` →
   `raise PlannerError(ErrorCode.link_invalid, "link already exists or violates a link constraint", {"from_id": ..., "to_id": ..., "kind": ...}) from exc`
   (D9 — race backstop for the partial unique index `idx_links_one_belongs_to`, the PK
   triple, and the `from_id <> to_id` CHECK; a raw IntegrityError never escapes).
6. **Event**: `append_event(conn, from_id, EventKind.link_added, {"from_id": from_id, "to_id": to_id, "kind": kind.value}, now)`.

### `def remove_link(conn: sqlite3.Connection, from_id: str, to_id: str, kind: LinkKind, now: int) -> None:`

1. `DELETE FROM links WHERE from_id=? AND to_id=? AND kind=?`.
2. `cursor.rowcount == 0` → `PlannerError(ErrorCode.not_found, "link not found", {...same detail keys})`.
3. `append_event(conn, from_id, EventKind.link_removed, {"from_id": from_id, "to_id": to_id, "kind": kind.value}, now)`.

### Blocked derivation (§3.6 line 57)

```python
def blocked_target_ids(conn: sqlite3.Connection) -> set[str]:
    """Ids (tickets or sprint items) currently blocked: targets of a blocks link
    whose source ticket is not done/dropped."""
```
SQL (exact):
```sql
SELECT DISTINCT l.to_id FROM links l
JOIN tickets src ON src.id = l.from_id
WHERE l.kind = 'blocks' AND src.state NOT IN ('done', 'dropped')
```

```python
def is_blocked(conn: sqlite3.Connection, entity_id: str) -> bool:
```
Same shape with `AND l.to_id = ?` and `SELECT 1 ... LIMIT 1`; returns row-existence.
The join to `tickets` makes the rule literal: only a *ticket* source in an open state
blocks; a missing or non-ticket source never blocks (D8).

## File 7 — `src/planner/dispatch/data.py`

Module docstring: the dispatch data layer over sqlite3 — claims (§7.3), heartbeat,
reclaim, run close + circuit breaker (§7.5), candidate assembly (§7.2 inputs). Config
values (`ttl_seconds`, `failure_limit`) are always parameters; this module never reads
config (same rule as `core/events.py`). All functions take `now: int` — no clock import.

Imports: `json`, `sqlite3`, `collections.abc.Callable`, `typing.NamedTuple` (+ `Final`),
`planner.core.contracts` (`EventKind`, `JsonDict`, `Priority`),
`planner.core.errors` (`ErrorCode`, `PlannerError`),
`planner.core.events.append_event`, `planner.core.ids` (`new_claim`, `new_id`),
`planner.core.links.blocked_target_ids`,
`planner.dispatch.contracts` (`AGENT_CLOSE_OUTCOMES`, `DispatchCandidate`,
`FAILURE_STATUSES`, `RunStatus`),
`planner.dispatch.logic` (`expiry_at`, `gating_field_pending`, `has_active_claim`,
`is_expired`, `next_breaker_state`),
`planner.tickets.contracts` (`AtCap`, `TicketState`).

Module constant: `_CLOSABLE: Final[frozenset[RunStatus]] = AGENT_CLOSE_OUTCOMES | FAILURE_STATUSES` (D20).

The connection is autocommit (`isolation_level=None`, set by `core.db.connect`); no
explicit BEGIN anywhere. Single-winner guarantees come from the two CAS statements
(claim CAS on `tickets.claim_lock`, finalize CAS on `runs.status`), each a single atomic
UPDATE. Follow-up statements run only on the CAS winner's path, so they cannot double-run.

### `def claim(conn, ticket_id: str, now: int, ttl_seconds: int, pid: int | None = None) -> tuple[str, str] | None:`

(Full annotation: `conn: sqlite3.Connection` here and in every function below.)

1. `token = new_claim()`; `expires = expiry_at(now, ttl_seconds)`.
2. The CAS — **literally this statement, §7.3 SPEC line 153, no additions**:
   ```sql
   UPDATE tickets SET claim_lock=?, claim_expires=? WHERE id=? AND claim_lock IS NULL
   ```
   params `(token, expires, ticket_id)`. `cursor.rowcount == 0` → `return None` (lost —
   or unknown ticket id, D13). No SELECT-then-UPDATE, no Python-side locking.
3. `run_id = new_id("run")`; insert the runs row (§7.3):
   ```sql
   INSERT INTO runs (id, ticket_id, status, started_at, ended_at, summary, error, pid)
   VALUES (?, ?, 'running', ?, NULL, NULL, NULL, ?)
   ```
   params `(run_id, ticket_id, now, pid)`.
4. `append_event(conn, ticket_id, EventKind.run_started, {"run_id": run_id, "pid": pid}, now)`.
5. `return run_id, token`.

No eligibility re-check, no `updated_at` (D7, D13).

### `def heartbeat(conn, run_id: str, now: int, ttl_seconds: int) -> int:`

1. `SELECT ticket_id, status FROM runs WHERE id=?` — no row →
   `PlannerError(ErrorCode.not_found, "run not found", {"run_id": run_id})`.
2. `status != RunStatus.running.value` →
   `PlannerError(ErrorCode.stale_claim, "run is not running", {"run_id": run_id, "run_status": <status>})` (D3).
3. `SELECT claim_lock, claim_expires FROM tickets WHERE id=?` — if
   `not has_active_claim(claim_lock, claim_expires, now)` →
   `PlannerError(ErrorCode.stale_claim, "claim expired or cleared", {"ticket_id": <ticket_id>, "claim_expires": <claim_expires>})` (D1/D3 — no revival of an expired lease).
4. `new_expires = expiry_at(now, ttl_seconds)` (D2 — exactly now + one TTL);
   ```sql
   UPDATE tickets SET claim_expires=? WHERE id=?
   ```
5. `append_event(conn, ticket_id, EventKind.claim_heartbeat, {"run_id": run_id, "claim_expires": new_expires}, now)`.
6. `return new_expires`.

### Private finalizer (D5) — the single door for run status + breaker

```python
class _Finalized(NamedTuple):
    ticket_id: str
    consecutive_failures: int
    newly_auto_blocked: bool

def _finalize_run(
    conn: sqlite3.Connection, run_id: str, status: RunStatus, now: int,
    summary: str | None, error: str | None, failure_limit: int,
) -> _Finalized | None:
```

1. `SELECT ticket_id FROM runs WHERE id=?` — no row → `return None`.
2. The finalize CAS:
   ```sql
   UPDATE runs SET status=?, ended_at=?, summary=?, error=? WHERE id=? AND status='running'
   ```
   params `(status.value, now, summary, error, run_id)`. `rowcount == 0` → `return None`
   (the run already closed or was reclaimed — the caller decides what that means).
3. `SELECT consecutive_failures, auto_blocked FROM tickets WHERE id=?` (ticket_id).
4. `new_failures, tripped = next_breaker_state(current, status, failure_limit)`;
   `was_blocked = bool(row["auto_blocked"])`;
   `newly = tripped and not was_blocked`;
   `flag = 1 if (was_blocked or tripped) else 0` (sticky — never written back to 0 here).
5. Clear the lease and apply the breaker in one statement (no `updated_at`, D7):
   ```sql
   UPDATE tickets SET claim_lock=NULL, claim_expires=NULL, consecutive_failures=?, auto_blocked=? WHERE id=?
   ```
6. `return _Finalized(ticket_id, new_failures, newly)`.

Safety of the unconditional lock clear: while a run is `running`, the ticket's lock is
that run's claim (a new claim requires `claim_lock IS NULL`, which requires this run to
have been finalized first — and the step-2 CAS just established we are the finalizer).
So step 5 can never clear a *newer* claim.

### `def close_run(conn, run_id: str, status: RunStatus, now: int, failure_limit: int, summary: str | None = None, error: str | None = None) -> None:`

1. `status not in _CLOSABLE` →
   `PlannerError(ErrorCode.validation, "not a closeable run status", {"status": status.value})` (D20).
2. `SELECT status FROM runs WHERE id=?` — no row →
   `PlannerError(ErrorCode.not_found, "run not found", {"run_id": run_id})`.
3. `fin = _finalize_run(conn, run_id, status, now, summary, error, failure_limit)`;
   `fin is None` → `PlannerError(ErrorCode.stale_claim, "run is not running", {"run_id": run_id, "run_status": <status from step 2>})` (D4 — covers close-after-reclaim).
4. `append_event(conn, fin.ticket_id, EventKind.run_closed, {"run_id": run_id, "status": status.value, "summary": summary}, now)`.
5. If `fin.newly_auto_blocked`:
   `append_event(conn, fin.ticket_id, EventKind.auto_blocked, {"consecutive_failures": fin.consecutive_failures}, now)` — in this order, after `run_closed`.

### `def sweep_reclaims(conn, now: int, pid_alive: Callable[[int], bool], failure_limit: int) -> list[str]:`

Stage 4's tick step (1) primitive. Returns the reclaimed run ids.

1. Scan:
   ```sql
   SELECT r.id AS run_id, r.pid, r.ticket_id, t.claim_expires
   FROM runs r JOIN tickets t ON t.id = r.ticket_id
   WHERE r.status = 'running' AND t.claim_lock IS NOT NULL
   ```
2. Per row, decide the reason (D14 — expiry first):
   - `is_expired(claim_expires, now)` → `reason = "expired"` (fires even when the PID is alive);
   - `elif pid is not None and not pid_alive(pid)` → `reason = "dead_pid"`;
   - else `continue`.
3. `fin = _finalize_run(conn, run_id, RunStatus.reclaimed, now, None, None, failure_limit)`;
   `fin is None` → `continue` silently (lost the CAS to a legitimate close, D5).
   `reclaimed` leaves the breaker unchanged (D6) and `newly_auto_blocked` is always
   False on this path; summary/error stay NULL; `ended_at = now`; lock + expiry cleared.
4. `append_event(conn, ticket_id, EventKind.claim_reclaimed, {"run_id": run_id, "reason": reason}, now)`.
5. Collect `run_id`; return the list.

### `def clear_auto_block(conn, ticket_id: str, now: int) -> None:`

Human clear-action (§7.5 line 163: "resets both flag and counter").

1. `SELECT 1 FROM tickets WHERE id=?` — no row → `PlannerError(ErrorCode.not_found, "ticket not found", {"ticket_id": ticket_id})`.
2. ```sql
   UPDATE tickets SET auto_blocked=0, consecutive_failures=0, updated_at=? WHERE id=?
   ```
   (`updated_at` bumped — human action, D7.) Idempotent: no guard on the current flag.
3. `append_event(conn, ticket_id, EventKind.auto_block_cleared, {}, now)` — payload `{}`
   (the EventKind comment specifies no keys).

### `def load_candidates(conn, now: int) -> list[DispatchCandidate]:`

Assembles the pure input rows (contract: "Assembled by the data layer"). Loads **all**
tickets — filtering is exclusively `logic.is_eligible`'s job (no `WHERE state NOT IN`
duplication of rules in SQL).

1. `blocked = blocked_target_ids(conn)`.
2. ```sql
   SELECT id, state, priority, deadline, created_at, ceiling, at_cap,
          auto_blocked, claim_lock, claim_expires, fields
   FROM tickets
   ```
3. Per row, with explicit coercions (sqlite3.Row indexing yields Any; coerce immediately
   so no Any leaks into the dataclass):
   ```python
   state = TicketState(str(row["state"]))
   raw_deadline = row["deadline"]
   fields_json: JsonDict = json.loads(str(row["fields"]))
   claim_lock = str(row["claim_lock"]) if row["claim_lock"] is not None else None
   claim_expires = int(row["claim_expires"]) if row["claim_expires"] is not None else None
   DispatchCandidate(
       ticket_id=str(row["id"]),
       state=state,
       priority=Priority(str(row["priority"])),
       deadline=str(raw_deadline) if raw_deadline is not None else None,
       created_at=int(row["created_at"]),
       ceiling=TicketState(str(row["ceiling"])),
       at_cap=AtCap(str(row["at_cap"])),
       auto_blocked=bool(row["auto_blocked"]),
       has_active_claim=has_active_claim(claim_lock, claim_expires, now),
       is_blocked=str(row["id"]) in blocked,
       gating_pending=gating_field_pending(state, fields_json),
   )
   ```
4. Return the list (order unspecified; `logic.eligible_ordered` sorts).

### Event kinds emitted per operation (summary)

| operation | event kind | entity_id | payload keys |
|---|---|---|---|
| `add_link` | `link_added` | from_id | `from_id`, `to_id`, `kind` |
| `remove_link` | `link_removed` | from_id | `from_id`, `to_id`, `kind` |
| `claim` (win) | `run_started` | ticket_id | `run_id`, `pid` |
| `heartbeat` | `claim_heartbeat` | ticket_id | `run_id`, `claim_expires` |
| `sweep_reclaims` (per reclaim) | `claim_reclaimed` | ticket_id | `run_id`, `reason` (`"expired"` \| `"dead_pid"`) |
| `close_run` | `run_closed` | ticket_id | `run_id`, `status`, `summary` |
| `close_run` (0→1 trip only) | `auto_blocked` | ticket_id | `consecutive_failures` |
| `clear_auto_block` | `auto_block_cleared` | ticket_id | (empty) |

All through `core.events.append_event` — no other events-table access.

---

## SPEC mapping (behavior → line)

| behavior | SPEC |
|---|---|
| links table shape, four kinds | line 55 |
| one belongs_to per ticket; self-links rejected; blocks/parent_child transitive cycle check at write time | line 56 |
| blocked iff target of `blocks` from a not-done/dropped ticket; blocked ⇒ dispatch-ineligible | line 57 |
| eligibility conjunction + (a)/(b) branches + at-ceiling+stop never eligible; ordering priority/deadline-NULLs-last/created_at | line 149 |
| claim CAS statement verbatim; rowcount 0 = lost; TTL 15 min; runs row per claim with the six columns | line 153 |
| heartbeat extends by one TTL; expired claim or dead PID → run `reclaimed`, lock cleared | line 154 (+ brief: "exactly one TTL **from now**") |
| breaker increments (crashed/timed_out/spawn_failed), done resets, limit 2 → sticky auto_blocked + event, never picked up, human clear resets flag AND counter | line 163 |
| `claim_ttl_seconds=900`, `failure_limit=2` config defaults (passed as parameters) | line 219 |
| one canonical writer per transition; pure logic dependency-free; append-only events | lines 224, 227, 229 |
| test items 9 / 11 / 14 / 15 / 16 | lines 286, 288, 291, 292, 293 |

## Edge-case register (each is implemented and/or tested as noted)

1. PID alive but claim expired → reclaimed, reason `"expired"` (D14; tested, a15).
2. PID dead, claim unexpired → reclaimed, reason `"dead_pid"` (tested, a15).
3. `pid IS NULL` → never dead-PID-reclaimed, only expiry (D14; exercised implicitly).
4. Close on an already-reclaimed run → `stale_claim`, run stays `reclaimed`, breaker
   untouched (D4/D5; tested, a15).
5. Reclaim racing a close → `runs.status='running'` CAS arbitrates; sweep skips silently,
   close raises (D5; race untestable single-threaded, guard asserted via edge 4).
6. Heartbeat at/after expiry → `stale_claim`, expiry unchanged — no lease revival
   (D1/D3; tested, a15).
7. Heartbeat on missing run → `not_found`; on a closed run → `stale_claim` (D3).
8. Exact-boundary semantics: `now == claim_expires` is expired; `now == claim_expires - 1`
   is active (D1; tested, a15 boundary test).
9. Second `belongs_to` → `link_invalid` via pre-check; concurrent race caught by the
   partial unique index `idx_links_one_belongs_to` → IntegrityError → `link_invalid` (D9).
10. Duplicate identical link triple → `link_invalid` (D9; tested, a09).
11. Cycle transitivity at depth ≥ 2 (3-node cycle) and direct 2-cycles both rejected;
    BFS visited-set terminates on any graph (D10; tested, a09).
12. `blocks` targeting a sprint item: target has no outgoing `blocks` edges, BFS just
    terminates; derivation set includes si targets harmlessly.
13. Claim CAS on unknown ticket id → `None` (D13).
14. `auto_blocked` event only on the flag's 0→1 transition; flag is sticky across later
    closes (D6; tested, a16).
15. `done` close when counter already 0 → stays 0, no event (D6).
16. `blocked` and `reclaimed` closes leave the counter unchanged (D6; tested, a16/a15).
17. `gating_field_pending` on `needs_review`/`done`/`dropped` → False (no gating field);
    eligibility already excluded those states before `ADVANCE_TARGET` is consulted.
18. State past the ceiling (human jumped it) → ineligible: branch (a) fails and
    "at its ceiling" means strict equality (tested, a11 matrix).
19. NULL deadline never beats a dated deadline within a priority band, but P0-undated
    beats P1-dated — priority dominates (tested, a11).

---

## File 8 — `tests/unit/test_dispatch.py`

Uses fixtures `cfg` and `tmp_db` from the shared conftest (read-only); `fake_clock` is
not needed (D17). Times are literal ints, baseline `T0 = 1_000_000`. Tickets are inserted
via direct SQL only (the tickets domain writers are being built concurrently — no import
of any `planner.tickets` module except `planner.tickets.contracts`). No `pytest.skip`/
`xfail`/empty bodies anywhere.

Imports: `json`, `sqlite3`, `pytest`, `pathlib.Path`,
`planner.core.db` (`connect`, `create_schema`), `planner.core.links as links`,
`planner.core.errors` (`ErrorCode`, `PlannerError`),
`planner.core.contracts` (`EventKind`, `LinkKind`, `Priority`),
`planner.dispatch import data`... (as `from planner.dispatch import data`),
`planner.dispatch.contracts` (`DispatchCandidate`, `RunStatus`),
`planner.dispatch.logic` (`eligible_ordered`, `is_eligible`, `next_breaker_state`, `ordering_key`),
`planner.tickets.contracts` (`AtCap`, `TicketState`), `planner.core.config.Config` for
type hints as needed.

Module helpers (plain functions, no fixtures):

```python
def _insert_ticket(conn, ticket_id, *, state="needs_success", priority="P3",
                   deadline=None, ceiling="needs_success", at_cap="propose",
                   created_at=T0, fields=None) -> None
```
Executes
`INSERT INTO tickets (id, title, state, priority, deadline, ceiling, at_cap, fields, created_at, updated_at) VALUES (?,?,?,?,?,?,?,COALESCE(?, <DDL default via omission>),?,?)`
— implement as two variants or build the column list dynamically; when `fields is None`
omit the column so the DDL default applies, else pass `json.dumps(fields)`. Title:
`f"ticket {ticket_id}"`.

```python
def _mk_candidate(ticket_id="t_x", *, state=TicketState.needs_success,
                  priority=Priority.P3, deadline=None, created_at=T0,
                  ceiling=TicketState.needs_success, at_cap=AtCap.propose,
                  auto_blocked=False, has_active_claim=False, is_blocked=False,
                  gating_pending=False) -> DispatchCandidate
```

```python
def _events(conn, entity_id, kind: EventKind) -> list[dict]  # payloads, parsed, id-ordered
def _ticket_row(conn, ticket_id) -> sqlite3.Row              # SELECT * FROM tickets WHERE id=?
def _run_row(conn, run_id) -> sqlite3.Row                    # SELECT * FROM runs WHERE id=?
def _candidate(conn, ticket_id, now) -> DispatchCandidate    # from data.load_candidates
```

`PENDING_FIELDS(field)` helper: returns the four-key fields dict with
`{"value": None, "proposal": {"body": "x", "proposed_by": "agent", "created_at": T0}, "notes": None}`
on the named field and empty slots elsewhere.

### Item 9 — blocking and links (SPEC line 286)

**`test_a09_blocked_by_open_ticket_ineligible(tmp_db)`**
Insert `t_blk` (state `in_progress`) and `t_tgt` (defaults: state `needs_success`,
ceiling `needs_success`, at_cap `propose` — eligible baseline via branch (b)).
`links.add_link(tmp_db, "t_blk", "t_tgt", LinkKind.blocks, T0)`.
Assert: `_candidate(tmp_db, "t_tgt", T0).is_blocked is True`;
`is_eligible(candidate) is False`; and the blocker's own candidate has
`is_blocked is False`.

**`test_a09_blocker_done_restores_eligibility(tmp_db)`**
Same setup; then `UPDATE tickets SET state='done' WHERE id='t_blk'` (direct SQL).
Assert: `is_blocked is False`, `is_eligible(...) is True`. Then a third pair with the
blocker set to `'dropped'` (fresh ids `t_blk2`/`t_tgt2`): also unblocked — locks the
"not done **or dropped**" wording of line 57.

**`test_a09_blocks_cycle_rejected(tmp_db)`**
Insert `t_a`, `t_b`, `t_c`. `add_link(a→b, blocks)`; `add_link(b→a, blocks)` must raise:
`pytest.raises(PlannerError)`, assert `exc.value.code is ErrorCode.link_cycle` **and**
`exc.value.code.value == "link_cycle"` (fence: exact error code). Then `add_link(b→c,
blocks)` succeeds and `add_link(c→a, blocks)` raises `link_cycle` (transitive, depth 2).
Assert `SELECT COUNT(*) FROM links WHERE kind='blocks'` == 2 (rejections inserted nothing).

**`test_a09_parent_child_cycle_rejected(tmp_db)`**
Same shape with `LinkKind.parent_child` over `t_p`, `t_q`, `t_r`: direct 2-cycle and
3-node transitive cycle both raise `link_cycle`; count of parent_child links == 2.

**`test_a09_self_link_second_belongs_to_and_duplicate_rejected(tmp_db)`**
(a) `add_link("t_a", "t_a", LinkKind.relates)` → `PlannerError` with
`code is ErrorCode.link_invalid`.
(b) `add_link("t_a", "si_one", LinkKind.belongs_to)` succeeds;
`add_link("t_a", "si_two", LinkKind.belongs_to)` → `link_invalid`; assert
`SELECT COUNT(*) FROM links WHERE from_id='t_a' AND kind='belongs_to'` == 1 and the
surviving `to_id` is `"si_one"`.
(c) repeat `add_link("t_a", "si_one", LinkKind.belongs_to)` (identical triple) →
`link_invalid`.
(No sprint_items rows needed — links has no FKs; endpoint checks are prefix-based.)

**`test_a09_link_events_and_remove(tmp_db)`**
`add_link("t_a", "t_b", LinkKind.relates, T0)` then
`links.remove_link(tmp_db, "t_a", "t_b", LinkKind.relates, T0 + 1)`.
Assert `_events(tmp_db, "t_a", EventKind.link_added)` has exactly one payload equal to
`{"from_id": "t_a", "to_id": "t_b", "kind": "relates"}`; same for `link_removed`; the
links row is gone; a second `remove_link` of the same triple raises `PlannerError` with
`code is ErrorCode.not_found`.

### Item 11 — eligibility gates and ordering (SPEC line 288)

**`test_a11_five_eligible_tickets_ordering()`** (pure — no DB)
Five candidates via `_mk_candidate` (all eligible: defaults, at-ceiling + propose):
- `c_p0_nodate`: P0, deadline None, created 200
- `c_p1_early`: P1, deadline `"2026-07-08"`, created 300
- `c_p1_late`: P1, deadline `"2026-07-10"`, created 100
- `c_p1_none_a`: P1, deadline None, created 50
- `c_p1_none_b`: P1, deadline None, created 60

Assert `all(is_eligible(c) for c in ...)` (they are the fence's "five eligible tickets"),
then `[c.ticket_id for c in eligible_ordered([c_p1_late, c_p1_none_b, c_p0_nodate,
c_p1_none_a, c_p1_early])] == ["c_p0_nodate", "c_p1_early", "c_p1_late", "c_p1_none_a",
"c_p1_none_b"]` — exact order proving: P0 before P1 (even undated-P0 over dated-P1);
equal priority by earlier deadline; NULL deadline last; then created_at ascending
(300-created dated ticket still beats 50-created undated one).

**`test_a11_eligibility_gates()`** (pure matrix over `_mk_candidate`, §7.2 line 149)
Assert `is_eligible` returns exactly:
- base (state `needs_success`, ceiling `needs_plan`, at_cap `propose`) → True (branch (a))
- same but `at_cap=stop`, still below ceiling → True (at_cap irrelevant below ceiling)
- `state=needs_review` → False; `state=done` → False; `state=dropped` → False
- base with `gating_pending=True` → False
- base with `is_blocked=True` → False
- base with `auto_blocked=True` → False
- base with `has_active_claim=True` → False
- `state=needs_plan, ceiling=needs_plan, at_cap=propose` → True (branch (b))
- `state=needs_plan, ceiling=needs_plan, at_cap=stop` → False (at-ceiling+stop never eligible)
- `state=in_progress, ceiling=needs_success, at_cap=propose` → False (past ceiling ≠ at ceiling)
- `state=in_progress, ceiling=done` → True (advance target `needs_review` ≤ `done`)

**`test_a11_gating_pending_assembly(tmp_db)`** (DB assembly of the §7.2 input)
Insert `t_gate` at state `needs_approach`, ceiling `in_progress`, with
`fields=PENDING_FIELDS("approach")` (pending proposal on the gating field): candidate has
`gating_pending is True`, `is_eligible` False. Insert `t_free` at state `needs_approach`,
ceiling `in_progress`, `fields=PENDING_FIELDS("plan")` (pending on a non-gating field):
`gating_pending is False`, `is_eligible` True.

### Item 14 — claim CAS (SPEC line 291)

**`test_a14_concurrent_claim_exactly_one_winner(tmp_path, cfg)`**
Build two real connections to one DB file (D18):
`db = tmp_path / "cas.db"`; `conn1 = connect(str(db))`; `create_schema(conn1)`;
`conn2 = connect(str(db))`. Insert `t_race` via `conn1`. Assert
`cfg.claim_ttl_seconds == 900` (SPEC §13 default). Then interleave the two connections
through the real CAS: `won = data.claim(conn1, "t_race", T0, cfg.claim_ttl_seconds,
pid=111)` and `lost = data.claim(conn2, "t_race", T0, cfg.claim_ttl_seconds, pid=222)`.
Assert: `won is not None` and `lost is None`; unpack `run_id, token = won`;
`SELECT COUNT(*) FROM runs WHERE ticket_id='t_race'` == 1 (exactly one runs row, fence);
the runs row has `status='running'`, `started_at=T0`, `ended_at IS NULL`, `pid=111`;
ticket row has `claim_lock == token` (the winner's token, not overwritten) and
`claim_expires == T0 + 900`; exactly one `run_started` event with payload
`{"run_id": run_id, "pid": 111}`. Close both connections at the end. The CAS UPDATE is
the arbiter — the loser's rowcount-0 comes from the real
`WHERE id=? AND claim_lock IS NULL` predicate, no Python-side locking anywhere.

### Item 15 — TTL, reclaim, heartbeat (SPEC line 292)

**`test_a15_expired_claim_reclaimed_and_eligible_again(tmp_db, cfg)`**
Insert eligible `t_exp`. `run_id, token = data.claim(tmp_db, "t_exp", T0, 900, pid=333)`.
Pre-check: `_candidate(tmp_db, "t_exp", T0).has_active_claim is True` and ineligible.
`reclaimed = data.sweep_reclaims(tmp_db, T0 + 901, pid_alive=lambda p: True,
failure_limit=cfg.failure_limit)` — PID reported alive, so this also proves the
"PID alive but claim expired" edge reclaims with reason `"expired"`.
Assert: `reclaimed == [run_id]`; run row `status == "reclaimed"`, `ended_at == T0 + 901`,
`summary IS NULL`; ticket `claim_lock IS NULL` and `claim_expires IS NULL` (lock cleared,
fence); `consecutive_failures == 0` (reclaim never touches the breaker);
one `claim_reclaimed` event, payload `{"run_id": run_id, "reason": "expired"}`;
`is_eligible(_candidate(tmp_db, "t_exp", T0 + 901)) is True` (eligible again, fence).

**`test_a15_expiry_boundary_exact(tmp_db)`**
Claim `t_bnd` at `T0` with ttl 900. Sweep at `T0 + 899` (pid_alive True) → returns `[]`,
run still `running`, `_candidate(..., T0 + 899).has_active_claim is True`. Sweep at
`T0 + 900` → returns `[run_id]` (D1: at exactly `claim_expires` the lease is dead).

**`test_a15_dead_pid_reclaimed(tmp_db, cfg)`**
Claim `t_dead` at `T0`, ttl 900, pid 444. Sweep at `T0 + 10` (unexpired) with
`pid_alive=lambda p: False` → `[run_id]`; run `reclaimed`; `claim_reclaimed` payload
`{"run_id": run_id, "reason": "dead_pid"}`; lock cleared.

**`test_a15_heartbeat_extends_exactly_one_ttl_from_now(tmp_db, cfg)`**
Claim `t_hb` at `T0`, ttl `cfg.claim_ttl_seconds` (900) → `claim_expires == T0 + 900`.
`new = data.heartbeat(tmp_db, run_id, T0 + 300, cfg.claim_ttl_seconds)`.
Assert `new == T0 + 1200` and ticket `claim_expires == T0 + 300 + 900` **exactly** —
i.e. one TTL from *now*, and specifically **not** `T0 + 900 + 900` (assert
`!= T0 + 1800` as the explicit anti-reading); `claim_heartbeat` event payload
`{"run_id": run_id, "claim_expires": T0 + 1200}`.

**`test_a15_heartbeat_on_expired_claim_rejected(tmp_db)`**
Claim `t_late` at `T0`, ttl 900. `data.heartbeat(tmp_db, run_id, T0 + 900, 900)` raises
`PlannerError` with `code is ErrorCode.stale_claim`; ticket `claim_expires` still
`T0 + 900` (no revival); no `claim_heartbeat` event.

**`test_a15_close_after_reclaim_rejected(tmp_db, cfg)`**
Claim `t_gone` at `T0`; sweep at `T0 + 901` reclaims it. Then
`data.close_run(tmp_db, run_id, RunStatus.done, T0 + 950, cfg.failure_limit)` raises
`PlannerError` with `code is ErrorCode.stale_claim` and detail `run_status == "reclaimed"`;
run row still `status == "reclaimed"` with `ended_at == T0 + 901`;
`consecutive_failures` still 0; no `run_closed` event exists for the ticket.

### Item 16 — circuit breaker (SPEC line 293)

**`test_a16_two_crashed_runs_auto_block_and_ineligible(tmp_db, cfg)`**
Assert `cfg.failure_limit == 2` (SPEC §13/§7.5 default). Insert eligible `t_cb`.
Run 1: `run1, _ = data.claim(tmp_db, "t_cb", T0, 900)`;
`data.close_run(tmp_db, run1, RunStatus.crashed, T0 + 10, cfg.failure_limit,
error="boom")`. Assert: ticket `consecutive_failures == 1`, `auto_blocked == 0`,
`claim_lock IS NULL` (claimable again); run1 row `status == "crashed"`,
`ended_at == T0 + 10`, `error == "boom"`; `run_closed` event payload
`{"run_id": run1, "status": "crashed", "summary": None}`; no `auto_blocked` event yet.
Run 2: `run2, _ = data.claim(tmp_db, "t_cb", T0 + 20, 900)`;
`close_run(run2, crashed, T0 + 30, cfg.failure_limit)`. Assert:
`consecutive_failures == 2`, `auto_blocked == 1`; exactly one `auto_blocked` event with
payload `{"consecutive_failures": 2}`; candidate at `T0 + 40` has `auto_blocked is True`
and `is_eligible(...) is False` (fence: two consecutive crashed → auto_blocked, ineligible).

**`test_a16_unblock_clears_flag_and_counter(tmp_db, cfg)`**
Drive `t_ub` to auto_blocked (two crashed closes as above). Then
`data.clear_auto_block(tmp_db, "t_ub", T0 + 100)`. Assert: `auto_blocked == 0` **and**
`consecutive_failures == 0` (fence: clears flag and counter); one `auto_block_cleared`
event with payload `{}`; `is_eligible(_candidate(tmp_db, "t_ub", T0 + 100)) is True`.

**`test_a16_done_run_resets_counter(tmp_db, cfg)`**
Insert `t_rst`. One crashed close → counter 1. Then claim + 
`close_run(run2, RunStatus.done, T0 + 40, cfg.failure_limit, summary="ok")` → assert
`consecutive_failures == 0`, `auto_blocked == 0` (fence: a done run resets the counter);
`run_closed` payload `{"run_id": run2, "status": "done", "summary": "ok"}`. Then one more
crashed close → counter is 1, not 2, and no `auto_blocked` event ever fired (proves the
reset was real, not cosmetic).

**`test_a16_blocked_outcome_leaves_counter(tmp_db, cfg)`**
Insert `t_neu`. Crashed close → counter 1. Claim again; `close_run(run2,
RunStatus.blocked, ...)` → counter still 1, `auto_blocked == 0`, no `auto_blocked` event
(§7.5 enumerates increment/reset exhaustively — `blocked` is neither).

**`test_a16_breaker_pure_matrix()`** (pure)
`next_breaker_state` exact returns:
`(0, crashed, 2) → (1, False)`; `(1, crashed, 2) → (2, True)`;
`(1, timed_out, 2) → (2, True)`; `(1, spawn_failed, 2) → (2, True)`;
`(1, done, 2) → (0, False)`; `(1, blocked, 2) → (1, False)`;
`(1, reclaimed, 2) → (1, False)`; `(0, crashed, 1) → (1, True)` (limit parameter honored).

Test count: 6 (a09) + 3 (a11) + 1 (a14) + 6 (a15) + 5 (a16) = 21 test functions, every
name carrying its item number, every fence value asserted exactly (states as exact
strings/enums, the full five-ticket order, error codes `link_cycle`/`link_invalid`/
`stale_claim`/`not_found` via `ErrorCode` identity).

---

## Lint / typing notes (mypy strict on src/, ruff E,F,W,I,UP,B, line 100)

- Full annotations on every function including `conn: sqlite3.Connection` and `-> None`.
- `Callable` from `collections.abc` (UP035); `Final` from `typing` for module constants.
- Coerce every `sqlite3.Row` access immediately (`str(...)`, `int(...)`, `bool(...)`,
  enum constructors) — no Any flows into dataclass fields or return values. Annotate
  `json.loads` results as `JsonDict` at the assignment.
- `logic/` modules import only stdlib + `planner.*.contracts` (+ sibling logic modules).
  Nothing in `logic/` imports `sqlite3`, `planner.core.db`, `planner.core.events`,
  `planner.core.config`, or FastAPI/pydantic. `data.py` and `links.py` import logic,
  never the reverse.
- `data.py` and `links.py` never import `planner.core.config` or `planner.core.clock`
  (`ttl_seconds`, `failure_limit`, `now` are parameters — config-free like `core/events.py`).
- Exception chaining: `raise PlannerError(...) from exc` inside the IntegrityError
  handler (B904).
- Keyword-only test-helper params after `*` avoid B006-style pitfalls (no mutable defaults).
- Module docstrings in the style of the existing core modules; comments explain "why"
  (e.g. the D1 boundary rationale at the `is_expired` definition).

## Definition of done

`./verify` run fresh: ruff clean, mypy strict clean on src/, all `test_a09_*`,
`test_a11_*`, `test_a14_*`, `test_a15_*`, `test_a16_*` green through the instrument
(items 9, 11, 14, 15, 16 PASS on the scoreboard), no skip-scan violations, and the Codex
implementation review reports no violations on: CAS correctness under interleaving, TTL
arithmetic (heartbeat = now + one TTL), the reclaim path (status `reclaimed` + lock
cleared + re-eligible), and the absence of any code path letting an expired claim keep
writing (D1/D3 guards).

---

## BINDING AMENDMENTS (post codex plan review — these override the sections above)

**A1 — `close_run` active-claim guard (supersedes the D4/close_run step list).** An
expired claim must not close its run. `close_run` step order becomes:
1. `status not in _CLOSABLE` → `PlannerError(validation)` (unchanged, D20).
2. `SELECT status, ticket_id FROM runs WHERE id=?` — no row → `not_found`.
3. `status != 'running'` → `stale_claim` with detail `{"run_id": ..., "run_status": <current>}` (D4).
4. **NEW:** `SELECT claim_lock, claim_expires FROM tickets WHERE id=?` (the run's ticket);
   if `not has_active_claim(claim_lock, claim_expires, now)` →
   `PlannerError(ErrorCode.stale_claim, "claim expired or cleared",
   {"ticket_id": ..., "claim_expires": <value>})`. Same predicate as the heartbeat guard —
   at `now == claim_expires` the close is rejected (D1 half-open lease). Nothing is
   written; the run's only exit is then `sweep_reclaims`.
5. `_finalize_run(...)`; `None` → `stale_claim` as before (race arbiter unchanged).
6. Events as before.
`sweep_reclaims` continues to call `_finalize_run` directly — reclaiming an expired claim
is the point, so it must NOT carry this guard. Edge-case register gains: "close at/after
expiry, before sweep → `stale_claim`, run stays `running`, lock untouched, breaker
untouched; sweep then reclaims it."

**New test `test_a15_close_after_expiry_rejected(tmp_db, cfg)`:** claim `t_lc` at `T0`,
ttl 900. `data.close_run(tmp_db, run_id, RunStatus.done, T0 + 900, cfg.failure_limit)`
raises `PlannerError` with `code is ErrorCode.stale_claim`; run row still `running`,
`ended_at IS NULL`; ticket `claim_lock` still the winner's token, `consecutive_failures`
== 0; no `run_closed` event. A close at `T0 + 899` on a *fresh* identical setup succeeds
(boundary check, second ticket `t_lc2`). Then `sweep_reclaims` at `T0 + 901` reclaims
`t_lc`'s run normally. (a15 test count becomes 7; total 22.)

**A2 — `add_link` serializes check+insert (supersedes D11).** Steps 3–6 of `add_link`
(belongs_to pre-check, cycle BFS, INSERT, `link_added` event) execute inside an explicit
transaction when the connection is not already in one:
```python
own_txn = not conn.in_transaction
if own_txn:
    conn.execute("BEGIN IMMEDIATE")
try:
    ...steps 3-6...
except BaseException:
    if own_txn:
        conn.execute("ROLLBACK")
    raise
if own_txn:
    conn.execute("COMMIT")
```
`BEGIN IMMEDIATE` acquires the write lock before the reads, so two connections cannot
interleave BFS-then-insert to admit a cycle or a second `belongs_to`; contention resolves
via the `busy_timeout` set in `core.db.connect`. Steps 1–2 (self-link, endpoint prefix —
pure checks) stay outside the transaction. `remove_link` unchanged (autocommit). D11's
"accepted race" text is void.

**A3 — `eligible_ordered` is dropped (supersedes parts of File 4 / D16 test wiring).**
Pure-logic modules import stdlib + contracts ONLY — no logic module imports a sibling
logic module. Concretely: `ordering.py` contains `ordering_key` only (imports:
`planner.core.contracts.Priority`, `planner.dispatch.contracts.DispatchCandidate`,
`typing.Final`); the `eligible_ordered` function is deleted from the plan; callers
compose `sorted((c for c in cands if is_eligible(c)), key=ordering_key)` themselves.
`logic/__init__.py` keeps its facade re-exports (established repo convention:
`sprints/logic/__init__.py`, `days/logic`, `seed/logic`) minus `eligible_ordered`:
`__all__` = `expiry_at`, `gating_field_pending`, `has_active_claim`, `is_eligible`,
`is_expired`, `next_breaker_state`, `ordering_key`. The item-11 ordering test sorts
explicitly: `ordered = sorted((c for c in shuffled if is_eligible(c)), key=ordering_key)`
and asserts the exact five-id list. Any other planned use of `eligible_ordered` (e.g. in
`_candidate`-based eligibility assertions) uses `is_eligible` + `sorted(...)` inline.

**A5 — Exactly ONE anchored test per item (orchestrator sense-check of the verify
scorer; supersedes D16 and the test naming throughout).** `scripts/verify_lib.py::score`
passes an item iff EXACTLY ONE collected test's bare name starts with `test_aNN_`;
multiple anchored matches FAIL the item. Therefore:
- Per item, exactly one test carries the anchor and covers that item's FULL §18.3 fence:
  - `test_a09_blocking_and_cycle` — blocked-by-open ineligible; blocker → `done` eligible
    again; `blocks` cycle rejected (direct AND transitive, `link_cycle` envelope per A4);
    rejected inserts leave no rows.
  - `test_a11_dispatch_ordering_five_eligible` — the exact five-id order (all five
    asserted eligible first).
  - `test_a14_claim_cas_exactly_one_winner` — unchanged.
  - `test_a15_ttl_reclaim_and_heartbeat` — expired claim reclaimed (run `reclaimed`,
    lock+expiry cleared, eligible again, `claim_reclaimed` event) AND heartbeat extends
    expiry to exactly now + one TTL (with the `!= T0+1800` anti-assertion); may use two
    tickets inside the one test.
  - `test_a16_circuit_breaker` — two consecutive `crashed` → counter 2, `auto_blocked`,
    one `auto_blocked` event, ineligible; `clear_auto_block` resets flag AND counter,
    eligible again; and a `done` close resets the counter (separate ticket in the same
    test; subsequent crash yields 1, no `auto_blocked` event).
- Every other planned test keeps its assertions but is renamed WITHOUT the anchor
  (must not start with any `test_aNN_`): `test_links_parent_child_cycle_rejected`,
  `test_links_self_link_second_belongs_to_duplicate_rejected`,
  `test_links_events_and_remove`, `test_links_blocker_dropped_unblocks`,
  `test_eligibility_gate_matrix`, `test_eligibility_gating_pending_assembly`,
  `test_claims_expiry_boundary_exact`, `test_claims_dead_pid_reclaimed`,
  `test_claims_heartbeat_on_expired_rejected`, `test_claims_close_after_reclaim_rejected`,
  `test_claims_close_after_expiry_rejected`, `test_breaker_blocked_outcome_leaves_counter`,
  `test_breaker_pure_matrix`.
- No assertion is dropped in the consolidation; the anchored tests may share setup
  helpers with the supplementary ones.

**A4 — Error assertions pin the full envelope (strengthens every error test).** Wherever
the test list says "raises `PlannerError` with `code is ErrorCode.X`", the test also
asserts, via `exc.value.to_payload()`: the top-level payload is exactly
`{"error": {...}}`; the inner dict has exactly the keys `{"code", "message", "detail"}`;
`payload["error"]["code"] == "<exact string>"` (`"link_cycle"`, `"link_invalid"`,
`"stale_claim"`, `"not_found"`); and `detail` contains the plan-specified keys for that
error site (link errors: `from_id`, `to_id`, `kind` — belongs_to-uniqueness: at least
`from_id`; close-after-reclaim: `run_id`, `run_status` with `run_status == "reclaimed"`;
close/heartbeat-after-expiry: `ticket_id`, `claim_expires`; remove-missing-link and
unknown-run/ticket `not_found`: the identifying key). `message` is asserted to be a
non-empty string only — prose is not SPEC-fixed.
