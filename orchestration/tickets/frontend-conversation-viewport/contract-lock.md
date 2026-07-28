# Contract lock

`web/src/components/conversation/viewport/ConversationViewport.svelte` accepts exactly:

```ts
{
  conversationId: string;
  rows: readonly TranscriptRow[];
  outgoingMessages: readonly OutgoingMessage[];
  models: readonly BackendModel[];
  ownSenderLabel: string | null;
  livenessPulse: number;
  conversationState: ConversationState | null;
  emptyState?: Snippet;
  onDiscardHeldPrompt?: (messageId: string) => void;
}
```

The pane passes every required prop explicitly. The component does not bind a prop back
to its parent and emits no custom event.

Observable component contract:

- It renders one `.chat-thread-shell` containing the existing
  `[data-conversation-thread]` region, transcript, optimistic messages, optional reserved
  space, and optional Latest control.
- It preserves existing data attributes, classes, roles, accessible names, keyed
  outgoing-message order, and event behavior.
- `conversationState` is an input to restoration across height changes; the viewport does
  not decide or mutate the state.
- `emptyState` is rendered only when both canonical rows and optimistic outgoing messages
  are empty.
- `onDiscardHeldPrompt` is offered only for an outgoing message whose known fate is
  `waiting_for_the_agent`.

Everything else is private implementation: element bindings, measured pixels, following
state, held views, timers/request counters, geometry operations, scroll handlers, Svelte
effects, and viewport constants.
