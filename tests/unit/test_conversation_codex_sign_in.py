"""Signing a codex account back in from Panels, and refusing to guess about it.

Every login file here is written by this module into a temporary directory. The token
strings are invented and signed with nothing. Nothing in this file reads the real login,
completes a real sign-in, or reaches the network: the app-server on the other end is a
scripted child process that answers to order.

What is under test is the part that is easy to get wrong. Starting a sign-in is one call.
Knowing what happened afterwards is a question about ordering — a completion that overtakes
its own start response, a completion racing a cancel in both directions, a child that dies,
a reaper that fires against a session that has already been replaced — and about honesty:
codex reporting success is not the same as Panels being able to see a usable credential.
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
from collections.abc import Awaitable, Callable, Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest
from tests.support.conversation_codex_sign_in_double import (
    SCRIPT_PATH_ENVIRONMENT_NAME,
    sign_in_double_launch,
)

from planner.conversation.backends.codex_app_server.login import (
    RememberedCodexLoginRejection,
    read_stored_codex_login,
)
from planner.conversation.backends.codex_app_server.login_session import (
    CodexSignInService,
    CodexSignInUnavailable,
    SignInBounds,
    SignInState,
    UnknownSignInSession,
)


def _run(exercise: Callable[[], Awaitable[None]], *, seconds: float = 30.0) -> None:
    asyncio.run(asyncio.wait_for(exercise(), seconds))


# --- the synthetic login ------------------------------------------------------------------


def _claims(payload: dict[str, Any]) -> str:
    encoded = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")
    return f"eyJhbGciOiJub25lIn0.{encoded}.c2lnbmF0dXJl"


def _write_login(home: Path, *, account_id: str = "account-1", last_refresh: str) -> None:
    """A saved codex login, invented here. Nothing in it has ever been a real token."""
    home.mkdir(parents=True, exist_ok=True)
    (home / "auth.json").write_text(
        json.dumps(
            {
                "tokens": {
                    "id_token": _claims({"email": "owner@example.invalid"}),
                    "access_token": _claims({"exp": 4102444800}),
                    "refresh_token": f"synthetic-refresh-{last_refresh}",
                    "account_id": account_id,
                },
                "last_refresh": last_refresh,
            }
        )
    )


@contextmanager
def _codex_home(home: Path) -> Iterator[None]:
    before = os.environ.get("CODEX_HOME")
    os.environ["CODEX_HOME"] = str(home)
    try:
        yield
    finally:
        if before is None:
            os.environ.pop("CODEX_HOME", None)
        else:
            os.environ["CODEX_HOME"] = before


@contextmanager
def _scripted_sign_in(
    tmp_path: Path, script: dict[str, Any]
) -> Iterator[Callable[[], Awaitable[Sequence[str] | None]]]:
    """A sign-in app-server that behaves as this script says, and the argv to reach it."""
    script_path = tmp_path / "sign-in-script.json"
    script_path.write_text(json.dumps(script))
    argv, overrides = sign_in_double_launch(script_path)
    before = {name: os.environ.get(name) for name in overrides}
    os.environ.update(overrides)

    async def resolve_argv() -> Sequence[str] | None:
        return argv

    try:
        yield resolve_argv
    finally:
        for name, value in before.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


def _service(
    resolve_argv: Callable[[], Awaitable[Sequence[str] | None]],
    *,
    rejection: RememberedCodexLoginRejection | None = None,
    bounds: SignInBounds | None = None,
) -> CodexSignInService:
    return CodexSignInService(
        resolve_argv=resolve_argv,
        remembered_login_rejection=rejection,
        bounds=bounds or SignInBounds(child_startup_seconds=15.0, session_seconds=600.0),
        read_login=read_stored_codex_login,
    )


# --- starting -----------------------------------------------------------------------------


def test_a_started_sign_in_hands_back_the_url_and_the_code(tmp_path: Path) -> None:
    """The two things a person needs to finish on another device, and a session to cancel."""
    home = tmp_path / "home"
    _write_login(home, last_refresh="before")
    script = {"user_code": "ABCD-1234", "verification_url": "https://example.invalid/device"}
    with _codex_home(home), _scripted_sign_in(tmp_path, script) as resolve_argv:
        service = _service(resolve_argv)

        async def exercise() -> None:
            started = await service.start()
            assert started.state is SignInState.pending
            assert started.verification_url == "https://example.invalid/device"
            assert started.user_code == "ABCD-1234"
            assert started.session_id
            assert started.deadline_at > started.started_at
            assert service.read() == started
            await service.shutdown()

        _run(exercise)


def test_starting_twice_hands_back_the_same_sign_in(tmp_path: Path) -> None:
    """Two one-time codes would mean one of them is a code nobody will honour."""
    home = tmp_path / "home"
    _write_login(home, last_refresh="before")
    with _codex_home(home), _scripted_sign_in(tmp_path, {}) as resolve_argv:
        service = _service(resolve_argv)

        async def exercise() -> None:
            first, second = await asyncio.gather(service.start(), service.start())
            assert first.session_id == second.session_id
            assert first.user_code == second.user_code
            await service.shutdown()

        _run(exercise)


def test_a_child_that_will_not_start_is_said_rather_than_raised_bare(tmp_path: Path) -> None:
    """No codex means no sign-in, and the surface is told that rather than a stack trace."""
    home = tmp_path / "home"
    _write_login(home, last_refresh="before")

    async def no_codex() -> Sequence[str] | None:
        return None

    with _codex_home(home):
        service = _service(no_codex)

        async def exercise() -> None:
            with pytest.raises(CodexSignInUnavailable):
                await service.start()
            # A failed start leaves a terminal session rather than a pending one, so a
            # page does not sit watching a sign-in that never began.
            read = service.read()
            assert read is not None
            assert read.state is SignInState.ended

        _run(exercise)


# --- success is a completion and a reading that agrees ------------------------------------


def test_a_success_whose_reading_shows_a_new_credential_is_signed_in(tmp_path: Path) -> None:
    """The whole point: a finished sign-in reads as signed in with no restart."""
    home = tmp_path / "home"
    _write_login(home, last_refresh="before")
    script = {"complete_after_start_response": {"success": True}}
    with _codex_home(home), _scripted_sign_in(tmp_path, script) as resolve_argv:
        service = _service(resolve_argv)

        async def exercise() -> None:
            await service.start()
            # The browser step is what writes a new credential. Here that is this line.
            _write_login(home, last_refresh="after")
            await _until_terminal(service)
            settled = service.read()
            assert settled is not None
            assert settled.state is SignInState.confirmed
            assert settled.account_label == "owner@example.invalid"
            # A terminal session stops presenting the code.
            assert settled.user_code is None
            assert settled.verification_url is None

        _run(exercise)


def test_a_success_whose_reading_shows_the_same_credential_is_unconfirmed(
    tmp_path: Path,
) -> None:
    """Codex said it worked and nothing changed. Claiming signed in here would be a lie."""
    home = tmp_path / "home"
    _write_login(home, last_refresh="before")
    script = {"complete_after_start_response": {"success": True}}
    with _codex_home(home), _scripted_sign_in(tmp_path, script) as resolve_argv:
        service = _service(resolve_argv)

        async def exercise() -> None:
            await service.start()
            await _until_terminal(service)
            settled = service.read()
            assert settled is not None
            assert settled.state is SignInState.unconfirmed
            assert settled.detail is not None
            assert "still holds the credential" in settled.detail

        _run(exercise)


def test_a_success_whose_credential_is_already_refused_is_unconfirmed(tmp_path: Path) -> None:
    """A new credential codex has already refused is not a working account."""
    home = tmp_path / "home"
    _write_login(home, last_refresh="before")
    rejection = RememberedCodexLoginRejection()
    with _codex_home(home), _scripted_sign_in(
        tmp_path, {"complete_after_start_response": {"success": True}}
    ) as resolve_argv:
        service = _service(resolve_argv, rejection=rejection)

        async def exercise() -> None:
            await service.start()
            _write_login(home, last_refresh="after")
            rejection.remember(read_stored_codex_login(home).generation)
            await _until_terminal(service)
            settled = service.read()
            assert settled is not None
            assert settled.state is SignInState.unconfirmed
            assert settled.detail is not None
            assert "already refused" in settled.detail

        _run(exercise)


def test_a_confirmed_sign_in_never_clears_a_rejection_by_hand(tmp_path: Path) -> None:
    """A rejection lapses because the generation moved, not because anyone forgot it.

    The memory is handed in already holding a rejection against the old credential. After a
    confirmed sign-in the old generation must still read as rejected: nothing called
    ``forget``, and the new credential is clean only because it is a different one.
    """
    home = tmp_path / "home"
    _write_login(home, last_refresh="before")
    rejection = RememberedCodexLoginRejection()
    old_generation = read_stored_codex_login(home).generation
    rejection.remember(old_generation)
    with _codex_home(home), _scripted_sign_in(
        tmp_path, {"complete_after_start_response": {"success": True}}
    ) as resolve_argv:
        service = _service(resolve_argv, rejection=rejection)

        async def exercise() -> None:
            await service.start()
            _write_login(home, last_refresh="after")
            await _until_terminal(service)
            settled = service.read()
            assert settled is not None
            assert settled.state is SignInState.confirmed
            assert rejection.applies_to(old_generation)
            assert not rejection.applies_to(read_stored_codex_login(home).generation)

        _run(exercise)


# --- ordering -----------------------------------------------------------------------------


def test_a_completion_that_overtakes_the_start_response_is_not_lost(tmp_path: Path) -> None:
    """The child may notify before the request it answers returns. Nothing is dropped."""
    home = tmp_path / "home"
    _write_login(home, last_refresh="before")
    script = {"complete_before_start_response": {"success": False, "error": "nope"}}
    with _codex_home(home), _scripted_sign_in(tmp_path, script) as resolve_argv:
        service = _service(resolve_argv)

        async def exercise() -> None:
            started = await service.start()
            # Either the parked completion settled it inside start, or it settles
            # immediately after. Both are the same promise: it is never ignored.
            if started.state is SignInState.pending:
                await _until_terminal(service)
            settled = service.read()
            assert settled is not None
            assert settled.state is SignInState.ended
            assert settled.detail is not None
            assert "nope" in settled.detail

        _run(exercise)


def test_a_cancel_that_wins_the_race_still_reads_the_login(tmp_path: Path) -> None:
    """``canceled`` is not evidence. The browser step can land in the same instant."""
    home = tmp_path / "home"
    _write_login(home, last_refresh="before")
    with _codex_home(home), _scripted_sign_in(tmp_path, {}) as resolve_argv:
        service = _service(resolve_argv)

        async def exercise() -> None:
            started = await service.start()
            # The credential lands while the cancel is on its way.
            _write_login(home, last_refresh="after")
            settled = await service.cancel(started.session_id)
            assert settled.state is SignInState.ended
            # The reading happened: the account label comes from the file, not the cancel.
            assert settled.account_label == "owner@example.invalid"

        _run(exercise)


def test_a_completion_after_a_cancel_does_not_rewrite_the_terminal_state(
    tmp_path: Path,
) -> None:
    """A late success is still read, and still does not resurrect a session that ended."""
    home = tmp_path / "home"
    _write_login(home, last_refresh="before")
    script = {"complete_after_cancel": {"success": True}}
    with _codex_home(home), _scripted_sign_in(tmp_path, script) as resolve_argv:
        service = _service(resolve_argv)

        async def exercise() -> None:
            started = await service.start()
            settled = await service.cancel(started.session_id)
            assert settled.state is SignInState.ended
            await asyncio.sleep(0.4)
            after = service.read()
            assert after is not None
            assert after.state is SignInState.ended

        _run(exercise)


def test_a_cancel_answered_not_found_is_not_read_as_failure(tmp_path: Path) -> None:
    """``notFound`` says the login id is unknown to codex. It says nothing about the file."""
    home = tmp_path / "home"
    _write_login(home, last_refresh="before")
    with _codex_home(home), _scripted_sign_in(
        tmp_path, {"cancel_status": "notFound"}
    ) as resolve_argv:
        service = _service(resolve_argv)

        async def exercise() -> None:
            started = await service.start()
            _write_login(home, last_refresh="after")
            settled = await service.cancel(started.session_id)
            assert settled.state is SignInState.ended
            assert settled.account_label == "owner@example.invalid"

        _run(exercise)


def test_a_child_that_exits_mid_session_ends_it_without_calling_it_a_failure(
    tmp_path: Path,
) -> None:
    """A child can die after writing a credential, so its exit is not read as failure."""
    home = tmp_path / "home"
    _write_login(home, last_refresh="before")
    with _codex_home(home), _scripted_sign_in(
        tmp_path, {"exit_after_start_response": True}
    ) as resolve_argv:
        service = _service(resolve_argv)

        async def exercise() -> None:
            await service.start()
            await _until_terminal(service)
            settled = service.read()
            assert settled is not None
            assert settled.state is SignInState.ended
            assert settled.detail is not None
            assert "stopped before it finished" in settled.detail

        _run(exercise)


# --- stale ids and replaced sessions ------------------------------------------------------


def test_a_stale_session_id_cannot_cancel_the_sign_in_in_force(tmp_path: Path) -> None:
    """A page reconnecting after a reap must not cancel a sign-in somebody just started."""
    home = tmp_path / "home"
    _write_login(home, last_refresh="before")
    with _codex_home(home), _scripted_sign_in(tmp_path, {}) as resolve_argv:
        service = _service(resolve_argv)

        async def exercise() -> None:
            first = await service.start()
            await service.cancel(first.session_id)
            second = await service.start()
            assert second.session_id != first.session_id
            with pytest.raises(UnknownSignInSession):
                await service.cancel(first.session_id)
            still_pending = service.read()
            assert still_pending is not None
            assert still_pending.state is SignInState.pending
            await service.shutdown()

        _run(exercise)


def test_a_session_that_replaced_an_ended_one_is_not_cleared_by_it(tmp_path: Path) -> None:
    """A sign-in started after an older one ended is not touched by the older one.

    The first session is left to reach Panels' deadline, so its reaper has fired and its
    child has been stopped. Only then is a second sign-in started. The second must be
    pending and carry its own code: nothing from the first reaches it.
    """
    home = tmp_path / "home"
    _write_login(home, last_refresh="before")
    with _codex_home(home), _scripted_sign_in(tmp_path, {}) as resolve_argv:
        service = _service(
            resolve_argv,
            bounds=SignInBounds(child_startup_seconds=15.0, session_seconds=0.5),
        )

        async def exercise() -> None:
            first = await service.start()
            await _until_terminal(service, seconds=8.0)
            second = await service.start()
            assert second.session_id != first.session_id
            assert second.state is SignInState.pending
            assert second.user_code is not None
            # The first session's id is gone for good, and cannot end the second.
            with pytest.raises(UnknownSignInSession):
                await service.cancel(first.session_id)
            still = service.read()
            assert still is not None
            assert still.session_id == second.session_id
            assert still.state is SignInState.pending
            await service.shutdown()

        _run(exercise)


def test_a_completion_for_another_login_id_is_dropped(tmp_path: Path) -> None:
    """Correlation, at the only seam a test can reach it: a foreign id changes nothing."""
    home = tmp_path / "home"
    _write_login(home, last_refresh="before")
    script = {
        "complete_after_start_response": {"success": True, "login_id": "some-other-login"}
    }
    with _codex_home(home), _scripted_sign_in(tmp_path, script) as resolve_argv:
        service = _service(resolve_argv)

        async def exercise() -> None:
            await service.start()
            _write_login(home, last_refresh="after")
            await asyncio.sleep(0.5)
            still = service.read()
            assert still is not None
            # The foreign completion was dropped, so this is still pending rather than
            # confirmed by somebody else's sign-in.
            assert still.state is SignInState.pending
            await service.shutdown()

        _run(exercise)


# --- Panels' own deadline -----------------------------------------------------------------


def test_panels_stops_presenting_the_code_at_its_own_deadline(tmp_path: Path) -> None:
    """Panels' bound, not upstream's. The double never completes and never expires."""
    home = tmp_path / "home"
    _write_login(home, last_refresh="before")
    with _codex_home(home), _scripted_sign_in(tmp_path, {}) as resolve_argv:
        service = _service(
            resolve_argv,
            bounds=SignInBounds(child_startup_seconds=15.0, session_seconds=0.5),
        )

        async def exercise() -> None:
            await service.start()
            await _until_terminal(service, seconds=8.0)
            settled = service.read()
            assert settled is not None
            assert settled.state is SignInState.ended
            assert settled.detail is not None
            assert "its own time limit" in settled.detail
            assert settled.user_code is None

        _run(exercise)


def test_a_start_that_never_answers_is_bounded_and_cleaned_up(tmp_path: Path) -> None:
    """A child that comes up and then says nothing must not hold a sign-in open forever."""
    home = tmp_path / "home"
    _write_login(home, last_refresh="before")
    with _codex_home(home), _scripted_sign_in(
        tmp_path, {"never_answer_start": True}
    ) as resolve_argv:
        service = _service(
            resolve_argv,
            bounds=SignInBounds(child_startup_seconds=0.6, session_seconds=600.0),
        )

        async def exercise() -> None:
            with pytest.raises(CodexSignInUnavailable):
                await service.start()
            read = service.read()
            assert read is not None
            assert read.state is SignInState.ended

        _run(exercise)


# --- the saved login is not touched by a cancelled sign-in --------------------------------


def test_a_started_and_cancelled_sign_in_leaves_the_saved_login_alone(tmp_path: Path) -> None:
    """Panels' own path writes nothing. This is the promise the surface makes to a user."""
    home = tmp_path / "home"
    _write_login(home, last_refresh="before")
    before = (home / "auth.json").read_bytes()
    with _codex_home(home), _scripted_sign_in(tmp_path, {}) as resolve_argv:
        service = _service(resolve_argv)

        async def exercise() -> None:
            started = await service.start()
            assert (home / "auth.json").read_bytes() == before
            await service.cancel(started.session_id)
            assert (home / "auth.json").read_bytes() == before

        _run(exercise)


