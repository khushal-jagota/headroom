"""Fake alarm channels for the stall watchdog: a Web Push adapter and a Chief call that
record what they were given. No notification or Chief message is sent."""

from __future__ import annotations

from collections.abc import Callable

from planner.notifications.contracts import (
    NotificationIntent,
    PushSubscription,
    WebPushIdentity,
    WebPushResult,
)


class FakeWebPush:
    def __init__(self) -> None:
        self.sent: list[NotificationIntent] = []

    def send(
        self,
        subscription: PushSubscription,
        intent: NotificationIntent,
        identity: WebPushIdentity,
        *,
        subject: str,
    ) -> WebPushResult:
        self.sent.append(intent)
        return WebPushResult(delivered=True)


class FakeChief:
    def __init__(self, answer: Callable[[str], bool]) -> None:
        self.messages: list[str] = []
        self._answer = answer

    def __call__(self, text: str) -> bool:
        self.messages.append(text)
        return self._answer(text)
