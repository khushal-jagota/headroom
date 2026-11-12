"""Versions required by the shared execution identity contract."""

from typing import Final

# Protocol 23 adds authoritative Ticket workspace lifetimes to accepted leases.
# Both peers must understand the cleanup contract before they exchange work.
PROTOCOL_VERSION: Final = 24
MIN_SUPPORTED_PROTOCOL_VERSION: Final = PROTOCOL_VERSION
SPOOL_FORMAT_VERSION: Final = 5
# The Mac's spool/commands.sqlite3, kept apart from the outbox so the outbox format
# (and with it the supervisor's revert to the previous runtime) stays unchanged.
COMMAND_JOURNAL_FORMAT_VERSION: Final = 1
