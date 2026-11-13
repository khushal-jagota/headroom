# Mac executor HTTP transport

`MacHttpClient` owns seven independent HTTP/HTTPS agents and imports the shared executor codec.
It refuses redirects, ignores proxy variables and cookies, and never decompresses responses.
The configuration module supplies connector limits, idle keepalive, connect and route deadlines.
Poll replies remain bounded raw bytes. Outcome reports stream a borrowed canonical reader.
The reporter retains the reader's close capability.

`MacFileTransport` uses an invocation-owned `FilesService` for bounded transfer bytes,
digest checks, retained descriptors and full flush. Download failure preserves the partial file,
as Python does. Transfer close failure faults the files owner and retains both primary and cleanup errors.
The host must retain and stop the supplied files owner separately.

A request timeout ends the caller's waiter. Its producer, response consumer, request and socket
remain retained until actual completion. `reset()` replaces sessions and joins their actual work.
`stop()` seals admission and observes that join with the host's original absolute deadline.
`settled()` waits for actual cleanup. Neither timeout nor cancellation proves cleanup.

The direct comparison owns a numeric-loopback TLS listener, private certificate and synthetic homes.
It compares populated requests and replies against fresh Python, including unsafe integers,
redirect and size refusals, transfer bytes, and reset. The same fixture proves channel isolation
and actual producer ownership. The containment check precedes client construction and network I/O.
No host, provider, lease loop, outbox sender or update controller starts in this module.
