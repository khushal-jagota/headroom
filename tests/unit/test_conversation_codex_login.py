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
    CodexAuthReading,
    error_summary_for,
    read_a_failed_turn,
)
from planner.conversation.backends.codex_app_server.client import (
    CodexAppServerClient,
    CodexChildWouldNotStart,
    CodexLoginIsDuplicated,
    child_environment,
)
from planner.conversation.backends.codex_app_server.login import (
    CodexLoginGeneration,
    RememberedCodexLoginRejection,
    codex_home,
    logins_hold_the_same_credential,
    read_stored_codex_login,
)
from planner.conversation.contracts import ConversationBackendKey
from planner.conversation.message_content import text_message_content
from planner.conversation.snapshot import (
    BackendIdentityStatus,
    BackendSnapshotService,
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


def test_a_saved_login_states_its_expiry_its_account_and_its_generation(tmp_path: Path) -> None:
    _write_login(tmp_path / "home")

    stored = read_stored_codex_login(tmp_path / "home")

    assert stored.present and stored.readable
    assert stored.account_label == "someone@example.com"
    assert not stored.access_token_has_expired(now=datetime.now(UTC))
    assert stored.generation == CodexLoginGeneration(
        account_id=ACCOUNT_ID, last_refresh=REFRESH_STAMP
    )


def test_an_expired_access_token_is_read_as_expired(tmp_path: Path) -> None:
    _write_login(tmp_path / "home", expires_at=datetime.now(UTC) - timedelta(minutes=1))

    stored = read_stored_codex_login(tmp_path / "home")

    assert stored.access_token_has_expired(now=datetime.now(UTC))


def test_a_login_that_is_not_there_and_one_that_will_not_parse_are_different(
    tmp_path: Path,
) -> None:
    """Signed out and unreadable are different facts, and a card says the right one."""
    missing = read_stored_codex_login(tmp_path / "empty")

    unreadable_home = tmp_path / "broken"
    unreadable_home.mkdir()
    (unreadable_home / "auth.json").write_text("{not json")
    unreadable = read_stored_codex_login(unreadable_home)

    assert (missing.present, missing.readable) == (False, False)
    assert (unreadable.present, unreadable.readable) == (True, False)


def test_a_login_without_an_account_or_a_refresh_stamp_cannot_be_named(tmp_path: Path) -> None:
    _write_login(tmp_path / "no-account", account_id=None)
    _write_login(tmp_path / "no-stamp", last_refresh=None)

    assert read_stored_codex_login(tmp_path / "no-account").generation is None
    assert read_stored_codex_login(tmp_path / "no-stamp").generation is None


def test_codex_home_follows_the_variable_codex_itself_reads(tmp_path: Path) -> None:
    assert codex_home({"CODEX_HOME": str(tmp_path)}) == tmp_path
    assert codex_home({}) == Path.home() / ".codex"


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


def test_a_shared_api_key_is_also_the_same_credential(tmp_path: Path) -> None:
    real = tmp_path / "real"
    copy = tmp_path / "copy"
    _write_login(real, refresh_token=None, api_key="shared-api-key")
    _write_login(copy, refresh_token=None, api_key="shared-api-key")

    assert logins_hold_the_same_credential(copy, real) is True


def test_different_credentials_and_different_kinds_are_not_duplicates(tmp_path: Path) -> None:
    real = tmp_path / "real"
    its_own = tmp_path / "its-own"
    by_key = tmp_path / "by-key"
    _write_login(real, refresh_token="real-token")
    _write_login(its_own, refresh_token="another-token")
    _write_login(by_key, refresh_token=None, api_key="an-api-key")

    assert logins_hold_the_same_credential(its_own, real) is False
    assert logins_hold_the_same_credential(by_key, real) is False


def test_a_home_with_no_login_holds_nothing_to_duplicate(tmp_path: Path) -> None:
    real = tmp_path / "real"
    _write_login(real)

    assert logins_hold_the_same_credential(tmp_path / "empty", real) is False


def test_a_login_that_will_not_parse_cannot_be_called_a_copy(tmp_path: Path) -> None:
    """Unreadable is not an answer, and the guard must not invent one either way."""
    real = tmp_path / "real"
    _write_login(real)
    broken = tmp_path / "broken"
    broken.mkdir()
    (broken / "auth.json").write_text("{not json")

    assert logins_hold_the_same_credential(broken, real) is None


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


def test_a_scratch_home_with_its_own_login_or_none_at_all_still_starts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_user_home = tmp_path / "user"
    _write_login(fake_user_home / ".codex", refresh_token="the-one-token")
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake_user_home))
    its_own = tmp_path / "its-own"
    _write_login(its_own, refresh_token="a-different-token")
    empty = tmp_path / "empty"
    empty.mkdir()

    assert child_environment(overrides=(("CODEX_HOME", str(its_own)),))["CODEX_HOME"] == str(
        its_own
    )
    assert child_environment(overrides=(("CODEX_HOME", str(empty)),))["CODEX_HOME"] == str(empty)


