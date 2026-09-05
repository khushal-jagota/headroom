from __future__ import annotations

import logging
import threading

import pytest

from planner.core import change_signal


def test_subscribe_returns_the_one_call_that_removes_the_subscriber() -> None:
    calls: list[int] = []
    unsubscribe = change_signal.subscribe(lambda: calls.append(1))

    change_signal.emit()
    assert calls == [1]

    unsubscribe()
    change_signal.emit()
    assert calls == [1]


def test_unsubscribing_twice_is_harmless() -> None:
    unsubscribe = change_signal.subscribe(lambda: None)
    before = change_signal.subscriber_count()
    unsubscribe()
    unsubscribe()
    assert change_signal.subscriber_count() == before - 1


def test_every_subscriber_is_called_and_one_that_raises_does_not_stop_the_rest(
    caplog: pytest.LogCaptureFixture,
) -> None:
    seen: list[str] = []

    def raising() -> None:
        seen.append("raising")
        raise RuntimeError("subscriber blew up")

    unsubscribe_raising = change_signal.subscribe(raising)
    unsubscribe_second = change_signal.subscribe(lambda: seen.append("second"))
    try:
        with caplog.at_level(logging.ERROR):
            change_signal.emit()
    finally:
        unsubscribe_second()
        unsubscribe_raising()

    assert seen == ["raising", "second"]
    assert "change signal subscriber failed" in caplog.text


def test_a_signal_raised_on_another_thread_reaches_the_subscriber() -> None:
    delivered = threading.Event()
    unsubscribe = change_signal.subscribe(delivered.set)
    try:
        thread = threading.Thread(target=change_signal.emit)
        thread.start()
        thread.join(5)
        assert delivered.wait(5)
    finally:
        unsubscribe()


