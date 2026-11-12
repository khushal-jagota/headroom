"""Versions required by the shared execution identity contract."""

from typing import Final

# Protocol 22 is the mailbox transport over HTTPS. Protocol 21 was the WebSocket
# session, and no Mac speaks both, so there is no older window.
PROTOCOL_VERSION: Final = 22
MIN_SUPPORTED_PROTOCOL_VERSION: Final = PROTOCOL_VERSION
SPOOL_FORMAT_VERSION: Final = 5
# The Mac's spool/commands.sqlite3, kept apart from the outbox so the outbox format
# (and with it the supervisor's revert to the previous runtime) stays unchanged.
COMMAND_JOURNAL_FORMAT_VERSION: Final = 1