def test_an_unreadable_scratch_login_is_allowed_rather_than_guessed_at(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Refusing every unparseable file under a test's home would stop innocent work."""
    fake_user_home = tmp_path / "user"
    _write_login(fake_user_home / ".codex")
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake_user_home))
    broken = tmp_path / "broken"
    broken.mkdir()
    (broken / "auth.json").write_text("{not json")

    assert child_environment(overrides=(("CODEX_HOME", str(broken)),))["CODEX_HOME"] == str(broken)


# --- what a failed turn says --------------------------------------------------------------


def test_an_explicit_refusal_is_read_as_a_rejected_login() -> None:
    """The failure that cost twelve days, graded from the two things a turn has."""
    reading = read_a_failed_turn(
        error_summary="workspace routing discovery unauthorized (401)",
        standard_error_this_turn=(
            "codex_login::auth::manager: Failed to refresh token: Your access token could "
            "not be refreshed because your refresh token was already used. Please log out "
            "and sign in again."
        ),
    )

    assert reading is CodexAuthReading.login_is_rejected
    assert error_summary_for(reading, "workspace routing discovery unauthorized (401)") == (
        LOGIN_IS_REJECTED_SUMMARY
    )


def test_a_tools_own_logged_out_message_is_not_codex_saying_it_is_logged_out() -> None:
    """Standard error is the whole child, not codex's verdict.

    A worker runs `gh auth status`, or anything else with its own account, and the words
    land in the same stream. Read loosely, that forges a codex sign-out: the person is
    told to replace a working login, and the card remembers the refusal against it. The
    phrase only counts on a line codex's own login machinery wrote.
    """
    reading = read_a_failed_turn(
        error_summary="the model stopped responding",
        standard_error_this_turn=(
            "running `gh auth status`\nerror: not logged in to github.com\n"
        ),
    )

    assert reading is CodexAuthReading.says_nothing_about_the_login


def test_another_tools_auth_line_is_not_codex_saying_anything(tmp_path: Path) -> None:
    """A line about somebody else's account, in codex's stream, is not codex's verdict.

    Every one of these was accepted as a terminal codex refusal by an earlier reading that
    took any line mentioning auth. The test is codex's own crate path now, so a tool that
    writes its own auth lines cannot send a person to replace a working codex login.
    """
    for said in (
        "github auth: not logged in",
        "other_tool auth: invalid_grant",
        "auth: not logged in",
        "gcloud auth: you are logged out",
        "docker: auth: not logged in to registry",
        "AUTH: not logged in",
    ):
        assert (
            read_a_failed_turn(
                error_summary="the tool exited 1", standard_error_this_turn=said
            )
            is CodexAuthReading.says_nothing_about_the_login
        ), said

    for codex_said in (
        "codex_login::auth::manager: Failed to refresh token: refresh token was already used",
        "2026-10-16T00:22:20.1Z ERROR codex_login::auth: not logged in",
    ):
        assert (
            read_a_failed_turn(error_summary="turn failed", standard_error_this_turn=codex_said)
            is CodexAuthReading.login_is_rejected
        ), codex_said