# --- nothing secret is kept ---------------------------------------------------------------


def test_a_terminal_session_stops_carrying_the_code(tmp_path: Path) -> None:
    """The one-time code is for finishing a sign-in. A finished one has no use for it."""
    home = tmp_path / "home"
    _write_login(home, last_refresh="before")
    with _codex_home(home), _scripted_sign_in(tmp_path, {}) as resolve_argv:
        service = _service(resolve_argv)

        async def exercise() -> None:
            started = await service.start()
            assert started.user_code is not None
            settled = await service.cancel(started.session_id)
            assert settled.user_code is None
            assert settled.verification_url is None

        _run(exercise)


# --- the small shared wait ----------------------------------------------------------------


async def _until_terminal(service: CodexSignInService, *, seconds: float = 10.0) -> None:
    """Wait for the session to settle, rather than sleeping a guessed amount."""
    deadline = asyncio.get_running_loop().time() + seconds
    while asyncio.get_running_loop().time() < deadline:
        read = service.read()
        if read is not None and read.state.is_terminal:
            return
        await asyncio.sleep(0.05)
    raise AssertionError("the sign-in never settled")


def test_the_double_is_reached_through_its_environment_name(tmp_path: Path) -> None:
    """A guard on the harness itself: the script path is what makes the double behave."""
    with _scripted_sign_in(tmp_path, {"user_code": "X"}):
        assert os.environ[SCRIPT_PATH_ENVIRONMENT_NAME].endswith("sign-in-script.json")


