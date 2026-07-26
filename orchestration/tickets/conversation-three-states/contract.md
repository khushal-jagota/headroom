# The conversation's three states — contract

Implements `kickoff.md`. Read that first; this fixes the names so four pieces of work can
happen at once without colliding.

## The enum

New file `web/src/lib/conversation/conversationState.ts`:

```ts
/** How far open the conversation is. The person moves it; nothing else does. */
export type ConversationState = "rest" | "peeked" | "opened";
```

## The property the page sets

`LiveConversation` gains exactly one new prop:

```ts
conversationState = $bindable<ConversationState | null>(null)
```

- `null` — the conversation is not a layer. It fills its container exactly as it does
  today. Chief of Staff, the Workspace desk and the dev route pass nothing and are
  unchanged.
- `"rest" | "peeked" | "opened"` — the conversation is a layer, at that state. The page
  sets what it opens in by initialising the variable it binds; the conversation writes back
  when the person moves it; the page may write it too.

`LiveConversation` passes it straight through to `ConversationPane`, which is where the
state is drawn. Nothing else in the chain knows about it.

## What each state is

| state | pane head | transcript | rest bar | look |
| --- | --- | --- | --- | --- |
| `rest` | hidden | hidden (still mounted) | shown | no card — the composer sits on the page |
| `peeked` | shown | shown | **gone** | a card over the page |
| `opened` | shown | shown | gone | full height, not a card |

## Transitions

- **rest → peeked** — the person clicks the composer's input.
- **peeked → opened** — `[data-conversation-expand]`, in the pane head.
- **opened → peeked** — `[data-conversation-collapse]`, same place.
- **peeked → rest**, **opened → peeked** — the person clicks the ticket behind, or presses
  Escape. One state back per click. The click is not swallowed: the ticket behind stays
  readable, scrollable and clickable.

Nothing changes state on its own. No auto-peek on a permission ask, none on a turn
starting.

## Selectors

Existing ones keep their names. New:

- `[data-conversation-pane][data-conversation-state="rest|peeked|opened"]` — absent when
  the conversation is not a layer.
- `[data-conversation-rest-bar]` — the bar's root, present only at rest.
- `[data-conversation-rest-line]` — the one line's text.
- `[data-conversation-rest-who]` — who produced it, when it is said.
- `[data-conversation-rest-aside]` — the plan's progress, when there is one.
- `[data-conversation-rest-waiting]` — present only when something is waiting on the person.
- `[data-conversation-expand]`, `[data-conversation-collapse]` — the two controls.
- `[data-conversation-layer-host]` — the ticket page's positioned host for the layer.

## The rest line

New file `web/src/lib/conversation/restLine.ts`:

```ts
export type RestLine = {
  /** Who produced it — "you", an agent's label — or null when the line speaks for itself. */
  who: string | null;
  /** The one line. The first line of whatever happened last. */
  text: string;
  /** A quieter fact beside it: the plan's progress while a turn runs. */
  aside: string | null;
  /** Something is waiting on the person. The bar's most important job. */
  waiting: boolean;
  /** When a turn is running, when it started — so the bar counts as the turn head does. */
  workingSinceUnixMilliseconds: number | null;
};

export function restLineFrom(
  rows: readonly TranscriptRow[],
  ownSenderLabel: string,
): RestLine | null;
```

In priority order:

1. A live permission ask is waiting → `waiting: true`, the ask's title as the text. This is
   the whole reason the bar exists: with the conversation closed, a request that needs you
   is already on screen.
2. A turn is running → `text` is the newest tool call's line, `aside` is the plan's
   progress when there is a plan, `workingSinceUnixMilliseconds` is the turn's start.
3. Otherwise → the first line of whatever happened last, whoever produced it. Your own
   message counts. A stopped turn counts.
4. Nothing has happened → `null`, and the bar draws nothing.

## Scroll and draft

One conversation at three heights. `LiveConversation` and `ConversationPane` are mounted
once per page and never remounted, never keyed, never behind an `{#if}`. A transition
changes CSS, nothing else.

The gate the design is most likely to fail: a transition must keep the reader's position in
the transcript and the composer's draft.

## What is split how

- **A — the rest line.** `restLine.ts`, `ConversationRestBar.svelte`, and the unit test.
- **B — the state in the pane.** `conversationState.ts`, `ConversationPane.svelte`,
  `LiveConversation.svelte`. Owns the state, the controls, the scroll preservation, and the
  pane's own scoped styles.
- **C — the ticket page.** `TicketRoute.svelte`, `assets/app.css`. Owns where the layer
  sits, its size per state, and the card look.
- **D — the browser spec.** `tests/e2e/test_conversation_three_states.py`.
