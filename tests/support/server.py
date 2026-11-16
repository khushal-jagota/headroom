"""Shared process handles and public HTTP helpers, without browser dependencies."""

from __future__ import annotations

import asyncio
import subprocess
import sys
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from planner.conversation.events import ConversationEventPayload
from planner.conversation.storage import ConversationStore

REPO_ROOT = Path(__file__).resolve().parents[2]
PLAN_BIN = Path(sys.executable).parent / "panels"
FAKE_NOW = "2026-07-04T12:00:00"
WAIT_MS = 10_000  # every Playwright wait

# A decoded JSON object, as the API and the CLI's --json hand one back. The values stay
# ``Any`` on purpose: this is somebody else's JSON, and pretending to know its shape here
# would be a fiction the tests would then have to fight.
type JsonObject = dict[str, Any]


@dataclass(frozen=True)
class ServerHandle:
    base: str
    proc: subprocess.Popen[bytes]
    db_path: Path
    log_path: Path
    port: int
    control_socket_path: Path

    @property
    def owner_cookies(self) -> dict[str, str]:
        """Khushal's cookie: the owner credential this server created beside its database."""
        from planner.core.owner_credential import OWNER_CREDENTIAL_COOKIE, owner_credential_path

        credential = owner_credential_path(str(self.db_path)).read_text(encoding="ascii").strip()
        return {OWNER_CREDENTIAL_COOKIE: credential}


class ApiHelper:
    """The three ways a test reaches the server's JSON API.

    A named class rather than a ``SimpleNamespace``: attribute access on a namespace is
    ``Any``, so every call a test made through this fixture was invisible to the type
    checker. The call syntax in the tests is unchanged.
    """

    def get(self, server: ServerHandle, path: str) -> JsonObject:
        resp = httpx.get(server.base + path, cookies=server.owner_cookies, timeout=10.0)
        assert resp.status_code < 300, f"GET {path} -> {resp.status_code}: {resp.text}"
        answer: JsonObject = resp.json()
        return answer

    def direct_post(self, server: ServerHandle, path: str, json_body: JsonObject) -> JsonObject:
        # The owner's cookie, which the direct-only /accept and PATCH fields permit.
        resp = httpx.post(
            server.base + path, json=json_body, cookies=server.owner_cookies, timeout=10.0
        )
        assert resp.status_code < 300, f"POST {path} -> {resp.status_code}: {resp.text}"
        answer: JsonObject = resp.json()
        return answer

    def direct_patch(self, server: ServerHandle, path: str, json_body: JsonObject) -> JsonObject:
        # The owner's PATCH. The day brief writer is direct-only, so this is how a
        # test seeds or edits a brief.
        resp = httpx.patch(
            server.base + path, json=json_body, cookies=server.owner_cookies, timeout=10.0
        )
        assert resp.status_code < 300, f"PATCH {path} -> {resp.status_code}: {resp.text}"
        answer: JsonObject = resp.json()
        return answer

    def create_ticket(
        self, server: ServerHandle, body: JsonObject, *, settle_brief: bool = False
    ) -> JsonObject:
        """Create a Ticket as Khushal, the way ``ticket create --input-json`` posts it.

        ``settle_brief`` then accepts the parked Brief with no next ceiling, as Khushal
        does in the browser, so the Ticket reaches its Worker stage.
        """
        created = self.direct_post(server, "/api/tickets", body)
        if not settle_brief:
            return created
        detail = self.get(server, f"/api/tickets?detail=full&id={created['id']}")
        proposal = detail["pending_proposal"]
        revision = proposal["revision"]
        return self.direct_post(
            server,
            f"/api/tickets/{created['id']}/accept/{proposal['field']}",
            {
                "expected_proposal_revision": revision,
                "decision_id": f"{created['id']}:{revision}:accept",
                "next_ceiling": "none",
                "next_holder": {"kind": "owner", "id": "owner"},
            },
        )

    def direct_put(self, server: ServerHandle, path: str, json_body: JsonObject) -> JsonObject:
        resp = httpx.put(
            server.base + path, json=json_body, cookies=server.owner_cookies, timeout=10.0
        )
        assert resp.status_code < 300, f"PUT {path} -> {resp.status_code}: {resp.text}"
        answer: JsonObject = resp.json()
        return answer


def _append_rows(
    server: ServerHandle, conversation_id: str, *payloads: ConversationEventPayload
) -> None:
    store = ConversationStore(str(server.db_path))

    async def write() -> None:
        for payload in payloads:
            await store.append_event(conversation_id, payload)

    failures: list[BaseException] = []

    def run() -> None:
        try:
            asyncio.run(write())
        except BaseException as error:  # noqa: BLE001 - re-raised on the browser thread
            failures.append(error)

    writer = threading.Thread(target=run)
    writer.start()
    writer.join(timeout=10)
    assert not writer.is_alive()
    if failures:
        raise failures[0]
