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
from datetime import UTC, datetime
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


def _write_login(
    home: Path,
    *,
    account_id: str = "account-1",
    last_refresh: str,
    access_token_expires_at: int = 4102444800,
) -> None:
    """A saved codex login, invented here. Nothing in it has ever been a real token."""
    home.mkdir(parents=True, exist_ok=True)
    (home / "auth.json").write_text(
        json.dumps(
            {
                "tokens": {
                    "id_token": _claims({"email": "owner@example.invalid"}),
                    "access_token": _claims({"exp": access_token_expires_at}),
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
    now: Callable[[], datetime] | None = None,
) -> CodexSignInService:
    return CodexSignInService(
        resolve_argv=resolve_argv,
        remembered_login_rejection=rejection,
        bounds=bounds or SignInBounds(child_startup_seconds=15.0, session_seconds=600.0),
        read_login=read_stored_codex_login,
        now=now or (lambda: datetime(2026, 10, 16, tzinfo=UTC)),
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


def test_a_success_whose_new_credential_is_already_expired_is_unconfirmed(
    tmp_path: Path,
) -> None:
    """A new credential that cannot be shown to work is not a sign-in.

    The clock is held still, and the credential written by the browser step carries an
    expiry in the past. It is genuinely a different generation, so the generation check
    alone would call this signed in. The card would call the same login ``unknown``, and a
    sign-in must not claim more than the card will.
    """
    home = tmp_path / "home"
    _write_login(home, last_refresh="before")
    frozen = datetime(2026, 10, 16, 12, 0, tzinfo=UTC)
    script = {"complete_after_start_response": {"success": True}}
    with _codex_home(home), _scripted_sign_in(tmp_path, script) as resolve_argv:
        service = _service(resolve_argv, now=lambda: frozen)

        async def exercise() -> None:
            await service.start()
            _write_login(home, last_refresh="after", access_token_expires_at=1)
            await _until_terminal(service)
            settled = service.read()
            assert settled is not None
            assert settled.state is SignInState.unconfirmed
            assert settled.detail is not None
            assert "already" in settled.detail
            assert "expired" in settled.detail

        _run(exercise)


def test_the_sign_in_and_the_identity_card_agree_about_an_expired_credential(
    tmp_path: Path,
) -> None:
    """Two readers of one file, and neither is allowed to say signed in.

    The sign-in refuses to confirm a credential that arrives already expired. The card
    reads the same file and answers ``unknown`` with its own reason. Both are asserted
    here because the defect this covers was the sign-in claiming more than the card will.
    """
    from planner.conversation.snapshot import (
        BackendIdentityStatus,
        CodexLoginStanding,
        CommandOutcome,
        _IdentityRequest,
        _read_codex_identity,
    )

    home = tmp_path / "home"
    _write_login(home, last_refresh="before")
    frozen = datetime(2026, 10, 16, 12, 0, tzinfo=UTC)
    script = {"complete_after_start_response": {"success": True}}
    with _codex_home(home), _scripted_sign_in(tmp_path, script) as resolve_argv:
        service = _service(resolve_argv, now=lambda: frozen)

        async def exercise() -> None:
            await service.start()
            _write_login(home, last_refresh="after", access_token_expires_at=1)
            await _until_terminal(service)

        _run(exercise)

    settled = service.read()
    assert settled is not None
    card = _read_codex_identity(
        _IdentityRequest(
            outcome=CommandOutcome(
                exit_code=0, standard_output="Logged in using ChatGPT", standard_error=""
            ),
            codex_login=CodexLoginStanding(
                stored=read_stored_codex_login(home), rejected=False
            ),
            now=frozen,
        )
    )

    # The same file, read two ways, and the two readings agree.
    assert settled.state is SignInState.unconfirmed
    assert card.status is BackendIdentityStatus.unknown


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


def test_a_success_delivered_while_a_cancel_is_settling_is_not_lost(tmp_path: Path) -> None:
    """The defect this test used to accept.

    The child answers the cancel and then reports success, so the completion is delivered
    while ``cancel`` owns the service lock and the consumer that would have read it has
    just been stopped. An earlier version dropped it and reported ``ended``, throwing away
    the one piece of evidence that a credential had been written. The credential is real
    here — a new generation is on disk — so the honest answer is ``confirmed``.
    """
    home = tmp_path / "home"
    _write_login(home, last_refresh="before")
    script = {"complete_after_cancel": {"success": True}}
    with _codex_home(home), _scripted_sign_in(tmp_path, script) as resolve_argv:
        service = _service(resolve_argv)

        async def exercise() -> None:
            started = await service.start()
            # The browser step landed in the same instant as the cancel.
            _write_login(home, last_refresh="after")
            settled = await service.cancel(started.session_id)
            if settled.state is SignInState.ended:
                # The completion may still be in flight when cancel returns. It must
                # arrive, and it must be taken in.
                await asyncio.sleep(0.6)
                settled = service.read() or settled
            assert settled.state is SignInState.confirmed
            assert settled.account_label == "owner@example.invalid"

        _run(exercise)


def test_a_success_delivered_while_the_consumer_waits_for_the_lock_is_not_lost(
    tmp_path: Path,
) -> None:
    """The second ordering, and the narrowest one.

    The child reports success and only then answers the cancel, so the completion is
    delivered while ``cancel`` is still holding the service lock waiting for that answer.
    The consumer is woken and blocks on the lock. Settling then cancels that consumer.

    An earlier version took the event off a queue before waiting for the lock, so the
    consumer held the only copy in a local variable and cancelling it destroyed the
    evidence: the drain found an empty queue and reported ``ended``. Nothing is removed
    outside the lock now, so the event is still there to be taken in.
    """
    home = tmp_path / "home"
    _write_login(home, last_refresh="before")
    script = {
        "complete_before_cancel_answer": {"success": True},
        "cancel_delay_seconds": 0.5,
    }
    with _codex_home(home), _scripted_sign_in(tmp_path, script) as resolve_argv:
        service = _service(resolve_argv)

        async def exercise() -> None:
            started = await service.start()
            # The browser step landed. The credential is really on disk.
            _write_login(home, last_refresh="after")
            settled = await service.cancel(started.session_id)
            assert settled.state is SignInState.confirmed
            assert settled.account_label == "owner@example.invalid"

        _run(exercise)


def test_a_failure_delivered_while_the_consumer_waits_for_the_lock_still_ends_it(
    tmp_path: Path,
) -> None:
    """The same ordering with no credential behind it must not become a success."""
    home = tmp_path / "home"
    _write_login(home, last_refresh="before")
    script = {
        "complete_before_cancel_answer": {"success": False, "error": "user declined"},
        "cancel_delay_seconds": 0.5,
    }
    with _codex_home(home), _scripted_sign_in(tmp_path, script) as resolve_argv:
        service = _service(resolve_argv)

        async def exercise() -> None:
            started = await service.start()
            settled = await service.cancel(started.session_id)
            assert settled.state is SignInState.ended
            assert settled.detail is not None
            assert "user declined" in settled.detail

        _run(exercise)


def test_a_foreign_success_delivered_in_that_same_window_is_dropped(
    tmp_path: Path,
) -> None:
    """Closing the window did not weaken correlation inside it."""
    home = tmp_path / "home"
    _write_login(home, last_refresh="before")
    script = {
        "complete_before_cancel_answer": {"success": True, "login_id": "some-other-login"},
        "cancel_delay_seconds": 0.5,
    }
    with _codex_home(home), _scripted_sign_in(tmp_path, script) as resolve_argv:
        service = _service(resolve_argv)

        async def exercise() -> None:
            started = await service.start()
            _write_login(home, last_refresh="after")
            settled = await service.cancel(started.session_id)
            assert settled.state is SignInState.ended

        _run(exercise)


def test_a_success_delivered_while_the_server_shuts_down_is_not_lost(tmp_path: Path) -> None:
    """Shutdown drains an already delivered success before the consumer can take it."""
    home = tmp_path / "home"
    _write_login(home, last_refresh="before")
    with _service_over_a_test_client(home) as (service, made):

        async def exercise() -> None:
            await service.start()
            _write_login(home, last_refresh="after")
            # A start response does not guarantee that a later wire notification arrived.
            # Deliver without yielding, so shutdown must drain the pending inbox itself.
            await _deliver_together(made[0], _completion(success=True))
            pending = service.read()
            assert pending is not None
            assert pending.state is SignInState.pending
            await service.shutdown()
            settled = service.read()
            assert settled is not None
            assert settled.state is SignInState.confirmed
            assert made[0].stopped

        _run(exercise)


def test_a_success_not_delivered_before_shutdown_cannot_confirm_it(tmp_path: Path) -> None:
    """A changed login alone, or a completion after shutdown, is not a reported success."""
    home = tmp_path / "home"
    _write_login(home, last_refresh="before")
    with _service_over_a_test_client(home) as (service, made):

        async def exercise() -> None:
            await service.start()
            _write_login(home, last_refresh="after")
            release_completion = asyncio.Event()
            reader_waiting = asyncio.Event()

            async def deliver_after_shutdown() -> None:
                reader_waiting.set()
                await release_completion.wait()
                await _deliver_together(made[0], _completion(success=True))

            reader = asyncio.create_task(deliver_after_shutdown())
            await reader_waiting.wait()
            await service.shutdown()
            settled = service.read()
            assert settled is not None
            assert settled.state is SignInState.ended
            assert made[0].stopped
            release_completion.set()
            await reader
            assert service.read() == settled

        _run(exercise)


def test_a_queued_completion_for_another_session_is_still_dropped(tmp_path: Path) -> None:
    """Draining the inbox does not weaken correlation.

    The child reports success under a login id this session does not own, delivered in the
    same window the drain covers. It must be dropped exactly as the consumer would drop
    it, so a foreign completion cannot confirm somebody else's sign-in.
    """
    home = tmp_path / "home"
    _write_login(home, last_refresh="before")
    script = {"complete_after_cancel": {"success": True, "login_id": "some-other-login"}}
    with _codex_home(home), _scripted_sign_in(tmp_path, script) as resolve_argv:
        service = _service(resolve_argv)

        async def exercise() -> None:
            started = await service.start()
            _write_login(home, last_refresh="after")
            await service.cancel(started.session_id)
            await asyncio.sleep(0.6)
            settled = service.read()
            assert settled is not None
            assert settled.state is SignInState.ended

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


# --- one delivered batch holding two events ----------------------------------------------
#
# A child exiting and a completion arriving are delivered by two independent tasks inside
# the client: the process watcher does not wait for the stdout reader. So both can land
# before the consumer looks, and the consumer then holds a batch of two. Provoking that
# order against a real child is a race, and a race is not a regression — so these drive the
# service through its own client seam, which is what makes the ordering exact.


class _ClientThatDeliversWhatATestSays:
    """A codex app-server client that answers the three calls and nothing else.

    Used only where the ordering under test cannot be produced deterministically on a real
    wire. Every other exercise in this file uses a real child process.
    """

    def __init__(self, *, handler: Any, description: str, consumed_notifications: Any) -> None:
        self.handler = handler
        self.description = description
        self.is_alive = True
        self.stopped = False

    async def start(self, *, argv: Any, environment: Any, working_directory: Any) -> None:
        return

    async def request(self, method: str, params: Any = None) -> Any:
        if method == "initialize":
            return {}
        if method == "account/login/start":
            return {
                "type": "chatgptDeviceCode",
                "loginId": "login-1",
                "verificationUrl": "https://auth.openai.com/codex/device",
                "userCode": "BATCH-CODE",
            }
        if method == "account/login/cancel":
            return {"status": "canceled"}
        raise AssertionError(f"this client was asked for {method}")

    async def notify(self, method: str, params: Any = None) -> None:
        return

    async def stop(self) -> None:
        self.is_alive = False
        self.stopped = True


def _completion(*, success: bool, login_id: str = "login-1") -> Any:
    from planner.conversation.backends.codex_app_server import (
        login_bindings_gen as login_bindings,
    )

    return login_bindings.AccountLoginCompletedNotification(
        loginId=login_id, success=success, error=None, onboardingEntrypoint=None
    )


@contextmanager
def _service_over_a_test_client(
    home: Path,
) -> Iterator[tuple[CodexSignInService, list[_ClientThatDeliversWhatATestSays]]]:
    made: list[_ClientThatDeliversWhatATestSays] = []

    def factory(**keywords: Any) -> Any:
        client = _ClientThatDeliversWhatATestSays(**keywords)
        made.append(client)
        return client

    async def resolve_argv() -> Sequence[str] | None:
        return ("this-client-never-spawns",)

    with _codex_home(home):
        yield (
            CodexSignInService(
                resolve_argv=resolve_argv,
                bounds=SignInBounds(child_startup_seconds=15.0, session_seconds=600.0),
                read_login=read_stored_codex_login,
                now=lambda: datetime(2026, 10, 16, tzinfo=UTC),
                client_factory=factory,
            ),
            made,
        )


async def _deliver_together(client: Any, *events: Any) -> None:
    """Hand several events over with no suspension between them.

    Both handler calls only append and set a flag, so awaiting them does not yield to the
    event loop. That is what puts every event into one batch for the consumer.
    """
    for event in events:
        if event is None:
            await client.handler.on_child_ended()
        else:
            await client.handler.on_notification("account/login/completed", event)


def test_a_child_ending_and_a_matching_success_in_one_batch_is_a_success(
    tmp_path: Path,
) -> None:
    """The ordering this test exists for.

    The consumer takes a batch of [child ended, success]. An earlier version handled the
    batch item by item: the child ending settled the session to ``ended`` while the success
    was still only in the consumer's hands, so the settle drained nothing and the success
    had nowhere to go. The credential is real here, so the honest answer is ``confirmed``.
    """
    home = tmp_path / "home"
    _write_login(home, last_refresh="before")
    with _service_over_a_test_client(home) as (service, made):

        async def exercise() -> None:
            started = await service.start()
            assert started.user_code == "BATCH-CODE"
            # The browser step landed, and then the child went away.
            _write_login(home, last_refresh="after")
            await _deliver_together(made[0], None, _completion(success=True))
            await _until_terminal(service)
            settled = service.read()
            assert settled is not None
            assert settled.state is SignInState.confirmed
            assert settled.account_label == "owner@example.invalid"

        _run(exercise)


def test_a_success_then_a_child_ending_in_one_batch_is_also_a_success(
    tmp_path: Path,
) -> None:
    """The same batch the other way round. Neither order may lose the success."""
    home = tmp_path / "home"
    _write_login(home, last_refresh="before")
    with _service_over_a_test_client(home) as (service, made):

        async def exercise() -> None:
            await service.start()
            _write_login(home, last_refresh="after")
            await _deliver_together(made[0], _completion(success=True), None)
            await _until_terminal(service)
            settled = service.read()
            assert settled is not None
            assert settled.state is SignInState.confirmed

        _run(exercise)


def test_a_foreign_success_beside_a_child_ending_in_one_batch_is_dropped(
    tmp_path: Path,
) -> None:
    """Aggregating a batch did not weaken correlation inside it.

    The success in the batch belongs to another login id. It must be dropped, so the child
    ending is the only evidence left and the session ends rather than claiming a sign-in
    somebody else's completion reported.
    """
    home = tmp_path / "home"
    _write_login(home, last_refresh="before")
    with _service_over_a_test_client(home) as (service, made):

        async def exercise() -> None:
            await service.start()
            _write_login(home, last_refresh="after")
            await _deliver_together(
                made[0], None, _completion(success=True, login_id="some-other-login")
            )
            await _until_terminal(service)
            settled = service.read()
            assert settled is not None
            assert settled.state is SignInState.ended
            assert settled.detail is not None
            assert "stopped before it finished" in settled.detail

        _run(exercise)


def test_a_known_success_is_not_overwritten_by_a_later_failure_in_one_batch(
    tmp_path: Path,
) -> None:
    """A success and a failure for one login id means the child said both."""
    home = tmp_path / "home"
    _write_login(home, last_refresh="before")
    with _service_over_a_test_client(home) as (service, made):

        async def exercise() -> None:
            await service.start()
            _write_login(home, last_refresh="after")
            await _deliver_together(
                made[0], _completion(success=True), _completion(success=False)
            )
            await _until_terminal(service)
            settled = service.read()
            assert settled is not None
            assert settled.state is SignInState.confirmed

        _run(exercise)


def test_a_child_ending_alone_in_a_batch_still_ends_the_sign_in(tmp_path: Path) -> None:
    """The ordinary case, so the aggregation did not make a lone ending a no-op."""
    home = tmp_path / "home"
    _write_login(home, last_refresh="before")
    with _service_over_a_test_client(home) as (service, made):

        async def exercise() -> None:
            await service.start()
            await _deliver_together(made[0], None)
            await _until_terminal(service)
            settled = service.read()
            assert settled is not None
            assert settled.state is SignInState.ended
            assert made[0].stopped

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
