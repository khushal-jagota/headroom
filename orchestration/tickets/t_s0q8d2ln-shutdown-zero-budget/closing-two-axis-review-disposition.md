# Closing two-axis review disposition

The spec finding is accepted. SQLite is canonical, but shutdown cannot spend a normal five-second
busy timeout after its deadline. The runner's shutdown-only connections and each shutdown query or
settlement attempt will derive their busy timeout from time remaining, capped by the configured
normal timeout and non-blocking once exhausted. Lock failure is best-effort: it is logged, gateway
cleanup continues, and the durable running Ticket/session remains available to startup recovery.

The shared `connect(..., busy_timeout_ms)` helper currently sets SQLite's busy timeout only after
opening the connection. Its initial `sqlite3.connect` timeout will be aligned with the same parameter
so the shutdown caller's zero budget also covers initial journal-mode setup. This is the only reason
`core/db.py` enters the ticket boundary.

A focused locked-database regression must first show the current stop call exceeding its expired
deadline, then prove bounded return without losing durable Ticket/session identity. Normal runtime
database callers keep their existing configured timeout.

## Correction outcome

Both regressions were observed RED and are now GREEN. Initial connection setup honors the existing
busy timeout. Shutdown recomputes remaining SQLite time for connection setup and again before each
Ticket snapshot or turn settlement; each snapshot uses one read transaction and each settlement one
writer-lock acquisition. Lock failure is logged and leaves the durable Ticket/session recoverable.
Six focused runner tests, three DB tests, Ruff, mypy across 113 files, and diff checks pass.
