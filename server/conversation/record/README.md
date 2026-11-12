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

`transact_events` accepts domain writes and returned event payloads. The store
alone appends those payloads. A domain that already owns a transaction uses
`append_in_transaction` with its scoped `TransactionSql`. Neither path opens or
commits another writer. Participants cannot return promises or retain SQL access.

`ConversationRecordStore` and `RecordParticipants` in `contracts.ts` define the
core interface and domain seams. Attention, Manager notices, credentials,
deployment recovery, and runtime publication stay with their owning units.
An isolated record instance does not prove those integrations.

The executor participant can refuse before it takes a writer. If it refuses after
a take, this store rolls back the whole transaction. The Python savepoint outcome
requires Unit 22 integration. No rejected event batch commits silently.

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
.venv/bin/python server/conversation/views/generate_fixture_oracle.py
```

The oracle is a local fixture proof. The populated six-route comparison in CI
checks current Python behavior after staging merges.

Without a deployment participant, `reconcile_in_transaction` rotates
`conversation_runtime_epoch` before it reconciles the facts. When Unit 19 supplies
`reconcile_deployment`, that participant owns the complete order: import captured
evidence against the old epoch, then rotate once. The store does not rotate again.
The real core must reuse one startup transaction and must not reconcile twice.
