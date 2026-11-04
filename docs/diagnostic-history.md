# Diagnostic history

Diagnostic history is the conversation event record in the database. Headroom retains it as evidence of the work and the messages that agents received.

There are no history expiry or compaction commands. Existing omission metadata remains available to conversation reads.

## Code

- `src/planner/conversation/`: the event record and its readers.
- `src/planner/conversation/views/omissions.py`: retained omission metadata.

_Last verified: 2026-11-04._
