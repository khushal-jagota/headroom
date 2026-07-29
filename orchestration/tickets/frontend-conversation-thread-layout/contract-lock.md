# Contract lock

The new external seam is exactly:

```ts
export type ThreadItem =
  | {
      kind: "row";
      key: string;
      row: TranscriptRow;
      behindTheFoldOf: string | null;
    }
  | {
      kind: "turn";
      key: string;
      turnKey: string;
      settled: boolean;
      stopped: boolean;
      plan: readonly PlanEntry[] | null;
      startedAtUnixMilliseconds: number | null;
      ending: ConversationTurnEnding | null;
      isLatest: boolean;
      durationSeconds: number | null;
      toolCallCount: number;
      foldedMessageCount: number;
    }
  | {
      kind: "work_group";
      key: string;
      turnKey: string;
      entries: readonly ToolCallRow[];
      settled: boolean;
    };

export function threadItems(rows: readonly TranscriptRow[]): ThreadItem[];
```

It lives at `web/src/lib/conversation/threadLayout/index.ts`; callers import from the
`threadLayout` module folder.

`TranscriptRow` and `ToolCallRow` are imported as types from
`../transcript`. `ConversationTurnEnding` and `PlanEntry` are imported as types from
`../wire`.

There are no other exports. In particular, open-turn state, layout constants, counters,
settlement helpers, presentation wording, timer calculations, and work-group display
policy remain private to their appropriate modules.

Observable rules:

- A prompt opens one stable turn anchor; steer prompts join it.
- Consecutive tool-call rows form one work group at their chronological position.
- Settling a turn folds all but its final agent message and settles every work group.
- Prompt and permission rows never go behind a turn fold.
- A stopped turn has no invented duration.
- The newest turn alone has `isLatest: true`.
- The newest stated plan alone remains attached to a turn anchor.
