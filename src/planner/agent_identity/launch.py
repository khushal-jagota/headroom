"""Give only a local native child its conversation's transient credential.

The persisted start and remote executor commands never pass through this boundary.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from planner.agent_identity.contracts import (
    LAUNCH_TOKEN_ENVIRONMENT_NAME,
    UNIDENTIFIED_LAUNCH_TOKEN,
)
from planner.agent_identity.data import canonical_principal, issue_launch_token
from planner.conversation.backends.contracts import BackendChild
from planner.conversation.contracts import ConversationRoleMaterials, ResolvedConversationStart
from planner.core.db import connect
from planner.core.task_settlement import run_on_worker_thread


def local_launch_start(db_path: str, start: ResolvedConversationStart) -> ResolvedConversationStart:
    """Mint at native launch from the current association, never from role variables."""
    conn = connect(db_path)
    try:
        token = (
            UNIDENTIFIED_LAUNCH_TOKEN
            if canonical_principal(conn, start.conversation_id) is None
            else issue_launch_token(conn, start.conversation_id)
        )
    finally:
        conn.close()
    role = start.role_materials
    environment = dict(role.identity_environment_variables if role else ())
    environment[LAUNCH_TOKEN_ENVIRONMENT_NAME] = token
    return replace(
        start,
        role_materials=ConversationRoleMaterials(
            role_text=role.role_text if role else "",
            identity_environment_variables=tuple(environment.items()),
        ),
    )


class AuthenticatedLocalChild:
    """Forward the child contract, replacing only the environment at native start."""

    def __init__(self, child: BackendChild, db_path: str) -> None:
        self._child = child
        self._db_path = db_path

    def __getattr__(self, name: str) -> Any:
        return getattr(self._child, name)

    async def start(
        self, resolved_start: ResolvedConversationStart, *, vendor_session_cursor: str | None
    ) -> None:
        await self._child.start(
            await run_on_worker_thread(local_launch_start, self._db_path, resolved_start),
            vendor_session_cursor=vendor_session_cursor,
        )
