"""One codex device-code sign-in at a time, owned by this process.

Panels used to have nothing to offer a dead codex login except the name of a command to
run in a terminal. The app server it already speaks to can start the sign-in itself:
``account/login/start`` answers at once with a verification URL and a one-time code, and
``account/login/cancel`` ends it. That is what this module drives.

Four rules hold it together, and each of them exists because the obvious alternative is
wrong.

**Nothing here infers credential state.** ``canceled``, ``notFound``, a deadline passing
and a child exiting all say what *Panels* did. None of them is evidence about the saved
login, because a browser step can land in the same instant as a cancel. So every way a
session can end runs the same last three steps: settle the child, drain any completion that
already arrived, then re-read the saved login. The reading is last so the file it reads is
not being written underneath it.

**Success is a completion and a reading that agrees.** A notification saying ``success`` is
necessary and not sufficient. The saved login must then be present, readable, carrying a
credential generation different from the one the session started on, and not under a
remembered rejection. If those disagree the session is ``unconfirmed`` — not signed in and
not failed. Nothing is ever told to forget a rejection: a rejection is already scoped to
its generation and lapses on its own, so reaching for ``forget`` would be Panels asserting
the very thing it just failed to confirm.

**Every message is correlated, and old news cannot overwrite new.** A session carries a
Panels session id and a sequence number this process mints, alongside codex's ``loginId``.
A notification matching neither id is dropped. A write issued under an older sequence
number is discarded, which is what stops a late completion or a late reaper from clearing a
sign-in that started after it.

**Nothing here tells the card to change.** The backend snapshot service already
re-probes codex when the saved login stops reading the same, so a sign-in that lands shows
up on its own. A hook from here would be a second mechanism for the same thing.

**Every wait is bounded, and every bound is Panels policy.** The command line prints that
its one-time code expires in fifteen minutes. That is a program's words, not a protocol
guarantee, and nothing here depends on it. Panels bounds its own waits and stops presenting
a code when its own deadline passes. An earlier upstream expiry is handled by the same
settle-and-re-read path rather than predicted.
"""

from __future__ import annotations

import asyncio
import logging
import secrets
from collections.abc import Awaitable, Callable, Mapping, Sequence
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from pathlib import Path
from typing import Any, Final

from pydantic import BaseModel

from planner.conversation.backends.codex_app_server import login_bindings_gen as login_bindings
from planner.conversation.backends.codex_app_server.client import (
    CodexAppServerClient,
    CodexAppServerError,
    CodexChildWouldNotStart,
    CodexServerMessageHandler,
    child_environment,
)
from planner.conversation.backends.codex_app_server.login import (
    CodexLoginGeneration,
    CodexLoginRejectionMemory,
    StoredCodexLogin,
    codex_home,
    read_stored_codex_login,
)

LOGGER = logging.getLogger("planner.conversation.backends.codex_app_server.login_session")

START_METHOD: Final = "account/login/start"
CANCEL_METHOD: Final = "account/login/cancel"
COMPLETED_NOTIFICATION: Final = "account/login/completed"

# The one notification a sign-in child is listened to for. Its model comes from the login
# bundle, which is pinned separately from the turn adapter's, so it is handed to the client
# per instance rather than added to the adapter's table.
SIGN_IN_NOTIFICATIONS: Mapping[str, type[BaseModel]] = {
    COMPLETED_NOTIFICATION: login_bindings.AccountLoginCompletedNotification,
}


class SignInState(StrEnum):
    """How a sign-in stands. Three terminal answers, and none of them is a guess.

    ``unconfirmed`` is a real answer and the reason this is not a boolean: codex said the
    sign-in worked and Panels could not see a new usable credential. Calling that signed in
    would be the lie this whole module is built to avoid, and calling it failed would throw
    away codex's own report.
    """

    pending = "pending"
    confirmed = "confirmed"
    unconfirmed = "unconfirmed"
    ended = "ended"

    @property
    def is_terminal(self) -> bool:
        return self is not SignInState.pending


