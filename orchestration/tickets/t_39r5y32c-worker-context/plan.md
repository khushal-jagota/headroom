# Accepted implementation plan

1. Create a first-class `planner/worker_context/` package with contracts, SQLite data operations, and context composition/delivery acknowledgement. Store coalescing rows keyed by worker entity and context key, with text and revision.
2. Give producer domains one set/upsert API. The ticket domain owns `ticket_changed` and marks relevant human Ticket/Review edits; repeated edits coalesce, while agent writes and unedited approvals do not mark it.
3. Inject the generic context service into the worker SharedGateway. Every path reaching `prompt.submit` prepares model text with pending context while Panels chat retains the original visible text.
4. Acknowledge only exact key/revision pairs after Hermes accepts submission. Busy/failed sends retain them and concurrent updates survive.
5. Test subsystem boundaries, producer classification, all submit paths, coalescing/concurrency/failure behavior, browser flows, docs, and full verification.
