"""Report a retained workspace when its reason first appears or changes."""

import logging


class RetainedWorkspaceReporter:
    def __init__(self) -> None:
        self._reported: set[tuple[str, str]] = set()

    def report(self, logger: logging.Logger, skipped: list[dict[str, str]]) -> None:
        current = {(entry["path"], entry["reason"]) for entry in skipped}
        for path, reason in sorted(current - self._reported):
            logger.warning("Ticket workspace retained: %s (%s)", path, reason)
        self._reported = current
