"""A double of `GET /api/updates` and its owner action routes, for the browser case.

It answers exactly the shape in the self-update contract (section 10). The browser
proof is about the card, its dialogs and the connection: the server's own state
rules are proved by its unit tests, so this double only follows the routes the
page calls and lets the case move the server answer the way an update does.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

JsonObject = dict[str, Any]


class OwnerUpdatesFixture:
    def __init__(self) -> None:
        self.current_sha = "a" * 40
        self.ready_sha: str | None = "b" * 40
        self.activity = "unknown"
        self.state = "available"
        self.attempt: JsonObject | None = None
        self.detail: str | None = None
        self.deadline_at: str | None = None
        self.mac_details: JsonObject | None = None
        self.can_retry = True
        self._attempts = 0

    # --- what the page reads -------------------------------------------------

    def status(self) -> JsonObject:
        allowed = {
            "up_to_date": [],
            "available": ["update"] if self.ready_sha else [],
            "waiting": ["cancel", "update_now"],
            "updating": [],
            "failed": ["retry"] if self.can_retry else [],
            "mac_failed": ["retry_mac"],
            "mac_unreachable": ["retry_mac"],
        }[self.state]
        return {
            "state": self.state,
            "allowed_actions": allowed,
            "activity": self.activity,
            "available": {"app_sha": self.ready_sha} if self.ready_sha else None,
            "attempt": self.attempt,
            "app_sha": self.current_sha,
            "detail": self.detail,
            "deadline_at": self.deadline_at,
            "mac_details": self.mac_details,
        }

    def respond(self, method: str, path: str, body: JsonObject | None) -> tuple[int, JsonObject]:
        if method == "GET" and path == "/api/meta":
            return 200, {"app_sha": self.current_sha, "test_mode": True}
        if method == "GET" and path == "/api/updates":
            return 200, self.status()
        if method == "POST" and path == "/api/updates/install":
            assert body is not None and set(body) == {"app_sha", "mode", "confirm_activity"}, body
            if self.state != "available" or body["app_sha"] != self.ready_sha:
                return 409, {"detail": "This update is no longer available."}
            waits = body["mode"] == "idle" and self.activity != "idle"
            self._start(body["mode"], body["confirm_activity"], waiting=waits)
            return 200, self.status()
        prefix = "/api/updates/attempts/"
        if method == "POST" and path.startswith(prefix) and self.attempt is not None:
            attempt_id, _, action = path[len(prefix):].partition("/")
            if attempt_id != self.attempt["attempt_id"]:
                return 409, {"detail": "The update changed."}
            if action == "cancel" and self.state == "waiting":
                self.attempt, self.state = None, "available"
                return 200, self.status()
            if action == "now" and self.state == "waiting":
                assert body == {"confirm_activity": True}, body
                self.attempt = {**self.attempt, "mode": "now", "state": "installing"}
                self._updating()
                return 200, self.status()
            if action == "retry" and self.state == "failed" and self.can_retry:
                self._start("now", True, waiting=False)
                return 200, self.status()
            if action == "retry-mac" and self.state in {"mac_failed", "mac_unreachable"}:
                self.detail = None
                self.state = "updating"
                return 200, self.status()
        return 409, {"detail": f"Not allowed now: {method} {path}"}

    # --- how the case moves the server ---------------------------------------

    def fail_install(self, detail: str) -> None:
        assert self.attempt is not None
        self.attempt = {**self.attempt, "state": "failed", "detail": detail}
        self.state, self.detail, self.deadline_at = "failed", detail, None

    def finish_install(self) -> None:
        """The new server answers. The Mac part keeps the update running."""
        assert self.attempt is not None
        self.current_sha = self.attempt["to_sha"]
        self.attempt = {**self.attempt, "state": "done"}
        self.deadline_at = None

    def mac_outcome(self, state: str, detail: str | None = None) -> None:
        self.state, self.detail = state, detail
        if state == "up_to_date":
            self.ready_sha = None

    def _start(self, mode: str, confirm_activity: bool, *, waiting: bool) -> None:
        self._attempts += 1
        self.attempt = {
            "attempt_id": f"attempt-{self._attempts}",
            "from_sha": self.current_sha,
            "to_sha": self.ready_sha,
            "mode": mode,
            "state": "waiting" if waiting else "installing",
            "detail": None,
            "deadline_at": None,
            "confirm_activity": confirm_activity,
        }
        self.detail = None
        if waiting:
            self.state = "waiting"
        else:
            self._updating()

    def _updating(self) -> None:
        self.state = "updating"
        self.deadline_at = (datetime.now(UTC) + timedelta(minutes=10)).isoformat()
        assert self.attempt is not None
        self.attempt = {**self.attempt, "deadline_at": self.deadline_at}
