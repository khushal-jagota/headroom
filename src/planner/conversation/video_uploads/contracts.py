"""Canonical video upload wire and admission contracts."""

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field

from planner.conversation.message_files import KeptMessageFile

DestinationKind = Literal["chief", "manager", "ticket", "conversation"]
UploadState = Literal[
    "reserved", "transferring", "ready", "admitted", "linked", "failed", "cancelled"
]


class UploadDestination(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    kind: DestinationKind
    id: str
    expected_conversation_id: str | None


class ReserveVideoUpload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    destination: UploadDestination
    filename: str
    byte_count: int = Field(gt=0)


class VideoUploadStatus(BaseModel):
    upload_id: str
    destination: UploadDestination
    filename: str
    byte_count: int
    received_bytes: int
    state: UploadState
    media_type: str | None
    expires_at: int
    error: str | None


@dataclass(frozen=True)
class CheckedVideoUpload:
    """Preflight result captured under the destination's existing lock."""

    upload_id: str
    destination: UploadDestination
    sender_message_id: str
    conversation_id: str | None


class VideoUploadServiceContract(Protocol):
    def reserve(self, request: ReserveVideoUpload) -> VideoUploadStatus: ...
    def status(self, upload_id: str) -> VideoUploadStatus: ...
    def cancel(self, upload_id: str) -> None: ...
    async def transfer(self, upload_id: str, chunks: AsyncIterator[bytes]) -> VideoUploadStatus: ...
    def preflight(
        self, upload_id: str, destination: UploadDestination, sender_message_id: str
    ) -> CheckedVideoUpload: ...
    def adopt(self, checked: CheckedVideoUpload, conversation_id: str) -> KeptMessageFile: ...
    def finish(
        self, checked: CheckedVideoUpload, outcome: Literal["accepted", "refused", "uncertain"]
    ) -> None: ...
    def recover_and_cleanup(self) -> None: ...


class VideoUploadError(ValueError):
    def __init__(self, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.status_code = status_code
