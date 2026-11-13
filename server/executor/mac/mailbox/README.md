# Mac mailbox custody

`MacMailbox` is inert until `start()`. Its required ports supply a runner, catalog, HTTP client, journal, incarnation, and host fault callback.
The ports import generated executor types. They provide no decoder, provider implementation, or private native-process cohort.
The journal worker and queues import the real shared codec. Production callers cannot substitute a decoder.
The journal public constructor fixes that same codec and its worker entry. Trusted fault injection stays in storage test support.

Each child keeps mailbox order. Stops bypass that queue. The runner owns withdrawal and idempotent result/output settlement.
Queues retain each admitted runner promise until it settles. Model requests run independently.
The required runner signal stops native work through its existing child owners and joins actual cleanup.
`MailboxRunCancelled` carries the original `StopOptions` object. Cancellation does not create another shutdown budget.
The catalog owner encodes a complete `CommandResult` off main and retains immutable canonical bytes.
Each persistence retry opens a fresh reader, so it does not repeat the catalog read or clone numeric trees.
Admission stopped or catalog read failure preserves Python's null-snapshot result.
An admitted catalog task includes its eventual result write and canonical-owner close, even if stop starts during the read.

Poll waits on its own cancellable planned-replay waiter before each request.
It records the complete bounded poll response before handoff. The worker decodes each retained canonical command before delivery.
Main receives decoded values through the shared tagged numeric transport. It does not materialize or parse command bytes.
The journal's original `command_count` detects duplicates without another reply decode.
A committed handoff failure during live operation faults journal/root admission and requests host shutdown.
After stop, poll closes the decoded cursor without dispatch. Recorded commands remain for uncertain recovery after restart.
Raw poll bytes retain the existing 4 MiB cap. Canonical commands can grow through defaults and are not subject to that raw-body cap again.
Canonical expansion stays in the worker. The existing byte-reader API remains available to storage consumers.
`JOURNAL_INPUT_BYTES` bounds raw polls. `JOURNAL_RESULT_BYTES` preserves the accepted native ceiling for result ingestion and generic result readers.

Report selection, normalization, root validation, framing, and held-result fairness stay inside the journal worker.
It fetches `32 + held.size`, excludes held IDs first, and uses held insertion order only if no other candidates remain.
It preserves Python's byte accounting and always selects a first result, even above 4 MiB.
The client refuses that oversized body before HTTP. It never truncates or silently discards it.
Main receives at most 32 IDs and one canonical body. It sends only submitted-and-settled IDs back.
Every selection receives one accepted feedback attempt. A database failure in that attempt updates fairness and releases the selection before rejection.
Ordinary admission retains its 32-operation cap and `code: EBUSY, unadmitted: true` refusal.
Feedback and reader close release already-owned selections or readers. They enter the same ordered tail without ordinary capacity refusal.
Those existing resource owners bound cleanup. No retry loop or new coordinator admits their release.
Real `SQLITE_BUSY` after acceptance is not retried as the same feedback.
Actual request or reply-handling failure attempts null feedback before body close and keeps failure fairness.
A selection withdrawn before HTTP, or after a stopped request rejects, receives private `cancelled` feedback. It does not charge failure fairness.
A successful response after stop still applies its recorded/rejected IDs. Stop starts no further HTTP request.
Actual cleanup failures retain their primary failure too. Stop alone creates no journal fault or host notification.

The host must stop and join the mailbox before it stops the journal/outbox.
These services are not peer journal sidecars. Stop seals queues synchronously, then stops poll and reporter admission.
Queue settlement does not wait for unrelated reporter latency. The aggregate joins both before actual completion.
Startup replay gets one lease interval before initial poll admission. Its work continues if that observation expires.
Stop during startup prevents subsequent loop admission. The later host starts lease work after this startup window.

All stages receive the same absolute `StopOptions.deadline` through shared `observe_shutdown`.
Actual joins outlive that observation and reject only after every owned task/cleanup settles.
The aggregate also retains every child bounded-stop result. Only bounded `ShutdownDeadlineExceeded` errors are excluded from actual failure history.
Actual-join failures remain visible regardless of their type. A non-deadline stop failure survives even when that child's actual cleanup succeeds.
Typed business cancellation is recognized at pause, wake, and planned-waiter boundaries.
Any runner rejection after its invocation signal aborts is cancellation. The queue records one debug message and does not report a host failure.
An HTTP request error after stop is cancellation regardless of its class. Its separate cleanup failures remain visible.
Separate reader, feedback, and catalog-owner cleanup errors remain failures, including DOM `AbortError`.
Known result commits survive final stream-close failure. That cleanup fault requests host shutdown and remains in actual ownership evidence.
The actual aggregate cleanup chain continues after a deadline, so the host can retain journal/database/lock ownership until it ends.

Retry diagnostics contain error class, status/code, and cause class. Rejected reasons use Python's 200-code-point prefix.
The future provider adapters must forward the invocation's process owners. No private production cohort belongs in these ports.
