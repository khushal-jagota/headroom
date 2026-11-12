"""Compare content-decoded HTTP document bytes; retain compressed entity bytes too.

Original JSON lexemes remain intact: documents are never serialized for comparison.
"""

from __future__ import annotations

import base64
import json
import os
import re
import runpy
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, cast
from unittest.mock import patch
from urllib.parse import urlsplit

import httpx


@contextmanager
def python_capture(destination: Path, base: str | None = None) -> Iterator[None]:
    original = httpx.Client.send
    owned = urlsplit(base or os.environ["PLAN_SERVER_URL"])

    def send(client: httpx.Client, request: httpx.Request, **kwargs: Any) -> httpx.Response:
        target = urlsplit(str(request.url))
        assert (target.scheme, target.hostname, target.port) == (
            owned.scheme,
            owned.hostname,
            owned.port,
        )
        assert not kwargs.get("stream"), "CLI wire capture requires the normal buffered client"
        response = original(client, request, **{**kwargs, "stream": True})
        source = cast(httpx.SyncByteStream, response.stream)
        chunks: list[bytes] = []

        class Observed(httpx.SyncByteStream):
            def __iter__(self) -> Iterator[bytes]:
                for chunk in source:
                    chunks.append(chunk)
                    yield chunk

            def close(self) -> None:
                source.close()

        response.stream = Observed()
        try:
            response.read()
        except BaseException:
            response.close()
            raise
        with destination.open("a") as output:
            output.write(
                json.dumps(
                    {
                        "method": request.method,
                        "path": request.url.raw_path.decode("ascii"),
                        "status": response.status_code,
                        "encoding": response.headers.get("content-encoding", ""),
                        "body_base64": base64.b64encode(response.content).decode(),
                        "transport_body_base64": base64.b64encode(b"".join(chunks)).decode(),
                        "complete": True,
                    }
                )
                + "\n"
            )
        return response

    with patch.object(httpx.Client, "send", send):
        yield


def read_capture(destination: Path) -> list[dict[str, Any]]:
    captured = [json.loads(line) for line in destination.read_text().splitlines()]
    for row in captured:
        encodings = {item.strip() for item in row["encoding"].split(",")}
        assert row["complete"] and encodings <= {"", "identity", "gzip", "deflate", "br"}
    return captured


def _body(
    text: str, normalizer: Any, context: str, preview_created_at: int | None, catalog: bool = False
) -> str:
    """JSONDecoder supplies locations; only declared value spans may change."""
    decoder = json.JSONDecoder()
    replacements: list[tuple[int, int, str]] = []
    ignored = normalizer.rules.get("response_ignore", {})
    tokens = normalizer.rules.get("response_tokens", {})

    def whitespace(index: int) -> int:
        return len(text) - len(text[index:].lstrip())

    def walk(index: int, path: str) -> int:
        index = whitespace(index)
        value, end = decoder.raw_decode(text, index)
        kind = type(value).__name__
        if path == "body.revision" and catalog:
            assert type(value) is str
            digest = re.fullmatch(r'"([0-9a-f]{64})(:[0-9]+)"', text[index:end])
            assert digest is not None
            token = normalizer.token(digest[1], context + ".catalog_hash")
            replacements.append((index + 1, index + 65, f"<catalog-hash:str:{token}>"))
        elif path in ignored:
            replacements.append((index, end, f"<declared-ignore:{kind}:{path}>"))
        elif path in tokens:
            token = normalizer.token(value, context + "." + path)
            replacements.append((index, end, f"<declared-token:{kind}:{token}>"))
        elif path == "body.created_at" and preview_created_at is not None:
            assert type(value) is int and value == preview_created_at
            replacements.append((index, end, "<preview-created-at>"))
        elif text[index] in "{[":
            is_object = text[index] == "{"
            closing = "}" if is_object else "]"
            cursor = index + 1
            while True:
                cursor = whitespace(cursor)
                if text[cursor] == closing:
                    break
                child_path = path + ".*"
                if is_object:
                    key, cursor = decoder.raw_decode(text, cursor)
                    cursor = whitespace(cursor)
                    assert text[cursor] == ":"
                    cursor += 1
                    child_path = path + "." + key
                cursor = whitespace(walk(cursor, child_path))
                if text[cursor] == closing:
                    break
                assert text[cursor] == ","
                cursor += 1
        return end

    if ignored or tokens or preview_created_at is not None or catalog:
        try:
            _, end = decoder.raw_decode(text, len(text) - len(text.lstrip()))
        except json.JSONDecodeError:
            return cast(str, normalizer.normalize(text))  # Plain-text artifact response.
        if text[end:].strip():
            return cast(str, normalizer.normalize(text))
        walk(0, "body")
    for start, end, replacement in sorted(replacements, reverse=True):
        text = text[:start] + replacement + text[end:]
    return cast(str, normalizer.normalize(text))


def wire_view(
    answer: dict[str, Any], normalizer: Any, *, preview_created_at: int | None = None
) -> list[Any]:
    result = []
    for index, row in enumerate(answer["wire"]):
        assert row["complete"]
        raw = base64.b64decode(row["body_base64"], validate=True).decode("utf-8")
        path = normalizer.normalize(row["path"])
        catalog = (
            bool(normalizer.rules.get("cli_wire_rules", {}).get("goal_catalog_revision"))
            and row["method"] == "GET"
            and re.fullmatch(
                r"/api/artifact-catalog/goal/goal_[a-z0-9]{8}", urlsplit(row["path"]).path
            )
            is not None
        )
        result.append(
            {
                "method": row["method"],
                "path": path,
                "status": row["status"],
                "body": (
                    _body(
                        raw,
                        normalizer,
                        f"wire.{index}.{row['method']}.{path}",
                        preview_created_at,
                        catalog,
                    )
                    if row["path"].startswith("/api/")
                    else normalizer.normalize(raw)
                ),
            }
        )
    return result


def compare_wire(
    left: dict[str, Any], right: dict[str, Any], left_normalizer: Any, right_normalizer: Any
) -> None:
    views = [wire_view(left, left_normalizer), wire_view(right, right_normalizer)]
    assert views[0] == views[1], views


if __name__ == "__main__":
    destination = Path(sys.argv.pop(1))
    assert destination.is_file() and not destination.is_symlink()
    sys.argv[0] = "panels"
    with python_capture(destination):
        runpy.run_module("planner", run_name="__main__")
