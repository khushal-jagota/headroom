"""Public request state. Secret entry has no representation in these contracts."""

from typing import Literal, TypedDict

from planner.credentials.contracts import CredentialReferenceBody


class RequestReplacement(TypedDict):
    credential_id: str
    name: str
    revision: int


class RequestCredential(TypedDict):
    id: str
    reference: CredentialReferenceBody
    auth_kind: Literal["bearer", "header"]
    revision: int


type RequestDeliveryStatus = Literal[
    "pending", "started", "queued", "injected", "refused", "uncertain", "canceled"
]


class RequestDelivery(TypedDict):
    attempt_id: str
    status: RequestDeliveryStatus
    refusal_reason: str | None


class CredentialRequestRowBody(TypedDict):
    kind: Literal["credential_request"]
    request_id: str
    name: str
    provider: str
    use: str
    auth_kind: Literal["bearer", "header"]
    header_name: str | None
    help: str | None
    help_url: str | None
    replacement: RequestReplacement | None
    state: Literal["pending", "saved", "replaced", "rejected", "reused"]
    credential: RequestCredential | None
    delivery: RequestDelivery | None


class CreateCredentialRequestBody(TypedDict):
    request_id: str
    name: str
    provider: str
    use: str
    auth_kind: Literal["bearer", "header"]
    header_name: str | None
    help: str | None
    help_url: str | None
    replacement: RequestReplacement | None
