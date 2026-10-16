"""The codex login on this machine, read without spending it.

Codex keeps one ChatGPT or API-key login in a file under its home. Panels does not own
that file and never writes it, but three things it must say honestly can only be read
there: whether the saved access token has expired, which credential generation is in
force, and whether a scratch home holds a copy of the live credential.

Two rules hold this module together.

**A reading never spends the login.** Everything here is a file read and a base64 decode.
Nothing reaches the network, so asking whether codex can work costs nothing and cannot
itself consume the rotating refresh token.

**A secret is compared and dropped, never kept.** The duplicate check needs to know
whether two files hold the same credential, which is a question about secrets. It is
answered inside one call with a key minted for that call, and nothing derived from the
secret outlives it or reaches a log.
"""

from __future__ import annotations

import base64
import binascii
import hmac
import json
import logging
import os
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

LOGGER = logging.getLogger("planner.conversation.backends.codex_app_server.login")

LOGIN_FILE_NAME: Final = "auth.json"


def codex_home(environment: Any = None) -> Path:
    """The home codex reads its login from, given the environment it will run in.

    ``CODEX_HOME`` comes from the environment handed in. ``HOME`` does not: it is this
    process's, because a codex child inherits this process's home and nothing in Panels
    overrides it. A caller that did override it would need this to read it too.
    """
    source = os.environ if environment is None else environment
    named = source.get("CODEX_HOME")
    return Path(named) if named else Path.home() / ".codex"


@dataclass(frozen=True, slots=True)
class CodexLoginGeneration:
    """Which saved credential a reading, a rejection, or a success is about.

    The account id and the last-refresh stamp both come out of the login file and neither
    is secret. Together they change whenever codex writes a new credential, which is
    exactly when a remembered rejection stops applying. A login missing either one cannot
    be named, and nothing is remembered against a credential that cannot be named.
    """

    account_id: str
    last_refresh: str


@dataclass(frozen=True, slots=True)
class StoredCodexLogin:
    """What the saved login says about itself, with no network call made.

    ``readable`` is false both for a file that is not there and for one that will not
    parse. The two are different facts, so ``present`` keeps them apart: a missing login
    means signed out, and an unreadable one means Panels cannot tell.
    """

    present: bool
    readable: bool
    account_label: str | None = None
    access_token_expires_at: datetime | None = None
    generation: CodexLoginGeneration | None = None

    def access_token_has_expired(self, *, now: datetime) -> bool:
        expires_at = self.access_token_expires_at
        return expires_at is not None and expires_at <= now


def read_stored_codex_login(home: Path | None = None) -> StoredCodexLogin:
    """Read the saved login's own account of itself."""
    path = (home or codex_home()) / LOGIN_FILE_NAME
    try:
        document = json.loads(path.read_text())
    except FileNotFoundError:
        return StoredCodexLogin(present=False, readable=False)
    except (OSError, ValueError):
        return StoredCodexLogin(present=True, readable=False)
    if not isinstance(document, dict):
        return StoredCodexLogin(present=True, readable=False)
    tokens = document.get("tokens")
    tokens = tokens if isinstance(tokens, dict) else {}
    account_id = _text(tokens.get("account_id"))
    last_refresh = _text(document.get("last_refresh"))
    return StoredCodexLogin(
        present=True,
        readable=True,
        account_label=_account_label(tokens),
        access_token_expires_at=_token_expiry(tokens.get("access_token")),
        generation=(
            CodexLoginGeneration(account_id=account_id, last_refresh=last_refresh)
            if account_id is not None and last_refresh is not None
            else None
        ),
    )


def _account_label(tokens: dict[str, Any]) -> str | None:
    """The signed-in account as the login itself names it."""
    claims = _token_claims(tokens.get("id_token"))
    if claims is None:
        return None
    return _text(claims.get("email"))


def _token_expiry(token: Any) -> datetime | None:
    claims = _token_claims(token)
    if claims is None:
        return None
    expires_at = claims.get("exp")
    if not isinstance(expires_at, int):
        return None
    try:
        return datetime.fromtimestamp(expires_at, UTC)
    except (OverflowError, OSError, ValueError):
        return None


def _token_claims(token: Any) -> dict[str, Any] | None:
    """The middle segment of a JWT, which carries no secret and states the expiry."""
    if not isinstance(token, str):
        return None
    parts = token.split(".")
    if len(parts) != 3:
        return None
    payload = parts[1]
    payload += "=" * (-len(payload) % 4)
    try:
        decoded = json.loads(base64.urlsafe_b64decode(payload))
    except (binascii.Error, ValueError):
        return None
    return decoded if isinstance(decoded, dict) else None


def _text(value: Any) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


# --- is this the same credential twice ---------------------------------------------------


def logins_hold_the_same_credential(one: Path, other: Path) -> bool | None:
    """Whether two codex homes hold the same credential. ``None`` means unreadable.

    The comparison is the credential itself — the rotating refresh token, or the API key —
    and nothing else. Account metadata is deliberately not part of it: a copy whose account
    fields were edited or dropped still rotates the same token, and that is the hazard.

    Both secrets are compared through one digest key minted here and thrown away with the
    call, so no value derived from a credential is persisted, returned, or logged.
    """
    first = _credentials(one)
    second = _credentials(other)
    if first is None or second is None:
        return None
    if not first or not second:
        return False
    key = secrets.token_bytes(32)
    marked = {hmac.new(key, secret.encode(), "sha256").digest() for secret in first}
    return any(hmac.new(key, secret.encode(), "sha256").digest() in marked for secret in second)


def _credentials(home: Path) -> tuple[str, ...] | None:
    """Every secret a login file holds, or ``None`` when the file cannot be read.

    A file that is not there holds no credential, which is an answer: there is nothing to
    duplicate. A file that will not parse is not an answer, and saying so is better than
    guessing either way.
    """
    path = home / LOGIN_FILE_NAME
    try:
        document = json.loads(path.read_text())
    except FileNotFoundError:
        return ()
    except (OSError, ValueError):
        return None
    if not isinstance(document, dict):
        return None
    tokens = document.get("tokens")
    tokens = tokens if isinstance(tokens, dict) else {}
    held = (tokens.get("refresh_token"), document.get("OPENAI_API_KEY"))
    return tuple(secret for secret in held if isinstance(secret, str) and secret)


# --- what a turn established about the login ---------------------------------------------


class RememberedCodexLoginRejection:
    """The saved login codex refused, kept only while that same credential is in force.

    A file reading cannot tell a login that still works from one whose refresh the server
    will reject: both look like an expired access token, and the only way to find out is
    to spend a refresh. A turn that already failed has spent one, and its answer is worth
    keeping — scoped, so it can never be read as an answer about a credential it was not
    about. A rejection is recorded against the generation the turn ran on, and it stops
    applying the moment the saved login reports a different one.

    This lives for as long as the process. A restart forgets it, and the card falls back to
    what the file alone supports, which is honest: it says the login cannot be confirmed
    rather than claiming it is good.
    """

    def __init__(self) -> None:
        self._rejected: CodexLoginGeneration | None = None

    def remember(self, generation: CodexLoginGeneration | None) -> None:
        """Record that this credential was refused. An unnamable one records nothing."""
        if generation is None:
            return
        self._rejected = generation

    def forget(self, generation: CodexLoginGeneration | None) -> None:
        """A turn worked on this credential, so any rejection against it is over."""
        if generation is not None and self._rejected == generation:
            self._rejected = None

    def applies_to(self, generation: CodexLoginGeneration | None) -> bool:
        """Whether the credential in force now is the one that was refused."""
        return generation is not None and self._rejected == generation
