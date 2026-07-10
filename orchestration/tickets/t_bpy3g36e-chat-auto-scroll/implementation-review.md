# Codex implementation review

Model `gpt-5.5`, read-only sandbox, high reasoning. The full review output was surfaced in the worker transcript.

## Findings and disposition

1. **Following was checked only after full output** — accepted. Browser coverage now separately asserts bottom position after the human send/pending state, while planner output is visible during the active turn, and after settlement.
2. **Scroll preservation was checked only once** — accepted. Browser coverage now separately asserts the unchanged viewport after the human line, during live planner output, and after settlement.

The slow fake writes its two token chunks back-to-back before a deterministic pre-completion pause, so the browser-level live assertion uses full output while the active pending turn still exists rather than a race-prone half-token string.

Codex re-reviewed the strengthened test and returned `RESOLVED`, finding no false positive against the ticket contract.
