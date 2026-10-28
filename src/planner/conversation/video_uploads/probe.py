"""A bounded local ffprobe adapter; successful probing is not full decoding."""

import asyncio
import json
import shutil
from pathlib import Path

from planner.conversation.video_uploads import config
from planner.conversation.video_uploads.contracts import VideoUploadError


def require_probe() -> str:
    executable = shutil.which("ffprobe")
    if executable is None:
        raise VideoUploadError("Video uploads are unavailable: ffprobe is not installed.", 503)
    return executable


async def probe_video(path: Path, filename: str) -> str:
    process = await asyncio.create_subprocess_exec(
        require_probe(),
        "-v",
        "error",
        "-protocol_whitelist",
        "file",
        "-show_entries",
        "format=format_name:format_tags=major_brand:stream=codec_type",
        "-of",
        "json",
        str(path),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
        limit=config.PROBE_OUTPUT_BYTES + 1,
    )
    try:
        async with asyncio.timeout(config.PROBE_TIMEOUT_SECONDS):
            assert process.stdout is not None
            output = bytearray()
            while chunk := await process.stdout.read(4096):
                output.extend(chunk)
                if len(output) > config.PROBE_OUTPUT_BYTES:
                    raise VideoUploadError("Video validation produced too much output.")
            await process.wait()
        if process.returncode != 0:
            raise VideoUploadError("This file could not be validated as video.")
        value = json.loads(output)
        formats = set(value.get("format", {}).get("format_name", "").split(","))
        if not any(stream.get("codec_type") == "video" for stream in value.get("streams", [])):
            raise VideoUploadError("This file has no video stream.")
        suffix = Path(filename).suffix.lower()
        if suffix in (".mp4", ".mov") and "mov" in formats:
            brand = value.get("format", {}).get("tags", {}).get("major_brand", "")
            return "video/quicktime" if brand.strip() == "qt" or not brand else "video/mp4"
        if suffix == ".webm" and "webm" in formats:
            with path.open("rb") as source:
                header = source.read(config.CONTAINER_HEADER_BYTES)
            # ffprobe's demuxer reports 'matroska,webm' for both containers. Check
            # EBML's actual DocType rather than accepting renamed Matroska files.
            marker = header.find(b"\x42\x82")
            if marker >= 0 and marker + 2 < len(header):
                offset = marker + 2
                first = header[offset]
                width = next((n for n in range(1, 9) if first & (1 << (8 - n))), 0)
                if width and offset + width <= len(header):
                    length = first & ((1 << (8 - width)) - 1)
                    for byte in header[offset + 1 : offset + width]:
                        length = (length << 8) | byte
                    if length == 4 and header[offset + width : offset + width + 4] == b"webm":
                        return "video/webm"
        raise VideoUploadError("The video container does not match MP4, MOV, or WebM.")
    except (TimeoutError, json.JSONDecodeError, AttributeError, TypeError) as exc:
        raise VideoUploadError("Video validation failed or timed out.") from exc
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()
