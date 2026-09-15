import type { RunValues } from "../composer";
import type {
  BackendModel,
  BackendSnapshot,
  ConversationBackendKey,
} from "../wire";
import type { ModelPickerView } from "../modelPicker";

export type ComposerRunSelection = Readonly<{
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
  deliveryMode?: import("../wire").PromptDeliveryMode;
}>;

export type ComposerRunSelectionIntent =
  | { intent: "choose_backend"; backendKey: ConversationBackendKey }
  | { intent: "choose_model"; model: string; reasoningEffort: string | null }
  | { intent: "choose_reasoning_effort"; reasoningEffort: string };

export type ComposerRunControlsView = Readonly<{
  normalizedSelection: ComposerRunSelection;
  carriedRunValues: RunValues;
  disabled: boolean;
  picker: ModelPickerView;
  pickerSource: Readonly<{
    backends: readonly BackendSnapshot[];
    models: readonly BackendModel[];
    backendEffortOptions: readonly string[];
  }>;
  submit: Readonly<{
    active: boolean;
    sending: boolean;
    disabled: boolean;
    title: string;
    ariaLabel: string;
  }>;
  showStop: boolean;
  deliveryMode: import("../wire").PromptDeliveryMode;
}>;

export type ComposerRunControlIntents = Readonly<{
  chooseBackend: (backendKey: ConversationBackendKey) => void;
  chooseModel: (model: string, reasoningEffort: string | null) => void;
  chooseReasoningEffort: (reasoningEffort: string) => void;
  chooseDeliveryMode: (mode: import("../wire").PromptDeliveryMode) => void;
  send: () => void;
  stop: () => void;
}>;
