# Accepted differences from Python

This file records intentional differences that the migration Manager accepted. No new difference enters this file without that acceptance. Close an entry when Python adopts the same behavior.

## Open: settle an accepted held prompt before a secondary Ticket-model error

Accepted by the migration Manager on November 12, 2026, during review of PR 712.

**Python behavior:** After a held prompt commits, the core calls the secondary Ticket-model writer before it settles the phase and reservation. If that writer raises, the accepted receipt stays behind a pending barrier. The conversation cannot process later backend news.

**TypeScript behavior:** The core settles the running phase, reservation, and queued sender identities first. It then propagates the same secondary writer error. It does not repeat the provider write or the committed append.

**Reason:** This corrects a Python defect. A secondary bookkeeping error must not strand work that the provider accepted and the record committed. The Manager reports the Python defect to the Chief for staging. Close this entry after the Python fix reaches the migration comparison baseline.

**Pinned by:** `server/tests/conversation_core_delivery.test.ts`, test `publication failure after receipt commit does not append or deliver twice`. The same fixture queues a model change, throws the exact secondary error after commit, checks one delivery and append, retries the sender identity, and proves that later backend news still persists.

- Projection rebuild uses TEMP tables on the single writer connection in batches of 500 events. Python uses a second connection. Results match. Sustained-load latency is not measured.
