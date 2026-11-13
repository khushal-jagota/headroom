# The conversation record

`ConversationStore` owns event appends. Each append writes its sequence, events,
lifecycle facts, projection, and synchronous participants through one write door.
The caller publishes the returned rows to the tail only after the method returns.
Transient frames never enter the durable event record.

Every synchronous method makes one transaction attempt. The core wraps writes in
`settle_recording` to retain Python's contention and save retry policy:

```ts
const written = await store.settle_recording(conversation_id, () =>
    store.append_events(conversation_id, payloads, options),
);
for (const row of written) {
    live_tail.publish_event(row);
}
```

The core retains its per-conversation ordering across that wait. No provider call
belongs in the retry operation. Before shutdown releases database ownership, the
core awaits `finish_writes()`.

`RecordingSettlementOptions.shutdown_signal` belongs only to runtime shutdown.
It cancels a new outer settlement attempt or the generic exponential-backoff wait.
An already-started write, including every SQLite BUSY retry, keeps its original
promise and runs to completion. Caller departure never aborts this signal.
The core fences native observers and new operations separately. It joins the
fenced task cleanup before it detaches children, including a child that an
interrupted stop restores. This never joins an unfenced provider promise.
It then awaits `finish_writes()` before runtime ownership can be released.
The lifecycle awaits the core's `shutdown()` and performs no second detach.

`transact_events` accepts domain writes and returned event payloads. The store
alone appends those payloads. A domain that already owns a transaction uses
`append_in_transaction` with its scoped `TransactionSql`. Neither path opens or
commits another writer. Participants cannot return promises or retain SQL access.

`ConversationRecordStore` and `RecordParticipants` in `contracts.ts` define the
core interface and domain seams. Attention, Manager notices, credentials,
deployment recovery, and runtime publication stay with their owning units.
An isolated record instance does not prove those integrations.

The executor participant owns a write-door savepoint around its domain writer.
A refusal after take rolls back the event and commits its refusal receipt outside
that savepoint. The store publishes no written events for that refused outcome.

The startup method reconciles lifecycle facts without a synthetic closing event.
The record runtime reuses the server's existing ownership lock and starts background
projection rebuild after reconciliation. Rebuild uses private, distinctly named
TEMP tables on the one writer connection. The projector replays at most 500 events
per write-door batch and yields between batches. A final transaction catches up
and installs all five tables. TEMP names do not shadow canonical projection tables.

This differs from Python's private computation on a second connection: each TS
batch briefly holds the write lock. The single-writer rule remains intact, and
ordinary source writes keep the same projector.

To regenerate the fixture oracle after Python view code changes, run from the
repository root with this checkout's installed Python dependencies:

```sh
.venv/bin/python tests/compare/tools/generate_fixture_oracle.py
```

The comparison workflow also runs the generator with `--check` on both Node jobs.
It fails if current Python produces different bytes. The oracle is a local fixture proof. The populated six-route comparison in CI
checks current Python behavior after staging merges.

Without a deployment participant, `reconcile_in_transaction` rotates
`conversation_runtime_epoch` before it reconciles the facts. When Unit 19 supplies
`reconcile_deployment`, that participant owns the complete order: import captured
evidence against the old epoch, then rotate once. The store does not rotate again.
The real core must reuse one startup transaction and must not reconcile twice.

Unit 19 adds two synchronous record operations. `bind_interruption_delivery(conversation_id, turn_number, references = null, recovery_sender_message_ids = null)` calls its optional participant in one write transaction before child startup. It commits the binding and returns the notice. A failed participant rolls back that binding. Later child or wire failures do not erase a committed binding.

`fail_recovery_claims(conversation_id, turn_number)` calls its optional participant in one later write transaction. It compares Ticket claim revisions before and after that callback. A changed claim emits the change signal. A failed callback rolls back that transaction and leaves the prior binding intact. The existing core persistence scope supplies retries and shutdown ownership. Provider operations remain outside both transactions.

Both callbacks are optional. Without them, binding returns null and failure settlement changes no rows. Existing participant names and signatures remain intact. The concrete core snapshots previous-process task IDs before it starts children. `tell_agents_of_background_tasks_lost_at_restart()` takes only those IDs and queues the existing lost-task prompt for each removed task. Tasks from this process remain untouched.
