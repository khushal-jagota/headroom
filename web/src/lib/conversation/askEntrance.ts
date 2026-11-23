/** An existing ask is restored in place. Only a new request on a visible, initialized
 * reader earns an entrance. Reader, visibility, and dock changes reset that permission. */
export interface AskEntranceContext {
  source: object | null;
  ready: boolean;
  visible: boolean;
  mode: string | null;
  requestKey: string | null;
}
/** The status the waiting asks are read from. A reader that replaces another for the same
 * conversation (the dock opened, the lens or the tab changed) starts with no status, so the
 * asks are read from what the replaced reader last knew until the new one says otherwise, or
 * its stream fails. Nothing waiting on the person disappears and comes back. */
export function statusForAsks<S>(
  reader: { id: string; status: S | null; trouble: boolean } | null,
  replaced: { id: string; status: S | null } | null
): S | null {
  if (reader === null) return null;
  if (reader.status !== null || reader.trouble) return reader.status;
  return replaced?.id === reader.id ? replaced.status : null;
}

export class AskEntranceTracker {
  private previous: AskEntranceContext | null = null;
  private animate = false;

  update(current: AskEntranceContext): boolean {
    const previous = this.previous;
    const continuous = previous !== null && previous.ready && current.ready
      && previous.visible && current.visible && previous.source === current.source
      && previous.mode === current.mode;
    if (!continuous || current.requestKey === null) this.animate = false;
    else if (current.requestKey !== previous.requestKey) this.animate = true;
    this.previous = current;
    return this.animate;
  }
}
