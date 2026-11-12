# Accepted migration differences

- Conversation-view rebuild: 373.7 seconds in TS versus 94.5 seconds in Python for the same 200,000 events (about 3.96 times as long), accepted by the Manager as a known performance difference with a follow-up before the first projection-version change after cutover. [Measured profile](https://github.com/khushal-jagota/headroom/blob/a201941e82dde385572a87602f127e0ae18e379d/server/lifecycle/REBUILD_PROFILE.md). [Implementation difference](conversation/DEVIATIONS.md).
