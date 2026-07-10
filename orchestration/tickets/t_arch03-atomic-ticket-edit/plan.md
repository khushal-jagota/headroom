# t_arch03 implementation plan — atomic ordinary Ticket edit

## Current behavior that must be changed or preserved

`PATCH /api/tickets/{ticket_id}` currently performs the following work in
`tickets/api.py`:

1. It rejects the first request key outside `title`, `user_note`, `priority`,
   `deadline`, `project`, `project_id`, and `sprint_id` with
   `validation / unknown ticket field`, and rejects an empty object with
   `validation / no ticket fields to update`.
2. Before parsing values, `reject_agent_fields` rejects `title`, `user_note`,
   `project`, or `project_id` for any attributed non-Chief actor. It permits those
   fields for an unattributed request or `X-Plan-Actor: chief`, and permits
   `priority`, `deadline`, and `sprint_id` for a worker. This ordering matters: a
   worker's forbidden key remains `agent_forbidden` even when its value is malformed.
3. `title`, `user_note`, and `priority` require strings. `priority` is parsed to the
   existing `Priority` enum. `deadline`, `project`, `project_id`, and `sprint_id`
   accept either a string or JSON null. The existing `body_str`, `body_opt_str`, and
   `parse_enum` error envelopes stay unchanged.
4. `project` is the legacy name selector and `project_id` is the ID selector.
   `projects_data.resolve_project` preserves case-insensitive name lookup, accepts
   either selector or both when they identify the same project, rejects a mismatch,
   and preserves the existing `invalid project`, `invalid project_id`, and
   `project_id and project do not match` validation messages/details. If either key
   occurs and the resolved value is null, the operation explicitly clears the
   Ticket's project. If neither key occurs, project is omitted from the edit.
5. The route then calls up to six self-transacting writers in fixed order: title,
   user note, priority, deadline, project ID, sprint ID. Each writer loads the Ticket,
   writes `updated_at`, appends one `ticket_updated` event, and calls
   `set_ticket_changed`, even when the supplied value equals the stored value. A
   direct compound edit therefore increments the coalesced worker-context revision
   once per field. A worker edit produces no pending context because worker actors
   are not direct actors.
6. The current event payloads are `{\"field\": <name>, \"from\": <old>, \"to\":
   <new>}`. Priority uses its wire strings; nullable fields use JSON null. Event order
   follows the fixed writer order above. Ordinary edits do not check
   `ticket_status`, a running chat turn, or a worker claim, so they remain valid while
   work is active.
7. Project editing on a parented Ticket currently raises
   `validation / project is derived when parented`. Sprint editing on a parented
   Ticket raises `sprint_derived / sprint_id is derived from the parent item` with
   `ticket_id` and `sprint_item_id`. An unknown sprint raises
   `not_found / sprint not found` with `sprint_id`. Title and deadline retain the
   existing admission errors, including `title_too_long` and the ISO-date validation
   envelope.

The defect is step 5: every field is a separate commit. A valid early field can land,
with its event and worker context, before a later field fails. The replacement keeps
all behavior above except the false-change writes and the separate transactions.

## Contract decision

Add `TicketEdit` to `src/planner/tickets/contracts.py` as an internal parsed
`TypedDict(total=False)`:

```python
class TicketEdit(TypedDict, total=False):
    title: str
    user_note: str
    priority: Priority
    deadline: str | None
    project_id: str | None
    sprint_id: str | None
```

Key membership, not a nullable default, defines whether an attribute was requested.
This makes the semantics exact:

- omitted key: preserve the stored value;
- `deadline`, `project_id`, or `sprint_id` present with null: explicitly clear it;
- `title`, `user_note`, or `priority` present with null: reject at the route with the
  existing validation envelope;
- `project` never enters the data contract: the route resolves the wire alias and
  writes one present `project_id` key whenever `project` and/or `project_id` appeared;
- an empty `TicketEdit` is harmless at the data boundary, while the HTTP route keeps
  rejecting an empty request body exactly as it does today.

Expose one public writer:

```python
edit_ticket(
    conn,
    ticket_id,
    *,
    edit: TicketEdit,
    title_max_chars: int,
    actor: str,
    now: int,
) -> Ticket
```

