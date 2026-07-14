"""Managed chat-image publication, path safety, upload, and serving."""

from __future__ import annotations

import asyncio
import base64
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from sqlite3 import Connection

import pytest
from fastapi.testclient import TestClient

from planner.chat.contracts import (
    GatewayStatus,
    HumanChatCompletion,
    HumanChatObservation,
)
from planner.chat.service import CHIEF_OF_STAFF_ENTITY_ID
from planner.core.adapters.base import HumanSessionKeyBinder
from planner.core.adapters.registry import Adapters, build_adapters
from planner.core.clock import build_clock
from planner.core.config import load_config
from planner.core.db import connect, create_schema
from planner.core.server import create_app
from planner.files.chat_images import store_chat_image
from planner.files.logic.paths import resolve_chat_file
from planner.tickets.contracts import EmployeeSessionHistory
from planner.tickets.data import create_ticket

PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGP4z8AAAAMBAQDJ/pLvAAAAAElFTkSuQmCC"
)
GIF = base64.b64decode("R0lGODlhAQABAIAAAAAAAP///ywAAAAAAQABAAACAUwAOw==")
WEBP = base64.b64decode("UklGRiIAAABXRUJQVlA4IBYAAAAwAQCdASoBAAEAAUAmJaQAA3AA/vuU")
ANIMATED_WEBP = base64.b64decode(
    "UklGRoQAAABXRUJQVlA4WAoAAAACAAAAAAAAAAAAQU5JTQYAAAAAAAD/AABBTk1GKgAAAAAAAAAA"
    "AAAAAAAAAGQAAAJWUDhMEQAAAC8AAAAAB9D//ve//4GI6H8AAEFOTUYmAAAAAAAAAAAAAAAAAAAA"
    "ZAAAAFZQOEwOAAAALwAAAAAHEBH9D0RE/wM="
)
JPEG = base64.b64decode(
    "/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAP//////////////////////////////////////////////////////////////////////////////////////"
    "2wBDAf//////////////////////////////////////////////////////////////////////////////////////"
    "wAARCAABAAEDASIAAhEBAxEB/8QAFQABAQAAAAAAAAAAAAAAAAAAAAf/xAAUEAEAAAAAAAAAAAAAAAAAAAAA/9oADAMBAAIQAxAAAAF//8QAFBABAAAAAAAAAAAAAAAAAAAAAP/aAAgBAQABBQJ//8QAFBEBAAAAAAAAAAAAAAAAAAAAAP/aAAgBAwEBPwF//8QAFBEBAAAAAAAAAAAAAAAAAAAAAP/aAAgBAgEBPwF//8QAFBABAAAAAAAAAAAAAAAAAAAAAP/aAAgBAQAGPwJ//8QAFBABAAAAAAAAAAAAAAAAAAAAAP/aAAgBAQABPyF//9oADAMBAAIAAwAAABD/xAAUEQEAAAAAAAAAAAAAAAAAAAAA/9oACAEDAQE/EB//xAAUEQEAAAAAAAAAAAAAAAAAAAAA/9oACAECAQE/EB//xAAUEAEAAAAAAAAAAAAAAAAAAAAA/9oACAEBAAE/EB//2Q=="
)


MALFORMED_PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR"
    + (1).to_bytes(4, "big")
    + (1).to_bytes(4, "big")
    + b"\x08\x02\x00\x00\x00"
    + b"\x00\x00\x00\x00"
    + b"not image data"
    + b"\x00\x00\x00\x00IEND\xaeB`\x82"
)


def _make_app(tmp_path: Path) -> tuple[object, Path]:
    db_path = tmp_path / "data" / "planning-test.db"
    db_path.parent.mkdir(parents=True)
    boot = connect(str(db_path))
    create_schema(boot)
    boot.close()
    config = load_config(
        path=None,
        env={
            "PLAN_TEST_MODE": "1",
            "PLAN_GATEWAY_ADAPTER": "fake",
            "PLAN_DB_PATH": str(db_path),
        },
    )
    clock = build_clock(config)
    adapters = build_adapters(config)

    def conn_factory() -> Connection:
        return connect(str(db_path))

    return create_app(config, clock, adapters, conn_factory), db_path


