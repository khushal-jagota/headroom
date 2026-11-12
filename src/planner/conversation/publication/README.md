# Conversation status publication

This directory calculates conversation statuses and publishes changes to navigation.
The runtime keeps its existing ownership lease. SQLite saves need no admission token.

Source triggers record changed conversation IDs and Ticket or agent associations.
Their SQL uses no connection-specific functions, so ordinary SQLite connections work.
Existing databases replace the former guarded triggers and drop the private guards,
operations table, and batches table.

Before an application transaction commits, publication reads the changed conversations,
combines durable facts with current runtime state, and stores their status values.
Navigation consumes those values in the same transaction. Notifications follow a
successful commit. Rollback remains an ordinary SQLite operation.

Startup recalculates every conversation status and refreshes associations before
normal delivery starts. This corrects stale status values after a crash or direct
SQLite edit. Direct edits leave change marks but do not emit a process-local wake.

Publication no longer keeps operation receipts, batch evidence, mutation undo, or
uncertain-save repair state. It does not switch the screen to a degraded status mode
when a save fails. Runtime ownership and stream continuity remain separate concerns.

The browser signal test exercises application save, status calculation, navigation,
stream delivery, and visible dots together. Unit and integration tests cover direct
SQLite edits, rollback, and startup recalculation at their narrower boundaries.
