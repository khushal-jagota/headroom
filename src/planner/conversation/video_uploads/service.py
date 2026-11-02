"""Durable bounded intake, atomic adoption, and conservative recovery."""

from __future__ import annotations

import os
import re
import shutil
import sqlite3
import time
from collections.abc import AsyncIterator, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Literal, cast
from uuid import uuid4

from planner.conversation.message_files import MEDIA_TYPE_SUFFIX, KeptMessageFile
from planner.conversation.video_uploads import config
from planner.conversation.video_uploads.contracts import (
    CheckedVideoUpload,
    ReserveVideoUpload,
    UploadDestination,
    VideoUploadError,
    VideoUploadStatus,
)
from planner.conversation.video_uploads.logic.rules import (
    expiry_eligible,
    require_capacity,
    validate_binding,
    validate_filename,
)
from planner.conversation.video_uploads.probe import probe_video, require_probe
from planner.core.db import connect
from planner.files.logic.paths import conversation_files_root

_ACTIVE = ("reserved", "transferring", "ready", "admitted", "linked")
_SAFE_ID = re.compile(r"^[A-Za-z0-9_-]+$")


class VideoUploadService:
    def __init__(self, db_path: str | Path) -> None:
        self.db_path = str(db_path)
        self.root = Path(db_path).parent / "files" / "video-uploads"
        self.root.mkdir(parents=True, exist_ok=True)

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        conn = connect(self.db_path)
        try:
            conn.execute("BEGIN IMMEDIATE")
            yield conn
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _row(self, conn: sqlite3.Connection, upload_id: str) -> sqlite3.Row:
        row = conn.execute(
            "SELECT * FROM conversation_video_uploads WHERE upload_id=?", (upload_id,)
        ).fetchone()
        if row is None:
            raise VideoUploadError("Video upload not found.", 404)
        return cast(sqlite3.Row, row)

    def _temporary(self, upload_id: str) -> Path:
        if not _SAFE_ID.fullmatch(upload_id):
            raise VideoUploadError("Invalid upload identifier.")
        return self.root / upload_id

    def _path(self, row: sqlite3.Row) -> Path:
        if row["conversation_id"] is not None:
            return (
                conversation_files_root(self.db_path)
                / str(row["conversation_id"])
                / str(row["stored_file_id"])
            )
        return self._temporary(row["upload_id"])

    @staticmethod
    def _status(row: sqlite3.Row) -> VideoUploadStatus:
        return VideoUploadStatus(
            **{
                name: row[name]
                for name in (
                    "upload_id",
                    "filename",
                    "byte_count",
                    "received_bytes",
                    "state",
                    "media_type",
                    "expires_at",
                    "error",
                )
            },
            destination=UploadDestination.model_validate_json(row["destination_json"]),
        )

    def status(self, upload_id: str) -> VideoUploadStatus:
        with self._transaction() as conn:
            return self._status(self._row(conn, upload_id))

    def _disk_capacity(self, conn: sqlite3.Connection, incoming: int = 0) -> None:
        outstanding = conn.execute("""SELECT COALESCE(SUM(byte_count-received_bytes),0)
            FROM conversation_video_uploads
            WHERE state IN ('reserved','transferring')""").fetchone()[0]
        if (
            shutil.disk_usage(self.root).free - outstanding - incoming
            < config.FREE_SPACE_FLOOR_BYTES
        ):
            raise VideoUploadError("Not enough free disk space for this video.", 507)

    def reserve(self, request: ReserveVideoUpload) -> VideoUploadStatus:
        require_probe()
        validate_filename(request.filename)
        if request.byte_count > config.MAX_VIDEO_BYTES:
            raise VideoUploadError("Video exceeds the 250 MiB limit.", 413)
        if not _SAFE_ID.fullmatch(request.destination.id):
            raise VideoUploadError("Invalid destination.")
        upload_id = "vu_" + uuid4().hex
        with self._transaction() as conn:
            used = conn.execute(
                """SELECT COALESCE(SUM(byte_count),0)
                FROM conversation_video_uploads WHERE state IN (?,?,?,?,?)""",
                _ACTIVE,
            ).fetchone()[0]
            require_capacity(used, request.byte_count, config.VIDEO_BUDGET_BYTES)
            self._disk_capacity(conn, request.byte_count)
            conn.execute(
                """INSERT INTO conversation_video_uploads
                (upload_id,destination_json,filename,byte_count,state,expires_at,stored_file_id)
                VALUES (?,?,?,?,?,?,?)""",
                (
                    upload_id,
                    request.destination.model_dump_json(),
                    request.filename,
                    request.byte_count,
                    "reserved",
                    int(time.time()) + config.UPLOAD_TTL_SECONDS,
                    "f_" + uuid4().hex,
                ),
            )
            return self._status(self._row(conn, upload_id))

    def _remove(self, row: sqlite3.Row) -> None:
        path = self._path(row)
        path.unlink(missing_ok=True)
        if row["conversation_id"] is not None:
            path.with_name(path.name + MEDIA_TYPE_SUFFIX).unlink(missing_ok=True)
        self._temporary(row["upload_id"]).unlink(missing_ok=True)

    def cancel(self, upload_id: str) -> None:
        with self._transaction() as conn:
            row = self._row(conn, upload_id)
            if row["state"] in ("admitted", "linked"):
                raise VideoUploadError("This video is in use. It cannot be removed.", 409)
            self._remove(row)
            conn.execute(
                "UPDATE conversation_video_uploads SET state='cancelled' WHERE upload_id=?",
                (upload_id,),
            )

    async def transfer(self, upload_id: str, chunks: AsyncIterator[bytes]) -> VideoUploadStatus:
        with self._transaction() as conn:
            row = self._row(conn, upload_id)
            if row["state"] != "reserved" or row["expires_at"] <= int(time.time()):
                raise VideoUploadError(
                    "This upload is no longer available. Attach the video again.", 409
                )
            conn.execute(
                "UPDATE conversation_video_uploads SET state='transferring' WHERE upload_id=?",
                (upload_id,),
            )
            declared, filename = row["byte_count"], row["filename"]
        received = 0
        try:
            with self._temporary(upload_id).open("xb") as output:
                async for incoming in chunks:
                    for offset in range(0, len(incoming), config.TRANSFER_CHUNK_BYTES):
                        chunk = incoming[offset : offset + config.TRANSFER_CHUNK_BYTES]
                        if received + len(chunk) > declared:
                            raise VideoUploadError("Transfer exceeds its reserved size.", 413)
                        with self._transaction() as conn:
                            if self._row(conn, upload_id)["state"] != "transferring":
                                raise VideoUploadError(
                                    "The video upload was cancelled or expired.", 409
                                )
                            self._disk_capacity(conn)
                            if (
                                shutil.disk_usage(self.root).free - len(chunk)
                                < config.FREE_SPACE_FLOOR_BYTES
                            ):
                                raise VideoUploadError(
                                    "Video upload stopped: disk space is low.", 507
                                )
                            output.write(chunk)
                            received += len(chunk)
                            conn.execute(
                                "UPDATE conversation_video_uploads SET received_bytes=? "
                                "WHERE upload_id=?",
                                (received, upload_id),
                            )
                output.flush()
                os.fsync(output.fileno())
            if received != declared:
                raise VideoUploadError("Video transfer was interrupted. Retry the attachment.")
            media_type = await probe_video(self._temporary(upload_id), filename)
            with self._transaction() as conn:
                if self._row(conn, upload_id)["state"] != "transferring":
                    raise VideoUploadError("The video upload was cancelled or expired.", 409)
                conn.execute(
                    "UPDATE conversation_video_uploads SET state='ready',media_type=? "
                    "WHERE upload_id=?",
                    (media_type, upload_id),
                )
            return self.status(upload_id)
        except BaseException as exc:
            self._temporary(upload_id).unlink(missing_ok=True)
            with self._transaction() as conn:
                conn.execute(
                    "UPDATE conversation_video_uploads SET state='failed',error=? "
                    "WHERE upload_id=?",
                    (str(exc) or "Upload interrupted.", upload_id),
                )
            if isinstance(exc, OSError):
                raise VideoUploadError(
                    "Video could not be stored. Retry the attachment.", 507
                ) from exc
            raise

    def preflight(
        self, upload_id: str, destination: UploadDestination, sender_message_id: str
    ) -> CheckedVideoUpload:
        if not sender_message_id:
            raise VideoUploadError("Video messages require a stable message identity.")
        with self._transaction() as conn:
            row = self._row(conn, upload_id)
            stored = UploadDestination.model_validate_json(row["destination_json"])
            same_destination = (stored.kind, stored.id) == (destination.kind, destination.id)
            retry = (
                row["sender_message_id"] == sender_message_id
                and same_destination
                and row["state"] in ("admitted", "linked")
            )
            # Admission can precede creation. Until adoption records its target, an
            # interrupted first send still owns the original explicit-absence binding.
            if not retry or row["conversation_id"] is None:
                validate_binding(stored, destination)
            if not retry:
                if row["sender_message_id"] not in (None, sender_message_id):
                    raise VideoUploadError("This video already belongs to another message.", 409)
            if row["state"] not in ("ready", "admitted", "linked"):
                raise VideoUploadError("The video is not ready. Retry its upload.", 409)
            if row["state"] == "ready" and row["expires_at"] <= int(time.time()):
                raise VideoUploadError("The video expired. Attach it again.", 409)
            if row["state"] != "linked":
                conn.execute(
                    "UPDATE conversation_video_uploads SET state='admitted',sender_message_id=? "
                    "WHERE upload_id=?",
                    (sender_message_id, upload_id),
                )
            return CheckedVideoUpload(upload_id, stored, sender_message_id, row["conversation_id"])

    def _checked_row(self, conn: sqlite3.Connection, checked: CheckedVideoUpload) -> sqlite3.Row:
        row = self._row(conn, checked.upload_id)
        if (
            row["sender_message_id"] != checked.sender_message_id
            or row["destination_json"] != checked.destination.model_dump_json()
        ):
            raise VideoUploadError("Video admission does not match this message.", 409)
        return row

    def adopt(self, checked: CheckedVideoUpload, conversation_id: str) -> KeptMessageFile:
        if not _SAFE_ID.fullmatch(conversation_id):
            raise VideoUploadError("Invalid conversation identifier.")
        with self._transaction() as conn:
            row = self._checked_row(conn, checked)
            if row["state"] not in ("admitted", "linked"):
                raise VideoUploadError("Video admission is no longer active.", 409)
            if row["conversation_id"] is not None and row["conversation_id"] != conversation_id:
                raise VideoUploadError("This video belongs to a different conversation.", 409)
            # Persist the destination BEFORE rename, allowing crash recovery to find either side.
            conn.execute(
                "UPDATE conversation_video_uploads SET conversation_id=? WHERE upload_id=?",
                (conversation_id, checked.upload_id),
            )
        with self._transaction() as conn:
            row = self._checked_row(conn, checked)
            target = self._path(row)
            target.parent.mkdir(parents=True, exist_ok=True)
            source = self._temporary(checked.upload_id)
            if source.exists():
                os.replace(source, target)
            if not target.is_file() or target.stat().st_size != row["byte_count"]:
                raise VideoUploadError("The video file is missing. Retry the attachment.", 409)
            metadata = target.with_name(target.name + MEDIA_TYPE_SUFFIX)
            temporary = target.with_name("." + uuid4().hex)
            try:
                temporary.write_text(row["media_type"], encoding="utf-8")
                os.replace(temporary, metadata)
            finally:
                temporary.unlink(missing_ok=True)
            return KeptMessageFile(
                conversation_id, row["stored_file_id"], row["media_type"], target, row["byte_count"]
            )

    def finish(
        self, checked: CheckedVideoUpload, outcome: Literal["accepted", "refused", "uncertain"]
    ) -> None:
        with self._transaction() as conn:
            row = self._checked_row(conn, checked)
            # A later refusal must never unpin a file an earlier attempt already accepted.
            if row["state"] == "linked":
                return
            if outcome == "refused" and row["conversation_id"] is not None:
                source = self._path(row)
                temporary = self._temporary(checked.upload_id)
                if source.exists():
                    os.replace(source, temporary)
                if not temporary.is_file():
                    raise VideoUploadError("The video file is missing. Attach it again.", 409)
                source.with_name(source.name + MEDIA_TYPE_SUFFIX).unlink(missing_ok=True)
                conn.execute(
                    "UPDATE conversation_video_uploads SET conversation_id=NULL WHERE upload_id=?",
                    (checked.upload_id,),
                )
            state = {"accepted": "linked", "refused": "ready", "uncertain": "admitted"}[outcome]
            conn.execute(
                "UPDATE conversation_video_uploads SET state=?,outcome=? WHERE upload_id=?",
                (state, outcome, checked.upload_id),
            )

    def recover_and_cleanup(self) -> None:
        """Startup/periodic recovery: unknown admissions remain pinned within quota."""
        now = int(time.time())
        with self._transaction() as conn:
            rows = conn.execute("SELECT * FROM conversation_video_uploads").fetchall()
            for row in rows:
                if row["state"] == "admitted" and row["conversation_id"]:
                    accepted = conn.execute(
                        """SELECT 1 FROM conversation_events WHERE conversation_id=?
                        AND kind='prompt'
                        AND json_extract(payload,'$.sender_message_id')=? LIMIT 1""",
                        (row["conversation_id"], row["sender_message_id"]),
                    ).fetchone()
                    if accepted:
                        conn.execute(
                            "UPDATE conversation_video_uploads "
                            "SET state='linked',outcome='accepted' "
                            "WHERE upload_id=?",
                            (row["upload_id"],),
                        )
                    # Held and uncertain admissions retain their pin even without a prompt.
                if expiry_eligible(row["state"], row["expires_at"], now):
                    self._remove(row)
                    conn.execute(
                        "UPDATE conversation_video_uploads SET state='cancelled' WHERE upload_id=?",
                        (row["upload_id"],),
                    )