@dataclass(frozen=True, slots=True)
class SignInBounds:
    """Panels' own waits. None of these is a claim about how long a code stays valid."""

    child_startup_seconds: float = 20.0
    # Deliberately shorter than the fifteen minutes the command line prints, so Panels
    # stops presenting a code before upstream is known to have stopped honouring it. That
    # is not a guarantee the code was still valid up to this point.
    session_seconds: float = 10 * 60.0
    cancel_seconds: float = 10.0


@dataclass(frozen=True, slots=True)
class CodexSignIn:
    """What a surface may say about a sign-in. No secret beyond the one-time code."""

    session_id: str
    state: SignInState
    verification_url: str | None
    user_code: str | None
    started_at: datetime
    deadline_at: datetime
    # What the terminal state is, in words a person reads. Absent while pending.
    detail: str | None = None
    account_label: str | None = None


class CodexSignInUnavailable(Exception):
    """The sign-in could not be started, and why."""


class UnknownSignInSession(Exception):
    """The session id given is not the one in force. Never treated as the current one."""


@dataclass(frozen=True, slots=True)
class _FromTheChild:
    """One thing the child said. A completion, or the fact that it ended."""

    completion: login_bindings.AccountLoginCompletedNotification | None = None
    child_ended: bool = False


@dataclass(slots=True)
class _Session:
    """The mutable sign-in. Only the service touches it, and only under its lock."""

    session_id: str
    sequence: int
    started_at: datetime
    deadline_at: datetime
    generation_at_start: CodexLoginGeneration | None
    client: CodexAppServerClient
    login_id: str | None = None
    state: SignInState = SignInState.pending
    detail: str | None = None
    account_label: str | None = None
    verification_url: str | None = None
    user_code: str | None = None
    # Everything the child has said and nobody has acted on yet.
    #
    # This exists for a deadlock, and it is a list rather than a queue for a second bug.
    #
    # The deadlock: the reader that routes the answer to ``account/login/start`` is the
    # same reader that delivers a completion. If delivering one had to wait for the
    # service lock, and the lock were held by the start waiting for that reader to route
    # its answer, neither would ever move. So the wire's end only appends and sets
    # ``arrived``, and a consumer takes the lock afterwards.
    #
    # Why not a queue: taking from a queue removes the item, and the consumer then has to
    # wait for the lock while holding it in a local variable. A settle that cancels the
    # consumer in that window destroys the only copy — which lost a success that landed in
    # the same instant as a cancel. Nothing is ever removed outside the lock here, so
    # every delivered event survives the consumer being cancelled at any point.
    delivered: list[_FromTheChild] = field(default_factory=list)
    arrived: asyncio.Event = field(default_factory=asyncio.Event)
    completion: login_bindings.AccountLoginCompletedNotification | None = None
    child_ended: bool = False
    # Set by the reaper before it settles. The wording used to infer this by comparing a
    # clock against the deadline, which is a second source of truth for something one
    # line already knows.
    reaped: bool = False
    reaper: asyncio.Task[None] | None = None
    consumer: asyncio.Task[None] | None = None

    def view(self) -> CodexSignIn:
        return CodexSignIn(
            session_id=self.session_id,
            state=self.state,
            verification_url=self.verification_url if not self.state.is_terminal else None,
            user_code=self.user_code if not self.state.is_terminal else None,
            started_at=self.started_at,
            deadline_at=self.deadline_at,
            detail=self.detail,
            account_label=self.account_label,
        )


