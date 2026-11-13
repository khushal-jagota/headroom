"""Compare decoded HTTP fields, with raw document and compressed bytes as evidence."""

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
        assert row["complete"] and encodings <= {
            "",
            "identity",
            "gzip",
            "deflate",
            "br",
        }
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
    """Compare JSON fields and numeric values, rather than Python serialization bytes."""
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        return cast(str, normalizer.normalize(text))
    ignored = normalizer.rules.get("response_ignore", {})
    tokens = normalizer.rules.get("response_tokens", {})

    def walk(item: Any, path: str) -> Any:
        if path == "body.revision" and catalog:
            assert isinstance(item, str)
            digest = re.fullmatch(r"([0-9a-f]{64})(:[0-9]+)", item)
            assert digest is not None
            token = normalizer.token(digest[1], context + ".catalog_hash")
            return f"<catalog-hash:{token}>{digest[2]}"
        kind = "number" if type(item) in (int, float) else type(item).__name__
        if path in ignored:
            return f"<declared-ignore:{kind}:{path}>"
        if path in tokens:
            return f"<declared-token:{kind}:{normalizer.token(item, context + '.' + path)}>"
        if path == "body.created_at" and preview_created_at is not None:
            assert type(item) is int and item == preview_created_at
            return "<preview-created-at>"
        if isinstance(item, dict):
            diagnostic = isinstance(item.get("error"), dict) and "message" in item["error"]
            if diagnostic:
                assert isinstance(item["error"]["message"], str), item
            result = {key: walk(child, path + "." + key) for key, child in item.items()}
            if diagnostic:
                result["error"]["message"] = "<diagnostic>"
            return result
        if isinstance(item, list):
            return [walk(child, path + ".*") for child in item]
        if isinstance(item, float) and item.is_integer():
            return int(item)
        return item

    return cast(
        str,
        normalizer.normalize(json.dumps(walk(value, "body"), sort_keys=True, ensure_ascii=False)),
    )


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
                r"/api/artifact-catalog/goal/goal_[a-z0-9]{8}",
                urlsplit(row["path"]).path,
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
