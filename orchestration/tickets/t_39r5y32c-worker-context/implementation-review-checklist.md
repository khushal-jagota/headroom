# Implementation review checklist

- First-class `worker_context` package keeps contracts, SQLite operations, and delivery composition separate.
- Storage is a keyed coalescing map with revision-safe compare-and-ack.
- Deleting a ticket removes its pending worker context; schema/migration choices do not break legacy ticket-table rebuilds.
- `ticket_changed` stays in the ticket producer domain; SharedGateway contains no key-specific logic.
- Human edits mark context: edited acceptance, direct value, Ticket PATCH, scope, ticket user note, human recap, and human field notes. Agent proposal/recap/note and unedited acceptance do not.
- Every actual `prompt.submit` path is covered, including normal/claimed System B, sync/stream/background human turns, sync/stream model-backed commands, and return-for-revision.
- Non-model commands do not consume context.
- Context is acknowledged immediately after submit acceptance; busy/error retains; racing revision survives.
- Streaming image attachment still happens before `prompt.submit`; an `image.attach` failure leaves pending context unacknowledged.
- Panels-visible text remains the original prompt/message.
- Schema migration works from the prior version and fresh schema.
- Focused tests and full `./verify` pass.