def _ticket(db_path: Path) -> str:
    conn = connect(str(db_path))
    try:
        return create_ticket(
            conn,
            worker_type="coding",
            title="Image chat",
            actor="human",
            now=0,
            title_max_chars=200,
        ).id
    finally:
        conn.close()


async def _chunks(*parts: bytes) -> AsyncIterator[bytes]:
    for part in parts:
        yield part


@pytest.mark.parametrize(
    ("payload", "claimed_name", "extension"),
    [
        (PNG, "wrong.jpg", ".png"),
        (JPEG, "wrong.gif", ".jpg"),
        (GIF, "wrong.webp", ".gif"),
        (WEBP, "wrong.png", ".webp"),
        (ANIMATED_WEBP, "animated.bin", ".webp"),
    ],
)
def test_store_chat_image_uses_sniffed_extension_and_atomic_publication(
    tmp_path: Path, payload: bytes, claimed_name: str, extension: str
) -> None:
    db_path = tmp_path / "data" / "planning.db"

    stored = asyncio.run(
        store_chat_image(db_path, "t_image123", _chunks(payload[:7], payload[7:]), claimed_name)
    )

    assert stored.entity_id == "t_image123"
    assert stored.relative_path.endswith(extension)
    assert stored.absolute_path.read_bytes() == payload
    assert stored.absolute_path.parent == db_path.parent / "files" / "chats" / "t_image123"
    assert not list((db_path.parent / "files").glob(".chat-upload-*"))
    assert resolve_chat_file(db_path, "t_image123", stored.relative_path) == stored


@pytest.mark.parametrize("entity_kind", ["ticket", "day", "chief"])
def test_chat_image_upload_accepts_each_real_chattable_entity(
    tmp_path: Path, entity_kind: str
) -> None:
    app, db_path = _make_app(tmp_path)
    entity_id = {
        "ticket": _ticket(db_path),
        "day": "day_2026-07-10",
        "chief": CHIEF_OF_STAFF_ENTITY_ID,
    }[entity_kind]

    with TestClient(app) as client:
        response = client.post(
            f"/api/chat/{entity_id}/images",
            content=PNG,
            headers={"Content-Type": "application/octet-stream", "X-Filename": "spoofed.txt"},
        )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["reference"].startswith(f"/files/chats/{entity_id}/")
    assert body["reference"].endswith(".png")
    assert body["markdown"] == f"![Attached image]({body['reference']})"
    assert (db_path.parent / body["reference"].lstrip("/")).read_bytes() == PNG


def test_chat_image_upload_rejects_agents_unknown_entities_and_spoofed_content(
    tmp_path: Path,
) -> None:
    app, db_path = _make_app(tmp_path)
    ticket_id = _ticket(db_path)

    with TestClient(app) as client:
        agent = client.post(
            f"/api/chat/{ticket_id}/images", content=PNG, headers={"X-Plan-Actor": "agent"}
        )
        missing = client.post("/api/chat/t_missing/images", content=PNG)
        spoofed = client.post(
            f"/api/chat/{ticket_id}/images",
            content=b"not an image",
            headers={"Content-Type": "image/png", "X-Filename": "image.png"},
        )

    assert agent.status_code == 400
    assert agent.json()["error"]["code"] == "agent_forbidden"
    assert missing.status_code == 404
    assert spoofed.status_code == 400
    assert spoofed.json()["error"]["code"] == "validation"
    assert list((db_path.parent / "files" / "chats").glob("**/*")) == []


