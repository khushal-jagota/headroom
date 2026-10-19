"""What Panels can say about the codex login without spending it.

Every login here is a synthetic file written in a temporary directory. The token strings
are made up and signed with nothing, which is all this code reads: an expiry claim, an
account id, a refresh stamp. Nothing in this module touches the real login, asks a server
anything, or spends a refresh.

The behaviour under test is the honesty rule the ticket exists for. A saved login that is
there is not the same as a saved login that works, and the three states Panels can tell
apart — good, unconfirmed, refused — must each be reported as themselves.
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from tests.support.conversation_codex_app_server_bench import _scripted_child
from tests.unit.test_conversation_snapshot import (
    _codex_that_says_nothing,
    _installed_codex,
    _run,
)

from planner.conversation.backend_state import DurableCodexLoginRejection
from planner.conversation.backends.codex_app_server.auth_failure import (
    LOGIN_IS_REJECTED_SUMMARY,
)
from planner.conversation.backends.codex_app_server.client import (
    CodexAppServerClient,
    CodexLoginIsDuplicated,
    child_environment,
)
from planner.conversation.backends.codex_app_server.login import (
    CodexLoginGeneration,
    RememberedCodexLoginRejection,
    logins_hold_the_same_credential,
    read_stored_codex_login,
)
from planner.conversation.contracts import ConversationBackendKey
from planner.conversation.message_content import text_message_content
from planner.conversation.snapshot import (
    BackendIdentityStatus,
    CodexLoginStanding,
    probe_backend,
)
from planner.core.db import connect, create_schema

REFRESH_STAMP = "2026-10-16T00:33:10.678890887Z"
ACCOUNT_ID = "11111111-2222-3333-4444-555555555555"


@contextmanager
def _codex_home_pointed_at(home: Path) -> Iterator[None]:
    """Point this process's codex home at a synthetic login for the duration.

    The snapshot service reads the login where codex would read it, so a test that drives
    the real service has to put its synthetic one there.
    """
    before = os.environ.get("CODEX_HOME")
    os.environ["CODEX_HOME"] = str(home)
    try:
        yield
    finally:
        if before is None:
            del os.environ["CODEX_HOME"]
        else:
            os.environ["CODEX_HOME"] = before


def _token(**claims: Any) -> str:
    """A JWT shape with a readable middle and nothing that could be a secret."""
    payload = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=")
    return f"header.{payload}.signature"


def _write_login(
    home: Path,
    *,
    expires_at: datetime | None = None,
    account_id: str | None = ACCOUNT_ID,
    last_refresh: str | None = REFRESH_STAMP,
    refresh_token: str | None = "synthetic-refresh-token",
    api_key: str | None = None,
    email: str | None = "someone@example.com",
) -> Path:
    home.mkdir(parents=True, exist_ok=True)
    expiry = expires_at or datetime.now(UTC) + timedelta(days=9)
    tokens: dict[str, Any] = {
        "access_token": _token(exp=int(expiry.timestamp())),
        "id_token": _token(email=email),
    }
    if account_id is not None:
        tokens["account_id"] = account_id
    if refresh_token is not None:
        tokens["refresh_token"] = refresh_token
    document: dict[str, Any] = {"auth_mode": "chatgpt", "tokens": tokens}
    if last_refresh is not None:
        document["last_refresh"] = last_refresh
    if api_key is not None:
        document["OPENAI_API_KEY"] = api_key
    path = home / "auth.json"
    path.write_text(json.dumps(document))
    return path


# --- reading the saved login -------------------------------------------------------------












# --- the same credential twice -----------------------------------------------------------


def test_the_same_refresh_token_is_the_same_credential_however_the_metadata_differs(
    tmp_path: Path,
) -> None:
    """The secret is the whole comparison.

    A copy whose account id or refresh stamp was edited, or dropped altogether, still
    rotates the one token the real login depends on. Comparing whole files, or insisting
    the metadata match, would let exactly that copy through.
    """
    real = tmp_path / "real"
    edited = tmp_path / "edited"
    stripped = tmp_path / "stripped"
    _write_login(real, refresh_token="shared-token")
    _write_login(edited, refresh_token="shared-token", account_id="99999999-0000-0000-0000-0",
                 last_refresh="2026-01-01T00:00:00Z", email="other@example.com")
    _write_login(stripped, refresh_token="shared-token", account_id=None, last_refresh=None)

    assert logins_hold_the_same_credential(edited, real) is True
    assert logins_hold_the_same_credential(stripped, real) is True










def test_a_child_pointed_at_a_copy_of_the_live_login_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The one door every Panels-launched codex goes through stops the duplicate."""
    fake_user_home = tmp_path / "user"
    _write_login(fake_user_home / ".codex", refresh_token="the-one-token")
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake_user_home))
    copied = tmp_path / "scratch"
    _write_login(copied, refresh_token="the-one-token")

    with pytest.raises(CodexLoginIsDuplicated) as refused:
        child_environment(overrides=(("CODEX_HOME", str(copied)),))

    assert str(copied) in str(refused.value)






