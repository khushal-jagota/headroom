# Accepted plan

1. Add near-bottom measurement and follow-mode state to `ChatPanel`, initializing loaded conversations at the bottom and updating follow mode from reader scrolling.
2. React to transcript and live-output growth after rendering: keep the thread pinned only while follow mode is active, otherwise preserve its scroll position.
3. Add and style a jump-down control whose visibility depends only on meaningful distance from the conversation bottom; clicking it—or manually returning near the bottom—restores follow mode.
4. Extend the existing chat browser flow to prove initial positioning, live following, preserved scroll-back, distance-based control visibility even without new content, and both ways of resuming follow.
5. Update the chat documentation to describe the new behavior, then run `./verify`.
