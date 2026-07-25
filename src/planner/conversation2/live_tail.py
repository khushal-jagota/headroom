"""What a conversation is showing right now, to whoever is watching it.

The record is the notebook: rows, numbered, kept forever. This is the other half — the
half that is only ever *shown*. A watcher subscribes to a conversation and is handed two
kinds of thing as they happen: the rows that have just been committed, and the half-
finished text a backend streams while it works.

Nothing here is durable and nothing here is a source of truth. A restart empties it, and
that is correct: a browser that comes back asks the record for everything after the last
row it saw, which is the same path it uses to open the conversation in the first place.
Losing the tail loses nothing but the few characters that had not finished arriving.

Publishing never waits. A backend adapter reporting news, and the core writing a row, both
hand the item over and carry on; a watcher that is not reading fast enough holds its own
items and nobody else's.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Iterator
from typing import Final

from planner.conversation2.events import ConversationLiveTailFrame
from planner.conversation2.storage import StoredConversationEvent

# The two things a watcher is handed. A stored event is a row that has been committed and
# can be asked for again; a frame is shown once and forgotten.
type ConversationTailItem = StoredConversationEvent | ConversationLiveTailFrame


# How far behind a watcher may fall before its watch is closed. Generous enough that a
# browser reading normally never reaches it, small enough that a reader which has stopped
# reading cannot grow this process's memory without limit.
MAXIMUM_HELD_TAIL_ITEMS: Final = 2048


class _TailClosed:
    """The last thing a subscription is handed, when its watch ends."""


_TAIL_CLOSED = _TailClosed()


class ConversationTailSubscription:
    """One watcher's view of one conversation, from the moment it subscribed.

    Subscribing registers the queue before anything is read from storage, which is what
    lets a reader replay the record and then carry straight on live without a hole in
    between: anything committed during the replay is already waiting here.
    """

    def __init__(self, live_tail: ConversationLiveTail, conversation_id: str) -> None:
        self._live_tail = live_tail
        self._conversation_id = conversation_id
        # One more than a watcher may hold, and that one is spoken for: it is the slot the
        # closing sentinel goes in, so a watch can always be told it is over — including
        # the watch that was closed for filling this queue up in the first place.
        self._items: asyncio.Queue[ConversationTailItem | _TailClosed] = asyncio.Queue(
            maxsize=MAXIMUM_HELD_TAIL_ITEMS + 1
        )
        self._closed = False

    @property
    def conversation_id(self) -> str:
        return self._conversation_id

    def deliver(self, item: ConversationTailItem) -> None:
        """Take an item for this watcher. Never waits, and never grows without limit.

        A watcher this far behind is not reading, and holding more for it would cost this
        process memory for a browser that is not there. So the watch is closed instead —
        which is not a loss: a closed tail is the reconnect path, and reconnecting is
        asking for everything after the last row seen. Dropping items quietly would be the
        harmful choice, because a row dropped from the middle is a gap that never heals.
        """
        if self._closed:
            return
        if self._items.qsize() >= MAXIMUM_HELD_TAIL_ITEMS:
            self.close()
            return
        self._items.put_nowait(item)

    def close(self) -> None:
        """Stop watching. The iterator finishes; nothing else is delivered."""
        if self._closed:
            return
        self._closed = True
        self._live_tail.forget(self)
        self._items.put_nowait(_TAIL_CLOSED)

    async def next_item(self) -> ConversationTailItem | None:
        """The next thing to show, or nothing at all once the watch has been closed.

        Waiting here can be abandoned — a reader that gives up to send a heartbeat and
        comes back later has lost nothing, because an item that was not taken is still
        waiting where it was.
        """
        item = await self._items.get()
        return None if isinstance(item, _TailClosed) else item

    async def __aiter__(self) -> AsyncIterator[ConversationTailItem]:
        while (item := await self.next_item()) is not None:
            yield item

    def __enter__(self) -> ConversationTailSubscription:
        return self

    def __exit__(self, *exception: object) -> None:
        self.close()


class ConversationLiveTail:
    """Every open watcher, by conversation. In this process only, and only for now."""

    def __init__(self) -> None:
        self._subscriptions: dict[str, set[ConversationTailSubscription]] = {}

    def subscribe(self, conversation_id: str) -> ConversationTailSubscription:
        """Start watching a conversation. Registered before this returns."""
        subscription = ConversationTailSubscription(self, conversation_id)
        self._subscriptions.setdefault(conversation_id, set()).add(subscription)
        return subscription

    def forget(self, subscription: ConversationTailSubscription) -> None:
        watchers = self._subscriptions.get(subscription.conversation_id)
        if watchers is None:
            return
        watchers.discard(subscription)
        if not watchers:
            del self._subscriptions[subscription.conversation_id]

    def publish_event(self, event: StoredConversationEvent) -> None:
        """Show a row that has just been committed to everyone watching it."""
        for subscription in self._watchers(event.conversation_id):
            subscription.deliver(event)

    def publish_frame(self, conversation_id: str, frame: ConversationLiveTailFrame) -> None:
        """Show a piece of something that has not finished arriving."""
        for subscription in self._watchers(conversation_id):
            subscription.deliver(frame)

    def close_all_subscriptions(self) -> None:
        """End every open watch.

        A tail is idle nearly all the time and only ends when its browser leaves, so a
        server that is shutting down closes them itself rather than waiting for readers
        that are not coming back.
        """
        for subscription in tuple(self._all_subscriptions()):
            subscription.close()

    def open_subscription_count(self) -> int:
        return sum(1 for _ in self._all_subscriptions())

    def _watchers(self, conversation_id: str) -> tuple[ConversationTailSubscription, ...]:
        # A copy, because delivering may close a subscription and change the set.
        return tuple(self._subscriptions.get(conversation_id, ()))

    def _all_subscriptions(self) -> Iterator[ConversationTailSubscription]:
        for watchers in tuple(self._subscriptions.values()):
            yield from tuple(watchers)