def test_chat_image_publication_cleans_oversize_and_interrupted_temp_files(tmp_path: Path) -> None:
    db_path = tmp_path / "data" / "planning.db"

    with pytest.raises(ValueError, match="too large"):
        asyncio.run(store_chat_image(db_path, "t_image123", _chunks(PNG), "image.png", max_bytes=8))

    async def interrupted() -> AsyncIterator[bytes]:
        yield PNG[:8]
        raise ConnectionError("client disconnected")

    with pytest.raises(ConnectionError, match="disconnected"):
        asyncio.run(store_chat_image(db_path, "t_image123", interrupted(), "image.png"))

    files_root = db_path.parent / "files"
    assert not list((files_root / "chats").glob("**/*"))
    assert not list(files_root.glob(".chat-upload-*"))


def test_chat_image_publication_rejects_structurally_invalid_png(tmp_path: Path) -> None:
    db_path = tmp_path / "data" / "planning.db"

    with pytest.raises(ValueError, match="unsupported or invalid"):
        asyncio.run(store_chat_image(db_path, "t_image123", _chunks(MALFORMED_PNG), "image.png"))

    assert not list((db_path.parent / "files" / "chats").glob("**/*"))


@pytest.mark.parametrize(
    "payload",
    [
        b"\xff\xd8xx\xff\xc0xx\xff\xdaxx\xff\xd9",
        b"GIF89a\x01\x00\x01\x00xxxx;",
        b"RIFF"
        + (22).to_bytes(4, "little")
        + b"WEBPVP8X"
        + (10).to_bytes(4, "little")
        + b"0123456789",
    ],
)
def test_chat_image_publication_rejects_structurally_invalid_non_png_images(
    tmp_path: Path, payload: bytes
) -> None:
    db_path = tmp_path / "data" / "planning.db"

    with pytest.raises(ValueError, match="unsupported or invalid"):
        asyncio.run(store_chat_image(db_path, "t_image123", _chunks(payload), "image.bin"))

    assert not list((db_path.parent / "files" / "chats").glob("**/*"))


def test_chat_image_publication_does_not_write_through_entity_symlink(tmp_path: Path) -> None:
    db_path = tmp_path / "data" / "planning.db"
    chats_root = db_path.parent / "files" / "chats"
    chats_root.mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    (chats_root / "t_image123").symlink_to(outside, target_is_directory=True)

    with pytest.raises(ValueError, match="managed chat image directory"):
        asyncio.run(store_chat_image(db_path, "t_image123", _chunks(PNG), "image.png"))

    assert list(outside.iterdir()) == []
    assert not list((db_path.parent / "files").glob(".chat-upload-*"))


def test_chat_image_publication_does_not_write_through_chat_root_symlink(tmp_path: Path) -> None:
    db_path = tmp_path / "data" / "planning.db"
    files_root = db_path.parent / "files"
    files_root.mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    (files_root / "chats").symlink_to(outside, target_is_directory=True)

    with pytest.raises(ValueError, match="managed chat image directory"):
        asyncio.run(store_chat_image(db_path, "t_image123", _chunks(PNG), "image.png"))

    assert list(outside.iterdir()) == []
    assert not list(files_root.glob(".chat-upload-*"))


@pytest.mark.parametrize(
    ("entity_id", "relative_path"),
    [
        ("", "image.png"),
        ("../other", "image.png"),
        ("t_image123", ""),
        ("t_image123", "/image.png"),
        ("t_image123", "nested/../image.png"),
        ("t_image123", "%2e%2e/image.png"),
        ("t_image123", "nested%2fimage.png"),
        ("t_image123", "%252e%252e/image.png"),
    ],
)
def test_resolve_chat_file_rejects_unsafe_paths(
    tmp_path: Path, entity_id: str, relative_path: str
) -> None:
    with pytest.raises(ValueError):
        resolve_chat_file(tmp_path / "planning.db", entity_id, relative_path)


