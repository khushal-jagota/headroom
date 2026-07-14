"""Real-process regression for service shutdown after its grace is exhausted."""

from __future__ import annotations

import os
import signal
import socket
import sqlite3
import subprocess
import sys
import textwrap
import time
from datetime import datetime
from pathlib import Path

from planner.core.db import connect, create_schema
from planner.days import data as days_data
from planner.days.logic import dates
from planner.tickets import data as tickets_data
from planner.tickets.contracts import NO_FURTHER, AtCap, TicketStatus


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _seed_eligible_ticket(db_path: Path) -> str:
    conn = connect(str(db_path))
    try:
        create_schema(conn)
        ticket = tickets_data.create_ticket(
            conn,
            worker_type="coding",
            title="Shutdown recovery",
            actor="human",
            now=1,
            title_max_chars=200,
        )
        tickets_data.accept_proposal(
            conn,
            ticket.id,
            field="kickoff",
            actor="human",
            now=2,
            next_ceiling=NO_FURTHER,
            at_cap=AtCap.propose,
        )
        today_id = dates.resolve_day_id("today", datetime.now().astimezone(), 5)
        days_data.add_day_ticket(conn, today_id, ticket.id, 3)
        return ticket.id
    finally:
        conn.close()


def _write_fake_hermes_interpreter(path: Path) -> None:
    path.write_text(
        textwrap.dedent(
            """\
            #!{python}
            import json
            import sys

            def send(frame):
                print(json.dumps(frame), flush=True)

            send({{"jsonrpc": "2.0", "method": "event", "params": {{"type": "gateway.ready"}}}})
            for line in sys.stdin:
                frame = json.loads(line)
                request_id = frame["id"]
                method = frame["method"]
                if method == "session.create":
                    send({{"jsonrpc": "2.0", "id": request_id, "result": {{
                        "session_id": "live-shutdown", "stored_session_id": "stored-shutdown"
                    }}}})
                elif method == "prompt.submit":
                    send({{
                        "jsonrpc": "2.0", "id": request_id,
                        "result": {{"status": "streaming"}}
                    }})
                    send({{"jsonrpc": "2.0", "method": "event", "params": {{
                        "type": "message.start", "session_id": "live-shutdown", "payload": {{}}
                    }}}})
                    send({{"jsonrpc": "2.0", "method": "event", "params": {{
                        "type": "message.delta", "session_id": "live-shutdown",
                        "payload": {{"text": "partial shutdown output"}}
                    }}}})
                elif method == "session.interrupt":
                    continue
                else:
                    send({{"jsonrpc": "2.0", "id": request_id, "result": {{}}}})
            """
        ).format(python=sys.executable),
        encoding="utf-8",
    )
    path.chmod(0o755)


def test_serve_exits_cleanly_when_employee_uses_the_entire_shutdown_deadline(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "planning.db"
    ticket_id = _seed_eligible_ticket(db_path)
    fake_hermes = tmp_path / "fake-hermes-python"
    _write_fake_hermes_interpreter(fake_hermes)
    log_path = tmp_path / "server.log"
    env = {key: value for key, value in os.environ.items() if not key.startswith("PLAN_")}
    env.update(
        {
            "PYTHONPATH": str(Path(__file__).resolve().parents[2] / "src"),
            "PLAN_DB_PATH": str(db_path),
            "PLAN_PORT": str(_free_port()),
            "PLAN_LOGS_DIR": str(tmp_path / "logs"),
            "PLAN_DISPATCHER_LOCK_PATH": str(tmp_path / "dispatcher.lock"),
            "PLAN_HERMES_HOME": str(tmp_path / "hermes-home"),
            "PLAN_HERMES_PYTHON": str(fake_hermes),
            "PLAN_TICK_SECONDS": "1",
            "PLAN_SHUTDOWN_GRACE_SECONDS": "0",
        }
    )

    with log_path.open("wb") as log:
        process = subprocess.Popen(
            [sys.executable, "-m", "planner", "serve"],
            cwd=Path(__file__).resolve().parents[2],
            env=env,
            stdout=log,
            stderr=subprocess.STDOUT,
        )
    try:
        deadline = time.monotonic() + 15.0
        stored_session_id: str | None = None
        while time.monotonic() < deadline:
            if process.poll() is not None:
                break
            try:
                conn = sqlite3.connect(db_path)
                row = conn.execute(
                    "SELECT tickets.ticket_status, tickets.employee_session_id, "
                    "chat_turns.output_text FROM tickets "
                    "LEFT JOIN chat_turns ON chat_turns.entity_id = tickets.id "
                    "AND chat_turns.origin = 'worker' AND chat_turns.mode = 'worker_step' "
                    "WHERE tickets.id = ?",
                    (ticket_id,),
                ).fetchone()
                conn.close()
            except sqlite3.Error:
                row = None
            if (
                row is not None
                and row[0] == TicketStatus.agent_running_step.value
                and row[1]
                and row[2]
            ):
                stored_session_id = str(row[1])
                break
            time.sleep(0.05)
        assert stored_session_id == "stored-shutdown", log_path.read_text(errors="replace")

        process.send_signal(signal.SIGINT)
        process.wait(timeout=10.0)
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5.0)

    output = log_path.read_text(encoding="utf-8", errors="replace")
    assert process.returncode == 0
    assert "Application shutdown complete" in output
    assert "Hermes session ingress router did not terminate within 0.0s" not in output
    assert "Application shutdown failed" not in output
    assert "employee shutdown settlement could not acquire SQLite" not in output
    conn = connect(str(db_path))
    try:
        ticket = tickets_data.read_ticket(conn, ticket_id)
        worker_turn = conn.execute(
            "SELECT status, session_key FROM chat_turns "
            "WHERE entity_id = ? AND origin = 'worker' AND mode = 'worker_step'",
            (ticket_id,),
        ).fetchone()
    finally:
        conn.close()
    assert ticket.ticket_status is TicketStatus.agent_running_step
    assert ticket.employee_session_id == stored_session_id
    assert worker_turn is not None
    assert worker_turn["status"] == "interrupted"
    assert worker_turn["session_key"] == stored_session_id