Delete the six public ordinary one-field writers after every caller has moved:
`set_title`, `set_user_note`, `set_priority`, `set_deadline`, `set_project`, and
`set_sprint`. Do not retain public compatibility wrappers. `set_field_user_note` and
all state, scope, proposal, recap, settlement, parentage, create, and Chief external-
work writers are different operations and remain unchanged.

## RED-first tests

Create `tests/unit/test_ticket_edit_api.py` against a real temporary SQLite database
and the real FastAPI route. Add these black-box tests before production changes, then
run only this file and capture the expected failures:

1. **Late rejection is all-or-nothing.** Snapshot all six ordinary columns,
   `updated_at`, the Ticket's events, and pending worker context. Send a direct PATCH
   with a valid changed title and a missing `sprint_id`. Assert the existing sprint
   error and byte-for-byte-equivalent snapshots. The current code fails because the
   title, event, timestamp, and context commit first.
2. **All six fields form one edit.** Create a project and sprint, submit all six keys
   in a deliberately scrambled JSON order, and assert the final values plus exactly
   six new `ticket_updated` payloads in canonical order: title, user note, priority,
   deadline, project ID, sprint ID. Assert one `ticket_changed` row at revision 1.
   The current code fails on the context revision.
3. **Stored values are a true no-op.** In one case, seed non-null deadline, project,
   and sprint values and PATCH those exact strings with the exact stored title,
   user note, and priority. In a second case, leave all three nullable fields null and
   send each as an explicit JSON null. In both cases assert an unchanged `updated_at`,
   event list, and worker-context snapshot. The current code fails all three kinds of
   assertion; the second case pins that a present null is an explicit clear which is
   nevertheless a no-op when the stored value is already null.
4. **Selector and validation compatibility.** Pin name-only, ID-only, matching
   name+ID, explicit-null clear, mismatched selectors, unknown name, unknown ID,
   unknown sprint, invalid title/deadline, and parent-derived project/sprint cases to
   their current codes, messages, and details. Assert every rejected compound request
   leaves values, timestamp, events, and context unchanged.
5. **Active work remains editable.** Put a Ticket in `agent_running_step` (and cover a
   running Ticket chat turn where the fixture makes that practical), perform a direct
   ordinary edit, and assert success plus the one context signal. No active-control
   guard is introduced.

Extend `tests/unit/test_authctx_routes.py` with:

6. A worker request whose insertion order puts a permitted priority/deadline change
   before a forbidden title or user-note change. Assert `agent_forbidden` and no
   value, timestamp, event, or context effect.
7. A compound worker edit of the permitted priority, deadline, and sprint fields.
   Assert success and no direct-worker context. Preserve the existing single-field
   permission tests.
8. Explicit-Chief and unattributed ordinary PATCH requests. Assert both remain
   permitted and retain the ordinary operation's event/context semantics; neither is
   redirected into Chief reconciliation.

Update the existing direct data callers in `tests/unit/test_tickets_engine.py` and
`tests/unit/test_worker_context.py` to construct `TicketEdit` and call `edit_ticket`.
Keep their current assertions for sprint/project errors, event payloads, legacy
`human` direct-actor compatibility, and worker-context coalescing. Do not weaken or
delete those regressions merely because the old writer names disappear.

## Implementation sequence

1. **Land after t_arch02.** Re-read the post-t_arch02 `tickets/api.py` and docs before
   editing. Record a fixed caller audit with `rg` for all six old writer names. The
   only production caller should still be ordinary PATCH.
2. **Add the parsed contract.** Add the `TicketEdit` `TypedDict` above. It contains no
   wire-level `project` alias and no fields outside the six ordinary attributes.
3. **Parse the whole request before writing.** Keep unknown-key, empty-body, and
   `reject_agent_fields` checks in their current order. Build one `TicketEdit` in
   canonical field order, using the existing body helpers and enum parser. Resolve
   project name/ID before the writer call, but preserve key presence so explicit null
   becomes a clear. Then call `tickets_data.edit_ticket` exactly once and serialize
   its returned Ticket. Do not perform any ordinary write during parsing or project
   resolution.
