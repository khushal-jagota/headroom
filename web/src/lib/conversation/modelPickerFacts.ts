import type { SelectionKind } from "./modelResolutionWire";
import type { ConversationBackendKey } from "./wire";

/** Caller-owned intent is independent of advisory catalogue/provider observations. */
export type ModelPickerDisplaySelection = Readonly<{
  backendKey: ConversationBackendKey | null;
  model: string | null;
  reasoningEffort: string | null;
  selectionKind?: SelectionKind;
}>;
