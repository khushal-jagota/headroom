# Mac mailbox custody

`MacMailbox` is inert until `start()`. Its required ports supply a runner, catalog, HTTP client, journal, incarnation, and host fault callback.
The ports import generated executor types. They provide no decoder, provider implementation, or private native-process cohort.
Poll and queues import the real shared `protocol.ts` codec directly. Production callers cannot substitute a decoder.
The journal public constructor fixes that same codec and its worker entry. Trusted fault injection stays in storage test support.

Each child keeps mailbox order. Stops bypass that queue. The runner owns withdrawal, native shielding, and idempotent result/output settlement.
Queues retain each admitted runner promise until it settles. Model requests run independently.
The catalog owner encodes a complete `CommandResult` off main and retains immutable canonical bytes.
Each persistence retry opens a fresh reader, so it does not repeat the catalog read or clone numeric trees.
Admission stopped or catalog read failure preserves Python's null-snapshot result.
An admitted catalog task includes its eventual result write and canonical-owner close, even if stop starts during the read.

Poll waits on its own cancellable planned-replay waiter before each request.
It records the complete bounded poll response before handoff, then decodes each fresh canonical command once.
The journal's original `command_count` detects duplicates without another reply decode.
A committed handoff failure faults journal/root admission and requests host shutdown.
Raw poll bytes retain the existing 4 MiB cap. Canonical commands can grow through defaults and are not subject to that raw-body cap again.
Their materialized main-thread read retains the journal's existing native byte ceiling. Real-codec expansion and timer cost require measured evidence.

Report selection, normalization, root validation, framing, and held-result fairness stay inside the journal worker.
It fetches `32 + held.size`, excludes held IDs first, and uses held insertion order only if no other candidates remain.
It preserves Python's byte accounting and always selects a first result, even above 4 MiB.
The client refuses that oversized body before HTTP. It never truncates or silently discards it.
Main receives at most 32 IDs and one canonical body. It sends only submitted-and-settled IDs back.
Every selection receives one accepted feedback attempt. A database failure in that attempt updates fairness and releases the selection before rejection.
Healthy main capacity refusal carries `code: EBUSY, unadmitted: true`. Only that refusal is retried before acceptance.
Body close uses the same unadmitted-cleanup retry. Real `SQLITE_BUSY` after acceptance is not retried as the same feedback.
No new HTTP attempt starts if stop occurs during report preparation. The selection still receives failure feedback and body close.

The host must stop and join the mailbox before it stops the journal/outbox.
These services are not peer journal sidecars. Stop first seals poll/reporter admission, then drains committed poll handoff before it seals queues.
Queue settlement does not wait for unrelated reporter latency. The aggregate joins both before actual completion.
Startup replay gets one lease interval before initial poll admission. Its work continues if that observation expires.
Stop during startup prevents subsequent loop admission. The later host starts lease work after this startup window.

All stages receive the same absolute `StopOptions.deadline` through shared `observe_shutdown`.
Actual joins outlive that observation and reject only after every owned task/cleanup settles.
The aggregate also retains every child bounded-stop result. Only bounded `ShutdownDeadlineExceeded` errors are excluded from actual failure history.
Actual-join failures remain visible regardless of their type. A non-deadline stop failure survives even when that child's actual cleanup succeeds.
Typed business cancellation is recognized only at pause, wake, planned-waiter, and HTTP-request boundaries.
Reader, feedback, native-task, and catalog-owner cleanup errors remain failures, including DOM `AbortError`.
Known result commits survive final stream-close failure. That cleanup fault requests host shutdown and remains in actual ownership evidence.
The actual aggregate cleanup chain continues after a deadline, so the host can retain journal/database/lock ownership until it ends.

Retry diagnostics contain error class, status/code, and cause class. Rejected reasons use Python's 200-code-point prefix.
The future provider adapters must forward the invocation's process owners. No private production cohort belongs in these ports.
