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
Runtime ownership and the background projection rebuild join startup in PR3.
