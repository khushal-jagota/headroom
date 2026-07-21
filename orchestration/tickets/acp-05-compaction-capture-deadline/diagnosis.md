# Compaction capture deadline diagnosis

## Result

Hermes created a valid compacted fork. Panels' reused 10-second shutdown budget expired during the
fork/load transition before it could install that fork.

Real runtime evidence:

- Hermes persisted fork `66c4a130-1555-4d9b-b0b7-eb2aaad6acaa` with 13 messages and exactly one valid
  7,565-character compaction summary.
- The real `HermesAcpTurnStrategy.capture_compaction_from_updates()` normalizes that replay as
  `state="compacted"`.
- Panels' durable Chief binding remained original session `2dc5f884-fd9c-43ab-9c6b-ba6f90d21c26`,
  binding generation 6.

The broker currently sources its compaction budget from
`ACP_CONVERSATION_SERVICE_SHUTDOWN_TIMEOUT_SECONDS = 10`. Hermes's official fork constructs a full new
agent, copies history, and persists it before replying. The live fork consumed nearly that full budget.
Panels then attempted private candidate load with the same absolute deadline; it expired. Exact-N
restore also received that already-expired deadline, so the registry invalidated the uncertain child
and raised generation-fatal.

A scaled 0.2-second registry reproduction produced the same
`ConversationRuntimeGenerationFatal: compaction preparation failed and original session could not be
restored`: fork consumed 0.16 seconds, candidate load exhausted the remainder, original load was
attempted with no remaining budget, the durable binding stayed unchanged, and generation N was
invalidated.

The visible `Context compressed: 30 -> 13...` line is Hermes's slash-command response. It proves the
model-side compression happened; it does not prove Panels completed the official fork, private replay,
durable CAS, and browser transition.
