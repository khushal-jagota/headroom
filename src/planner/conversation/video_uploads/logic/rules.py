"""Pure filename, budget, binding, and expiry decisions."""

from pathlib import PurePath

from planner.conversation.video_uploads.contracts import UploadDestination, VideoUploadError


def validate_filename(filename: str) -> None:
    if (
        not filename
        or len(filename) > 255
        or filename in (".", "..")
        or any(ord(char) < 32 for char in filename)
        or "/" in filename
        or "\\" in filename
        or PurePath(filename).suffix.lower() not in (".mp4", ".mov", ".webm")
    ):
        raise VideoUploadError("Choose an MP4, MOV, or WebM file with a plain filename.")


def validate_binding(stored: UploadDestination, current: UploadDestination) -> None:
    if stored != current:
        raise VideoUploadError("The conversation changed. Remove and attach the video again.", 409)


def require_capacity(used: int, requested: int, limit: int) -> None:
    if used + requested > limit:
        raise VideoUploadError("Video storage is full. Remove this attachment to continue.", 507)


def expiry_eligible(state: str, expires_at: int, now: int) -> bool:
    return (
        state in ("reserved", "transferring", "ready", "failed", "cancelled") and expires_at <= now
    )
