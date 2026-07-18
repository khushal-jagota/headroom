"""Isolated configuration for the neutral-relay tunables.

PRINCIPLES: "Anything intended to be tuned later (sizes, formulas, timings, limits)
lives in an isolated configuration module, not inline." These are the only knobs the
neutral relay layer exposes; every module imports them from here rather than inlining a
literal. Named ``Final`` constants only — no logic.
"""

from __future__ import annotations

from typing import Final

# The bound on the REAL downstream backpressure queue (`conn.outbound`). The neutral
# route swaps `conn.outbound` for an `OverflowSignallingQueue(maxsize=this)` immediately
# after `register_downstream()`. Reaching it means the browser consumer is too slow;
# policy = disconnect-the-slow-consumer (plan §6).
NEUTRAL_OUTBOUND_MAX_FRAMES: Final = 512

# The deterministic bound on a tool-activity preview string (`ToolActivityEvent.preview`).
# Previews are truncated with `preview[:TOOL_PREVIEW_MAX_CHARS]` (plan §2).
TOOL_PREVIEW_MAX_CHARS: Final = 2000

# The bound on the transcript-mirror tee's internal work queue (settled-turn records
# awaiting the write-behind worker). Reaching it drops the oldest record with a log
# line — the mirror is best-effort and off the conversation path (plan §5).
TEE_WORK_QUEUE_MAX_RECORDS: Final = 1000
