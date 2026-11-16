"""The owner credential: the one secret that makes a request Khushal's.

Panels creates a random secret the first time it opens a data folder and keeps it in
``owner-credential`` beside the database, readable only by this user. Serving the web
app page hands it to the browser as the ``panels_owner`` cookie. A request that carries
the cookie is the owner; nothing else is.

The bar is accidents, not an agent that sets out to cheat: any process running as this
user can read the file, and any local process that loads the web app page receives the
cookie.
"""

from __future__ import annotations

import hmac
import os
import secrets
from pathlib import Path
from typing import Final

OWNER_CREDENTIAL_COOKIE: Final = "panels_owner"
OWNER_CREDENTIAL_FILE_NAME: Final = "owner-credential"
# Ten years, though a browser may cap it lower. Every load of the app page renews it.
OWNER_CREDENTIAL_COOKIE_MAX_AGE_SECONDS: Final = 10 * 365 * 24 * 60 * 60


def owner_credential_path(db_path: str) -> Path:
    return Path(db_path).expanduser().resolve(strict=False).parent / OWNER_CREDENTIAL_FILE_NAME


def load_or_create_owner_credential(db_path: str) -> str:
    """Read this data folder's owner secret, creating it on first start.

    The secret is written whole to a private temporary file, then linked into place, so
    a crash or a second starting server never sees a partial or empty file.
    """
    path = owner_credential_path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        pending = path.with_name(f".{path.name}.{secrets.token_hex(8)}")
        descriptor = os.open(pending, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(descriptor, "w", encoding="ascii") as handle:
                handle.write(secrets.token_urlsafe(32) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
            try:
                os.link(pending, path)
            except FileExistsError:
                pass  # Another server created it first; both read that one.
        finally:
            pending.unlink(missing_ok=True)
    credential = path.read_text(encoding="ascii").strip()
    if not credential:
        raise ValueError(f"owner credential file is empty: {path}")
    return credential


def presents_owner_credential(presented: str | None, owner_credential: str | None) -> bool:
    """Whether a request's cookie is this server's owner secret, compared in constant time."""
    if not presented or not owner_credential:
        return False
    return hmac.compare_digest(presented.encode(), owner_credential.encode())
