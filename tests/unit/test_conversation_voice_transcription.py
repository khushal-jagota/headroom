"""Voice input's server side: the transcription route and the module under it.

The route tests drive the real router over a real store, with the transcription function
itself patched. They prove parsing, validation, error mapping, and the absence of
conversation storage. The trim tests run the real ffmpeg on generated audio, because the
trim's whole job is what ffmpeg actually does to bytes.
"""

from __future__ import annotations

import asyncio
import base64
import io
import math
import shutil
import struct
import wave
from collections.abc import Callable, Coroutine, Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from planner.conversation import api as conversation_api
from planner.conversation import voice_transcription
from planner.conversation.api import ConversationRuntime, router
from planner.conversation.backend_lifecycle import BackendLifecycleCoordinator
from planner.conversation.backend_usage import BackendUsageService
from planner.conversation.contracts import ConversationBackendKey
from planner.conversation.live_tail import ConversationLiveTail
from planner.conversation.message_files import ConversationMessageFiles
from planner.conversation.snapshot import BackendSnapshotService
from planner.conversation.storage import ConversationStore
from planner.conversation.system import SqliteProcessConversationSystem
from planner.conversation.voice_transcription import (
    VoiceTranscriptionFailed,
    transcribe_conversation_audio,
    trim_surrounding_silence,
    without_whisper_hallucination,
)
from planner.core.config import load_config
from planner.core.db import connect, create_schema


def _run(exercise: Callable[[], Coroutine[Any, Any, None]]) -> None:
    asyncio.run(asyncio.wait_for(exercise(), 30.0))


# --- the junk-phrase filter ----------------------------------------------------------------


@pytest.mark.parametrize("fabricated", ["Thank you."])
def test_a_whole_transcript_that_is_a_known_fabrication_becomes_empty(
    fabricated: str,
) -> None:
    assert without_whisper_hallucination(fabricated) == ""


@pytest.mark.parametrize(
    "spoken", ["Thank you for the report, please continue."]
)
def test_a_transcript_that_merely_contains_a_fabrication_phrase_is_kept(
    spoken: str,
) -> None:
    assert without_whisper_hallucination(spoken) == spoken


def test_a_kept_transcript_is_returned_with_its_edges_trimmed() -> None:
    assert without_whisper_hallucination("  Deploy it.  ") == "Deploy it."


# --- the silence trim, against the real ffmpeg ---------------------------------------------


