"""Bounded view notifications and temporary, turn-scoped Full output.

A subscription queues one contentless wake. Output bytes stay on disk after the first
explicit Full demand, through reader absence, until the canonical turn ends.
"""

from __future__ import annotations

import asyncio
import hashlib
import shutil
import tempfile
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from functools import wraps
from pathlib import Path
from typing import BinaryIO, Concatenate
from uuid import uuid4

from planner.conversation import events as event
from planner.conversation.views.configuration import BODY_CHUNK_BYTES, SUBSCRIBER_CHANGE_LIMIT
from planner.conversation.views.contracts import (
    ConversationViewLens,
    ViewBodyChunk,
    ViewBodyReference,
    ViewLiveOutput,
)
from planner.conversation.views.storage import ViewBodyChanged


def _locked[**P, T](
    method: Callable[Concatenate[ConversationViewLiveOutput, P], T],
) -> Callable[Concatenate[ConversationViewLiveOutput, P], T]:
    @wraps(method)
    def invoke(self: ConversationViewLiveOutput, /, *args: P.args, **kwargs: P.kwargs) -> T:
        with self._lock:
            return method(self, *args, **kwargs)

    return invoke


@dataclass(slots=True)
class _LiveConversation:
    turn_key: str | None = None
    revision: int = 0
    activity_at: int | None = None
    spool: Path | None = None
    spool_bytes: int = 0
    output_generation: int = 0
    latest_tool_id: str | None = None


@dataclass(eq=False, slots=True)
class ViewSubscription:
    owner: ConversationViewLiveOutput
    conversation_id: str
    lens: ConversationViewLens
    transcript: bool
    wake: asyncio.Event = field(default_factory=asyncio.Event)
    pending_changes: int = 0
    overflow: bool = False
    closed: bool = False

    def notify(self, *, durable: bool) -> None:
        if self.closed:
            return
        if durable:
            self.pending_changes += 1
            if self.pending_changes > SUBSCRIBER_CHANGE_LIMIT:
                self.overflow = True
        self.wake.set()

    async def next_wake(self) -> bool:
        await self.wake.wait()
        self.wake.clear()
        self.pending_changes = 0
        return not self.closed and not self.overflow

    def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        self.owner.forget(self)
        self.wake.set()