# --- what a failed turn says --------------------------------------------------------------
















# --- a rejection belongs to one credential ------------------------------------------------








# --- what the card says -------------------------------------------------------------------


def _codex_card(standing: CodexLoginStanding | None) -> Any:
    async def exercise() -> None:
        card = await probe_backend(
            ConversationBackendKey.codex,
            _installed_codex(),
            codex_model_catalog_probe=_codex_that_says_nothing,
            codex_login=standing,
        )
        _codex_card.result = card  # type: ignore[attr-defined]

    _run(exercise)
    return _codex_card.result  # type: ignore[attr-defined]


def _standing(home: Path, *, rejected: bool = False) -> CodexLoginStanding:
    return CodexLoginStanding(stored=read_stored_codex_login(home), rejected=rejected)






def test_a_rejected_login_reads_as_signed_out_and_outranks_a_fresh_token(
    tmp_path: Path,
) -> None:
    """A refusal is the only thing here that was actually tested, so it wins."""
    _write_login(tmp_path / "home")

    card = _codex_card(_standing(tmp_path / "home", rejected=True))

    assert card.identity.status is BackendIdentityStatus.unauthenticated
    assert card.identity.detail == "This saved login was rejected the last time codex used it."
    assert card.identity.login_command == "codex login"






# --- a real turn, through the real adapter ------------------------------------------------

REFRESH_REJECTED_ON_STDERR = (
    "codex_login::auth::manager: Failed to refresh token: Your access token could not be "
    "refreshed because your refresh token was already used. Please log out and sign in "
    "again.\n"
)


def test_a_failed_turn_says_the_login_was_rejected_and_remembers_that_credential(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The whole path, on a synthetic login: codex's words in, an honest line out."""
    original_read = CodexAppServerClient._read_standard_error

    class DelayedStream(asyncio.StreamReader):
        def __init__(self, stream: asyncio.StreamReader) -> None:
            super().__init__()
            self.stream = stream

        async def readline(self) -> bytes:
            line = await self.stream.readline()
            if line:
                await asyncio.sleep(0.01)
            return line

    async def delayed_read(
        self: CodexAppServerClient, stream: asyncio.StreamReader
    ) -> None:
        await original_read(self, DelayedStream(stream))

    # Force the stdout notification to beat an earlier stderr write.
    monkeypatch.setattr(CodexAppServerClient, "_read_standard_error", delayed_read)
    home = tmp_path / "codex-home"
    _write_login(home)
    remembered = RememberedCodexLoginRejection()
    script = {
        "turns": [
            {
                "actions": [
                    {"do": "say_on_stderr", "text": REFRESH_REJECTED_ON_STDERR},
                    {
                        "do": "complete",
                        "status": "failed",
                        "error": {"message": "workspace routing discovery unauthorized (401)"},
                    },
                ]
            }
        ]
    }

    async def exercise() -> None:
        async with _scripted_child(
            tmp_path,
            script=script,
            remembered_login_rejection=remembered,
            codex_home=home,
        ) as scripted:
            await scripted.start(cursor=None)
            await scripted.write_prompt(1, text_message_content("run"))
            await scripted.sink.wait_for_the_turn_to_end()

            assert scripted.sink.error_summaries == [LOGIN_IS_REJECTED_SUMMARY]
            # The operator's line still carries codex's own words, which say where the
            # refusal was seen.
            assert "refresh token was already used" in str(scripted.sink.standard_error_tails)

    _run(exercise)

    assert remembered.applies_to(
        CodexLoginGeneration(account_id=ACCOUNT_ID, last_refresh=REFRESH_STAMP)
    )




















def test_a_rejection_a_turn_found_survives_a_restart(tmp_path: Path) -> None:
    """A refused login is still refused after a deploy, so the fact is written down.

    The process-lifetime memory lost this at every restart, which put the card back to
    "cannot be confirmed" — honest, but it threw away the only evidence anybody had paid
    for, and Panels restarts on every deploy.
    """
    database = tmp_path / "planner.db"
    connection = connect(str(database))
    try:
        create_schema(connection)
    finally:
        connection.close()
    was_rejected = CodexLoginGeneration(account_id=ACCOUNT_ID, last_refresh=REFRESH_STAMP)
    signed_in_again = CodexLoginGeneration(
        account_id=ACCOUNT_ID, last_refresh="2026-10-17T09:00:00Z"
    )

    DurableCodexLoginRejection(str(database)).remember(was_rejected)

    # A different object entirely, as a restarted server would build.
    after_a_restart = DurableCodexLoginRejection(str(database))
    assert after_a_restart.applies_to(was_rejected)
    assert not after_a_restart.applies_to(signed_in_again)

    after_a_restart.forget(signed_in_again)
    assert after_a_restart.applies_to(was_rejected)

    after_a_restart.forget(was_rejected)
    assert not after_a_restart.applies_to(was_rejected)
