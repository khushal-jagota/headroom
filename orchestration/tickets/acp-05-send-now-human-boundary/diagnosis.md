# Send Now post-recovery boundary diagnosis

## Result

The merge is owned by Panels' post-reset server epoch, not Hermes and not an incorrect browser
separator.

Hermes stored three distinct durable rows during real Computer Use:

- assistant `Operation interrupted.`;
- user `Reply exactly SEND NOW RECOVERY READY.`; and
- assistant `SEND NOW RECOVERY READY.`

Panels published the successor's ordinary `human_echo` before requested-cancel recovery. The
same-binding reset then intentionally cleared the transcript and optimistic humans. Recovery commit
published only reset, durable replay, ready, and a queue snapshot. It neither restored retained FIFO
human echoes nor the captured Send Now successor echo. The broker then published `started` and ran the
successor. Because `started` is deliberately non-terminal, the browser correctly appended the new
missing-ID agent chunk to the open missing-ID replay group.

## Deterministic proof

An in-memory Vite harness fed the production reducer the exact current epoch and produced:

```text
[{"role":"agent","id":"fallback-1-agent-1","text":"Operation interrupted.SEND NOW RECOVERY READY."}]
```

Adding one post-reset successor `human_echo` produced:

```text
[
  {"role":"agent","text":"Operation interrupted."},
  {"role":"user","text":"Reply exactly SEND NOW RECOVERY READY."},
  {"role":"agent","text":"SEND NOW RECOVERY READY."}
]
```

The recovery commit is the correct owner because it atomically owns reset/replay publication. The
existing compaction commit already demonstrates the required pattern by re-emitting queued human
echoes after ready and before its queue snapshot. No frontend production change or new wire event is
needed.