class CodexSignInService:
    """At most one pending codex sign-in per process, and nothing persisted.

    Nothing survives a restart on purpose. A ``loginId`` whose child is gone cannot be
    cancelled or completed by anybody, so storing one would store a handle to nothing; a
    restart drops the session and the surface says the pending sign-in ended.
    """

    def __init__(
        self,
        *,
        resolve_argv: Callable[[], Awaitable[Sequence[str] | None]],
        remembered_login_rejection: CodexLoginRejectionMemory | None = None,
        bounds: SignInBounds | None = None,
        now: Callable[[], datetime] | None = None,
        read_login: Callable[[Path], StoredCodexLogin] = read_stored_codex_login,
        client_factory: Callable[..., CodexAppServerClient] | None = None,
    ) -> None:
        self._resolve_argv = resolve_argv
        self._remembered_login_rejection = remembered_login_rejection
        self._bounds = bounds or SignInBounds()
        self._now = now or (lambda: datetime.now(UTC))
        self._read_login = read_login
        self._client_factory = client_factory or CodexAppServerClient
        self._session: _Session | None = None
        # Starts are serialized rather than racing. Two pending authorizations would mean
        # two one-time codes, and the one a person was shown might be the one nobody will
        # honour — so a second start waits here and is handed the session already running.
        self._lock = asyncio.Lock()
        self._sequence = 0

    # --- what a surface asks for --------------------------------------------------------

    def read(self) -> CodexSignIn | None:
        """The sign-in in force, pending or terminal, or nothing if there has been none."""
        session = self._session
        return None if session is None else session.view()

    async def start(self) -> CodexSignIn:
        """Begin a device-code sign-in, or hand back the one already pending.

        Raises ``CodexSignInUnavailable`` when no child could be brought up to ask.
        """
        async with self._lock:
            pending = self._session
            if pending is not None and not pending.state.is_terminal:
                return pending.view()
            return await self._start_a_session()

    async def cancel(self, session_id: str) -> CodexSignIn:
        """End the named sign-in. Raises ``UnknownSignInSession`` for a stale id.

        A stale id is refused rather than resolved to the current session, because a page
        reconnecting after a reap would otherwise cancel a sign-in somebody just started.
        """
        async with self._lock:
            session = self._session
            if session is None or session.session_id != session_id:
                raise UnknownSignInSession(session_id)
            if session.state.is_terminal:
                return session.view()
            await self._ask_to_cancel(session)
            return await self._settle(session, success_was_reported=False)

    async def shutdown(self) -> None:
        """Stop any child this process owns. Called when the server goes down.

        A pending session is settled rather than abandoned. The process is going away
        either way, but a sign-in that succeeded in the same moment the server stopped
        still wrote a credential, and settling is what reads it instead of guessing.
        """
        async with self._lock:
            session = self._session
            if session is None:
                return
            if session.state.is_terminal:
                self._stop_reaper(session)
                self._stop_consumer(session)
                await self._stop_child(session)
                return
            await self._settle(session, success_was_reported=False)

    # --- starting -----------------------------------------------------------------------

    async def _start_a_session(self) -> CodexSignIn:
        self._sequence += 1
        started_at = self._now()
        handler = _SignInMessages(self, self._sequence)
        session = _Session(
            session_id=secrets.token_urlsafe(12),
            sequence=self._sequence,
            started_at=started_at,
            deadline_at=started_at + timedelta(seconds=self._bounds.session_seconds),
            generation_at_start=self._read_the_login().generation,
            client=self._client_factory(
                handler=handler,
                description=f"sign-in.{self._sequence}",
                consumed_notifications=SIGN_IN_NOTIFICATIONS,
            ),
        )
        handler.bind(session)
        self._session = session
        try:
            await asyncio.wait_for(
                self._bring_up_the_child(session), self._bounds.child_startup_seconds
            )
            answer = await asyncio.wait_for(
                session.client.request(START_METHOD, {"type": "chatgptDeviceCode"}),
                self._bounds.child_startup_seconds,
            )
        except (
            TimeoutError,
            CodexSignInUnavailable,
            CodexAppServerError,
            OSError,
        ) as would_not_start:
            await self._stop_child(session)
            session.state = SignInState.ended
            session.detail = "Panels could not start a sign-in with codex."
            raise CodexSignInUnavailable(str(would_not_start)) from would_not_start
        try:
            self._take_the_start_answer(session, answer)
        except CodexSignInUnavailable:
            await self._stop_child(session)
            session.state = SignInState.ended
            session.detail = "Codex did not offer a device-code sign-in."
            raise
        session.reaper = asyncio.create_task(
            self._reap_at_the_deadline(session.sequence),
            name=f"planner.conversation.codex.signin.deadline.{session.sequence}",
        )
        # Only now is there a login id to correlate against, so only now is anything the
        # child already said acted on.
        session.consumer = asyncio.create_task(
            self._consume_what_the_child_says(session.sequence),
            name=f"planner.conversation.codex.signin.inbox.{session.sequence}",
        )
        return session.view()

    async def _bring_up_the_child(self, session: _Session) -> None:
        argv = await self._resolve_argv()
        if argv is None:
            raise CodexSignInUnavailable("codex is not installed on this machine")
        try:
            await session.client.start(
                argv=argv,
                environment=child_environment(),
                working_directory=Path.home(),
            )
        except CodexChildWouldNotStart as would_not_spawn:
            raise CodexSignInUnavailable(str(would_not_spawn)) from would_not_spawn
        await session.client.request(
            "initialize",
            {"clientInfo": {"name": "panels-sign-in", "version": "1"}},
        )
        await session.client.notify("initialized")

    def _take_the_start_answer(self, session: _Session, result: Any) -> None:
        try:
            decoded = login_bindings.LoginAccountResponse.model_validate(result).root
        except Exception as would_not_decode:  # noqa: BLE001 - reported, never swallowed
            raise CodexSignInUnavailable(
                f"codex answered {START_METHOD} with a shape this does not read: "
                f"{would_not_decode}"
            ) from would_not_decode
        if not isinstance(decoded, login_bindings.ChatgptDeviceCodev2LoginAccountResponse):
            raise CodexSignInUnavailable(
                f"codex answered a {getattr(decoded, 'type', 'different')} login rather "
                "than a device-code one"
            )
        session.login_id = decoded.loginId
        session.verification_url = decoded.verificationUrl
        session.user_code = decoded.userCode

    # --- what the child says ------------------------------------------------------------

    async def _consume_what_the_child_says(self, sequence: int) -> None:
        """Act on what the child said, one thing at a time, under the lock.

        Runs only after the start response, so the reader is never waiting on this.
        """
        while True:
            session = self._session
            if session is None or session.sequence != sequence:
                return
            await session.arrived.wait()
            async with self._lock:
                current = self._current(sequence)
                if current is None:
                    # The session this belonged to has been replaced. Old news gets
                    # nothing back, so it cannot clear or rewrite what replaced it.
                    return
                for said in self._take_what_is_delivered(current):
                    if said.child_ended:
                        await self._take_the_child_ending(current)
                    elif said.completion is not None:
                        await self._take_a_completion(current, said.completion)
                if current.state.is_terminal:
                    return

    async def _take_a_completion(
        self, session: _Session, notification: login_bindings.AccountLoginCompletedNotification
    ) -> None:
        if notification.loginId is not None and notification.loginId != session.login_id:
            LOGGER.warning(
                "codex sign-in: dropped a %s for a login id this session does not own",
                COMPLETED_NOTIFICATION,
            )
            return
        session.completion = notification
        if session.state.is_terminal:
            # The session already ended. The completion is still evidence that a
            # credential may have landed, so the account label is read again rather than
            # ignored, but nothing about the terminal state is rewritten.
            session.account_label = self._read_the_login().account_label
            return
        await self._settle(session, success_was_reported=notification.success)

    async def _take_the_child_ending(self, session: _Session) -> None:
        if session.state.is_terminal:
            return
        session.child_ended = True
        # A child can die after writing a credential, so its exit is not read as a
        # failure. It ends the session and the login is read.
        await self._settle(session, success_was_reported=False)

    # --- ending -------------------------------------------------------------------------

    async def _reap_at_the_deadline(self, sequence: int) -> None:
        await asyncio.sleep(self._bounds.session_seconds)
        async with self._lock:
            session = self._current(sequence)
            if session is None or session.state.is_terminal:
                return
            session.reaped = True
            await self._ask_to_cancel(session)
            await self._settle(session, success_was_reported=False)

    async def _ask_to_cancel(self, session: _Session) -> None:
        """Ask codex to cancel, bounded. A cancel that does not answer still ends it."""
        if session.login_id is None or not session.client.is_alive:
            return
        try:
            await asyncio.wait_for(
                session.client.request(CANCEL_METHOD, {"loginId": session.login_id}),
                self._bounds.cancel_seconds,
            )
        except (TimeoutError, CodexAppServerError) as would_not_answer:
            # Neither the answer nor its absence says anything about the saved login, so
            # there is nothing to do with this but say it happened.
            LOGGER.info("codex sign-in: the cancel did not answer: %s", would_not_answer)

    async def _settle(self, session: _Session, *, success_was_reported: bool) -> CodexSignIn:
        """Settle the child, take in what it already said, then read the login.

        The order is the whole correctness of this function.

        The child is stopped first, so nothing can still be writing the login file. Then
        the inbox is drained, because a completion delivered while this caller held the
        lock is sitting in a queue whose consumer has just been stopped — and dropping it
        would throw away the one piece of evidence that a credential landed. A success that
        arrives in the same instant as a cancel is the case that makes this necessary. The
        reading is last, so what it reads is settled.
        """
        self._stop_reaper(session)
        self._stop_consumer(session)
        await self._stop_child(session)
        self._take_in_what_was_already_delivered(session)
        reported_success = success_was_reported or bool(
            session.completion is not None and session.completion.success
        )
        reading = self._read_the_login()
        if reported_success:
            session.state, session.detail = self._judge_a_reported_success(session, reading)
        else:
            session.state = SignInState.ended
            session.detail = self._why_it_ended(session)
        session.account_label = reading.account_label
        return session.view()

    @staticmethod
    def _take_what_is_delivered(session: _Session) -> list[_FromTheChild]:
        """Everything said since the last look, removed under the lock and only there.

        Both readers of a session's events go through here — the consumer and the settle —
        so there is exactly one moment an event stops being recoverable, and it is a
        moment the lock is held. That is what makes cancelling the consumer safe at any
        point.
        """
        taken = list(session.delivered)
        session.delivered.clear()
        session.arrived.clear()
        return taken

    def _take_in_what_was_already_delivered(self, session: _Session) -> None:
        """Take in what the child said that nobody has acted on yet.

        The consumer is stopped by now, so this is the last chance anything delivered has
        to be read — including an event the consumer had already been woken for. Nothing
        was removed outside the lock, so there is nothing for its cancellation to have
        destroyed.

        Correlation is unchanged: a completion carrying another session's login id is
        dropped here exactly as the consumer would have dropped it.

        A reported success outranks a reported failure among what is taken in. Two
        completions for one login id means the child said both, and a success is the one
        that implies a credential may have been written — so it is the one that must not be
        the dropped one.
        """
        for said in self._take_what_is_delivered(session):
            if said.child_ended:
                session.child_ended = True
                continue
            completion = said.completion
            if completion is None:  # pragma: no cover - one of the two is always set
                continue
            if completion.loginId is not None and completion.loginId != session.login_id:
                LOGGER.warning(
                    "codex sign-in: dropped a delivered %s for a login id this session "
                    "does not own",
                    COMPLETED_NOTIFICATION,
                )
                continue
            held = session.completion
            if held is not None and held.success and not completion.success:
                continue
            session.completion = completion

    def _judge_a_reported_success(
        self, session: _Session, reading: StoredCodexLogin
    ) -> tuple[SignInState, str]:
        """Codex says it worked. Whether Panels can see that is a separate question."""
        if not reading.present:
            return (
                SignInState.unconfirmed,
                "Codex reported a sign-in, but there is no saved login to confirm it.",
            )
        if not reading.readable:
            return (
                SignInState.unconfirmed,
                "Codex reported a sign-in, but Panels could not read the saved login.",
            )
        generation = reading.generation
        if generation is None:
            return (
                SignInState.unconfirmed,
                "Codex reported a sign-in, but the saved login does not name which "
                "credential it holds.",
            )
        if generation == session.generation_at_start:
            return (
                SignInState.unconfirmed,
                "Codex reported a sign-in, but the saved login still holds the credential "
                "it had before.",
            )
        rejection = self._remembered_login_rejection
        if rejection is not None and rejection.applies_to(generation):
            return (
                SignInState.unconfirmed,
                "Codex reported a sign-in, but the credential now saved is one codex "
                "already refused.",
            )
        if reading.access_token_has_expired(now=self._now()):
            # The identity card calls an expired access token ``unknown`` rather than
            # signed in, because confirming it would mean spending a refresh nobody has
            # tried. A sign-in must not claim more than the card will: a credential that
            # arrives already expired is new, and still not shown to work.
            return (
                SignInState.unconfirmed,
                "Codex reported a sign-in, but the credential now saved has already "
                "expired, so Panels cannot confirm it works.",
            )
        return (SignInState.confirmed, "Signed in.")

    def _why_it_ended(self, session: _Session) -> str:
        completion = session.completion
        if completion is not None and completion.error:
            return f"The sign-in ended: {completion.error}"
        if session.reaped:
            return "Panels stopped showing the code after its own time limit."
        if session.child_ended:
            return "The sign-in ended because codex stopped before it finished."
        return "The sign-in ended."

    # --- the small shared pieces --------------------------------------------------------

    def _current(self, sequence: int) -> _Session | None:
        """The session this sequence number belongs to, or nothing if it has been replaced.

        This is the whole defence against old news. A completion or a reaper from a session
        that has since been replaced gets nothing back, so it cannot clear or rewrite the
        sign-in that replaced it.
        """
        session = self._session
        if session is None or session.sequence != sequence:
            return None
        return session

    def _read_the_login(self) -> StoredCodexLogin:
        return self._read_login(codex_home())

    def _stop_reaper(self, session: _Session) -> None:
        self._stop_task(session.reaper)
        session.reaper = None

    def _stop_consumer(self, session: _Session) -> None:
        self._stop_task(session.consumer)
        session.consumer = None

    @staticmethod
    def _stop_task(task: asyncio.Task[None] | None) -> None:
        """Cancel a task this session owns, unless it is the one asking.

        A task that cancelled itself here would never reach the rest of the settle, so
        the one case that is left alone is the caller's own.
        """
        if task is None or task is asyncio.current_task():
            return
        task.cancel()

    async def _stop_child(self, session: _Session) -> None:
        """Stop the child this session owns, by its own handle and nothing else."""
        with suppress(Exception):
            await session.client.stop()