# --- the routes a page actually calls -----------------------------------------------------
#
# The service above is driven directly, which is where the ordering lives. These exercise
# the three routes over the real router, because a page's whole contract with this feature
# is those three answers and the status codes that go with them.


@contextmanager
def _routed(
    tmp_path: Path, script: dict[str, Any], *, wired: bool = True
) -> Iterator[tuple[Any, CodexSignInService | None]]:
    from fastapi import FastAPI

    from planner.conversation.api import router
    from planner.core.db import connect, create_schema

    home = tmp_path / "home"
    _write_login(home, last_refresh="before")
    database = tmp_path / "sign-in-routes.db"
    connection = connect(str(database))
    create_schema(connection)
    connection.close()

    with _codex_home(home), _scripted_sign_in(tmp_path, script) as resolve_argv:
        service = _service(resolve_argv) if wired else None
        app = FastAPI()
        app.include_router(router, prefix="/api/conversation")
        app.state.conversation = _runtime_with_sign_in(database, service)
        yield app, service
        if service is not None:
            asyncio.run(service.shutdown())


def _runtime_with_sign_in(database: Path, service: CodexSignInService | None) -> Any:
    """A runtime carrying only what these three routes read."""
    from planner.conversation.api import ConversationRuntime
    from planner.conversation.backend_usage import BackendUsageService
    from planner.conversation.contracts import ConversationBackendKey
    from planner.conversation.live_tail import ConversationLiveTail
    from planner.conversation.message_files import ConversationMessageFiles
    from planner.conversation.snapshot import BackendSnapshotService
    from planner.conversation.storage import ConversationStore
    from planner.conversation.system import SqliteProcessConversationSystem

    store = ConversationStore(str(database))
    live_tail = ConversationLiveTail()
    message_files = ConversationMessageFiles(str(database))
    return ConversationRuntime(
        store=store,
        system=SqliteProcessConversationSystem(
            store=store,
            message_files=message_files,
            # No conversation is started here, but the system requires one factory per
            # backend, so it is built the way production builds it.
            backend_child_factories=dict.fromkeys(ConversationBackendKey, _never_spawns),
            live_tail=live_tail,
        ),
        live_tail=live_tail,
        message_files=message_files,
        database_path=str(database),
        backend_snapshots=BackendSnapshotService(),
        backend_usage=BackendUsageService({}),
        sse_heartbeat_ms=1000,
        codex_sign_in=service,
    )