def _wav_bytes(*segments: tuple[float, float]) -> bytes:
    """A mono 16-bit 16kHz WAV of (seconds, amplitude 0..1) segments; 0 is silence."""
    sample_rate = 16000
    samples = bytearray()
    for seconds, amplitude in segments:
        for n in range(int(seconds * sample_rate)):
            value = int(amplitude * 20000 * math.sin(2 * math.pi * 440 * n / sample_rate))
            samples += struct.pack("<h", value)
    container = io.BytesIO()
    with wave.open(container, "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(sample_rate)
        writer.writeframes(bytes(samples))
    return container.getvalue()


def _use_available_ffmpeg(monkeypatch: pytest.MonkeyPatch) -> None:
    executable = shutil.which("ffmpeg")
    assert executable is not None
    monkeypatch.setattr(voice_transcription, "FFMPEG_PATH", executable)


# ffmpeg is a hard dependency of the silence trim; these tests fail loudly
# without it rather than skipping, because verify forbids tainted suites.

def test_trim_removes_surrounding_silence_and_keeps_the_tone(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _use_available_ffmpeg(monkeypatch)
    with_silence = _wav_bytes((1.0, 0.0), (0.5, 0.9), (1.0, 0.0))

    async def exercise() -> None:
        trimmed, is_effectively_silent = await trim_surrounding_silence(with_silence)
        assert not is_effectively_silent
        assert trimmed != with_silence
        assert trimmed[:4] == b"OggS"

    _run(exercise)




def test_bytes_ffmpeg_cannot_read_come_back_unchanged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _use_available_ffmpeg(monkeypatch)
    not_audio = b"this is not audio at all"

    async def exercise() -> None:
        trimmed, is_effectively_silent = await trim_surrounding_silence(not_audio)
        assert trimmed == not_audio
        assert not is_effectively_silent

    _run(exercise)


def test_a_silent_clip_is_answered_empty_without_a_provider_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The provider is never spoken to for a clip the trim proved silent, so a key that
    would fail any real request goes unused."""
    _use_available_ffmpeg(monkeypatch)
    silence = _wav_bytes((2.0, 0.0))

    async def exercise() -> None:
        transcript = await transcribe_conversation_audio(
            silence,
            base_url="http://127.0.0.1:1/nowhere",
            model="whisper-large-v3-turbo",
            api_key="a-key",
        )
        assert transcript == ""

    _run(exercise)




# --- the route -----------------------------------------------------------------------------


def _no_child_in_this_test(**_ignored: Any) -> Any:
    raise AssertionError("a voice-transcription test spawned a backend child")


class _Harness:
    """The real router over a real store, with one conversation already in the record."""

    def __init__(self, db_path: Path) -> None:
        self.store = ConversationStore(str(db_path))
        self.message_files = ConversationMessageFiles(str(db_path))
        live_tail = ConversationLiveTail()
        self.runtime = ConversationRuntime(
            store=self.store,
            system=SqliteProcessConversationSystem(
                store=self.store,
                message_files=self.message_files,
                # No route here spawns a backend child; the factory is never called.
                backend_child_factories={
                    backend_key: _no_child_in_this_test
                    for backend_key in ConversationBackendKey
                },
                live_tail=live_tail,
            ),
            live_tail=live_tail,
            backend_snapshots=BackendSnapshotService(
                backend_lifecycle=BackendLifecycleCoordinator()
            ),
            backend_usage=BackendUsageService({}),
            message_files=self.message_files,
            database_path=str(db_path),
            sse_heartbeat_ms=1000,
        )
        self.app = FastAPI()
        self.app.include_router(router, prefix="/api/conversation")
        self.app.state.conversation = self.runtime
        self.app.state.config = load_config(path=None, env={"PLAN_GROQ_API_KEY": "a-key"})

    def client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app), base_url="http://conversation"
        )


@pytest.fixture
def harness(tmp_path: Path) -> Iterator[_Harness]:
    db_path = tmp_path / "voice.db"
    conn = connect(str(db_path))
    create_schema(conn)
    conn.close()
    yield _Harness(db_path)


def _post(
    client: httpx.AsyncClient, body: dict[str, Any]
) -> Coroutine[Any, Any, httpx.Response]:
    return client.post("/api/conversation/voice-transcriptions", json=body)


def _encoded(contents: bytes) -> str:
    return base64.b64encode(contents).decode("ascii")


def test_audio_is_transcribed_without_a_conversation_or_file(
    harness: _Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    heard: list[bytes] = []

    async def fake_transcribe(audio: bytes, **_ignored: Any) -> str:
        heard.append(audio)
        return "editable first message"

    monkeypatch.setattr(
        conversation_api, "transcribe_conversation_audio", fake_transcribe
    )

    async def exercise() -> None:
        async with harness.client() as client:
            response = await _post(
                client,
                {
                    "audio": _encoded(b"browser-held-audio"),
                    "media_type": "audio/webm;codecs=opus",
                },
            )
        assert response.status_code == 200
        assert response.json() == {"transcript": "editable first message"}
        assert heard == [b"browser-held-audio"]
        conn = connect(harness.runtime.database_path)
        try:
            assert conn.execute("SELECT COUNT(*) FROM conversations").fetchone()[0] == 0
        finally:
            conn.close()
        files = list(
            (Path(harness.runtime.database_path).parent / "files" / "conversations").rglob("*")
        )
        assert files == []

    _run(exercise)


def test_route_accepts_fresh_audio_only(harness: _Harness) -> None:
    async def exercise() -> None:
        async with harness.client() as client:
            stored_only = await _post(client, {"stored_file_id": "f_1"})
            both = await _post(
                client,
                {"audio": _encoded(b"x"), "stored_file_id": "f_1"},
            )
        assert stored_only.status_code == 422
        assert both.status_code == 422

    _run(exercise)




def test_provider_failure_does_not_name_or_store_a_file(
    harness: _Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def failing_transcribe(audio: bytes, **_ignored: Any) -> str:
        raise VoiceTranscriptionFailed("the transcription provider answered 500")

    monkeypatch.setattr(
        conversation_api, "transcribe_conversation_audio", failing_transcribe
    )

    async def exercise() -> None:
        async with harness.client() as client:
            response = await _post(
                client, {"audio": _encoded(b"browser-held-audio")}
            )
        assert response.status_code == 502
        assert response.json()["detail"] == "the transcription provider answered 500"
        conn = connect(harness.runtime.database_path)
        try:
            assert conn.execute("SELECT COUNT(*) FROM conversations").fetchone()[0] == 0
        finally:
            conn.close()

    _run(exercise)




def test_a_missing_key_is_a_503_naming_the_environment_variable(
    harness: _Harness,
) -> None:
    harness.app.state.config = load_config(path=None, env={})

    async def exercise() -> None:
        async with harness.client() as client:
            response = await _post(client, {"audio": _encoded(b"opus-bytes")})
        assert response.status_code == 503
        assert "PLAN_GROQ_API_KEY" in response.json()["detail"]

    _run(exercise)


def test_a_media_type_outside_the_allowlist_is_refused(harness: _Harness) -> None:
    async def exercise() -> None:
        async with harness.client() as client:
            response = await _post(
                client, {"audio": _encoded(b"x"), "media_type": "video/webm"}
            )
        assert response.status_code == 422

    _run(exercise)


def test_a_clip_past_the_provider_ceiling_is_refused(
    harness: _Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(conversation_api, "MAX_VOICE_AUDIO_BYTES", 16)

    async def exercise() -> None:
        async with harness.client() as client:
            response = await _post(client, {"audio": _encoded(b"x" * 17)})
        assert response.status_code == 422

    _run(exercise)
