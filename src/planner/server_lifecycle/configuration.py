"""Limits for reading the application's event-loop tasks over the local control socket."""

from typing import Final

# One answer, the whole snapshot. Oldest tasks are kept first; the rest are only counted,
# so this one number bounds both the bytes sent and the stack walking done on the loop.
MAX_EVENT_LOOP_TASKS_RESPONSE_BYTES: Final = 1024 * 1024
# How long the server gives one client to send its request and take the answer.
EVENT_LOOP_TASKS_EXCHANGE_SECONDS: Final = 10.0
# How long `panels server-event-loop-tasks` waits for an answer. A loop blocked by synchronous code
# never answers; then a native stack dump of the application process is the right tool.
EVENT_LOOP_TASKS_CLIENT_TIMEOUT_SECONDS: Final = 10.0