def test_resolve_chat_file_rejects_missing_directory_and_symlink_escape(tmp_path: Path) -> None:
    db_path = tmp_path / "data" / "planning.db"
    entity_root = db_path.parent / "files" / "chats" / "t_image123"
    entity_root.mkdir(parents=True)
    (entity_root / "folder").mkdir()
    outside = tmp_path / "outside.png"
    outside.write_bytes(PNG)
    (entity_root / "escape.png").symlink_to(outside)

    for path in ("missing.png", "folder", "escape.png"):
        with pytest.raises(ValueError):
            resolve_chat_file(db_path, "t_image123", path)


def test_resolve_chat_file_rejects_entity_directory_symlink_within_chat_root(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "data" / "planning.db"
    chats_root = db_path.parent / "files" / "chats"
    owner_root = chats_root / "t_owner"
    owner_root.mkdir(parents=True)
    (owner_root / "image.png").write_bytes(PNG)
    (chats_root / "t_route").symlink_to(owner_root, target_is_directory=True)

    with pytest.raises(ValueError, match="symlink"):
        resolve_chat_file(db_path, "t_route", "image.png")


def test_chat_file_route_serves_managed_image_inline_with_nosniff(tmp_path: Path) -> None:
    app, db_path = _make_app(tmp_path)
    target = db_path.parent / "files" / "chats" / "t_image123" / "pic.png"
    target.parent.mkdir(parents=True)
    target.write_bytes(PNG)

    with TestClient(app) as client:
        response = client.get("/files/chats/t_image123/pic.png")
        unsafe = client.get("/files/chats/t_image123/%2e%2e/pic.png")

    assert response.status_code == 200
    assert response.content == PNG
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["content-disposition"] == 'inline; filename="pic.png"'
    assert response.headers["content-type"].startswith("image/png")
    assert unsafe.status_code == 404


def test_chat_turn_accepts_same_entity_image_and_keeps_transcript_reference(tmp_path: Path) -> None:
    app, db_path = _make_app(tmp_path)
    ticket_id = _ticket(db_path)
    calls: list[tuple[str, tuple[Path, ...]]] = []

    class RecordingGateway:
        def status(self) -> GatewayStatus:
            return GatewayStatus(available=True)

        def read_employee_session_history(
            self, employee_session_id: str, ticket_id: str
        ) -> EmployeeSessionHistory:
            return EmployeeSessionHistory(messages=(), employee_session_id=employee_session_id)

        def run_human_turn(
            self,
            session_key: str | None,
            entity_id: str,
            text: str,
            mode: str,
            bind_session_key: HumanSessionKeyBinder,
            image_paths: tuple[Path, ...] = (),
        ) -> Iterator[HumanChatObservation]:
            calls.append((text, image_paths))
            bind_session_key("image-session")
            yield HumanChatCompletion("I can see it", "assistant")

    app.state.adapters = Adapters(gateway=RecordingGateway())
    with TestClient(app) as client:
        uploaded = [
            client.post(f"/api/chat/{ticket_id}/images", content=PNG).json(),
            client.post(f"/api/chat/{ticket_id}/images", content=GIF).json(),
        ]
        started = client.post(
            f"/api/chat/{ticket_id}/turns",
            json={
                "text": "What is shown?",
                "mode": "message",
                "image_references": [image["reference"] for image in uploaded],
            },
        )
        assert started.status_code == 200, started.text
        for _ in range(40):
            state = client.get(f"/api/chat/{ticket_id}/state").json()
            if state["active_turn"] is None and len(state["messages"]) == 2:
                break
            import time

            time.sleep(0.025)
        else:
            raise AssertionError(state)

    expected_paths = tuple(
        (db_path.parent / image["reference"].lstrip("/")).resolve(strict=True) for image in uploaded
    )
    assert calls == [("What is shown?", expected_paths)]
    assert state["messages"][0]["text"] == (
        "What is shown?\n\n"
        f"![Attached image]({uploaded[0]['reference']})\n"
        f"![Attached image]({uploaded[1]['reference']})"
    )
    assert state["messages"][1]["text"] == "I can see it"


def test_chat_turn_accepts_image_only_with_nonempty_model_cue(tmp_path: Path) -> None:
    app, db_path = _make_app(tmp_path)
    ticket_id = _ticket(db_path)
    calls: list[tuple[str, tuple[Path, ...]]] = []

    class RecordingGateway:
        def status(self) -> GatewayStatus:
            return GatewayStatus(available=True)

        def read_employee_session_history(
            self, employee_session_id: str, ticket_id: str
        ) -> EmployeeSessionHistory:
            return EmployeeSessionHistory(messages=(), employee_session_id=employee_session_id)

        def run_human_turn(
            self,
            session_key: str | None,
            entity_id: str,
            text: str,
            mode: str,
            bind_session_key: HumanSessionKeyBinder,
            image_paths: tuple[Path, ...] = (),
        ) -> Iterator[HumanChatObservation]:
            calls.append((text, image_paths))
            bind_session_key("image-session")
            yield HumanChatCompletion("image received", "assistant")

    app.state.adapters = Adapters(gateway=RecordingGateway())
    with TestClient(app) as client:
        uploaded = client.post(f"/api/chat/{ticket_id}/images", content=PNG).json()
        started = client.post(
            f"/api/chat/{ticket_id}/turns",
            json={"text": "", "mode": "message", "image_references": [uploaded["reference"]]},
        )
        assert started.status_code == 200, started.text
        for _ in range(40):
            state = client.get(f"/api/chat/{ticket_id}/state").json()
            if state["active_turn"] is None and len(state["messages"]) == 2:
                break
            import time

            time.sleep(0.025)
        else:
            raise AssertionError(state)

    expected_path = (db_path.parent / uploaded["reference"].lstrip("/")).resolve(strict=True)
    assert len(calls) == 1
    assert calls[0][0].strip()
    assert uploaded["reference"] not in calls[0][0]
    assert calls[0][1] == (expected_path,)
    assert state["messages"][0]["text"] == f"![Attached image]({uploaded['reference']})"


def test_chat_turn_rejects_non_owned_or_unsafe_image_before_creating_turn(tmp_path: Path) -> None:
    app, db_path = _make_app(tmp_path)
    owner_id = _ticket(db_path)
    route_id = _ticket(db_path)
    ticket_file = db_path.parent / "files" / "tickets" / route_id / "pic.png"
    ticket_file.parent.mkdir(parents=True)
    ticket_file.write_bytes(PNG)

    with TestClient(app) as client:
        owned = client.post(f"/api/chat/{owner_id}/images", content=PNG).json()["reference"]
        references = [
            owned,
            f"/files/tickets/{route_id}/pic.png",
            "https://example.com/image.png",
            f"/files/chats/{route_id}/missing.png",
            f"/files/chats/{route_id}/%252e%252e/image.png",
        ]
        for reference in references:
            response = client.post(
                f"/api/chat/{route_id}/turns",
                json={"text": "look", "mode": "message", "image_references": [reference]},
            )
            assert response.status_code == 400, (reference, response.text)
            assert response.json()["error"]["code"] == "validation"

        response = client.post(
            f"/api/chat/{owner_id}/turns",
            json={
                "text": "look",
                "mode": "message",
                "image_references": [
                    owned,
                    f"/files/chats/{owner_id}/missing.png",
                ],
            },
        )
        assert response.status_code == 400, response.text
        assert response.json()["error"]["code"] == "validation"

    conn = connect(str(db_path))
    try:
        assert (
            conn.execute(
                "SELECT COUNT(*) FROM chat_turns WHERE entity_id IN (?, ?)", (route_id, owner_id)
            ).fetchone()[0]
            == 0
        )
        assert (
            conn.execute(
                "SELECT COUNT(*) FROM chat_messages WHERE entity_id IN (?, ?)", (route_id, owner_id)
            ).fetchone()[0]
            == 0
        )
    finally:
        conn.close()
