"""The conditional-response cases the supervisor named. Probe S1 must satisfy all of them."""
from __future__ import annotations

import sqlite3
import time
from collections.abc import Callable

import httpx
from tests.e2e.harness import JsonObject, ServerHandle


def _ticket_with_file(server: ServerHandle, cli, body: bytes, name: str = "note.md") -> str:
    ticket_id: str = cli(server, "ticket", "create", "--worker-type", "coding",
                         "--title", "Conditional cases")["id"]
    root = server.db_path.parent / "files" / "tickets" / ticket_id
    root.mkdir(parents=True, exist_ok=True)
    (root / name).write_bytes(body)
    return ticket_id


def test_cases(server: ServerHandle, cli: Callable[..., JsonObject]) -> None:
    ticket_id = _ticket_with_file(server, cli, b"A" * 64)
    url = server.base + f"/files/tickets/{ticket_id}/note.md"
    root = server.db_path.parent / "files" / "tickets" / ticket_id
    ok = []

    first = httpx.get(url, timeout=10)
    etag, last_modified = first.headers["etag"], first.headers["last-modified"]

    def check(label: str, got: object, want: object) -> None:
        mark = "PASS" if got == want else "FAIL"
        ok.append(mark == "PASS")
        print(f"  [{mark}] {label}: {got!r}")

    print("\n--- validators ---")
    check("cache policy is private and revalidating",
          first.headers.get("cache-control"), "private, no-cache")
    check("a matching If-None-Match is a hit",
          httpx.get(url, headers={"If-None-Match": etag}, timeout=10).status_code, 304)

    print("\n--- validator precedence ---")
    # The browser sends both. A stale ETag must win over a still-matching date.
    both = httpx.get(url, headers={"If-None-Match": '"stale-etag"',
                                   "If-Modified-Since": last_modified}, timeout=10)
    check("stale If-None-Match beats a matching If-Modified-Since", both.status_code, 200)
    check("  and the body comes back", len(both.content), 64)

    print("\n--- same-size rapid rewrite ---")
    before = httpx.get(url, timeout=10).headers["etag"]
    (root / "note.md").write_bytes(b"B" * 64)       # same size, immediately after
    after = httpx.get(url, timeout=10)
    check("the validator changed when the bytes changed", after.headers["etag"] != before, True)
    check("the old validator is no longer a hit",
          httpx.get(url, headers={"If-None-Match": before}, timeout=10).status_code, 200)
    check("the new content is served", after.content, b"B" * 64)

    print("\n--- range and if-range ---")
    big = _ticket_with_file(server, cli, bytes(range(256)) * 4096, "big.bin")
    big_url = server.base + f"/files/tickets/{big}/big.bin"
    head = httpx.get(big_url, timeout=10)
    ranged = httpx.get(big_url, headers={"Range": "bytes=10-19"}, timeout=10)
    check("a range request still answers 206", ranged.status_code, 206)
    check("  with the right content-range",
          ranged.headers.get("content-range"), "bytes 10-19/1048576")
    if_range = httpx.get(big_url, headers={"Range": "bytes=10-19",
                                           "If-Range": head.headers["etag"]}, timeout=10)
    check("If-Range with the live validator still gives the range", if_range.status_code, 206)

    print("\n--- authorization runs before any 304 ---")
    denied = httpx.get(server.base + "/files/sprint-items/si_nope/secret.md",
                       headers={"If-None-Match": etag}, timeout=10)
    check("an unreadable Sprint Item file is refused, not 304-ed",
          denied.status_code in (403, 404), True)
    missing = httpx.get(server.base + f"/files/tickets/{ticket_id}/absent.md",
                        headers={"If-None-Match": etag}, timeout=10)
    check("a missing file is a miss, not a 304", missing.status_code, 404)

    print(f"\n{sum(ok)}/{len(ok)} passed")
    assert all(ok)
