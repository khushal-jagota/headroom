"""Frozen owner-approved server rule for the worker skill correction."""

from typing import Final

SERVER_OWNERSHIP_SECTION: Final[str] = """### The server is not yours

The Panels server that runs you is operator-owned. Khushal starts it, stops it and restarts it.

Do not signal it and do not replace it. Never run `kill`, `pkill`, `killall`, an `lsof`-driven
kill, or `systemctl` against it. Never run `panels serve` on its port.

A pattern cannot tell the live server from your own. On this machine the live deployment and a
scratch instance run the same command line, so `pkill -f "planner serve"` stops both. That took
the live server down on 15 August 2026 and again on 20 October 2026.

If you start your own server, record its PID at launch and stop that exact PID. If a port is
busy, pick another port. Never free a port.

If the live server must restart, say so and stop.
"""
