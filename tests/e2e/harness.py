"""The values and the handle the e2e fixtures and the e2e tests both build on.

Split out of ``conftest.py`` so that a test needing one of these imports an ordinary
module. pytest loads ``conftest.py`` under the bare name ``conftest``, so a test saying
``from conftest import ...`` depends on pytest having put this directory on ``sys.path``
— which reads as a missing module to every other tool, and which would load a second copy
of the module under a second name if it were imported the normal way. A plain module has
one name and one copy, from anywhere.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
from playwright.sync_api import Browser, Page

from planner.conversation.events import ConversationEventPayload
from planner.conversation.storage import ConversationStore

REPO_ROOT = Path(__file__).resolve().parents[2]
PLAN_BIN = Path(sys.executable).parent / "panels"
FAKE_NOW = "2026-07-04T12:00:00"
WAIT_MS = 10_000  # every Playwright wait
BOOT_BUDGET_S = 15.0  # server readiness budget

# A decoded JSON object, as the API and the CLI's --json hand one back. The values stay
# ``Any`` on purpose: this is somebody else's JSON, and pretending to know its shape here
# would be a fiction the tests would then have to fight.
type JsonObject = dict[str, Any]


def open_status_group(page: Page, key: str) -> None:
    """Open the Workspace status group named by ``key``.

    Which groups arrive open is a product choice, and no test here owns it. A test that
    must read a row inside a group opens that group itself, and says so by calling this.
    """
    group = f'[data-bucket-key="{key}"]'
    page.wait_for_selector(group, timeout=WAIT_MS)
    if page.get_attribute(group, "open") is None:
        page.click(f"{group} > summary")
    page.wait_for_function(
        "selector => document.querySelector(selector)?.open === true",
        arg=group,
        timeout=WAIT_MS,
    )


@dataclass(frozen=True)
class ServerHandle:
    base: str
    proc: subprocess.Popen[bytes]
    db_path: Path
    log_path: Path
    port: int
    control_socket_path: Path


class ApiHelper:
    """The three ways a test reaches the server's JSON API.

    A named class rather than a ``SimpleNamespace``: attribute access on a namespace is
    ``Any``, so every call a test made through this fixture was invisible to the type
    checker. The call syntax in the tests is unchanged.
    """

    def get(self, server: ServerHandle, path: str) -> JsonObject:
        resp = httpx.get(server.base + path, timeout=10.0)
        assert resp.status_code < 300, f"GET {path} -> {resp.status_code}: {resp.text}"
        answer: JsonObject = resp.json()
        return answer

    def direct_post(self, server: ServerHandle, path: str, json_body: JsonObject) -> JsonObject:
        # No X-Plan-* headers: authctx classifies this request as unattributed,
        # which the direct-only /accept and the direct-only PATCH fields permit.
        resp = httpx.post(server.base + path, json=json_body, timeout=10.0)
        assert resp.status_code < 300, f"POST {path} -> {resp.status_code}: {resp.text}"
        answer: JsonObject = resp.json()
        return answer

    def direct_patch(self, server: ServerHandle, path: str, json_body: JsonObject) -> JsonObject:
        # A headerless PATCH is unattributed. The day brief writer is direct-only,
        # so this is how a test seeds or edits a brief.
        resp = httpx.patch(server.base + path, json=json_body, timeout=10.0)
        assert resp.status_code < 300, f"PATCH {path} -> {resp.status_code}: {resp.text}"
        answer: JsonObject = resp.json()
        return answer

    def direct_put(self, server: ServerHandle, path: str, json_body: JsonObject) -> JsonObject:
        resp = httpx.put(server.base + path, json=json_body, timeout=10.0)
        assert resp.status_code < 300, f"PUT {path} -> {resp.status_code}: {resp.text}"
        answer: JsonObject = resp.json()
        return answer


def capture_success(page: Page, path: str | Path) -> None:
    """Capture a review image only when explicitly requested."""
    if output := os.environ.get("PANELS_UI_PROOF_DIR"):
        target = Path(output) / Path(path).name
        target.parent.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(target), full_page=True, animations="disabled")


@contextmanager
def browser_failure_evidence(browser: Browser, label: str) -> Iterator[None]:
    """Retain evidence before a standalone frontend browser exits on failure."""
    try:
        yield
    except Exception:
        folder = (
            Path(os.environ.get("PANELS_E2E_FAILURE_DIR", "data/verification/browser-failure"))
            / label
        )
        folder.mkdir(parents=True, exist_ok=True)
        for context_index, context in enumerate(browser.contexts):
            for page_index, page in enumerate(context.pages):
                stem = folder / f"context-{context_index}-page-{page_index}"
                try:
                    page.screenshot(
                        path=str(stem.with_suffix(".png")), full_page=True, timeout=5000
                    )
                    stem.with_suffix(".html").write_text(page.content())
                except Exception as error:
                    stem.with_suffix(".capture-error.txt").write_text(str(error))
            try:
                context.tracing.stop(path=str(folder / f"context-{context_index}-trace.zip"))
            except Exception:
                pass
        raise


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
