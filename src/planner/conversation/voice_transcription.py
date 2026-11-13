"""Spoken audio into words, for a conversation's voice input.

A voice note reaches the record as text — the conversation vocabulary deliberately has no
sound in it — so somewhere the bytes a microphone produced have to become words. This is
that place, and the only one: trim the silence a person left around what they said, hand
the rest to an OpenAI-compatible transcription provider, and refuse the things a speech
model makes up when handed nothing.

The provider is spoken to directly rather than through an adapter registry because there
is exactly one operation and the OpenAI ``/audio/transcriptions`` shape *is* the adapter
boundary: any provider this is pointed at (Groq today) speaks it. Which provider that is
lives in configuration, not here.
"""

from __future__ import annotations

import asyncio
from typing import Final

import httpx

# How long one transcription request may take, end to end. Voice notes are seconds to a
# couple of minutes of audio; a provider that has not answered in this long is down.
VOICE_TRANSCRIPTION_TIMEOUT_SECONDS: Final = 60.0

# How long ffmpeg may take to trim a clip. Trimming is a local, faster-than-realtime pass.
SILENCE_TRIM_TIMEOUT_SECONDS: Final = 30.0

FFMPEG_PATH: Final = "/usr/bin/ffmpeg"

# Trim quiet lead-in and tail-out: remove leading silence, reverse, remove the (now
# leading) trailing silence, reverse back. -45dB / 0.3s keeps quiet speech and breath
# while dropping the dead air around the press and release of a record button.
_SILENCE_TRIM_FILTER: Final = (
    "silenceremove=start_periods=1:start_threshold=-45dB:start_silence=0.3,"
    "areverse,"
    "silenceremove=start_periods=1:start_threshold=-45dB:start_silence=0.3,"
    "areverse"
)

# An Ogg/Opus file that is nothing but container headers and a breath of audio is a few
# hundred bytes. A trimmed output below this after a *successful* trim means the whole
# clip was silence, so there is nothing to transcribe and no provider call to make. The
# threshold is deliberately tiny — under it even one spoken word cannot fit — because a
# false "silence" would swallow speech, while a false "speech" merely costs one cheap
# provider call that comes back empty or as a filtered hallucination.
_EFFECTIVELY_SILENT_BYTES: Final = 1024

# Whole transcripts Whisper is known to fabricate over silence or noise. It was trained
# on captioned video, so an empty clip comes back as a caption sign-off. Matched against
# the entire transcript only (case-insensitive, punctuation stripped): a real message
# that *contains* one of these phrases is untouched, and matching only whole transcripts
# keeps this from ever eating part of something a person actually said.
WHISPER_HALLUCINATION_PHRASES: Final = frozenset(
    {
        "thank you",
        "thank you for watching",
        "thanks for watching",
        "subscribe",
        "please subscribe",
        "bye",
        "you",
        "the end",
        "so",
    }
)

# What is peeled off the edges before a transcript is compared against the phrases.
_SURROUNDING_PUNCTUATION: Final = " \t\n\r.,!?…\"'“”‘’-—"


class VoiceTranscriptionUnconfigured(RuntimeError):
    """No provider key is set, so transcription cannot happen at all."""


class VoiceTranscriptionFailed(RuntimeError):
    """The provider could not be reached or refused the request."""


def without_whisper_hallucination(transcript: str) -> str:
    """The transcript, or nothing when it is one of Whisper's silence fabrications.

    Whole-transcript match only. A finer rule — dropping a trailing "thank you" from a
    long real message — would risk deleting words a person said, and the fabrications
    this exists for arrive alone: silence in, one polite sign-off out.
    """
    stripped = transcript.strip()
    comparable = stripped.strip(_SURROUNDING_PUNCTUATION).lower()
    if not comparable or comparable in WHISPER_HALLUCINATION_PHRASES:
        return ""
    return stripped


async def trim_surrounding_silence(audio: bytes) -> tuple[bytes, bool]:
    """The clip with its silent edges removed, and whether it turned out to be all silence.

    Returns ``(trimmed_bytes, is_effectively_silent)``. The trim is best-effort: when
    ffmpeg is missing, fails, times out, or produces nothing, the original bytes come
    back unmarked and the provider hears the clip as recorded. Only a trim that
    *succeeded* and left near-nothing (see ``_EFFECTIVELY_SILENT_BYTES``) declares the
    clip silent — a conservative rule, so a broken ffmpeg can never eat a message.
    """
    command = (
        FFMPEG_PATH,
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        "pipe:0",
        "-af",
        _SILENCE_TRIM_FILTER,
        "-c:a",
        "libopus",
        "-f",
        "ogg",
        "pipe:1",
    )
    try:
        process = await asyncio.create_subprocess_exec(
            *command,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            trimmed, _stderr = await asyncio.wait_for(
                process.communicate(audio), timeout=SILENCE_TRIM_TIMEOUT_SECONDS
            )
        except TimeoutError:
            process.kill()
            await process.wait()
            return audio, False
    except OSError:
        return audio, False
    if process.returncode != 0 or not trimmed:
        return audio, False
    if len(trimmed) < _EFFECTIVELY_SILENT_BYTES:
        return trimmed, True
    return trimmed, False


async def transcribe_conversation_audio(
    audio: bytes, *, base_url: str, model: str, api_key: str | None
) -> str:
    """One voice clip, as the words it carried — possibly none.

    Trims the surrounding silence, sends what remains to the provider's OpenAI-compatible
    ``/audio/transcriptions``, and filters the known silence fabrications. A clip the trim
    proved silent is answered as empty without a provider call.

    Raises ``VoiceTranscriptionUnconfigured`` when no key is set, and
    ``VoiceTranscriptionFailed`` when the provider cannot be reached or says no.
    """
    if api_key is None:
        raise VoiceTranscriptionUnconfigured(
            "voice transcription needs PLAN_GROQ_API_KEY, which is not set"
        )
    trimmed, is_effectively_silent = await trim_surrounding_silence(audio)
    if is_effectively_silent:
        return ""
    url = f"{base_url.rstrip('/')}/audio/transcriptions"
    try:
        async with httpx.AsyncClient(timeout=VOICE_TRANSCRIPTION_TIMEOUT_SECONDS) as client:
            response = await client.post(
                url,
                headers={"Authorization": f"Bearer {api_key}"},
                files={"file": ("audio.ogg", trimmed, "audio/ogg")},
                data={"model": model, "response_format": "json"},
            )
    except httpx.HTTPError as unreachable:
        raise VoiceTranscriptionFailed(
            "the transcription provider could not be reached"
        ) from unreachable
    if response.status_code < 200 or response.status_code >= 300:
        raise VoiceTranscriptionFailed(
            f"the transcription provider answered {response.status_code}"
        )
    try:
        body = response.json()
    except ValueError as not_json:
        raise VoiceTranscriptionFailed(
            "the transcription provider answered something that is not JSON"
        ) from not_json
    text = body.get("text") if isinstance(body, dict) else None
    if not isinstance(text, str):
        raise VoiceTranscriptionFailed(
            "the transcription provider's answer carries no transcript"
        )
    return without_whisper_hallucination(text)


__all__ = [
    "VOICE_TRANSCRIPTION_TIMEOUT_SECONDS",
    "WHISPER_HALLUCINATION_PHRASES",
    "VoiceTranscriptionFailed",
    "VoiceTranscriptionUnconfigured",
    "transcribe_conversation_audio",
    "trim_surrounding_silence",
    "without_whisper_hallucination",
]
