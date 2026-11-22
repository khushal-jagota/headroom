import { errorMessage } from "../ui";

/** What a Settings switch shows while its save is in flight, and after.
 *
 * A switch moves as soon as it is pressed. If the save fails, it moves back to the value
 * the server holds, and the row says so. Only the latest press may move it back: an older
 * press that fails after a newer one would otherwise undo the newer one.
 */
export type OptimisticSwitchState = { shown: boolean; error: string | null; attempt: number };

export function createOptimisticSwitch(initial: boolean): OptimisticSwitchState {
  return { shown: initial, error: null, attempt: 0 };
}

export async function toggleOptimistically(
  state: OptimisticSwitchState,
  next: boolean,
  save: (next: boolean) => Promise<unknown>,
  failureText: string,
  saved: () => boolean
): Promise<void> {
  const attempt = state.attempt + 1;
  state.attempt = attempt;
  state.shown = next;
  state.error = null;
  try {
    await save(next);
  } catch (error) {
    if (state.attempt !== attempt) return;
    state.shown = saved();
    const detail = errorMessage(error);
    state.error = detail === "request failed" ? failureText : `${failureText} ${detail}`;
  }
}
