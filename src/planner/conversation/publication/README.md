# Signal producer

This directory owns domain publication. It does not replace retained core connections,
migration admission, navigation installation, delivery, or lifecycle infrastructure.

## Composition

1. Acquire the existing conversation-owner lease before migrations. Pass its exact open
   handle to `SignalPublicationCoordinator`. The coordinator never closes the handle.
2. Supply the real lease validator through `authority_check`. A filename or an open file
   alone cannot prove ownership. No production fallback grants authority.
3. Register the coordinator's `enter` factory through the separately reviewed retained
   core adapter. Register before any schema transaction, including raw migration connections.
   Attach its connection functions before protected source SQL.
4. Enter `bootstrap_authority` for migrations. `create_schema` installs
   producer tables only. Install source triggers explicitly under admitted bootstrap.
   A database with those triggers requires the retained adapter on every source connection.
5. Call `begin_initialization` after the schema commit. It expires the bootstrap token
   and returns exact reconciliation authority. Bind that token for reconciliation and
   bind the runtime reader. `initialize` publishes complete values in an admitted
   initialization transaction. A known commit expires reconciliation authority and
   permits installation only. A rolled-back initialization grants no runtime authority.
6. Install navigation with `attach_signal_publication`. Supply the retained nested
   `transaction_control` guard and managed connection factory. Installation commits
   before callback registration. Supply `bind_admission` for its exact connection token.
   Failure after commit leaves installation unavailable. Only complete attachment opens
   runtime admission. `admit_runtime` validates that completed boundary.
7. Retained core finalization runs Signal before the proof witness and navigation.
   It settles the guard after SQLite outcome classification and releases it before wakes.

`SignalRuntimeMutation` captures an exact epoch and turn object. `PlainFieldMutation`
replaces existing ordinary fields or dataclass slots. Collection operations replace
whole sets/maps, including provider-ID maps. Undo restores the exact prior references.
The worker uses no asyncio lock, provider callback, or event-loop wait under the gate.

Runtime candidates require an injected savepoint-depth adapter and an outer transaction.
Durable-only nested scopes retain transactional dirty rows and association effects.
Source triggers cover conversations, events, acknowledgments, lifecycle facts, current
Ticket/agent links, and historical Ticket links. They capture old/new conversation IDs.
Canonical private tables reject participant writes after producer finalization.

## Recovery and private evidence

Mint stable operation IDs outside storage retry loops. Record JSON results, such as
event sequences, with `record_result` before commit. Each batch gets a distinct identity.
Private batch evidence is not a replay journal. Unbound known outcomes queue cleanup
for the next relevant transaction. Operation owners call `accept_operation_result`
after accepting a result, or `acknowledge_operation` inside an admitted transaction.
Evidence cleanup rolls back with SQLite. Unresolved outcomes cannot be acknowledged.
Retained external outcomes set `retain_failure=True` on their mutation admission.
Expected validation errors before external effects do not retain a global failure.

An uncertain commit retains mutation undo and invalidates availability before release.
Resolve the failed connection first. On a fresh source snapshot, `resolve_operation`
returns committed evidence. An absent row permits undo only with adapter-established
rollback proof. A committed operation never repeats its append or provider action.
Every lease exposes the immutable `reconciliation_handle` before BEGIN. Generic core
propagates that opaque object on uncertainty. `resolve_reconciliation` rejects foreign,
unknown, obsolete-batch and acknowledged handles, including when rollback is established.
`confirm_repaired` requires resolved outcomes and complete source/publication equality.
A new complete attachment then restores current availability.

Generation permits invalidate old reads and attachments. Unavailable knowledge contains
no current runtime value. A separate coherent durable read can retain the unread dot and
failure facts. Unknown runtime truth never becomes false idle. Rebuild owns checks across
threaded reads, response admission, frames, heartbeats, and active network sends.

## Evidence boundary

`tests/unit/test_signal_publication.py` uses an injected SQLite transaction driver and a
fixture callback guard. It proves domain behavior only. It does not prove retained guard
registration, nested authorizer composition, migration admission, real runtime startup,
navigation cursor acceptance, browser marks, or stream recovery. Those need the assembled
immutable adapter handoff and the existing controlled runtime/navigation hosts.