def test_digits_inside_a_duration_or_a_timestamp_are_not_a_refusal() -> None:
    """401 and 403 are status codes, not substrings to go hunting for."""
    reading = read_a_failed_turn(
        error_summary="tool `pytest` exited 1",
        standard_error_this_turn=(
            "2026-10-16T03:14:01.401Z INFO codex_core: turn finished in 1403ms\n"
        ),
    )

    assert reading is CodexAuthReading.says_nothing_about_the_login


def test_a_bare_refusal_keeps_the_backends_words_and_claims_nothing() -> None:
    reading = read_a_failed_turn(
        error_summary="workspace routing discovery unauthorized (401)",
        standard_error_this_turn=None,
    )

    assert reading is CodexAuthReading.authorization_was_refused
    summary = error_summary_for(reading, "workspace routing discovery unauthorized (401)")
    assert summary is not None
    assert summary.startswith("workspace routing discovery unauthorized (401)")
    assert "may need attention" in summary


def test_an_ordinary_failure_is_left_exactly_as_codex_put_it() -> None:
    reading = read_a_failed_turn(
        error_summary="the model stream disconnected", standard_error_this_turn="thread panicked"
    )

    assert reading is CodexAuthReading.says_nothing_about_the_login
    assert error_summary_for(reading, "the model stream disconnected") == (
        "the model stream disconnected"
    )


def test_a_failure_with_nothing_to_read_claims_nothing() -> None:
    assert (
        read_a_failed_turn(error_summary=None, standard_error_this_turn=None)
        is CodexAuthReading.says_nothing_about_the_login
    )


# --- a rejection belongs to one credential ------------------------------------------------


def test_a_rejection_applies_to_the_credential_it_was_about_and_no_other() -> None:
    remembered = RememberedCodexLoginRejection()
    rejected = CodexLoginGeneration(account_id=ACCOUNT_ID, last_refresh=REFRESH_STAMP)
    signed_in_again = CodexLoginGeneration(
        account_id=ACCOUNT_ID, last_refresh="2026-10-17T09:00:00Z"
    )
    another_account = CodexLoginGeneration(account_id="other", last_refresh=REFRESH_STAMP)

    remembered.remember(rejected)

    assert remembered.applies_to(rejected)
    assert not remembered.applies_to(signed_in_again)
    assert not remembered.applies_to(another_account)
    assert not remembered.applies_to(None)


def test_a_success_clears_only_its_own_credential() -> None:
    remembered = RememberedCodexLoginRejection()
    rejected = CodexLoginGeneration(account_id=ACCOUNT_ID, last_refresh=REFRESH_STAMP)
    remembered.remember(rejected)

    remembered.forget(CodexLoginGeneration(account_id="other", last_refresh=REFRESH_STAMP))
    assert remembered.applies_to(rejected)

    remembered.forget(rejected)
    assert not remembered.applies_to(rejected)


def test_a_credential_that_cannot_be_named_is_never_remembered() -> None:
    remembered = RememberedCodexLoginRejection()

    remembered.remember(None)

    assert not remembered.applies_to(None)


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


def test_a_good_login_is_reported_as_authenticated_under_its_own_account(
    tmp_path: Path,
) -> None:
    _write_login(tmp_path / "home")

    card = _codex_card(_standing(tmp_path / "home"))

    assert card.identity.status is BackendIdentityStatus.authenticated
    assert card.identity.account_label == "someone@example.com"


