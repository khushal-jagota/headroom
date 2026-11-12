"""Voice policy agrees with Python through recorded trim and HTTP boundaries.

The route proof cannot establish provider request bytes or trim fallback policy.
This driver uses small synthetic bytes, the existing native-home fixture, and an
export-only TS companion. It starts no listener, ffmpeg, or provider connection.
"""

from __future__ import annotations

import asyncio
import json
import subprocess
import time
from pathlib import Path
from typing import Any
from unittest.mock import patch

import httpx
import pytest
from tests.support.synthetic_homes import create_synthetic_homes, synthetic_home_environment

from planner.conversation import voice_transcription as voice

ROOT = Path(__file__).resolve().parents[3]


def _spec() -> dict[str, Any]:
    ordinary = {
        "api_key": "synthetic-key",
        "trim_hex": b"trimmed synthetic audio".hex(),
        "silent": False,
        "status_code": 200,
        "response_text": json.dumps({"text": "  Keep these words.  "}),
    }
    return {
        "audio_hex": b"original synthetic audio".hex(),
        "phrases": [
            "  Thank you for watching!  ",
            "—PLEASE SUBSCRIBE…",
            "   ",
            "I want to thank you for watching the window.",
            "\x1cKeep this message.\x1f",
            "\ufeffthank you\ufeff",
        ],
        "transcriptions": [
            {**ordinary, "name": "spoken-words"},
            {**ordinary, "name": "unconfigured", "api_key": None},
            {**ordinary, "name": "silence", "silent": True},
            {**ordinary, "name": "unreachable", "transport_error": True},
            {**ordinary, "name": "provider-refused", "status_code": 429},
            {**ordinary, "name": "not-json", "response_text": "{"},
            {**ordinary, "name": "missing-transcript", "response_text": '{"other":"text"}'},
            {**ordinary, "name": "wrong-transcript-type", "response_text": '{"text":5}'},
            {**ordinary, "name": "hallucination", "response_text": '{"text":"Thank you!"}'},
        ],
        "trim_results": [
            {
                "name": "silent",
                "exit_code": 0,
                "contents_hex": (b"a" * 1023).hex(),
                "unavailable": False,
            },
            {
                "name": "threshold",
                "exit_code": 0,
                "contents_hex": (b"a" * 1024).hex(),
                "unavailable": False,
            },
            {"name": "empty", "exit_code": 0, "contents_hex": "", "unavailable": False},
            {
                "name": "failure",
                "exit_code": 1,
                "contents_hex": b"partial".hex(),
                "unavailable": False,
            },
            {"name": "unavailable", "exit_code": -1, "contents_hex": "", "unavailable": True},
        ],
    }


async def _transcription(scenario: dict[str, Any], audio: bytes) -> dict[str, Any]:
    calls: list[dict[str, Any]] = []

    async def trim(contents: bytes) -> tuple[bytes, bool]:
        calls.append({"operation": "trim", "audio_hex": contents.hex()})
        return bytes.fromhex(scenario["trim_hex"]), scenario["silent"]

    class Client:
        def __init__(self, *, timeout: float) -> None:
            self.timeout = timeout

        async def __aenter__(self) -> Client:
            return self

        async def __aexit__(self, *_arguments: Any) -> None:
            return None

        async def post(
            self,
            url: str,
            *,
            headers: dict[str, str],
            files: dict[str, tuple[str, bytes, str]],
            data: dict[str, str],
        ) -> httpx.Response:
            filename, contents, media_type = files["file"]
            calls.append(
                {
                    "operation": "http",
                    "url": url,
                    "headers": headers,
                    "file": {
                        "filename": filename,
                        "contents_hex": contents.hex(),
                        "media_type": media_type,
                    },
                    "data": data,
                    "timeout_seconds": self.timeout,
                }
            )
            if scenario.get("transport_error"):
                raise httpx.ConnectError("Synthetic HTTP failure")
            return httpx.Response(scenario["status_code"], content=scenario["response_text"])

    with (
        patch.object(voice, "trim_surrounding_silence", trim),
        patch.object(httpx, "AsyncClient", Client),
    ):
        try:
            answer: dict[str, Any] = {
                "transcript": await voice.transcribe_conversation_audio(
                    audio,
                    base_url="https://voice.invalid/openai/v1///",
                    model="recorded-whisper",
                    api_key=scenario["api_key"],
                )
            }
        except (voice.VoiceTranscriptionFailed, voice.VoiceTranscriptionUnconfigured) as error:
            answer = {"error": type(error).__name__, "detail": str(error)}
    return {"name": scenario["name"], "answer": answer, "calls": calls}


async def _trim(scenario: dict[str, Any], audio: bytes) -> dict[str, Any]:
    class Process:
        returncode = scenario["exit_code"]

        async def communicate(self, contents: bytes) -> tuple[bytes, bytes]:
            assert contents == audio
            return bytes.fromhex(scenario["contents_hex"]), b""

    async def start(*_arguments: Any, **_keywords: Any) -> Process:
        if scenario["unavailable"]:
            raise OSError("Synthetic missing ffmpeg")
        return Process()

    with patch.object(asyncio, "create_subprocess_exec", start):
        contents, silent = await voice.trim_surrounding_silence(audio)
    return {"name": scenario["name"], "contents_hex": contents.hex(), "silent": silent}


async def _python(spec: dict[str, Any]) -> dict[str, Any]:
    audio = bytes.fromhex(spec["audio_hex"])
    return {
        "phrases": [voice.without_whisper_hallucination(text) for text in spec["phrases"]],
        "transcriptions": [
            await _transcription(scenario, audio) for scenario in spec["transcriptions"]
        ],
        "trim_results": [await _trim(scenario, audio) for scenario in spec["trim_results"]],
    }


def test_unit19_voice_policy(tmp_path: Path, request: pytest.FixtureRequest) -> None:
    started = time.perf_counter()
    homes = create_synthetic_homes(tmp_path)
    spec = _spec()
    with synthetic_home_environment(homes):
        expected = asyncio.run(_python(spec))
        actual = json.loads(
            subprocess.check_output(
                [
                    "node",
                    "--input-type=module",
                    "-e",
                    "import {readFileSync} from 'node:fs'; "
                    "import {pathToFileURL} from 'node:url'; "
                    "const {compare_voice}=await import(pathToFileURL(process.argv[1]).href); "
                    "const spec=JSON.parse(readFileSync(0,'utf8')); "
                    "process.stdout.write(JSON.stringify(await compare_voice(spec)));",
                    str(ROOT / "server/conversation/voice_transcription/testing/direct.ts"),
                ],
                cwd=ROOT,
                input=json.dumps(spec),
                text=True,
            )
        )
    report = {
        "kind": "direct",
        "case": "unit19-voice-policy",
        "equal": actual == expected,
        "python": expected,
        "typescript": actual,
        "seconds": time.perf_counter() - started,
    }
    destination = request.config.getoption("--compare-report-dir")
    if destination:
        directory = Path(destination)
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "unit19-voice-policy.json").write_text(json.dumps(report, indent=2) + "\n")
    assert actual == expected
