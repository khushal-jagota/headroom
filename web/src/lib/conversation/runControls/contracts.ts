import type { RunValues } from "../composer";
import type {
  BackendModel,
  BackendSnapshot,
  ConversationBackendKey,
} from "../wire";
import type { ModelPickerView } from "../modelPicker";
import type { DriverPresentation } from "../../drivers/contracts";

export type ComposerRunSelection = Readonly<{
  pickedBackend: ConversationBackendKey | null;
  pickedModel: string | null;
  pickedReasoningEffort: string | null;
  effortIsSet?: boolean;
}>;

export type ComposerRunControlsInput = Readonly<{
  selection: ComposerRunSelection;
  readError?: string | null;
  backendKey: ConversationBackendKey | null;
  conversationExists: boolean;
  running: boolean;
  current: RunValues;
  models: readonly BackendModel[];
  backends: readonly BackendSnapshot[];
  /** The server's driver list; undefined while it loads. */
  drivers: readonly DriverPresentation[] | undefined;
  effortOptions: readonly string[];
  startsOnModel: string | null;
  startsOnReasoningEffort: string | null;
  inputDisabled: boolean;
  hasSendableContent: boolean;
  sendsInFlight: number;
  /** The mode the person last chose. It only matters while a turn runs. */
  deliveryMode?: import("../wire").PromptDeliveryMode;
  /** Whether the running turn can take a message into itself. Absent is the older
   *  caller that never asked, and keeps Steer on offer. */
  supportsSteer?: boolean;
  /** What a waiting ask makes of the draft. "answers": the draft is the person's own answer
   *  to the question on screen, so it goes to the question and not into the turn. "holds":
   *  the turn is blocked on the person (a permission, a key, a question with fixed answers),
   *  so nothing can be read into it until they decide; a message waits for the turn to end,
   *  or ends the turn now. Absent or null, no ask is waiting. */
  askWaiting?: "answers" | "holds" | null;
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
  readError?: string | null;
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
  /** How a send is delivered: the person's choice while a turn runs, and with nothing
   *  running the one way there is, a message that starts the next turn. */
  deliveryMode: import("../wire").PromptDeliveryMode;
  /** The choice is only drawn while a turn runs: with nothing running every mode means
   *  the same thing. */
  showDeliveryMode: boolean;
  /** The modes on offer while a turn runs, in the order they are drawn. */
  deliveryModes: readonly import("../wire").PromptDeliveryMode[];
}>;

export type ComposerRunControlIntents = Readonly<{
  chooseBackend: (backendKey: ConversationBackendKey) => void;
  chooseModel: (model: string, reasoningEffort: string | null) => void;
  chooseReasoningEffort: (reasoningEffort: string) => void;
  chooseDeliveryMode: (mode: import("../wire").PromptDeliveryMode) => void;
  send: () => void;
  stop: () => void;
}>;