4. **Implement one transaction in `tickets/data.py`.** Inside one `BEGIN IMMEDIATE`:
   load the Ticket once; overlay every present edit key onto the current values;
   validate the intended final title and deadline; validate requested parent-derived
   project then sprint restrictions with their exact existing errors; and validate any
   non-null project/sprint reference before the first write. Within the writer use
   title, deadline, project, then sprint as the deterministic validation order. Checking parent
   restrictions is based on key presence, so explicitly trying to edit a derived
   null field remains an error as it is today.
5. **Compute actual changes in one fixed order.** Compare typed final values with the
   loaded Ticket in this order: title, user note, priority, deadline, project ID,
   sprint ID. If none differ, return the loaded Ticket without an UPDATE, event, or
   context call. Otherwise issue one SQL UPDATE containing only changed columns plus
   `updated_at`, append one existing `ticket_updated` event per actual change in that
   same order, call `ticket_worker_context.set_ticket_changed` once, and reload the
   Ticket before the transaction commits. An append/context/load failure therefore
   rolls the row and all events/context back together.
6. **Remove the shallow writers and migrate callers.** Delete the six old definitions
   and update only their focused test callers. Run the caller audit again; zero old
   names must remain. Do not factor this through the Chief external-work machinery or
   a generic patch engine.
7. **Document the live rule.** Add a short plain-language paragraph to
   `docs/tickets-and-gates.md`: one ordinary PATCH validates the complete intended
   Ticket and commits only real field changes and their existing events together;
   equal values leave the Ticket untouched. Keep the separate Chief external-work
   explanation intact.

## t_arch02 integration boundary

t_arch02 and this ticket overlap in `tickets/api.py` and
`docs/tickets-and-gates.md`, so they must integrate serially, with t_arch02 first.
Preserve t_arch02's route-to-action rewiring for readiness-affecting operations and
its removal of loop dependencies. Ordinary PATCH remains deliberately outside the
readiness doorbell matrix: it calls `tickets_data.edit_ticket` directly, adds no
`tickets/actions.py` operation, accepts no doorbell dependency, and never rings.
Do not edit `runtime/readiness_doorbell.py`, `tickets/actions.py`, or t_arch02 tests.
The final t_arch02 source-level route regression must continue to pass.

## Exact write scope

Production and docs:

- `src/planner/tickets/contracts.py`
- `src/planner/tickets/data.py`
- `src/planner/tickets/api.py`
- `docs/tickets-and-gates.md`

Tests:

- add `tests/unit/test_ticket_edit_api.py`
- update `tests/unit/test_authctx_routes.py`
- update `tests/unit/test_tickets_engine.py`
- update `tests/unit/test_worker_context.py`

Run, but do not edit, `tests/unit/test_chief_external_work.py`, the t_arch02 readiness
tests, existing CLI/e2e Ticket edit coverage, and the frontend build/browser suite.
No `web/` file changes are needed: `TicketRoute.svelte` already sends the same PATCH
shape, usually one field at a time, and consumes the same Ticket response.

## Focused commands and completion gates

RED, before production changes:

```sh
.venv/bin/pytest -q tests/unit/test_ticket_edit_api.py
```

Focused GREEN after the writer, route, and caller migration:

```sh
.venv/bin/pytest -q \
  tests/unit/test_ticket_edit_api.py \
  tests/unit/test_authctx_routes.py \
  tests/unit/test_tickets_engine.py \
  tests/unit/test_worker_context.py \
  tests/unit/test_chief_external_work.py
```

Before handoff, run `rg` to prove the six removed writer names have no callers and
inspect the diff to prove there are no Chief writer, readiness, CLI, schema, migration,
event-contract, or frontend changes. The orchestrator runs the single full `./verify`
only after t_arch01, t_arch02, and t_arch03 have integrated and passed their independent
implementation reviews.

## Explicitly preserved and excluded

- Preserve the HTTP request/response shape, existing errors, per-field event kind and
  payloads, actor-neutral ordinary semantics, worker field permissions, active-worker
  edits, project alias compatibility, and frontend direct edits.
- Leave `create_ticket_from_external_work` and
  `reconcile_ticket_from_external_work` structurally and semantically separate. Do
  not extract shared multi-column helpers or change their authority, control guards,
  event order, context behavior, or doorbell wrapper.
- No readiness ring for ordinary PATCH; no action-module expansion for it.
- No frontend, CLI, schema, migration, new event kind, generic mutation/patch system,
  candidate 4, or candidate 6 work.
