import type { MarkdownPresentation, MarkdownTargetDemand, MarkdownTargetState } from '../../markdown/presentationContracts';
import type { ConversationContentLocation } from './anchorContracts';

export const conversationTextPresentations = Symbol('conversation direct text presentations');
/** A viewport owns only its direct message surfaces. Nested previews never register here. */
export class TextPresentationRegistry {
  private rows = new Map<string, Map<number, { host: HTMLElement; presentation: MarkdownPresentation }>>();
  constructor(private changed: () => void) {}
  register(key: string, piece: number, host: HTMLElement, presentation: MarkdownPresentation): () => void {
    let row = this.rows.get(key);
    if (!row) { row = new Map(); this.rows.set(key, row); }
    const entry = { host, presentation };
    row.set(piece, entry);
    this.changed();
    return () => { if (row.get(piece) === entry) { row.delete(piece); if (!row.size) this.rows.delete(key); this.changed(); } };
  }
  capture(key: string, viewport: DOMRectReadOnly): ConversationContentLocation | undefined {
    const candidates = [...this.rows.get(key)?.entries() ?? []].flatMap(([pieceIndex, entry]) => {
      if (!entry.host.isConnected) return [];
      const captured = entry.presentation.capture(viewport);
      return captured ? [{ pieceIndex, captured }] : [];
    }).sort((a, b) => a.captured.rect.top - b.captured.rect.top);
    const first = candidates[0];
    return first ? Object.freeze({ pieceIndex: first.pieceIndex, text: first.captured.location, lineOffset: first.captured.rect.top - viewport.top }) : undefined;
  }
  demand(key: string, location: ConversationContentLocation): MarkdownTargetDemand | null {
    return this.rows.get(key)?.get(location.pieceIndex)?.presentation.demand(location.text) ?? null;
  }
  measure(key: string, location: ConversationContentLocation): MarkdownTargetState {
    return this.rows.get(key)?.get(location.pieceIndex)?.presentation.measure(location.text) ?? {kind:'pending'};
  }
}
