"""The process-wide "something was written" signal.

One signal, no payload and no vocabulary: it says a transaction committed, not what
changed. Whoever cares re-reads what they are holding. Subscribers are called on
whichever thread committed, so a subscriber must be cheap and thread-safe — setting a
``threading.Event`` or handing work to an event loop, never real work and never a
database write.

The signal is best-effort by design. A subscriber that raises is logged and skipped,
because the database and the readiness loop's periodic timer remain the canonical
answer to "what is true now"; this only removes the wait.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable

_log = logging.getLogger(__name__)

_lock = threading.Lock()
_subscribers: list[Callable[[], None]] = []


def subscribe(callback: Callable[[], None]) -> Callable[[], None]:
    """Register a callback and hand back the one call that removes it again."""
    with _lock:
        _subscribers.append(callback)
    removed = False

    def unsubscribe() -> None:
        nonlocal removed
        with _lock:
            if not removed:
                _subscribers.remove(callback)
                removed = True

    return unsubscribe


def emit() -> None:
    """Tell every subscriber that a transaction committed."""
    with _lock:
        current = tuple(_subscribers)
    for callback in current:
        try:
            callback()
        except Exception:
            _log.exception("change signal subscriber failed")


def subscriber_count() -> int:
    with _lock:
        return len(_subscribers)
