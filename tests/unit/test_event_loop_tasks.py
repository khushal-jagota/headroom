"""`panels server-event-loop-tasks`: the application describes its own asyncio tasks, read-only.

These run the real event-loop-tasks socket and its client on a real event loop in this process.
The wiring into the supervised application process is proved in
tests/integration/test_event_loop_tasks.py.
"""

from __future__ import annotations

import asyncio
import socket
import time
from pathlib import Path

import pytest

from planner.server_lifecycle.control import (
    decode_event_loop_tasks_response,
    request_event_loop_tasks,
)
from planner.server_lifecycle.event_loop_tasks import (
    TaskCreationTimes,
    build_event_loop_tasks_answer,
    open_event_loop_tasks_listing,
)


async def _waits_for_the_mac_link(link_ready: asyncio.Event) -> str:
    await link_ready.wait()
    return "woken by its own event"


def test_a_task_blocked_on_an_await_is_listed_with_its_age_and_left_undisturbed(
    tmp_path: Path,
) -> None:
    socket_path = tmp_path / "t.sock"

    async def scenario() -> None:
        listing = await open_event_loop_tasks_listing(socket_path)
        assert listing is not None
        try:
            link_ready = asyncio.Event()
            blocked = asyncio.create_task(_waits_for_the_mac_link(link_ready), name="mac-link")
            await asyncio.sleep(0.25)
            younger = asyncio.create_task(asyncio.sleep(60))
            await asyncio.sleep(0)
            asked_at = time.monotonic()
            response = await asyncio.to_thread(request_event_loop_tasks, socket_path)
            elapsed = time.monotonic() - asked_at + 0.25

            by_name = {task.name: task for task in response.tasks}
            listed = by_name["mac-link"]
            assert listed.coroutine == "_waits_for_the_mac_link"
            assert [frame.function for frame in listed.stack] == [
                "_waits_for_the_mac_link",
                "Event.wait",
            ]
            assert listed.stack[0].file == __file__
            assert listed.age_seconds is not None
            assert 0.25 <= listed.age_seconds <= elapsed + 1.0
            # Oldest first, and an unnamed task keeps the loop's own name for it.
            names = [task.name for task in response.tasks]
            assert younger.get_name().startswith("Task-")
            assert names.index("mac-link") < names.index(younger.get_name())
            assert response.omitted_task_count == 0
            assert response.total_task_count == len(response.tasks)

            # Reading changed nothing: still waiting, and its own event still wakes it.
            assert not blocked.done()
            assert not link_ready.is_set()
            link_ready.set()
            assert await blocked == "woken by its own event"
            younger.cancel()
        finally:
            listing.close()
        assert not socket_path.exists()

    asyncio.run(scenario())


def test_many_tasks_fill_the_answer_oldest_first_and_count_the_rest() -> None:
    limit = 64 * 1024

    async def scenario() -> None:
        loop = asyncio.get_running_loop()
        creation_times = TaskCreationTimes(loop)
        creation_times.install()
        never = asyncio.Event()
        try:
            tasks = [asyncio.create_task(never.wait(), name=f"waiter-{n}") for n in range(5000)]
            await asyncio.sleep(0)
            answer = build_event_loop_tasks_answer(loop, creation_times, max_bytes=limit)
            response = decode_event_loop_tasks_response(answer)
            assert len(answer) <= limit
            assert response.total_task_count == 5001
            assert 0 < len(response.tasks) < 5000
            assert response.omitted_task_count == 5001 - len(response.tasks)
            # The scenario task predates recording and is stamped at install, so it leads.
            assert [task.name for task in response.tasks[1:4]] == [
                "waiter-0",
                "waiter-1",
                "waiter-2",
            ]
            assert not any(task.done() for task in tasks)
            for task in tasks:
                task.cancel()
        finally:
            creation_times.uninstall()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "request_bytes",
    [
        b'{"version":1,"operation":"list_event_loop_tasks"' + b" " * 5000 + b"}\n",
        b'{"version":1,"operation":"restart"}\n',
    ],
    ids=["over-the-control-bound", "restart-operation"],
)
def test_task_socket_refuses_what_is_not_a_bounded_task_list_request(
    tmp_path: Path,
    request_bytes: bytes,
) -> None:
    socket_path = tmp_path / "t.sock"

    def send_raw() -> bytes:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.settimeout(5)
            client.connect(str(socket_path))
            try:
                client.sendall(request_bytes)
            except BrokenPipeError:
                return b""
            return client.recv(65536)

    async def scenario() -> None:
        listing = await open_event_loop_tasks_listing(socket_path)
        assert listing is not None
        try:
            assert await asyncio.to_thread(send_raw) == b""
        finally:
            listing.close()

    asyncio.run(scenario())
