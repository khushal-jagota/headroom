"""Execute bounded probes in the retained historical interpreter and source tree.

The helper itself imports no application module until the subprocess has synthetic
runtime paths. Upgraded databases receive only raw read-only canonical readers.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path
from typing import Any

HISTORICAL_TIP = "595eb08ee85e60cb51b28332a850c59f3df26cb6"
CURRENT_STAGING_TIP = "86da50467e292a863283695dbbbdcf6f0b9d49a7"
F097_TIP = "f0977a512ba9a6bb1736e15ee9760aa39989a0fc"
PUBLIC_FOUR_TIP = "82d50c14f4057196883843640dae2ff1aa1d7850"
DEFAULT_SOURCE = Path(__file__).resolve().parents[3] / "t_3p0mtprp-baseline-595"


def historical_source() -> Path:
    return Path(
        os.environ.get("PANELS_NAVIGATION_HISTORICAL_SOURCE", str(DEFAULT_SOURCE))
    ).resolve()


def run_historical(
    source: Path,
    database: Path,
    action: str,
    *,
    expected_tip: str = HISTORICAL_TIP,
) -> dict[str, Any]:
    """Use the old tree without its cwd, inherited runtime paths, or credentials."""
    root = database.parent / ("historical-" + action)
    root.mkdir(parents=True, exist_ok=True)
    environment = {"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8"}
    for name, relative in {
        "HOME": "home",
        "CODEX_HOME": "home/codex",
        "HERMES_HOME": "home/hermes",
        "CLAUDE_CONFIG_DIR": "home/claude",
        "XDG_CONFIG_HOME": "home/config",
        "XDG_CACHE_HOME": "home/cache",
        "XDG_DATA_HOME": "home/data",
        "XDG_RUNTIME_DIR": "home/runtime",
        "XDG_STATE_HOME": "home/state",
        "TMPDIR": "temporary",
        "PLAN_LOGS_DIR": "logs",
        "PLAN_BACKUP_DIR": "backups",
        "PLAN_HERMES_HOME": "home/hermes",
        "PLAN_DISPATCHER_LOCK_PATH": "dispatcher.lock",
        "PLAN_SERVER_CONTROL_SOCKET": "server.sock",
    }.items():
        environment[name] = str(root / relative)
        if name not in {"PLAN_DISPATCHER_LOCK_PATH", "PLAN_SERVER_CONTROL_SOCKET"}:
            (root / relative).mkdir(parents=True, exist_ok=True)
    environment.update(
        PLAN_DB_PATH=str(database),
        PLAN_DISPATCH_ENABLED="0",
        PYTHONPATH=str(source / "src"),
        PYTHONDONTWRITEBYTECODE="1",
    )
    result = subprocess.run(
        [
            str(source / ".venv/bin/python"),
            str(Path(__file__).resolve()),
            action,
            str(database),
            str(source),
            expected_tip,
        ],
        cwd=root,
        env=environment,
        text=True,
        capture_output=True,
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    value: dict[str, Any] = json.loads(result.stdout)
    return value


def source_facts(connection: sqlite3.Connection) -> dict[str, Any]:
    """Return bounded raw rows, including payload text without JSON re-encoding."""
    queries = {
        "events": "SELECT conversation_id,sequence,kind,payload,created_at "
        "FROM conversation_events ORDER BY conversation_id,sequence",
        "conversations": "SELECT conversation_id,latest_sequence,owner_read_through_sequence,"
        "latest_agent_activity_sequence FROM conversations ORDER BY conversation_id",
        "aliases": "SELECT conversation_id,ticket_id FROM ticket_conversations ORDER BY 1",
        "tickets": "SELECT id,title,stage,worker_step_claim,is_backlogged,pending_proposal,"
        "pending_proposal_revision,field_values,project_id,sprint_item_id,conversation_id,"
        "ceiling_holder FROM tickets ORDER BY id",
        "heads": "SELECT version_num FROM alembic_version ORDER BY version_num",
    }
    return {name: [list(row) for row in connection.execute(sql)] for name, sql in queries.items()}


def _seed(database: Path) -> None:
    # These imports resolve to the verified retained source, never current helpers.
    from planner.conversation.contracts import PromptDeliveryMode
    from planner.conversation.events import (
        ConversationEventPayload,
        PromptDeliveryUncertainEventPayload,
        PromptDiscardedEventPayload,
        conversation_event_payload_to_canonical_json,
    )
    from planner.conversation.message_content import text_message_content
    from planner.core.db import connect, create_schema

    connection = connect(str(database))
    try:
        create_schema(connection)
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            "INSERT INTO projects(id,name,created_at,updated_at) "
            "VALUES('project_historical','Historical project',1,1)"
        )
        connection.execute(
            "INSERT INTO sprint_items(id,title,project_id,created_at,updated_at,"
            "supervisor_backend,supervisor_model) "
            "VALUES('si_historical','Historical orchestrator','project_historical',"
            "1,1,'codex','gpt-6.1-sol')"
        )
        for identity in ("c_current", "c_history"):
            connection.execute(
                "INSERT INTO conversations(conversation_id,backend_key,workspace_folder,"
                "access,created_at,latest_sequence,owner_read_through_sequence) "
                "VALUES(?,'codex',?,'full',1,2,1)",
                (identity, str(database.parent / "workspace")),
            )
        proposal = json.dumps(
            {"field": "plan", "body": "Proposed plan α", "proposed_by": "agent", "created_at": 7}
        )
        connection.execute(
            "INSERT INTO tickets(id,title,worker_type,employee_backend,employee_launch_model,"
            "stage,ceiling,field_values,created_at,updated_at,project_id,sprint_item_id,"
            "conversation_id,worker_step_claim,is_backlogged,pending_proposal,"
            "pending_proposal_revision,orchestrator_kind) "
            "VALUES('t_historical','Historical Ticket','coding',"
            "'codex','gpt-6.1-sol','needs_plan','needs_plan',?,1,2,NULL,"
            "'si_historical','c_current','out',1,?,3,'sprint_item')",
            (
                json.dumps(
                    {
                        "brief": "Approved brief",
                        "success_condition": "Preserve facts",
                        "what_changes": "Verify migration",
                    }
                ),
                proposal,
            ),
        )
        for identity in ("c_current", "c_history"):
            connection.execute(
                "INSERT INTO ticket_conversations VALUES(?,'t_historical')", (identity,)
            )
            content = text_message_content("Synthetic receipt α\nKeep exact bytes.")
            payloads: tuple[ConversationEventPayload, ...] = (
                PromptDeliveryUncertainEventPayload(
                    content, "Owner", PromptDeliveryMode.steer, sender_message_id="uncertain-1"
                ),
                PromptDiscardedEventPayload(content, "Owner", sender_message_id="discarded-2"),
            )
            for sequence, payload in enumerate(payloads, start=1):
                connection.execute(
                    "INSERT INTO conversation_events VALUES(?,?,?,?,?)",
                    (
                        identity,
                        sequence,
                        payload.kind.value,
                        conversation_event_payload_to_canonical_json(payload),
                        3 + sequence,
                    ),
                )
        connection.commit()
    finally:
        connection.close()


def _main() -> None:
    action, path, source_text, expected_tip = sys.argv[1:]
    source, database = Path(source_text), Path(path)
    observed = subprocess.check_output(
        ["git", "-C", str(source), "rev-parse", "HEAD"], text=True
    ).strip()
    assert expected_tip in {HISTORICAL_TIP, CURRENT_STAGING_TIP, F097_TIP, PUBLIC_FOUR_TIP}
    assert observed == expected_tip, observed
    subprocess.run(
        [
            "git",
            "-C",
            str(source),
            "diff",
            "--exit-code",
            "HEAD",
            "--",
            "src/planner",
            "requirements.txt",
            "pyproject.toml",
        ],
        check=True,
        capture_output=True,
    )
    import planner

    assert Path(planner.__file__).resolve() == source / "src/planner/__init__.py"
    assert Path(sys.prefix) == source / ".venv"
    public_snapshot = None
    if action in {"seed", "seed_public4"}:
        _seed(database)
        if action == "seed_public4":
            assert expected_tip == PUBLIC_FOUR_TIP
            sys.path.insert(0, str(source))
            from tests.support.navigation_current import current_navigation_database

            from planner.navigation import snapshot

            with current_navigation_database(database) as current:
                public_snapshot = snapshot(current)
                assert public_snapshot["schema_version"] == 4
    else:
        assert action in {"read", "read_incompatible"}, action
    connection = sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    try:
        from planner.projects.data import list_projects
        from planner.tickets.data import read_ticket
        from planner.worker_types.configuration import load_worker_runtime_definitions

        load_worker_runtime_definitions(connection)
        if action == "read_incompatible":
            assert expected_tip == F097_TIP
            failures: dict[str, str] = {}
            for name, read in (
                ("projects", lambda: list_projects(connection)),
                ("ticket", lambda: read_ticket(connection, "t_historical")),
            ):
                try:
                    read()
                except (sqlite3.OperationalError, IndexError) as error:
                    failures[name] = str(error)
                else:
                    raise AssertionError(f"obsolete {name} reader unexpectedly succeeded")
            print(
                json.dumps(
                    {"tip": observed, "failures": failures, "source": source_facts(connection)}
                )
            )
            return
        ticket = read_ticket(connection, "t_historical")
        denied: list[str] = []
        for statement in (
            "UPDATE native_usage_sources SET source_key='forbidden'",
            "CREATE TABLE forbidden(value)",
        ):
            try:
                connection.execute(statement)
            except sqlite3.OperationalError as error:
                assert "readonly" in str(error), str(error)
                denied.append(statement)
            else:
                raise AssertionError("legacy reader connection admitted a write")
        result = {
            "tip": observed,
            "public_snapshot": public_snapshot,
            "module": str(Path(planner.__file__).resolve()),
            "python_prefix": sys.prefix,
            "migration": hashlib.sha256(
                (
                    source / "src/planner/core/migrations/versions/remove_ticket_retirement.py"
                ).read_bytes()
            ).hexdigest(),
            "projects": {project.id: project.name for project in list_projects(connection)},
            "ticket": {
                "title": ticket.title,
                "stage": ticket.stage,
                "claim": ticket.worker_step_claim.value,
                "backlog": ticket.is_backlogged,
                "status": ticket.ticket_status.value,
                "project": ticket.project_id,
                "orchestrator": ticket.sprint_item_id,
                "conversation": ticket.conversation_id,
                "fields": dict(ticket.field_values),
                "proposal": None
                if ticket.pending_proposal is None
                else ticket.pending_proposal.body,
                "proposal_revision": ticket.pending_proposal_revision,
            },
            "denied_writes": len(denied),
            "source": source_facts(connection),
            "foreign_keys": [list(row) for row in connection.execute("PRAGMA foreign_key_check")],
        }
        print(json.dumps(result, ensure_ascii=False))
    finally:
        connection.close()


if __name__ == "__main__":
    _main()