def test_an_expired_login_is_unconfirmed_rather_than_healthy_or_signed_out(
    tmp_path: Path,
) -> None:
    """The state the old card could not express, and the whole point of the ticket.

    An expired access token is routine — codex refreshes on demand — so this is not a
    fault and must not read as one. It is also not proof of health, which is what the card
    used to claim. The honest answer is that nothing has tested it.
    """
    _write_login(tmp_path / "home", expires_at=datetime.now(UTC) - timedelta(hours=1))

    card = _codex_card(_standing(tmp_path / "home"))

    assert card.identity.status is BackendIdentityStatus.unknown
    assert card.identity.detail is not None
    assert "cannot confirm" in card.identity.detail
    assert card.identity.account_label == "someone@example.com"


def test_a_rejected_login_reads_as_signed_out_and_outranks_a_fresh_token(
    tmp_path: Path,
) -> None:
    """A refusal is the only thing here that was actually tested, so it wins."""
    _write_login(tmp_path / "home")

    card = _codex_card(_standing(tmp_path / "home", rejected=True))

    assert card.identity.status is BackendIdentityStatus.unauthenticated
    assert card.identity.detail == "This saved login was rejected the last time codex used it."
    assert card.identity.login_command == "codex login"


def test_a_missing_login_reads_as_signed_out(tmp_path: Path) -> None:
    card = _codex_card(_standing(tmp_path / "nothing-here"))

    assert card.identity.status is BackendIdentityStatus.unauthenticated


def test_a_login_that_cannot_be_read_says_so(tmp_path: Path) -> None:
    home = tmp_path / "broken"
    home.mkdir()
    (home / "auth.json").write_text("{not json")

    card = _codex_card(_standing(home))

    assert card.identity.status is BackendIdentityStatus.unknown
    assert card.identity.detail is not None
    assert "could not read" in card.identity.detail


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


def test_an_older_authentication_line_cannot_explain_a_later_unrelated_failure(
    tmp_path: Path,
) -> None:
    """The ring buffer is the child's history, not this turn's.

    One turn fails on the refusal, which is right. A later turn fails for its own reason,
    and the whole buffer still holds that refusal line. Reading the buffer would report a
    dead login to somebody whose next failure had nothing to do with it.
    """
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
            },
            {
                "actions": [
                    {
                        "do": "complete",
                        "status": "failed",
                        "error": {"message": "the model stream disconnected"},
                    }
                ]
            },
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
            scripted.sink.expect_another_turn()
            await scripted.write_prompt(2, text_message_content("run again"))
            await scripted.sink.wait_for_the_turn_to_end()

            assert scripted.sink.error_summaries == [
                LOGIN_IS_REJECTED_SUMMARY,
                "the model stream disconnected",
            ]

    _run(exercise)


