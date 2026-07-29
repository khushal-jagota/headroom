# Contract lock

## Contract exports

`web/src/lib/conversation/runControls/contracts.ts` exports exactly the following
types:

```ts
export type ComposerRunSelection = Readonly<{
  deliveryMode: PromptDeliveryMode;
  pickedBackend: ConversationBackendKey | null;
  pickedModel: string | null;
  pickedReasoningEffort: string | null;
}>;

export type ComposerRunControlsInput = Readonly<{
  selection: ComposerRunSelection;
  backendKey: ConversationBackendKey | null;
  conversationExists: boolean;
  running: boolean;
  current: RunValues;
  models: readonly BackendModel[];
  backends: readonly BackendSnapshot[];
  effortOptions: readonly string[];
  startsOnModel: string | null;
  startsOnReasoningEffort: string | null;
  inputDisabled: boolean;
  hasSendableContent: boolean;
  sendsInFlight: number;
}>;

export type ComposerRunSelectionIntent =
  | { intent: "choose_backend"; backendKey: ConversationBackendKey }
  | { intent: "choose_model"; model: string }
  | { intent: "choose_reasoning_effort"; reasoningEffort: string }
  | { intent: "choose_delivery_mode"; deliveryMode: PromptDeliveryMode };

export type ComposerRunControlChoice = Readonly<{
  value: string;
  name: string;
  detail: string | null;
}>;

export type ComposerRunControlsView = Readonly<{
  normalizedSelection: ComposerRunSelection;
  carriedRunValues: RunValues;
  effectiveDeliveryMode: PromptDeliveryMode;
  disabled: boolean;
  backend: Readonly<{
    showing: ConversationBackendKey | null;
    locked: boolean;
  }>;
  model: Readonly<{
    value: string;
    choices: readonly ComposerRunControlChoice[];
    title: string;
  }>;
  effort: Readonly<{
    value: string;
    choices: readonly ComposerRunControlChoice[];
    bare: boolean;
  }> | null;
  delivery: Readonly<{
    selected: PromptDeliveryMode;
    options: readonly DeliveryOption[];
  }> | null;
  submit: Readonly<{
    action: "send" | "stop";
    active: boolean;
    sending: boolean;
    disabled: boolean;
    title: string;
    ariaLabel: string;
  }>;
}>;

export type ComposerRunControlIntents = Readonly<{
  chooseBackend: (backendKey: ConversationBackendKey) => void;
  chooseModel: (model: string) => void;
  chooseReasoningEffort: (reasoningEffort: string) => void;
  chooseDeliveryMode: (deliveryMode: PromptDeliveryMode) => void;
  send: () => void;
  stop: () => void;
}>;
```

It exports nothing else.

`web/src/lib/conversation/runControls/logic/runSelection.ts` exports exactly:

```ts
export function resolveComposerRunControls(
  input: ComposerRunControlsInput
): ComposerRunControlsView;

export function applyComposerRunSelectionIntent(
  input: ComposerRunControlsInput,
  intent: ComposerRunSelectionIntent
): ComposerRunSelection;
```

It exports nothing else.

The logic imports every shared shape from `contracts.ts`; the renderer imports its view
and intent shapes from `contracts.ts`. No contract shape is redeclared locally.

## Resolver rules

- `normalizedSelection` clears a picked model only when the active catalog is non-empty
  and does not offer it.
- It clears a picked effort when the model actually showing does not offer it.
- Catalog/default reconciliation never mutates its input.
- An existing conversation shows and locks its own backend. Before one exists, an
  explicitly picked backend shows its reported catalog/defaults.
- Before a conversation exists, carried model is the explicit pick or the concrete model
  the active backend would start on. For an existing conversation, only the explicit
  model pick is carried.
- Untouched effort and backend are never invented as picks. A backend is carried only
  before creation and only when explicitly chosen.
- A current or preselected model/effort absent from the catalog remains visible and is
  added to its picker choices.
- Model choice details retain the existing catalog-detail/model-id rule.
- Effort options belong to the model actually showing, including a model's explicit
  empty list.
- Delivery options use the conversation's actual `backendKey`, exactly as before.
  `delivery` is null while not running.
- `effectiveDeliveryMode` is the selected mode while running and `run_when_free` while
  idle.
- Submit projection retains the exact existing class-state meaning, disabled rule,
  tooltip, accessible label, and send/stop action.

## Intent rules

- A locked or already-showing backend choice is a no-op.
- Choosing a new backend before conversation creation changes `pickedBackend` and clears
  model and effort picks.
- Choosing model, effort, or delivery changes only that value.
- Intent application performs no Svelte, DOM, focus, callback, or revision side effect.

## Renderer

`ComposerRunControls.svelte` has exactly:

```ts
let {
  view,
  intents
}: {
  view: ComposerRunControlsView;
  intents: ComposerRunControlIntents;
} = $props();
```

It has no bindable prop, exported method, event dispatcher, context, store, or local
canonical selection state.

It renders, in order and without a wrapper:

1. model picker with the backend rail snippet when a backend is showing;
2. effort picker when `view.effort` is non-null;
3. delivery segment when `view.delivery` is non-null;
4. send/stop button.

It retains the exact existing selectors, attributes, classes, accessibility labels,
titles, keying, and button text.

## Parent component

- `ConversationComposer` keeps its public prop interface exactly.
- It owns one `$state<ComposerRunSelection>` and no separate mode/backend/model/effort
  state.
- It derives one view and uses `view.carriedRunValues` and
  `view.effectiveDeliveryMode` for the send transaction.
- Its normalization effect applies only `view.normalizedSelection` and does not record a
  user draft revision.
- Applying a restored `ComposerDraft` replaces model/effort in the selection while
  retaining backend and delivery mode.
- One local intent handler applies pure selection intents. It records a draft revision
  only for a genuine backend/model/effort change. Model/effort choices restore textarea
  focus; backend and delivery choices retain their existing focus behavior.
- Send/stop remain component-owned operations exposed through the intents object.
- Production DOM outside the moved fragment is unchanged.
