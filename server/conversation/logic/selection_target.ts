/** The model and reasoning effort a prompt asks its child to run with. */
import type { BackendSelection } from '../../drivers/core/selection.ts';

export interface SelectionChange {
  readonly model_change: string | null;
  readonly reasoning_effort_change: string | null;
  /** True when the sender named the effort, even as null (the model default). */
  readonly reasoning_effort_is_set: boolean;
}
/** The change applied to a selection. A null effort change keeps its effort unless the sender set it to null. */
export function selection_target(
  message: SelectionChange,
  selection: BackendSelection,
): BackendSelection {
  return {
    model: message.model_change ?? selection.model,
    reasoning_effort:
      message.reasoning_effort_change === null && !message.reasoning_effort_is_set
        ? selection.reasoning_effort
        : message.reasoning_effort_change,
  };
}
