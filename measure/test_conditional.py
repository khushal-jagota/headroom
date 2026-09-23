"""Does the managed-file route answer a conditional request?"""
from __future__ import annotations
import sqlite3
from collections.abc import Callable
import httpx
from tests.e2e.harness import JsonObject, ServerHandle
from measure.test_measure_duplicate_fetches import _seed_files


def test_conditional(server: ServerHandle, cli: Callable[..., JsonObject]) -> None:
    ticket_id: str = cli(server, "ticket", "create", "--worker-type", "coding",
                         "--title", "Conditional probe")["id"]
    _seed_files(server, ticket_id)
    url = server.base + f"/files/tickets/{ticket_id}/images/shot-0.png"

    first = httpx.get(url, timeout=10)
    print("\nfirst  :", first.status_code, len(first.content), "bytes")
    for name in ("cache-control", "etag", "last-modified", "content-length", "vary"):
        print(f"   {name}: {first.headers.get(name, '(absent)')}")

    etag = first.headers.get("etag", "")
    second = httpx.get(url, headers={"If-None-Match": etag}, timeout=10)
    print("\nIf-None-Match:", second.status_code, len(second.content), "bytes  <- 304 with 0 bytes is a hit")

    third = httpx.get(url, headers={"If-Modified-Since": first.headers.get("last-modified", "")},
                      timeout=10)
    print("If-Modified-Since:", third.status_code, len(third.content), "bytes")

    video = server.base + f"/files/tickets/{ticket_id}/video/walkthrough.mp4"
    head = httpx.get(video, headers={"Range": "bytes=0-1023"}, timeout=30)
    print("\nvideo Range request:", head.status_code, len(head.content), "bytes",
          "accept-ranges:", head.headers.get("accept-ranges", "(absent)"),
          "content-range:", head.headers.get("content-range", "(absent)"))