def _never_spawns(**_unused: Any) -> Any:
    """These exercises touch no conversation, so a child is never asked for."""
    raise AssertionError("a sign-in exercise started a conversation")


def _asgi(app: Any) -> Any:
    import httpx

    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://conversation"
    )


def test_the_routes_start_read_and_cancel_one_sign_in(tmp_path: Path) -> None:
    """Start hands over the code, read hands back the same one, cancel ends it."""
    with _routed(tmp_path, {"user_code": "WXYZ-9999"}) as (app, _service_under_test):

        async def exercise() -> None:
            async with _asgi(app) as client:
                started = await client.post("/api/conversation/backends/codex/sign-in")
                assert started.status_code == 200
                body = started.json()["sign_in"]
                assert body["state"] == "pending"
                assert body["user_code"] == "WXYZ-9999"
                assert body["verification_url"]
                assert body["deadline_at"] > body["started_at"]

                read = await client.get("/api/conversation/backends/codex/sign-in")
                assert read.json()["sign_in"]["session_id"] == body["session_id"]

                ended = await client.delete(
                    f"/api/conversation/backends/codex/sign-in/{body['session_id']}"
                )
                assert ended.status_code == 200
                assert ended.json()["sign_in"]["state"] == "ended"
                # A terminal sign-in no longer carries the code.
                assert ended.json()["sign_in"]["user_code"] is None

        _run(exercise)


