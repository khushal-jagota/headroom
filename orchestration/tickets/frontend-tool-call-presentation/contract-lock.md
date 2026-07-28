# Contract lock

The new external seam is exactly:

```ts
export type ToolCallPresentation = {
  iconPaths: readonly string[];
  title: string;
  summary: string | null;
  detail: string | null;
  canExpand: boolean;
};

export function presentToolCall(row: ToolCallRow): ToolCallPresentation;
```

It lives at `web/src/lib/conversation/toolCallPresentation/index.ts`; callers import
from the `toolCallPresentation` module folder.

`ToolCallRow` is imported as a type from the existing transcript contract.

Generic conversation detail has one separate shared interface:

```ts
export function readableConversationDetail(
  detail: string | null | undefined
): string | null;
```

No glyph type, icon lookup, shell helper, line type, truncation helper, detail-comparison
helper, backend-specific parser, or constants are exported.

Observable rules:

- `detail` is made from `row.progress ?? row.detail`.
- `canExpand` is true only when `detail` is non-null and the visible title/summary do not
  already say the whole detail.
- Known tool kinds retain their existing SVG paths; unknown kinds use the existing
  neutral three-dot paths.
- Existing title, summary, truncation, and detail-formatting strings do not change.
