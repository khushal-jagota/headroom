# Maintenance

Headroom keeps conversation history. It has no history expiry or compaction commands.
Existing omission metadata remains available to conversation reads.

Cancelled-Ticket recovery for an old database that cannot migrate is a separate offline command.
See [Deployment](deployment.md).

_Last verified: 2026-11-04._
