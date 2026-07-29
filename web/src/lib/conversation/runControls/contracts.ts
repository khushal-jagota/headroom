import type { DeliveryOption, RunValues } from "../composer";
import type {
  BackendModel,
  BackendSnapshot,
  ConversationBackendKey,
  PromptDeliveryMode
} from "../wire";

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
