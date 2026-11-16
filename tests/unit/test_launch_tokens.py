"""Protect credential binding and failed-authentication owner fallback at the HTTP seam.

These use the existing fresh SQLite fixture, without a backend or browser host. Existing
authority cases cover rank; these cover the credential that now supplies that rank.
"""

from sqlite3 import Connection
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, Request

from planner.agent_identity.contracts import LAUNCH_TOKEN_ENVIRONMENT_NAME
from planner.agent_identity.data import (
    authenticate_launch_token,
    issue_launch_token,
    revoke_launch_tokens,
)
from planner.cli.http import _headers
from planner.conversation.contracts import ConversationRoleMaterials, ConversationStartRequest
from planner.conversation.logic.conversation_start_resolution import (
    resolve_conversation_start_request,
)
from planner.conversation.storage import ensure_started_conversation_record
from planner.core.authctx import AUTHENTICATED_OWNER_SCOPE_KEY, RequestContext, request_context
from planner.core.contracts import CHIEF_PRINCIPAL, OWNER_PRINCIPAL, UNIDENTIFIED_PRINCIPAL
from planner.core.errors import PlannerError


def _conversation(conn: Connection, conversation_id: str, *, linked: bool = True) -> None:
    with conn:
        ensure_started_conversation_record(
            conn,
            resolve_conversation_start_request(ConversationStartRequest(
                conversation_id=conversation_id,
                model="test-model",
                role_materials=ConversationRoleMaterials(
                    "Context is not authority", (("PLAN_ACTOR", "chief"),)
                ),
            )),
            created_at=1,
        )
        if linked:
            conn.execute(
                "INSERT INTO agents(agent_key,conversation_id) VALUES ('chief_of_staff',?) "
                "ON CONFLICT(agent_key) DO UPDATE SET conversation_id=excluded.conversation_id",
                (conversation_id,),
            )


def test_token_is_hash_only_rotates_and_requires_current_association(tmp_db: Connection) -> None:
    _conversation(tmp_db, "c_identity")
    token = issue_launch_token(tmp_db, "c_identity")
    authenticated = authenticate_launch_token(tmp_db, token)
    assert authenticated is not None
    assert authenticated.principal == CHIEF_PRINCIPAL
    assert authenticated.conversation_id == "c_identity"
    assert token not in "\n".join(tmp_db.iterdump())
    replacement = issue_launch_token(tmp_db, "c_identity")
    assert authenticate_launch_token(tmp_db, token) is None
    assert authenticate_launch_token(tmp_db, replacement) is not None
    with tmp_db:
        tmp_db.execute("UPDATE agents SET conversation_id=NULL WHERE agent_key='chief_of_staff'")
    assert authenticate_launch_token(tmp_db, replacement) is None
    revoke_launch_tokens(tmp_db, "c_identity")
    assert tmp_db.execute("SELECT count(*) FROM agent_launch_tokens").fetchone()[0] == 0
    _conversation(tmp_db, "c_forged", linked=False)
    with pytest.raises(PlannerError, match="no current agent identity"):
        issue_launch_token(tmp_db, "c_forged")


def test_request_token_binds_origin_and_failed_auth_never_becomes_owner(
    tmp_db: Connection,
) -> None:
    _conversation(tmp_db, "c_identity")
    token = issue_launch_token(tmp_db, "c_identity")
    app = FastAPI()
    app.state.config = SimpleNamespace(
        db_path=tmp_db.execute("PRAGMA database_list").fetchone()[2],
        trusted_ingress_provider=None,
    )

    def context(headers: dict[str, str], *, owner: bool = False) -> RequestContext:
        return request_context(Request({
            "type": "http", "app": app,
            "headers": [(name.lower().encode(), value.encode()) for name, value in headers.items()],
            AUTHENTICATED_OWNER_SCOPE_KEY: owner,
        }))

    identity = context({
        "Authorization": f"Bearer {token}",
        "X-Plan-Actor": "worker", "X-Plan-Ticket-ID": "someone-else",
        "X-Plan-Conversation-ID": "another-conversation",
    })
    assert identity.principal == CHIEF_PRINCIPAL
    assert identity.conversation_id == "c_identity"
    for headers in (
        {"Authorization": "Bearer invalid"},
        {"Authorization": ""},
        {"X-Plan-Actor": "chief"},
        {"X-Plan-Conversation-ID": "c_identity"},
    ):
        assert context(headers).principal == UNIDENTIFIED_PRINCIPAL
    refused = context({"Authorization": "Bearer invalid"}, owner=True)
    assert refused.principal == UNIDENTIFIED_PRINCIPAL
    duplicated = request_context(Request({
        "type": "http", "app": app,
        "headers": [(b"authorization", f"Bearer {token}".encode()),
                    (b"authorization", b"Bearer different")],
        AUTHENTICATED_OWNER_SCOPE_KEY: True,
    }))
    assert duplicated.principal == UNIDENTIFIED_PRINCIPAL
    revoke_launch_tokens(tmp_db, "c_identity")
    assert context({"Authorization": f"Bearer {token}"}).principal == UNIDENTIFIED_PRINCIPAL
    assert context({}).principal == OWNER_PRINCIPAL
    app.state.config.trusted_ingress_provider = "tailscale"
    assert context({}).principal == UNIDENTIFIED_PRINCIPAL
    assert context({}, owner=True).principal == OWNER_PRINCIPAL


def test_cli_sends_launch_credential_and_marks_an_agent_missing_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(LAUNCH_TOKEN_ENVIRONMENT_NAME, "synthetic-launch-token")
    monkeypatch.setenv("PLAN_ACTOR", "chief")
    monkeypatch.setenv("PLAN_CONVERSATION_ID", "forged-origin")
    assert _headers() == {"Authorization": "Bearer synthetic-launch-token"}
    monkeypatch.delenv(LAUNCH_TOKEN_ENVIRONMENT_NAME)
    assert _headers() == {"Authorization": "Bearer unidentified"}
    monkeypatch.delenv("PLAN_ACTOR")
    assert _headers() == {}
    monkeypatch.setenv(LAUNCH_TOKEN_ENVIRONMENT_NAME, "")
    assert _headers() == {"Authorization": "Bearer unidentified"}
