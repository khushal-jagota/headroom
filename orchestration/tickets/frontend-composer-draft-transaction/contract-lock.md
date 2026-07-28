# Contract lock

## Public component

`ConversationComposer.svelte` keeps its existing prop interface exactly. No prop,
callback, event, exported method, or context entry is added or removed.

Its existing DOM selectors, class names, accessibility labels, direct-child ordering,
and always-mounted textarea identity are behavior contracts.

## Private transaction module

`draftTransaction.ts` exports exactly these types and functions:

```ts
export type ComposerDraft = Readonly<{
  text: string;
  pendingImages: readonly PendingConversationImage[];
  pickedModel: string | null;
  pickedReasoningEffort: string | null;
  compositionRevision: number;
}>;

export type ComposerSendAttempt = Readonly<{
  content: readonly SentMessagePiece[];
  carriedRunValues: RunValues;
  draftBeforeSend: ComposerDraft;
  draftAfterSend: ComposerDraft;
}>;

export type RefusedComposerSendRestoration = Readonly<{
  restored: boolean;
  draft: ComposerDraft;
  nextImageId: number;
}>;

export function beginComposerSend(
  draft: ComposerDraft,
  carriedRunValues: RunValues,
  mode: PromptDeliveryMode
): ComposerSendAttempt;

export function restoreRefusedComposerSend(
  currentDraft: ComposerDraft,
  attempt: ComposerSendAttempt,
  nextImageId: number,
  imageIntakesInFlight: number
): RefusedComposerSendRestoration;
```

It exports nothing else.

## Begin-send rules

- Content is the trimmed non-empty text piece, when present, followed by every pending
  image in visible order.
- `draftBeforeSend` is an immutable snapshot of exactly the supplied draft.
- `draftAfterSend` always has empty text and no pending images.
- For `steer`, `draftAfterSend` retains the before-send model and effort picks because a
  steer cannot consume them.
- For `run_when_free` and `send_now`, `draftAfterSend` clears the model and effort picks.
- The composition revision is unchanged by beginning a send.
- `carriedRunValues` is the exact value supplied by the component; the transaction does
  not resolve backend catalogs or defaults.
- The module performs no Svelte, DOM, focus, URL, network, or resource-release side
  effect.

## Refusal-restoration rules

Restoration is allowed only when:

- no image intake is in flight;
- the current composition revision equals the attempt's after-send revision;
- current text, images, model pick, and effort pick equal the attempt's after-send draft.

When allowed:

- sent text and images return in their original order;
- restored images use the existing `restoredPendingImages` representation and receive
  ids starting at `nextImageId`;
- the before-send model and effort picks return;
- the returned revision is unchanged;
- the returned `nextImageId` follows the restored images.

When not allowed, the current draft and `nextImageId` are returned unchanged and
`restored` is false.

## Component-side rules

- One local named operation advances `compositionRevision`; no other direct increment
  remains.
- `send()` waits for `intakeTail` before reading the draft.
- The component applies `draftAfterSend` and releases the before-send image resources
  before awaiting `onSend`.
- Only a false `onSend` result invokes refusal restoration.
- The component applies a successful restoration before `tick()`, then focuses the same
  textarea and places the caret at the restored text's end if the restored draft is still
  current.
- Backend selection remains sticky exactly as before; it is not part of the cleared draft.
- A successful steer retains model and effort picks. The next accepted non-steer send
  consumes them.
