"""Passive failure evidence for the approval/message browser proof only."""

from __future__ import annotations

import json
import sqlite3
import time
import uuid
from collections.abc import Callable
from functools import partial
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

from playwright.sync_api import BrowserContext, Page, Request, Response
from tests.e2e.harness import ServerHandle

# Observe browser eligibility and rendered evidence without changing attention or reads.
_ATTENTION_SCRIPT = """() => {
  const key = '__approvalMessageProofHistory';
  let history = [];
  try { history = JSON.parse(sessionStorage.getItem(key) || '[]'); } catch {}
  const inViewport = e => {
    const r = e.getBoundingClientRect();
    return r.width > 0 && r.height > 0 && r.bottom > 0 && r.right > 0
      && r.top < innerHeight && r.left < innerWidth;
  };
  const record = reason => {
    const state = {
      time: Date.now(), reason,
      visibility: document.visibilityState, focused: document.hasFocus(),
      panes: [...document.querySelectorAll('[data-conversation-pane]')].map(p => ({
        state: p.getAttribute('data-conversation-state'),
        lens: p.getAttribute('data-conversation-lens'),
        rows: [...p.querySelectorAll('[data-conversation-row]')].map(r => ({
          kind: r.getAttribute('data-conversation-row'),
          visible: inViewport(r)
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
        self.request_ids: dict[Request, int] = {}
        self.pending: dict[str, dict[str, Any]] = {}
        self.page_counter = 0
        self.failed = False
        self.scope = uuid.uuid4().hex
        self.scope_path = server.db_path.parent / "owner-read-scope"
        self._attempt("activate child trace scope", lambda: self.scope_path.write_text(
            self.scope, encoding="utf-8"
        ))
        self._attempt("install attention observer", lambda: context.add_init_script(
            f"({_ATTENTION_SCRIPT})()"
        ))
        context.on("request", self._request)
        context.on("response", self._response)
        context.on("requestfailed", self._failed)
        context.on("requestfinished", self._finished)
        context.on("page", lambda page: self._attempt(
            "attach CDP", partial(self._attach_page, context, page)
        ))

    def record(self, phase: str, **detail: Any) -> None:
        # Existing hold/release observations pass their intercepted JSON here. Retain
        # only marker fields, never transcript text, headers, or complete payloads.
        if "body" in detail:
            try:
                detail["projection"] = self._projection(json.loads(detail.pop("body")))
            except Exception:
                detail.pop("body", None)
                detail["projection_unavailable"] = True
        if "url" in detail:
            detail.update(self._url(detail.pop("url")))
        self.events.append({"wall_ns": time.time_ns(), "monotonic_ns": time.monotonic_ns(),
                            "phase": phase, **detail})

    def _attempt(self, label: str, collect: Callable[[], Any]) -> Any:
        try:
            return collect()
        except Exception as error:
            self.record("collector-error", collector=label, exception_type=type(error).__name__)
            return None

    @staticmethod
    def _url(url: str) -> dict[str, Any]:
        parsed = urlsplit(url)
        origin = (f"{parsed.scheme}://{parsed.hostname}:{parsed.port}"
                  if parsed.hostname in ("127.0.0.1", "localhost", "::1") else "other-origin")
        result: dict[str, Any] = {"origin": origin, "path": parsed.path
                                  if ApprovalMessageDiagnostics._relevant(url) else "other"}
        after = parse_qs(parsed.query).get("after", [])
        if after and after[0].isdigit():
            result["after"] = int(after[0])
        return result

    @staticmethod
    def _relevant(url: str) -> bool:
        path = urlsplit(url).path
        return path in ("/api/board", "/api/changes") or (
            path.startswith("/api/conversation/conversations/dot-")
        ) or (path.startswith("/api/items/") and path.endswith("/workspace"))

    @staticmethod
    def _projection(value: Any) -> list[dict[str, Any]]:
        allowed = {"id", "conversation_id", "latest_sequence", "owner_read_through_sequence",
                   "through_sequence", "sequence", "kind", "awaiting_reply", "awaiting_answer",
                   "awaiting_approval", "awaiting_agent_approval", "assigned", "agent_state",
                   "stage", "ticket_status", "has_pending_proposal"}
        rows = []
        if isinstance(value, dict):
            row = {key: entry for key, entry in value.items() if key in allowed
                   and (entry is None or type(entry) in (str, int, bool))}
            if row:
                rows.append(row)
            for key, entry in value.items():
                # Event payloads contain message text and sender details, not projection facts.
                if key != "payload" and isinstance(entry, (dict, list)):
                    rows.extend(ApprovalMessageDiagnostics._projection(entry))
        elif isinstance(value, list):
            for entry in value:
                rows.extend(ApprovalMessageDiagnostics._projection(entry))
        return rows

    def _request_id(self, request: Request) -> int:
        if request not in self.request_ids:
            self.request_ids[request] = len(self.request_ids) + 1
        return self.request_ids[request]

    def _request(self, request: Request) -> None:
        if self._relevant(request.url):
            self.record("request", request_id=self._request_id(request),
                        url=request.url, method=request.method,
                        body=request.post_data or "{}")

    def _response(self, response: Response) -> None:
        if self._relevant(response.url):
            self.record("response", request_id=self._request_id(response.request),
                        url=response.url, status=response.status)

    def _finished(self, request: Request) -> None:
        path = urlsplit(request.url).path
        if self._relevant(request.url) and path != "/api/changes" and not path.endswith("/tail"):
            # Collect only completed bodies while their document still exists. Waiting
            # until failure loses response bodies evicted by intermediate navigation.
            self._attempt("completed response projection", partial(
                self._record_projection, request
            ))

    def _record_projection(self, request: Request) -> None:
        response = request.response()
        if response is not None:
            self.record("completed-response", request_id=self._request_id(request),
                        url=request.url, projection=self._projection(response.json()))

    def _failed(self, request: Request) -> None:
        if self._relevant(request.url):
            self.record("requestfailed", request_id=self._request_id(request),
                        url=request.url, error=request.failure)

    def _attach_page(self, context: BrowserContext, page: Page) -> None:
        session = context.new_cdp_session(page)
        self.page_counter += 1
        page_id = self.page_counter

        def observe(name: str, event: dict[str, Any]) -> None:
            identity = f"{page_id}:{event.get('requestId')}"
            detail = {"cdp_request_id": identity, "cdp_timestamp": event.get("timestamp")}
            if name == "requestWillBeSent":
                request = event["request"]
                detail.update(self._url(request["url"]))
                detail.update(method=request["method"], resource_type=event.get("type"),
                              cdp_wall_time=event.get("wallTime"))
                self.pending[identity] = dict(detail)
            elif name == "requestWillBeSentExtraInfo":
                detail["connect_timing"] = event.get("connectTiming")
            elif name == "responseReceived":
                response = event["response"]
                detail.update(self._url(response["url"]))
                for key in ("status", "connectionId", "connectionReused", "protocol",
                            "fromServiceWorker", "fromDiskCache", "timing"):
                    detail[key] = response.get(key)
            elif name in ("loadingFinished", "loadingFailed"):
                self.pending.pop(identity, None)
                detail.update(error=event.get("errorText"), canceled=event.get("canceled"))
            elif name == "eventSourceMessageReceived":
                detail["event_name"] = event.get("eventName")  # Never SSE data.
            self.record(f"cdp-{name}", **detail)

        for name in ("requestWillBeSent", "requestWillBeSentExtraInfo", "responseReceived",
                     "loadingFinished", "loadingFailed", "eventSourceMessageReceived"):
            session.on(f"Network.{name}", lambda event, name=name: self._attempt(
                "CDP event", partial(observe, name, event)
            ))
        session.send("Network.enable")  # Observation only: no Fetch interception changes.
        self.record("cdp-installed", page_id=page_id)

    def _database(self) -> dict[str, Any]:
        with sqlite3.connect(self.server.db_path) as conn:
            conn.row_factory = sqlite3.Row
            return {
                "conversations": [dict(row) for row in conn.execute(
                    "SELECT conversation_id, latest_sequence, owner_read_through_sequence "
                    "FROM conversations WHERE conversation_id LIKE 'dot-%'"
                )],
                "events": [dict(row) for row in conn.execute(
                    "SELECT conversation_id, sequence, kind FROM conversation_events "
                    "WHERE conversation_id LIKE 'dot-%' ORDER BY conversation_id, sequence"
                )],
            }

    def capture_failure(self, context: BrowserContext, error: BaseException) -> None:
        """Collect before held routes are fulfilled. Never replace the original failure."""
        self.record("assertion-failure", exception_type=type(error).__name__,
                    pending_requests=list(self.pending.values()))
        self.failed = True
        self._attempt("persist failure boundary", partial(
            self._persist, context, "failure-boundary"
        ))
        database = self._attempt("SQLite snapshot", self._database)
        pages = []
        for index, page in enumerate(context.pages):
            pages.append(self._attempt("browser state", partial(page.evaluate,
                "() => ({visibility: document.visibilityState, focused: document.hasFocus(), "
                "history: window.__approvalMessageProofHistory})"
            )))
            self._attempt("screenshot", partial(self._screenshot, page, index))
        payload = {"events": self.events, "database": database, "pages": pages}
        self._attempt("write failure evidence", lambda: (
            self.evidence / "failure.json"
        ).write_text(json.dumps(payload, indent=2), encoding="utf-8"))
        print(f"Approval/message failure evidence: {self.evidence / 'failure.json'}")
        print(json.dumps({"events": self.events, "database": database,
                          "browser_history": [p.get("history") for p in pages if p]}, indent=2))

    def _trace_rows(self, path: Path) -> list[dict[str, Any]]:
        rows = []
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                value = json.loads(line)
                if isinstance(value, dict):
                    rows.append(value)
            except json.JSONDecodeError:
                self.record("collector-error", collector="incomplete child trace row")
        return rows

    def _persist(self, context: BrowserContext, name: str) -> None:
        self.record("trace-cutoff", outcome="failed" if self.failed else "completed",
                    pending_requests=list(self.pending.values()))
        parent = self.server.db_path.parent
        installation = self._attempt("installation receipt", partial(
            self._trace_rows, parent / "owner-read-installation.jsonl"
        )) or []
        child = self._attempt("child observations", partial(
            self._trace_rows, parent / "owner-read-trace.jsonl"
        )) or []
        child = [row for row in child if row.get("scope") == self.scope]
        counts = {phase: sum(row.get("phase") == phase for row in child) for phase in (
            "ingress", "request-body", "storage-entry", "storage-return", "read-response-body",
        )}
        installed = {row["pid"] for row in installation if row.get("phase") == "installed"}
        observed = {row["pid"] for row in child}
        coverage = {
            "installed_pids": sorted(installed),
            "observed_pids": sorted(observed),
            "observed_child_installation_verified": bool(observed) and observed <= installed,
            "browser_cdp_installed": any(row["phase"] == "cdp-installed" for row in self.events),
            "child_phase_counts": counts,
            "read_commit_returns": sum(row.get("phase") == "sqlite-return"
                                       and row.get("operation") == "COMMIT"
                                       and row.get("total_changes", 0) > 0 for row in child),
            "missing_child_phases": [phase for phase, count in counts.items() if count == 0],
        }
        pages = [self._attempt("browser history", partial(page.evaluate,
                 "() => window.__approvalMessageProofHistory || []")) for page in context.pages]
        self.evidence.mkdir(parents=True, exist_ok=True)
        (self.evidence / f"{name}.json").write_text(json.dumps({
            "scope": self.scope, "coverage": coverage, "installation": installation,
            "child": child, "events": self.events, "browser_history": pages,
        }, indent=2), encoding="utf-8")

    def persist_and_close(self, context: BrowserContext) -> None:
        """Retain pass/fail observations before original route and fixture cleanup."""
        try:
            self._attempt("persist final trace", partial(self._persist, context, "trace"))
        finally:
            self._attempt("close child trace scope", self._close_scope)

    def _close_scope(self) -> None:
        if self.scope_path.read_text(encoding="utf-8") == self.scope:
            self.scope_path.unlink()

    def _screenshot(self, page: Page, index: int) -> None:
        page.screenshot(path=str(self.evidence / f"failure-{index}.png"), full_page=True)
