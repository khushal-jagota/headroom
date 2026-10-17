"""Passive failure evidence for the approval/message browser proof only."""

from __future__ import annotations

import json
import sqlite3
import time
from collections.abc import Callable
from functools import partial
from pathlib import Path
from typing import Any

from playwright.sync_api import BrowserContext, Page, Request, Response
from tests.e2e.harness import ServerHandle

# Observe browser eligibility and rendered evidence without changing attention or reads.
_ATTENTION_SCRIPT = """() => {
  const key = '__approvalMessageProofHistory';
  let history = [];
  try { history = JSON.parse(sessionStorage.getItem(key) || '[]'); } catch {}
  const record = reason => {
    const state = {
      time: Date.now(), reason, url: location.href,
      visibility: document.visibilityState, focused: document.hasFocus(),
      panes: [...document.querySelectorAll('[data-conversation-pane]')].map(p => ({
        state: p.getAttribute('data-conversation-state'),
        lens: p.getAttribute('data-conversation-lens'),
        transcript: p.querySelector('[data-conversation-transcript]')?.textContent,
        rows: [...p.querySelectorAll('[data-conversation-row]')].map(r => ({
          kind: r.getAttribute('data-conversation-row'), text: r.textContent,
          visible: r.getClientRects().length > 0
        }))
      })),
      marks: [...document.querySelectorAll('[data-workspace-mark]')].map(r => ({
        ticket: r.getAttribute('data-ticket-id'), mark: r.getAttribute('data-workspace-mark')
      }))
    };
    const previous = history.at(-1);
    if (reason === 'dom' && previous && JSON.stringify({...state, time: 0, reason: ''})
        === JSON.stringify({...previous, time: 0, reason: ''})) return;
    history.push(state);
    history = history.slice(-200);
    window.__approvalMessageProofHistory = history;
    try { sessionStorage.setItem(key, JSON.stringify(history)); } catch {}
  };
  record('init');
  document.addEventListener('visibilitychange', () => record('visibilitychange'));
  window.addEventListener('focus', () => record('focus'));
  window.addEventListener('blur', () => record('blur'));
  new MutationObserver(() => record('dom')).observe(document, {
    subtree: true, childList: true, characterData: true, attributes: true,
    attributeFilter: ['data-conversation-state', 'data-conversation-lens', 'data-workspace-mark']
  });
}"""


class ApprovalMessageDiagnostics:
    """Keep requests and explicit hold/release observations until an assertion fails."""

    def __init__(self, context: BrowserContext, server: ServerHandle, evidence: Path) -> None:
        self.server = server
        self.evidence = evidence
        self.events: list[dict[str, Any]] = []
        self.completed_responses: list[Response] = []
        self._attempt("install attention observer", lambda: context.add_init_script(
            f"({_ATTENTION_SCRIPT})()"
        ))
        context.on("request", self._request)
        context.on("response", self._response)
        context.on("requestfailed", self._failed)
        context.on("requestfinished", self._finished)

    def record(self, phase: str, **detail: Any) -> None:
        self.events.append({"time": time.time() * 1000, "phase": phase, **detail})

    def _attempt(self, label: str, collect: Callable[[], Any]) -> Any:
        try:
            return collect()
        except Exception as error:
            self.record("collector-error", collector=label, error=repr(error))
            return None

    @staticmethod
    def _relevant(url: str) -> bool:
        return "/api/conversation/conversations/dot-" in url or (
            "/api/items/" in url and "/workspace" in url
        )

    def _request(self, request: Request) -> None:
        if self._relevant(request.url):
            self.record("request", url=request.url, method=request.method, body=request.post_data)

    def _response(self, response: Response) -> None:
        if self._relevant(response.url):
            self.record("response", url=response.url, status=response.status)

    def _finished(self, request: Request) -> None:
        if self._relevant(request.url):
            # Retain completed responses. Reading bodies happens only after failure.
            response = request.response()
            if response is not None:
                self.completed_responses.append(response)

    def _failed(self, request: Request) -> None:
        if self._relevant(request.url):
            self.record("requestfailed", url=request.url, error=request.failure)

    def _database(self) -> dict[str, Any]:
        with sqlite3.connect(self.server.db_path) as conn:
            conn.row_factory = sqlite3.Row
            return {
                "conversations": [dict(row) for row in conn.execute(
                    "SELECT conversation_id, latest_sequence, owner_read_through_sequence "
                    "FROM conversations WHERE conversation_id LIKE 'dot-%'"
                )],
                "events": [dict(row) for row in conn.execute(
                    "SELECT conversation_id, sequence, kind, payload FROM conversation_events "
                    "WHERE conversation_id LIKE 'dot-%' ORDER BY conversation_id, sequence"
                )],
            }

    def capture_failure(self, context: BrowserContext, error: BaseException) -> None:
        """Collect before held routes are fulfilled. Never replace the original failure."""
        self.record("assertion-failure", error=repr(error))
        for response in self.completed_responses:
            self._attempt("completed response body", partial(self._record_body, response))
        database = self._attempt("SQLite snapshot", self._database)
        pages = []
        for index, page in enumerate(context.pages):
            pages.append(self._attempt("browser state", partial(page.evaluate,
                "() => ({url: location.href, visibility: document.visibilityState, "
                "focused: document.hasFocus(), history: window.__approvalMessageProofHistory, "
                "html: document.documentElement.outerHTML})"
            )))
            self._attempt("screenshot", partial(self._screenshot, page, index))
        payload = {"events": self.events, "database": database, "pages": pages}
        self._attempt("write failure evidence", lambda: (
            self.evidence / "failure.json"
        ).write_text(json.dumps(payload, indent=2), encoding="utf-8"))
        print(f"Approval/message failure evidence: {self.evidence / 'failure.json'}")
        print(json.dumps({"events": self.events, "database": database,
                          "browser_history": [p.get("history") for p in pages if p]}, indent=2))

    def _record_body(self, response: Response) -> None:
        self.record("completed-response-body", url=response.url, body=response.text())

    def _screenshot(self, page: Page, index: int) -> None:
        page.screenshot(path=str(self.evidence / f"failure-{index}.png"), full_page=True)
