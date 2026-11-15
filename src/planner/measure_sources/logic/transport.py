"""Authentication convenience for named credentials, without destination policy."""

from __future__ import annotations

import base64

from planner.credentials.contracts import BasicSecret, BearerSecret, CredentialSecret, HeaderSecret


def authentication_headers(secret: CredentialSecret) -> dict[str, str]:
    if isinstance(secret, BearerSecret):
        return {"Authorization": "Bearer " + secret.token}
    if isinstance(secret, HeaderSecret):
        return {secret.header_name: secret.value}
    assert isinstance(secret, BasicSecret)
    token = base64.b64encode(f"{secret.username}:{secret.password}".encode()).decode()
    return {"Authorization": "Basic " + token}