def test_a_refusal_during_the_handshake_belongs_to_the_turn_that_follows_it(
    tmp_path: Path,
) -> None:
    """A login dead enough to break the handshake must not go ungraded.

    Codex shakes hands, lists models and binds a thread before any turn exists, and a
    refused refresh is announced there. A window that began when the turn began would
    miss it and report a bare 401 instead, so a turn's window begins where the previous
    one ended — and for a child's first turn, that is the child's own beginning.
    """
    home = tmp_path / "codex-home"
    _write_login(home)
    remembered = RememberedCodexLoginRejection()
    script = {
        "stderr": REFRESH_REJECTED_ON_STDERR,
        "turns": [
            {
                "actions": [
                    {
                        "do": "complete",
                        "status": "failed",
                        "error": {"message": "workspace routing discovery unauthorized (401)"},
                    }
                ]
            }
        ],
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

    _run(exercise)

    assert remembered.applies_to(
        CodexLoginGeneration(account_id=ACCOUNT_ID, last_refresh=REFRESH_STAMP)
    )


def test_a_sign_in_that_lands_during_a_failing_turn_leaves_nothing_remembered(
    tmp_path: Path,
) -> None:
    """The turn still says the truth. It just does not speak for the new credential."""
    home = tmp_path / "codex-home"
    _write_login(home)
    remembered = RememberedCodexLoginRejection()
    script = {
        "turns": [
            {
                "actions": [
                    {"do": "say_on_stderr", "text": REFRESH_REJECTED_ON_STDERR},
                    {"do": "sleep", "seconds": 0.4},
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
            # Somebody signs in again while the turn is still winding down.
            _write_login(home, last_refresh="2026-10-17T09:00:00Z")
            await scripted.sink.wait_for_the_turn_to_end()

            assert scripted.sink.error_summaries == [LOGIN_IS_REJECTED_SUMMARY]

    _run(exercise)

    assert not remembered.applies_to(
        CodexLoginGeneration(account_id=ACCOUNT_ID, last_refresh=REFRESH_STAMP)
    )
    assert not remembered.applies_to(
        CodexLoginGeneration(account_id=ACCOUNT_ID, last_refresh="2026-10-17T09:00:00Z")
    )


def test_a_turn_that_works_on_a_new_credential_does_not_vouch_for_the_old_one(
    tmp_path: Path,
) -> None:
    """Two turns, two credentials: clearing is per credential or it is meaningless."""
    home = tmp_path / "codex-home"
    _write_login(home, last_refresh="2026-10-17T09:00:00Z")
    was_rejected = CodexLoginGeneration(account_id=ACCOUNT_ID, last_refresh=REFRESH_STAMP)
    remembered = RememberedCodexLoginRejection()
    remembered.remember(was_rejected)

    async def exercise() -> None:
        async with _scripted_child(
            tmp_path,
            script={"turns": [{"actions": [{"do": "complete", "status": "completed"}]}]},
            remembered_login_rejection=remembered,
            codex_home=home,
        ) as scripted:
            await scripted.start(cursor=None)
            await scripted.write_prompt(1, text_message_content("run"))
            await scripted.sink.wait_for_the_turn_to_end()

    _run(exercise)

    assert remembered.applies_to(was_rejected)
    assert not remembered.applies_to(
        CodexLoginGeneration(account_id=ACCOUNT_ID, last_refresh="2026-10-17T09:00:00Z")
    )


def test_a_refused_duplicate_is_reported_as_a_child_that_would_not_start(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A misconfiguration must reach the person as a failed start, not as a crash.

    Every caller of this door already has words for a child that would not spawn. A
    refusal that sat outside that family would escape them all and surface as an
    unhandled error, which is the opposite of the point of this ticket.
    """
    fake_user_home = tmp_path / "user"
    _write_login(fake_user_home / ".codex", refresh_token="the-one-token")
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake_user_home))
    copied = tmp_path / "scratch"
    _write_login(copied, refresh_token="the-one-token")

    with pytest.raises(CodexChildWouldNotStart):
        child_environment(overrides=(("CODEX_HOME", str(copied)),))


def test_an_unconfirmed_card_does_not_also_claim_the_account_is_unreadable(
    tmp_path: Path,
) -> None:
    """The diagnosis line must not contradict the card above it.

    "Panels could not read who codex is signed in as" is the right thing to say about a
    probe that came back empty. It is wrong about an expired login whose account is named
    right there, so the reader's own reason is used when it has one.
    """
    _write_login(tmp_path / "home", expires_at=datetime.now(UTC) - timedelta(days=2))

    card = _codex_card(_standing(tmp_path / "home"))

    assert card.diagnoses[0] == card.identity.detail
    assert not any("could not read who" in said for said in card.diagnoses)


def test_an_interrupted_turn_does_not_clear_a_rejection(tmp_path: Path) -> None:
    """Somebody pressed stop. That says nothing about the login.

    A dead login is perfectly capable of being present while a person cancels a turn, so
    treating a cancel as a success would throw away the one piece of evidence here that
    was actually tested — and put the card back to claiming a login nothing has checked.
    """
    home = tmp_path / "codex-home"
    _write_login(home)
    was_rejected = CodexLoginGeneration(account_id=ACCOUNT_ID, last_refresh=REFRESH_STAMP)
    remembered = RememberedCodexLoginRejection()
    remembered.remember(was_rejected)
    script = {
        "turns": [
            {"actions": [{"do": "await_interrupt"}, {"do": "complete", "status": "interrupted"}]}
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
            await scripted.child.cancel_running_turn()
            await scripted.sink.wait_for_the_turn_to_end()

    _run(exercise)

    assert remembered.applies_to(was_rejected)


def test_an_unchanged_login_goes_stale_when_its_token_expires_under_a_kept_card(
    tmp_path: Path,
) -> None:
    """Time passing is the change. The file does not have to move.

    This is the reading that an earlier version got wrong by comparing both sides at the
    moment of comparison: after expiry the kept card and the current login agreed that the
    token was expired, so the card never went stale and went on saying signed in for the
    life of the process. The card's reading is of the instant it was probed at.
    """
    home = tmp_path / "codex-home"
    expires_at = datetime.now(UTC) + timedelta(hours=1)
    _write_login(home, expires_at=expires_at)
    standing = CodexLoginStanding(stored=read_stored_codex_login(home), rejected=False)

    while_it_was_good = standing.everything_a_card_reads(now=expires_at - timedelta(minutes=5))
    after_it_expired = standing.everything_a_card_reads(now=expires_at + timedelta(minutes=5))

    assert while_it_was_good != after_it_expired


def test_a_kept_card_is_probed_again_once_its_token_expires(tmp_path: Path) -> None:
    """The same service, the same untouched login file, a clock that moved."""
    home = tmp_path / "codex-home"
    expires_at = datetime.now(UTC) + timedelta(hours=1)
    _write_login(home, expires_at=expires_at)
    reading_time = [expires_at - timedelta(minutes=5)]
    machine = _installed_codex()
    service = BackendSnapshotService(
        machine,
        codex_model_catalog_probe=_codex_that_says_nothing,
        now=lambda: reading_time[0],
    )

    async def exercise() -> None:
        while_it_was_good = await service.snapshot(ConversationBackendKey.codex)
        assert while_it_was_good.identity is not None
        assert while_it_was_good.identity.status is BackendIdentityStatus.authenticated

        reading_time[0] = expires_at + timedelta(minutes=5)
        after_it_expired = await service.snapshot(ConversationBackendKey.codex)
        assert after_it_expired.identity is not None
        assert after_it_expired.identity.status is BackendIdentityStatus.unknown

    with _codex_home_pointed_at(home):
        _run(exercise)


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


def test_a_child_that_dies_on_a_refused_login_still_says_so(tmp_path: Path) -> None:
    """The loudest version of this fault used to be the one that went ungraded.

    A codex that announces a refused refresh and then exits reported only that the process
    ended. The line explaining it was in the tail nobody reads, which is the whole problem
    this ticket exists for.
    """
    home = tmp_path / "codex-home"
    _write_login(home)
    remembered = RememberedCodexLoginRejection()
    script = {
        "turns": [
            {
                "actions": [
                    {"do": "say_on_stderr", "text": REFRESH_REJECTED_ON_STDERR},
                    {"do": "die", "stderr": "", "code": 7},
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

    _run(exercise)

    assert remembered.applies_to(
        CodexLoginGeneration(account_id=ACCOUNT_ID, last_refresh=REFRESH_STAMP)
    )


def test_a_child_that_dies_for_its_own_reasons_still_says_that(tmp_path: Path) -> None:
    """Grading the exit must not turn every dead child into a login problem."""
    home = tmp_path / "codex-home"
    _write_login(home)
    script = {"turns": [{"actions": [{"do": "die", "stderr": "thread panicked\n", "code": 7}]}]}

    async def exercise() -> None:
        async with _scripted_child(tmp_path, script=script, codex_home=home) as scripted:
            await scripted.start(cursor=None)
            await scripted.write_prompt(1, text_message_content("run"))
            await scripted.sink.wait_for_the_turn_to_end()

            assert scripted.sink.error_summaries == [
                "the codex process ended while the turn was running"
            ]

    _run(exercise)
