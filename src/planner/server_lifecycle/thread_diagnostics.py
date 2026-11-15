"""An operator-requested Python thread snapshot in the application's stderr log."""

from __future__ import annotations

import faulthandler
import os
import signal
import sys
import threading
import traceback
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from types import FrameType


@contextmanager
def thread_dump_signal() -> Iterator[None]:
    """Keep shutdown signals unchanged; restore the previous diagnostic handler on exit."""
    dumping = False

    def dump(signum: int, frame: FrameType | None) -> None:
        nonlocal dumping
        if dumping:
            return
        dumping = True
        try:
            frames = sys._current_frames()
            names = {thread.ident: thread.name for thread in threading.enumerate()}
            lines = [f"Python thread dump pid={os.getpid()} at {datetime.now(UTC).isoformat()}\n"]
            for ident, current in sorted(frames.items()):
                lines.append(f"Thread name={names.get(ident, '<unregistered>')!r} ident={ident}\n")
                lines.extend(traceback.format_stack(current))
            lines.append("End Python thread dump\n")
            data = "".join(lines).encode("utf-8", errors="backslashreplace")
            while data:
                data = data[os.write(2, data):]
        except OSError:
            # A closed log sink must not turn a diagnostic signal into shutdown.
            pass
        finally:
            dumping = False

    previous = signal.signal(signal.SIGUSR1, dump)
    try:
        # Raw C-level stacks remain available when the main thread cannot run
        # Python. Chaining schedules the named snapshot when it can run again.
        faulthandler.register(signal.SIGUSR1, file=2, all_threads=True, chain=True)
        try:
            yield
        finally:
            faulthandler.unregister(signal.SIGUSR1)
    finally:
        signal.signal(signal.SIGUSR1, previous)
