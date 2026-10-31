"""Real Linux evidence at the socket boundary, with no production state or listener."""

from __future__ import annotations

import asyncio
import os
import socket
import subprocess
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest
from tests.support.principals import OWNER_PRINCIPAL

from planner.core.db import connect, create_schema
from planner.previews.contracts import PreviewError
from planner.previews.process import LinuxProcessInspector, validate_worktree
from planner.previews.service import PreviewService
from planner.projects.data import create_project
from planner.tickets.data import create_ticket

APP = """import socket,sys
s=socket.socket()
s.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1)
s.bind(('127.0.0.1', int(sys.argv[1])))
s.listen()
print(s.getsockname()[1],flush=True)
while True:
 c,_=s.accept()
 c.settimeout(.15)
 try:
  data=c.recv(1024)
 except socket.timeout:
  print('NO_BYTES',flush=True)
  data=c.recv(1024)
 if data:
  c.sendall(b'ack')
 c.close()
"""


@contextmanager
def application(cwd: Path, port: int = 0) -> Iterator[tuple[subprocess.Popen[str], int]]:
    clean = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("PLAN_", "PANELS_REAL_"))
    }
    child = subprocess.Popen(
        [sys.executable, "-u", "-c", APP, str(port)],
        cwd=cwd,
        env=clean,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        assert child.stdout is not None
        yield child, int(child.stdout.readline())
    finally:
        child.terminate()
        child.wait(timeout=3)


@pytest.mark.skipif(sys.platform == "darwin", reason="Real listener ownership needs Linux /proc")
def test_real_socket_association_no_bytes_before_accept_and_irreversible_expiry(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    repository = project / "repository"
    subprocess.run(["git", "init", str(repository)], check=True, capture_output=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(repository),
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.com",
            "commit",
            "--allow-empty",
            "-m",
            "fixture",
        ],
        check=True,
        capture_output=True,
    )
    db_path = tmp_path / "preview.db"
    conn = connect(str(db_path))
    create_schema(conn)

    placement = create_project(conn, name="Preview integration", folder_path=project, now=1)
    ticket = create_ticket(
        conn,
        title="Preview",
        worker_type="coding",
        principal=OWNER_PRINCIPAL,
        now=1,
        title_max_chars=200,
        project_id=placement.id,
    )
    tree = project / "worktrees" / f"{ticket.id}-preview"
    subprocess.run(
        ["git", "-C", str(repository), "worktree", "add", "-b", "preview", str(tree)],
        check=True,
        capture_output=True,
    )
    assert validate_worktree(project, ticket.id) == tree
    inspector = LinuxProcessInspector()

    async def exercise() -> None:
        service = PreviewService(str(db_path), "https://panels.example", panels_port=8768)
        await service.start()
        try:
            prepared = await service.prepare(ticket.id)
            with pytest.raises(PreviewError, match="Panels"):
                await service.register(ticket.id, 8768)
            with application(project) as (_, wrong_port):
                with pytest.raises(PreviewError, match="outside"):
                    await service.register(ticket.id, wrong_port)
            with application(tree) as (child, port):
                active = await service.register(ticket.id, port)
                assert active.process and active.process.pid == child.pid
                await service.before_connection(active)
                upstream = socket.create_connection(("127.0.0.1", port), timeout=2)
                try:
                    await service.after_connection(active, upstream.getsockname()[1])
                    assert child.stdout is not None
                    # The application accepted the TCP connection but received no HTTP bytes.
                    assert await asyncio.to_thread(child.stdout.readline) == "NO_BYTES\n"
                    upstream.sendall(b"hello")
                    assert upstream.recv(3) == b"ack"
                finally:
                    upstream.close()
                child.terminate()
                child.wait(timeout=3)
                with pytest.raises(PreviewError):
                    await service.check(ticket.id, prepared.generation)
            with application(tree, port):
                with pytest.raises(PreviewError) as expired:
                    await service.check(ticket.id, prepared.generation)
                assert expired.value.status_code == 410
                fresh = await service.prepare(ticket.id)
                assert fresh.generation != prepared.generation
                renewed = await service.register(ticket.id, port)
                assert renewed.process and renewed.process.pid != active.process.pid
                inspector.check(renewed.process)
        finally:
            await service.close()

    try:
        asyncio.run(exercise())
    finally:
        conn.close()
