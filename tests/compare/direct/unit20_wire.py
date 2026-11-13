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
    text: str,
    normalizer: Any,
    context: str,
    preview_created_at: int | None,
    catalog: bool = False,
    year_fields: bool = False,
    chart_keys: bool = False,
) -> str:
    """Keep original spans; change only declared values or named map-member order."""
    decoder = json.JSONDecoder()
    replacements: list[tuple[int, int, str]] = []
    ignored = normalizer.rules.get("response_ignore", {})
    tokens = normalizer.rules.get("response_tokens", {})
    ordered_objects = 0
    if year_fields:
        assert normalizer.rules["cli_direct_rules"]["dataset_year_fields"]["fields"] == [
            "2025",
            "2024",
        ]
    chart_fields = ["20", "10", "__proto__", "constructor", "toString"]
    if chart_keys:
        assert (
            normalizer.rules["cli_direct_rules"]["goal_numeric_chart_keys"]["fields"]
            == chart_fields
        )

    def whitespace(index: int) -> int:
        return len(text) - len(text[index:].lstrip())

    def walk(index: int, path: str, exact_path: str = "body") -> int:
        nonlocal ordered_objects
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
            members: list[tuple[str, int, int]] = []
            while True:
                cursor = whitespace(cursor)
                if text[cursor] == closing:
                    break
                child_path = path + ".*"
                child_exact = exact_path + "." + str(len(members))
                start = cursor
                key = ""
                if is_object:
                    key, cursor = decoder.raw_decode(text, cursor)
                    cursor = whitespace(cursor)
                    assert text[cursor] == ":"
                    cursor += 1
                    child_path = path + "." + key
                    child_exact = exact_path + "." + key
                member_end = walk(cursor, child_path, child_exact)
                members.append((key, start, member_end))
                cursor = whitespace(member_end)
                if text[cursor] == closing:
                    break
                assert text[cursor] == ","
                cursor += 1
            if (year_fields and exact_path == "body.results.0.rows.0.values") or (
                chart_keys and exact_path == "body.charts"
            ):
                expected = chart_fields if chart_keys else ["2025", "2024"]
                assert is_object and len(members) == len(expected) and set(value) == set(expected)
                if chart_keys:
                    ordered = [
                        next(item for item in members if item[0] == key) for key in ("10", "20")
                    ]
                    ordered += [item for item in members if item[0] not in ("10", "20")]
                else:
                    ordered = sorted(members, key=lambda item: item[0], reverse=True)
                separator = text[members[0][2] : members[1][1]]
                assert all(
                    text[left[2] : right[1]] == separator
                    for left, right in zip(members, members[1:], strict=False)
                )
                replacements.append(
                    (
                        members[0][1],
                        members[-1][2],
                        separator.join(text[start:stop] for _, start, stop in ordered),
                    )
                )
                ordered_objects += 1
        return end

    if ignored or tokens or preview_created_at is not None or catalog or year_fields or chart_keys:
        try:
            _, end = decoder.raw_decode(text, len(text) - len(text.lstrip()))
        except json.JSONDecodeError:
            assert not (year_fields or chart_keys)
            return cast(str, normalizer.normalize(text))  # Plain-text artifact response.
        if text[end:].strip():
            assert not (year_fields or chart_keys)
            return cast(str, normalizer.normalize(text))
        walk(0, "body")
    if year_fields or chart_keys:
        assert ordered_objects == 1, "the named member-order rule requires exactly one object"
    for start, end, replacement in sorted(replacements, reverse=True):
        text = text[:start] + replacement + text[end:]
    return cast(str, normalizer.normalize(text))


def wire_view(
    answer: dict[str, Any],
    normalizer: Any,
    *,
    preview_created_at: int | None = None,
    year_fields: bool = False,
) -> list[Any]:
    result = []
    for index, row in enumerate(answer["wire"]):
        assert row["complete"]
        chart_keys = answer.get("numeric_chart_keys", False)
        if chart_keys:
            assert len(answer["wire"]) == 1 and row["method"] == "GET" and row["status"] == 200
            assert re.fullmatch(r"/api/goals/goal_[a-z0-9]{8}/readings", urlsplit(row["path"]).path)
        if year_fields:
            assert len(answer["wire"]) == 1 and row["method"] == "POST"
            assert row["path"] == "/api/datasets/read"
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
                        year_fields,
                        chart_keys,
                    )
                    if row["path"].startswith("/api/")
                    else normalizer.normalize(raw)
                ),
            }
        )
    return result


def compare_wire(
    left: dict[str, Any],
    right: dict[str, Any],
    left_normalizer: Any,
    right_normalizer: Any,
    *,
    year_fields: bool = False,
) -> None:
    views = [
        wire_view(left, left_normalizer, year_fields=year_fields),
        wire_view(right, right_normalizer, year_fields=year_fields),
    ]
    assert views[0] == views[1], views


if __name__ == "__main__":
    destination = Path(sys.argv.pop(1))
    assert destination.is_file() and not destination.is_symlink()
    sys.argv[0] = "panels"
    with python_capture(destination):
        runpy.run_module("planner", run_name="__main__")