class ConversationViewLiveOutput:
    def __init__(self, directory: Path | None = None) -> None:
        self._lock = threading.RLock()
        self.runtime_epoch = uuid4().hex
        self._directory = directory
        self._temporary: tempfile.TemporaryDirectory[str] | None = None
        self._conversations: dict[str, _LiveConversation] = {}
        self._subscriptions: dict[str, set[ViewSubscription]] = {}

    @_locked
    def cleanup_stale_epochs(self) -> None:
        """Call only after acquiring the canonical conversation runtime ownership."""
        if self._directory is None:
            return
        self._directory.mkdir(parents=True, exist_ok=True)
        for child in self._directory.glob("epoch-*"):
            if child.is_dir() and child.name != f"epoch-{self.runtime_epoch}":
                shutil.rmtree(child)

    def _root(self) -> Path:
        if self._directory is None:
            self._temporary = tempfile.TemporaryDirectory(prefix="panels-view-output-")
            self._directory = Path(self._temporary.name)
        root = self._directory / f"epoch-{self.runtime_epoch}"
        root.mkdir(parents=True, exist_ok=True)
        return root

    @staticmethod
    def _identity(value: str) -> str:
        return hashlib.sha256(value.encode("utf-8")).hexdigest()

    def _folder(self, conversation_id: str) -> Path:
        return self._root() / self._identity(conversation_id)

    @_locked
    def subscribe(
        self,
        conversation_id: str,
        lens: ConversationViewLens,
        *,
        transcript: bool,
        turn_key: str | None,
    ) -> ViewSubscription:
        subscription = ViewSubscription(self, conversation_id, lens, transcript)
        self._subscriptions.setdefault(conversation_id, set()).add(subscription)
        state = self._conversations.setdefault(
            conversation_id, _LiveConversation(turn_key=turn_key)
        )
        if state.turn_key != turn_key:
            self._reset_turn(conversation_id, turn_key)
        if lens == "full" and transcript and turn_key is not None:
            self._ensure_spool(conversation_id)
        return subscription

    @_locked
    def forget(self, subscription: ViewSubscription) -> None:
        watchers = self._subscriptions.get(subscription.conversation_id)
        if watchers is not None:
            watchers.discard(subscription)
            if not watchers:
                del self._subscriptions[subscription.conversation_id]
        state = self._conversations.get(subscription.conversation_id)
        # A spool survives final-reader departure. Nonspooling status does not.
        if state is not None and state.spool is None and not watchers:
            self._conversations.pop(subscription.conversation_id, None)

    def _reset_turn(self, conversation_id: str, turn_key: str | None) -> None:
        state = self._conversations.setdefault(conversation_id, _LiveConversation())
        if state.spool is not None:
            shutil.rmtree(state.spool.parent, ignore_errors=True)
        state.turn_key = turn_key
        state.spool = None
        state.spool_bytes = 0
        state.output_generation += 1
        state.latest_tool_id = None
        state.revision += 1

    def _ensure_spool(self, conversation_id: str) -> None:
        state = self._conversations[conversation_id]
        if state.spool is not None or state.turn_key is None:
            return
        folder = self._folder(conversation_id)
        folder.mkdir(parents=True, exist_ok=True)
        state.spool = folder / "output.txt"
        state.spool.touch()

    def _full_demand(self, conversation_id: str) -> bool:
        return any(
            watcher.lens == "full" and watcher.transcript
            for watcher in self._subscriptions.get(conversation_id, ())
        )

    @staticmethod
    def _replace_content(path: Path, content: bytes) -> None:
        # Readers with an open descriptor retain the previous immutable inode.
        # The owner lock serializes replacement writers and reference validation.
        replacement = path.with_name(path.name + ".replacement")
        replacement.write_bytes(content)
        replacement.replace(path)

    @_locked
    def publish_event(
        self, conversation_id: str, sequence: int, payload: event.ConversationEventPayload
    ) -> None:
        state = self._conversations.get(conversation_id)
        if state is not None:
            if isinstance(payload, event.PromptEventPayload) and state.turn_key is None:
                self._reset_turn(conversation_id, f"turn:e{sequence}")
                if self._full_demand(conversation_id):
                    self._ensure_spool(conversation_id)
            elif isinstance(payload, event.TurnEndedEventPayload):
                self._reset_turn(conversation_id, None)
                if not self._subscriptions.get(conversation_id):
                    self._conversations.pop(conversation_id, None)
            elif isinstance(payload, event.AgentMessageEventPayload) and state.spool is not None:
                self._replace_content(state.spool, b"")
                state.spool_bytes = 0
                state.output_generation += 1
                state.revision += 1
            elif (
                isinstance(payload, event.ToolCallFinishedEventPayload) and state.spool is not None
            ):
                progress = state.spool.parent / ("tool-" + self._identity(payload.tool_call_id))
                progress.unlink(missing_ok=True)
                if state.latest_tool_id == payload.tool_call_id:
                    state.latest_tool_id = None
            state.revision += 1
        for watcher in tuple(self._subscriptions.get(conversation_id, ())):
            watcher.notify(durable=True)

    @_locked
    def publish_frame(self, conversation_id: str, frame: event.ConversationLiveTailFrame) -> None:
        state = self._conversations.get(conversation_id)
        if state is None:
            return
        state.activity_at = time.time_ns() // 1_000_000
        state.revision += 1
        if state.spool is not None:
            if isinstance(frame, event.AgentMessageDeltaFrame):
                encoded = frame.text_delta.encode("utf-8")
                with state.spool.open("ab") as output:
                    output.write(encoded)
                state.spool_bytes += len(encoded)
            elif isinstance(frame, event.ToolCallProgressFrame):
                path = state.spool.parent / ("tool-" + self._identity(frame.tool_call_id))
                self._replace_content(path, frame.detail.encode("utf-8"))
                state.latest_tool_id = frame.tool_call_id
        for watcher in tuple(self._subscriptions.get(conversation_id, ())):
            watcher.notify(durable=False)

    @_locked
    def status(self, conversation_id: str) -> tuple[int, int | None]:
        state = self._conversations.get(conversation_id)
        return (state.revision, state.activity_at) if state is not None else (0, None)

    @_locked
    def output(self, conversation_id: str) -> ViewLiveOutput | None:
        state = self._conversations.get(conversation_id)
        if state is None or state.spool is None or state.turn_key is None:
            return None
        reference: ViewBodyReference = {
            "body_id": "live",
            "version": f"{self.runtime_epoch}:{state.turn_key}:{state.output_generation}",
            "byte_count": state.spool_bytes,
            "media_type": "text/markdown",
        }
        return {
            "runtime_epoch": self.runtime_epoch,
            "turn_key": state.turn_key,
            "version": state.revision,
            "byte_count": state.spool_bytes,
            "reference": reference,
        }

    @_locked
    def progress(
        self, conversation_id: str, tool_call_id: str | None = None
    ) -> ViewBodyReference | None:
        state = self._conversations.get(conversation_id)
        identity = tool_call_id or (state.latest_tool_id if state else None)
        if state is None or state.spool is None or identity is None:
            return None
        path = state.spool.parent / ("tool-" + self._identity(identity))
        if not path.exists():
            return None
        stat = path.stat()
        return {
            "body_id": "progress:" + self._identity(identity),
            "version": f"{self.runtime_epoch}:{stat.st_mtime_ns}",
            "byte_count": stat.st_size,
            "media_type": "text/plain",
        }

    @_locked
    def latest_tool_id(self, conversation_id: str) -> str | None:
        state = self._conversations.get(conversation_id)
        return state.latest_tool_id if state else None

    @_locked
    def read_chunk(
        self, conversation_id: str, reference: ViewBodyReference, offset: int
    ) -> ViewBodyChunk:
        state = self._conversations.get(conversation_id)
        if state is None or state.spool is None:
            raise ViewBodyChanged("Live output belongs to an ended runtime turn")
        if reference["body_id"] == "live":
            current = self.output(conversation_id)
            if (
                current is None
                or reference["version"] != current["reference"]["version"]
                or reference["byte_count"] > current["byte_count"]
            ):
                raise ViewBodyChanged("Live output source changed")
            path = state.spool
        elif reference["body_id"].startswith("progress:"):
            digest = reference["body_id"].removeprefix("progress:")
            if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
                raise ValueError("Invalid progress identity")
            path = state.spool.parent / ("tool-" + digest)
            if (
                not path.exists()
                or reference["version"] != f"{self.runtime_epoch}:{path.stat().st_mtime_ns}"
            ):
                raise ViewBodyChanged("Tool progress source changed")
        else:
            raise ValueError("Unsupported live body")
        if not 0 <= offset <= reference["byte_count"]:
            raise ValueError("Live body offset is outside the source")
        with path.open("rb") as source:
            source.seek(offset)
            encoded = source.read(min(BODY_CHUNK_BYTES + 3, reference["byte_count"] - offset))
        end = min(BODY_CHUNK_BYTES, len(encoded))
        while end < len(encoded) and encoded[end] & 0xC0 == 0x80:
            end -= 1
        try:
            text = encoded[:end].decode("utf-8")
        except UnicodeDecodeError as error:
            raise ValueError("Live body offset is not a UTF-8 boundary") from error
        next_offset = offset + end
        return {
            "reference": reference,
            "offset": offset,
            "text": text,
            "next_offset": next_offset if next_offset < reference["byte_count"] else None,
        }

    @_locked
    def open_content(self, conversation_id: str, reference: ViewBodyReference) -> BinaryIO:
        # Validation and opening share the lock. The descriptor pins the immutable
        # prefix even if the canonical turn removes its name while this read finishes.
        self.read_chunk(conversation_id, reference, 0)
        state = self._conversations[conversation_id]
        assert state.spool is not None
        path = (
            state.spool
            if reference["body_id"] == "live"
            else (state.spool.parent / ("tool-" + reference["body_id"].removeprefix("progress:")))
        )
        return path.open("rb")

    @_locked
    def close(self) -> None:
        for watchers in tuple(self._subscriptions.values()):
            for watcher in tuple(watchers):
                watcher.close()
        for conversation_id in tuple(self._conversations):
            self._reset_turn(conversation_id, None)
        self._conversations.clear()
        if self._temporary is not None:
            self._temporary.cleanup()
        elif self._directory is not None:
            shutil.rmtree(self._directory / f"epoch-{self.runtime_epoch}", ignore_errors=True)