def test_starting_twice_over_the_routes_returns_one_code(tmp_path: Path) -> None:
    """The second press is not an error, and does not mint a code nobody will honour."""
    with _routed(tmp_path, {}) as (app, _unused):

        async def exercise() -> None:
            async with _asgi(app) as client:
                first = (await client.post("/api/conversation/backends/codex/sign-in")).json()
                second = (await client.post("/api/conversation/backends/codex/sign-in")).json()
                assert first["sign_in"]["session_id"] == second["sign_in"]["session_id"]
                assert first["sign_in"]["user_code"] == second["sign_in"]["user_code"]

        _run(exercise)


def test_a_stale_session_id_is_refused_by_the_route(tmp_path: Path) -> None:
    """A page holding an old id gets a conflict, not somebody else's sign-in cancelled."""
    with _routed(tmp_path, {}) as (app, _unused):

        async def exercise() -> None:
            async with _asgi(app) as client:
                first = (await client.post("/api/conversation/backends/codex/sign-in")).json()
                session_id = first["sign_in"]["session_id"]
                await client.delete(f"/api/conversation/backends/codex/sign-in/{session_id}")
                await client.post("/api/conversation/backends/codex/sign-in")
                refused = await client.delete(
                    f"/api/conversation/backends/codex/sign-in/{session_id}"
                )
                assert refused.status_code == 409
                still = await client.get("/api/conversation/backends/codex/sign-in")
                assert still.json()["sign_in"]["state"] == "pending"

        _run(exercise)


