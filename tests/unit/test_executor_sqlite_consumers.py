"""Mac durable file writes propagate a failed full device flush."""

from __future__ import annotations

import fcntl
import os
import sys

import pytest

from planner.executor import files


def test_mac_full_flush_failure_propagates_after_regular_flush(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[tuple[str, int]] = []
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(fcntl, "F_FULLFSYNC", 51, raising=False)
    monkeypatch.setattr(os, "fsync", lambda fd: events.append(("fsync", fd)))

    def full_flush(fd: int, operation: int) -> None:
        assert operation == 51
        events.append(("fullfsync", fd))
        raise OSError("drive flush failed")

    monkeypatch.setattr(fcntl, "fcntl", full_flush)
    with pytest.raises(OSError, match="drive flush failed"):
        files.fsync_file(17)
    assert events == [("fsync", 17), ("fullfsync", 17)]
