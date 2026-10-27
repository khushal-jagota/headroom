"""Secret-bearing host contracts and strictly separate public credential metadata.

No public DTO includes an encrypted value or a decrypted value. Secret-bearing
objects suppress repr. Their serializers must never be generic public serializers.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Protocol, TypedDict


class CredentialScheme(StrEnum):
    local = "local"


class CredentialAuthKind(StrEnum):
    bearer = "bearer"
    header = "header"
    basic = "basic"


@dataclass(frozen=True, slots=True)
class CredentialScope:
    project_id: str
    account: str


@dataclass(frozen=True, slots=True)
class CredentialReference:
    scheme: CredentialScheme
    name: str


@dataclass(frozen=True, slots=True)
class CredentialMetadata:
    id: str
    scope: CredentialScope
    reference: CredentialReference
    auth_kind: CredentialAuthKind
    revision: int
    created_at: int
    updated_at: int


@dataclass(frozen=True, slots=True)
class BearerSecret:
    token: str = field(repr=False)


@dataclass(frozen=True, slots=True)
class HeaderSecret:
    header_name: str
    value: str = field(repr=False)


@dataclass(frozen=True, slots=True)
class BasicSecret:
    username: str = field(repr=False)
    password: str = field(repr=False)


type CredentialSecret = BearerSecret | HeaderSecret | BasicSecret


@dataclass(frozen=True, slots=True)
class EncryptedCredential:
    nonce: bytes = field(repr=False)
    ciphertext: bytes = field(repr=False)


@dataclass(frozen=True, slots=True)
class ResolvedCredential:
    """Host-only admission value. Never give this object to the script context."""

    metadata: CredentialMetadata
    secret: CredentialSecret = field(repr=False)


class CredentialReferenceBody(TypedDict):
    scheme: CredentialScheme
    name: str


class CreateCredentialBody(TypedDict):
    project_id: str
    account: str
    scheme: CredentialScheme
    name: str
    auth_kind: CredentialAuthKind


@dataclass(frozen=True, slots=True)
class CredentialWrite:
    """Canonical write input after private request parsing; never a response."""

    scope: CredentialScope
    reference: CredentialReference
    auth_kind: CredentialAuthKind
    secret: CredentialSecret = field(repr=False)


class BearerSecretBody(TypedDict):
    token: str


class HeaderSecretBody(TypedDict):
    header_name: str
    value: str


class BasicSecretBody(TypedDict):
    username: str
    password: str


type SecretBody = BearerSecretBody | HeaderSecretBody | BasicSecretBody


class CreateCredentialRequest(CreateCredentialBody):
    secret: SecretBody


class ReplaceCredentialRequest(TypedDict):
    expected_revision: int
    secret: SecretBody


class CredentialCipher(Protocol):
    """Replaceable authenticated-encryption boundary; AAD is canonical metadata."""

    def encrypt(
        self, secret: CredentialSecret, *, associated_data: bytes
    ) -> EncryptedCredential: ...

    def decrypt(
        self, encrypted: EncryptedCredential, *, associated_data: bytes
    ) -> CredentialSecret: ...


class CredentialResolver(Protocol):
    """Host-only lookup with no environment or cross-scope fallback."""

    def resolve(
        self, scope: CredentialScope, reference: CredentialReference
    ) -> ResolvedCredential: ...
