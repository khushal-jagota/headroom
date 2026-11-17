# Mac executor HTTP transport

`MacHttpClient` owns seven independent HTTP/HTTPS agents and imports the shared executor codec.
It refuses redirects, ignores proxy variables and cookies, and never decompresses responses.
The configuration module supplies connector limits, idle keepalive, connect and route deadlines.
Poll replies remain bounded raw bytes. Outcome reports stream a borrowed canonical reader.
The reporter retains the reader's close capability.

`MacFileTransport` uses an invocation-owned `FilesService` for bounded transfer bytes,
digest checks, retained descriptors and full flush. This binding discards a failed download through
its retained parent. Python's low-level HTTP client leaves the partial file, but its `MacFiles`
binding removes the temporary in `finally`. This binding supplies that cleanup before host composition.
Transfer close failure faults the files owner and retains both primary and cleanup errors.
The host must retain and stop the supplied files owner separately.

A request timeout ends the caller's waiter. Its producer, response consumer, request and socket
remain retained until actual completion. `reset()` replaces sessions and joins their actual work.
`stop()` seals admission and observes that join with the host's original absolute deadline.
`settled()` waits for actual cleanup. Neither timeout nor cancellation proves cleanup.

The direct comparison owns a numeric-loopback TLS listener, private certificate and synthetic homes.
It compares populated requests and replies against fresh Python, including unsafe integers,
redirect and size refusals, transfer bytes, and reset. The same fixture proves channel isolation
and actual producer ownership. The containment check precedes client construction and network I/O.
Imports start no loops or providers. The host explicitly starts `MacLeaseLoop`.
Its lease reports backlog and working turns, offers updates, and fences a replaced incarnation.
It captures the journal cursor before the request so a restore cannot erase a concurrent poll advance.
`MacLiveSender` sends each conversation independently and drops failed batches and excess waiting events.
Both owners use the host's shared stop deadline and expose their actual loop settlement.

`MacOutboxSender` uses the existing store for ordered batches and durable receipts.
It listens for committed appends before its first scan and remains idle without a scan timer.
Four active senders rotate conversations after each batch. Each conversation owns its capped retry delay.
An item gets five minutes from its first attempt. Items carry no files, so no file transfer uses that time. The first-attempt timestamp survives restart.
Definite HTTP refusals settle the item locally. At expiry, the sender retains the failed content and advances to later items.
A local failure receipt uses `result.local_delivery_failure`, `result.delivery_outcome`, and a zero VPS timestamp.
An unconfirmed outcome means that the VPS can already hold the effect. It does not prove remote refusal or safe repetition.
Confirmed notifications run after the local receipt commits. Logs include the real reason, HTTP status/code, sequence, attempt, and retry delay.
The sender scans every pending conversation; only lease reports retain their bounded conversation list.
The host stops the sender before it closes the store and file transport.

Known limit: the inherited directory walk lacks a retry owner if an intermediate ancestor close fails.

`MacTicketFileSync` sends each Ticket's `artifacts/` folder to the home, apart from every item.
A Ticket's item, or a restart, requests a pass. One pass runs for a Ticket, and at most one waits.
A pass compares the folder with the versions the home confirmed. It sends each new or changed file once and removes each file the folder no longer has.
It removes nothing when a folder is absent or unreadable. Symlinks and all other Ticket folders stay local.
The file worker hashes each file. A failed file is logged and the other files still go. The Ticket's next pass retries it after a capped delay. A file the home refuses, or one that cannot be read, waits until it changes.