def test_a_backend_panels_cannot_sign_in_says_so(tmp_path: Path) -> None:
    """Claude keeps its command-line text. A route that accepted it would be a promise."""
    with _routed(tmp_path, {}) as (app, _unused):

        async def exercise() -> None:
            async with _asgi(app) as client:
                refused = await client.post("/api/conversation/backends/claude/sign-in")
                assert refused.status_code == 404
                assert "cannot be signed in" in refused.json()["detail"]

        _run(exercise)


def test_a_process_with_no_sign_in_service_refuses_rather_than_pretends(
    tmp_path: Path,
) -> None:
    """Absent is a real state, and the route says it instead of answering with nothing."""
    with _routed(tmp_path, {}, wired=False) as (app, _unused):

        async def exercise() -> None:
            async with _asgi(app) as client:
                refused = await client.post("/api/conversation/backends/codex/sign-in")
                assert refused.status_code == 404

        _run(exercise)


def test_reading_before_anything_started_says_there_is_no_sign_in(tmp_path: Path) -> None:
    """Nothing is persisted, so a sign-in nobody started is simply absent."""
    with _routed(tmp_path, {}) as (app, _unused):

        async def exercise() -> None:
            async with _asgi(app) as client:
                read = await client.get("/api/conversation/backends/codex/sign-in")
                assert read.status_code == 200
                assert read.json()["sign_in"] is None

        _run(exercise)
