# Plan-review disposition

## 1. Employee interrupt could overrun the shared deadline

Accepted. The ticket and plan now permit one compatible optional keyword-only deadline on the
concrete `SharedGateway.interrupt` method without changing `GatewayAdapter` or any contract/type
file. The Employee runner passes the existing absolute deadline. The deadline-aware path uses only
an already-owned child/live session, bounds the Hermes request wait by the time remaining, and fails
immediately instead of spawning or resuming. The real-process fake now withholds the interrupt reply,
and the Employee-runner slice has a focused overrun regression.

## 2. Concurrent stop lacked exactly-once acceptance proof

Accepted. The runner slice now requires a concurrent-stop test. The first caller is identified by
the existing `_stopping` transition and owns the only active-Ticket snapshot; later callers take no
interrupt snapshot and only join the same bounded drain. No duplicate lifecycle state is added.

## Orchestrator correction

The planning sub-agent proposed catching `BaseException` while iterating role gateways. That was not
required by the ticket and would defer process-control exceptions. The corrected plan catches only
ordinary `Exception`, still attempts every unique gateway after ordinary cleanup failures, and
re-raises the first one unchanged.

## Corrected-plan review

### 3. Missing no-spawn/no-resume proof

Accepted. `tests/unit/test_minds.py` is now in scope for two direct concrete-gateway tests: an
unstarted gateway cannot spawn, and a started gateway without the target live session cannot issue
`session.resume` when the deadline-aware interrupt path is used.

### 4. Parked revision reservations are active but not interruptible

Accepted. The runner now requires an active running `worker_step` turn whose bound session id equals
the Ticket's durable `employee_session_id`. This uses Panels Chat only as session-control identity and
does not confuse the visible row with worker context. A parked revision reservation has no running
bound worker turn and gets a focused non-interruption regression.

### 5. First-error ordering proof was optional

Accepted. The routed-gateway test must make both unique gateways raise distinct exceptions, then
assert the first exception object is re-raised after the second cleanup attempt.