class _SignInMessages(CodexServerMessageHandler):
    """The wire's end of one session, carrying the sequence number it was made for."""

    def __init__(self, service: CodexSignInService, sequence: int) -> None:
        self._service = service
        self._sequence = sequence
        self._session: _Session | None = None

    def bind(self, session: _Session) -> None:
        self._session = session

    async def on_notification(self, method: str, notification: BaseModel) -> None:
        if method != COMPLETED_NOTIFICATION or not isinstance(
            notification, login_bindings.AccountLoginCompletedNotification
        ):  # pragma: no cover - the client only decodes the one method this asked for
            return
        self._enqueue(_FromTheChild(completion=notification))

    async def on_server_request(self, method: str, request_id: Any, params: BaseModel) -> None:
        # A sign-in child is asked nothing of us. The client refuses unknown requests on
        # its own, so anything arriving here is a method it decoded and this does not want.
        LOGGER.debug("codex sign-in: ignored the server request %s", method)

    async def on_child_ended(self) -> None:
        self._enqueue(_FromTheChild(child_ended=True))

    def _enqueue(self, said: _FromTheChild) -> None:
        """Hand it over without waiting for anything. The reader must never block.

        An append and a flag, both synchronous. Nothing here takes the service lock, and
        nothing here can be cancelled midway in a way that loses the event.
        """
        session = self._session
        if session is None:  # pragma: no cover - bound before the child is started
            return
        session.delivered.append(said)
        session.arrived.set()
